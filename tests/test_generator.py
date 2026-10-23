import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import agent, config, db, generator, routes, supervisor, tools


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "l4rpness.db")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(agent, "provider_key", lambda provider: "test-key")
    generator.GENERATORS.clear()
    db.init()
    with db.db() as conn:
        conn.execute(
            "INSERT INTO campaigns (id, target, model, effort) VALUES (1, 'target.example', 'm', 'high')"
        )


def _call(call_id: str, name: str, args: dict):
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args))
    )


def _response(message):
    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    return response


def test_hypothesis_generate_dedupe_and_update():
    args = {
        "title": "SQLi in search",
        "angle": "quote in q reaches the query",
        "vuln_class": "sqli",
        "rationale": "raw f-string",
    }
    allow = tools.allowed_for("generator")
    first, ok1, _ = asyncio.run(tools.execute("hypothesis_generate", args, 1, allow=allow))
    second, ok2, _ = asyncio.run(tools.execute("hypothesis_generate", args, 1, allow=allow))
    assert ok1 and ok2
    assert "hypothesis #1 filed" in first
    assert "already exists" in second

    with db.db() as conn:
        assert conn.execute("SELECT count(*) AS c FROM hypotheses").fetchone()["c"] == 1

    out, ok, _ = asyncio.run(
        tools.execute(
            "hypothesis_update",
            {"hypothesis_id": 1, "status": "working", "result": "testing the quote"},
            1,
            allow=tools.allowed_for("supervisor"),
        )
    )
    assert ok and "working" in out

    listing, ok, _ = asyncio.run(
        tools.execute("hypothesis_list", {}, 1, allow=tools.allowed_for("supervisor"))
    )
    assert ok
    assert "1 working" in listing
    assert "testing the quote" in listing


def test_permission_layer_blocks_foreign_tools():
    out, ok, _ = asyncio.run(
        tools.execute("hypothesis_generate", {"title": "x"}, 1, allow=tools.allowed_for("supervisor"))
    )
    assert not ok and "not available" in out

    out, ok, _ = asyncio.run(
        tools.execute("spawn_hypothesis_generator", {}, 1, allow=tools.allowed_for("generator"))
    )
    assert not ok and "not available" in out

    out, ok, _ = asyncio.run(
        tools.execute("hypothesis_list", {}, 1, allow=tools.allowed_for("supervisor"))
    )
    assert ok


def test_generator_files_hypotheses(monkeypatch):
    calls = [
        _call("c1", "hypothesis_generate", {"title": "A", "angle": "a", "vuln_class": "xss", "rationale": "r"}),
        _call("c2", "hypothesis_generate", {"title": "B", "angle": "b", "vuln_class": "idor", "rationale": "r"}),
        _call("c3", "generator_done", {"status": "done", "summary": "2 filed"}),
    ]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_response(SimpleNamespace(content="filed", tool_calls=calls))
    )
    monkeypatch.setattr("app.agent._client", lambda provider: client)

    state: dict = {}

    async def drive():
        state["id"] = generator.spawn(1, "auth")
        assert state["id"]
        await generator.GENERATORS[1][state["id"]]

    asyncio.run(drive())
    generator_id = state["id"]

    with db.db() as conn:
        rows = conn.execute("SELECT title FROM hypotheses ORDER BY id").fetchall()
        gen = conn.execute("SELECT status FROM generators WHERE id = ?", (generator_id,)).fetchone()
        stored = conn.execute(
            "SELECT count(*) AS c FROM generator_messages WHERE generator_id = ?", (generator_id,)
        ).fetchone()["c"]
        owners = [row["generator_id"] for row in conn.execute("SELECT generator_id FROM hypotheses")]

    assert [row["title"] for row in rows] == ["A", "B"]
    assert gen["status"] == "done"
    assert stored == 1
    assert all(owner == generator_id for owner in owners)
    assert generator.running_count(1) == 0


def test_generator_limit_and_stop(monkeypatch):
    monkeypatch.setattr(config, "MAX_GENERATORS", 1)
    client = MagicMock()

    async def never(*args, **kwargs):
        await asyncio.Event().wait()

    client.chat.completions.create = never
    monkeypatch.setattr("app.agent._client", lambda provider: client)

    async def drive():
        first = generator.spawn(1, "a")
        assert first
        await asyncio.sleep(0.01)
        assert generator.spawn(1, "b") == 0
        await generator.stop_campaign(1)
        assert generator.running_count(1) == 0

    asyncio.run(drive())

    with db.db() as conn:
        statuses = [row["status"] for row in conn.execute("SELECT status FROM generators")]
    assert statuses == ["cancelled"]


def test_generator_status_and_spotlight():
    with db.db() as conn:
        cur = conn.execute(
            "INSERT INTO generators (campaign_id, focus, status) VALUES (1, 'auth', 'running')"
        )
        generator_id = cur.lastrowid
        assert generator_id is not None
        conn.execute(
            "INSERT INTO generator_messages (generator_id, content, parts) VALUES (?, ?, ?)",
            (
                generator_id,
                "checking",
                json.dumps([
                    {
                        "type": "tool",
                        "id": "c1",
                        "name": "bash",
                        "args": {"cmd": "curl"},
                        "output": "HTTP 200",
                        "ok": True,
                        "ms": 5,
                    }
                ]),
            ),
        )
        conn.execute(
            "INSERT INTO hypotheses (campaign_id, generator_id, title, vuln_class) VALUES (1, ?, 'T1', 'xss')",
            (generator_id,),
        )

    listing, ok, _ = asyncio.run(
        tools.execute("generator_status", {}, 1, allow=tools.allowed_for("supervisor"))
    )
    assert ok
    assert f"#{generator_id}" in listing
    assert "auth" in listing and "HTTP 200" in listing

    detail, ok, _ = asyncio.run(
        tools.execute(
            "generator_status",
            {"generator_id": generator_id, "limit": 5},
            1,
            allow=tools.allowed_for("supervisor"),
        )
    )
    assert ok and "HTTP 200" in detail and "T1" in detail

    line = supervisor._backlog_line(1)
    assert "GEN #" in line


def test_backlog_line():
    line = supervisor._backlog_line(1)
    assert "none yet" in line
    assert "spawn_hypothesis_generator" in line

    allow = tools.allowed_for("generator")
    asyncio.run(tools.execute("hypothesis_generate", {"title": "T1"}, 1, allow=allow))
    line = supervisor._backlog_line(1)
    assert "1 new" in line
    assert "hypothesis_list" in line


def test_a_generator_that_files_nothing_is_nudged(monkeypatch):
    """Its job is filing, not testing: five minutes with nothing filed gets it
    told so, and the operator gets a count of how many are stuck."""
    monkeypatch.setattr(generator, "NUDGE_SECONDS", 0)
    monkeypatch.setattr(
        routes, "push_event", lambda cid, name, data: notices.append((name, data.get("text", "")))
    )
    notices: list = []
    seen: list[list[str]] = []

    with db.db() as conn:
        conn.execute("INSERT INTO generators (id, campaign_id, focus) VALUES (5, 1, 'auth')")

    async def create(**kwargs):
        seen.append([m["content"] for m in kwargs["messages"] if m["role"] == "user"])
        if len(seen) == 1:
            # looks at the target, files nothing
            return _response(_msg([_call("c1", "read_file", {"path": "notes.md"})]))
        return _response(_msg([_call("c2", "generator_done", {"status": "done", "summary": "none"})]))

    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr("app.agent._client", lambda provider: client)

    asyncio.run(generator._run(1, 5, "auth"))

    assert generator.NUDGE in seen[0], "the nudge must ride the very next request"
    assert notices and "1 running generator(s)" in notices[0][1]
    assert notices[0][0] == "notice"


def _msg(calls):
    return SimpleNamespace(content="", reasoning_content="", tool_calls=calls)

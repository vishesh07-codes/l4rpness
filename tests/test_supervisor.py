import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import agent, config, db, routes, supervisor


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "l4rpness.db")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(agent, "provider_key", lambda provider: "test-key")
    routes.RUNNING.clear()
    db.init()


def test_start_refuses_without_key(monkeypatch):
    monkeypatch.setattr(agent, "provider_key", lambda provider: "")
    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (1, 't', 'm')")

    assert supervisor.start(1) is False
    assert supervisor.is_running(1) is False
    with db.db() as conn:
        row = conn.execute("SELECT auto_running FROM campaigns WHERE id = 1").fetchone()
    assert row["auto_running"] == 0


def test_loop_runs_chains_then_stop(monkeypatch):
    chains: list[dict] = []

    async def fake_run(campaign_id, campaign, attachments, history, content, run, files=(), store_user=True, keep=False, tool_kind=None, include_attachments=True):
        chains.append({"history": history, "store_user": store_user, "keep": keep})
        with db.db() as conn:
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'assistant', ?)",
                (campaign_id, f"reply {len(chains)}"),
            )
        return {"ok": True, "stopped": False, "empty": False, "error": ""}

    monkeypatch.setattr(routes, "_run", fake_run)

    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (1, 'target.example', 'm')")

    async def drive():
        assert supervisor.start(1)
        task = supervisor.SUPERVISORS[1]
        for _ in range(200):
            if len(chains) >= 3:
                break
            await asyncio.sleep(0.01)
        supervisor.stop(1)
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(drive())

    assert len(chains) >= 3
    assert all(chain["store_user"] is False and chain["keep"] for chain in chains)
    assert "Begin the engagement against target.example" in chains[0]["history"][-1]["content"]
    assert chains[1]["history"][-1]["content"].startswith(supervisor.CONTINUE_PROMPT)
    # with no backlog yet, the chain carries the spawn nudge along
    assert "spawn_hypothesis_generator" in chains[1]["history"][-1]["content"]
    # the next chain still sees everything the previous one produced
    assert any(m.get("content") == "reply 1" for m in chains[1]["history"])
    assert 1 not in routes.RUNNING
    assert supervisor.is_running(1) is False
    with db.db() as conn:
        row = conn.execute("SELECT auto_running, status FROM campaigns WHERE id = 1").fetchone()
    assert row["auto_running"] == 0
    assert row["status"] == "idle"


def test_start_cancels_a_pending_stop(monkeypatch):
    chains: list[int] = []

    async def fake_run(campaign_id, campaign, attachments, history, content, run, files=(), store_user=True, keep=False, tool_kind=None, include_attachments=True):
        chains.append(1)
        await asyncio.sleep(0.05)
        return {"ok": True, "stopped": False, "empty": False, "error": ""}

    monkeypatch.setattr(routes, "_run", fake_run)

    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (1, 't', 'm')")

    async def drive():
        assert supervisor.start(1)
        task = supervisor.SUPERVISORS[1]
        await asyncio.sleep(0.01)  # the loop is inside its first chain
        supervisor.stop(1)  # operator hits stop
        assert supervisor.start(1)  # a steer arrives right after
        for _ in range(100):
            if len(chains) >= 2:
                break
            await asyncio.sleep(0.01)
        supervisor.stop(1)
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(drive())

    assert len(chains) >= 2


def test_start_after_full_stop_starts_a_fresh_loop(monkeypatch):
    chains: list[int] = []

    async def fake_run(campaign_id, campaign, attachments, history, content, run, files=(), store_user=True, keep=False, tool_kind=None, include_attachments=True):
        chains.append(1)
        return {"ok": True, "stopped": False, "empty": False, "error": ""}

    monkeypatch.setattr(routes, "_run", fake_run)

    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (1, 't', 'm')")

    async def drive():
        assert supervisor.start(1)
        task = supervisor.SUPERVISORS[1]
        for _ in range(200):
            if chains:
                break
            await asyncio.sleep(0.01)
        supervisor.stop(1)
        await asyncio.wait_for(task, timeout=2)
        assert not supervisor.is_running(1)

        # steering a stopped campaign resumes it from the stored context
        assert supervisor.start(1)
        for _ in range(200):
            if len(chains) >= 2:
                break
            await asyncio.sleep(0.01)
        supervisor.stop(1)
        await asyncio.wait_for(supervisor.SUPERVISORS[1], timeout=2)

    asyncio.run(drive())

    assert len(chains) >= 2
    with db.db() as conn:
        row = conn.execute("SELECT auto_running FROM campaigns WHERE id = 1").fetchone()
    assert row["auto_running"] == 0


def test_steer_lands_in_the_next_chain(monkeypatch):
    chains: list[list[dict]] = []

    async def fake_run(campaign_id, campaign, attachments, history, content, run, files=(), store_user=True, keep=False, tool_kind=None, include_attachments=True):
        chains.append(history)
        if len(chains) == 1:
            # what the /steer endpoint does while a chain is running
            with db.db() as conn:
                conn.execute(
                    "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'user', ?)",
                    (campaign_id, "focus on the API surface"),
                )
        return {"ok": True, "stopped": False, "empty": False, "error": ""}

    monkeypatch.setattr(routes, "_run", fake_run)

    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (1, 't', 'm')")

    async def drive():
        assert supervisor.start(1)
        task = supervisor.SUPERVISORS[1]
        for _ in range(200):
            if len(chains) >= 2:
                break
            await asyncio.sleep(0.01)
        supervisor.stop(1)
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(drive())

    assert len(chains) >= 2
    assert {"role": "user", "content": "focus on the API surface"} in chains[1]


def test_history_replays_tool_calls():
    calls = [{"id": "c1", "name": "bash", "args": {"cmd": "id"}, "output": "uid=0", "ok": True, "ms": 3}]
    parts = [
        {"type": "thinking", "text": "running id"},
        {"type": "tool", "id": "c1", "name": "bash", "args": {"cmd": "id"}, "output": "uid=0", "ok": True, "ms": 3},
    ]
    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (1, 't', 'm')")
        conn.execute("INSERT INTO messages (campaign_id, role, content) VALUES (1, 'user', 'go')")
        conn.execute(
            """
            INSERT INTO messages (campaign_id, role, content, reasoning, tool_calls, parts)
            VALUES (1, 'assistant', 'ran it', 'running id', ?, ?)
            """,
            (json.dumps(calls), json.dumps(parts)),
        )
        conn.execute("INSERT INTO messages (campaign_id, role, content) VALUES (1, 'assistant', '')")

    history = supervisor._history(1)

    assert history[0] == {"role": "user", "content": "go"}
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] == "ran it"
    assert history[1]["tool_calls"][0]["function"]["name"] == "bash"
    assert json.loads(history[1]["tool_calls"][0]["function"]["arguments"]) == {"cmd": "id"}
    assert history[2] == {"role": "tool", "tool_call_id": "c1", "content": "uid=0"}
    # the empty placeholder never reaches the model
    assert [m["role"] for m in history] == ["user", "assistant", "tool"]


def test_history_includes_steer_attachment_text():
    upload_dir = config.UPLOAD_DIR / "1" / "chat"
    upload_dir.mkdir(parents=True)
    (upload_dir / "notes.txt").write_text("internal host: 10.0.0.5")

    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (1, 't', 'm')")
        cur = conn.execute(
            "INSERT INTO messages (campaign_id, role, content) VALUES (1, 'user', 'look at this')"
        )
        conn.execute(
            """
            INSERT INTO message_attachments (campaign_id, message_id, filename, content_type, size, path)
            VALUES (1, ?, 'notes.txt', 'text/plain', 23, '1/chat/notes.txt')
            """,
            (cur.lastrowid,),
        )

    history = supervisor._history(1)
    assert "look at this" in history[0]["content"]
    assert "internal host: 10.0.0.5" in history[0]["content"]


def test_create_campaign_starts_the_supervisor(monkeypatch):
    started: list[int] = []
    monkeypatch.setattr(supervisor, "start", lambda campaign_id: started.append(campaign_id) or True)

    async def fake_models():
        return ["m"]

    monkeypatch.setattr(agent, "models", fake_models)

    from fastapi.testclient import TestClient

    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        res = client.post("/api/campaigns", data={"target": "target.example", "model": "m"})
        assert res.status_code == 201, res.text
        assert started == [res.json()["id"]]


def test_compact_folds_old_messages_into_memory(monkeypatch):
    monkeypatch.setattr(config, "COMPACT_KEEP_MESSAGES", 2)

    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (1, 't', 'm')")
        for role, body in [
            ("user", "first task"),
            ("assistant", "first answer"),
            ("user", "second task"),
            ("assistant", "second answer"),
            ("user", "newest task"),
            ("assistant", "newest answer"),
        ]:
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content) VALUES (1, ?, ?)",
                (role, body),
            )

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=SimpleNamespace(content="MEMORY: target t; admin token found."))]
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_resp)
    monkeypatch.setattr("app.agent._client", lambda provider: mock_client)

    folded = asyncio.run(supervisor.compact_campaign(1))
    assert folded == 4

    with db.db() as conn:
        camp = conn.execute(
            "SELECT memory_summary, compacted_until FROM campaigns WHERE id = 1"
        ).fetchone()
    assert "admin token" in camp["memory_summary"]
    assert camp["compacted_until"] == 4

    history = supervisor._history(1)
    assert history[0]["role"] == "user"
    assert "CAMPAIGN MEMORY" in history[0]["content"]
    assert "admin token" in history[0]["content"]
    # the newest messages stay verbatim; everything older is only in the summary
    assert [m["role"] for m in history[1:]] == ["user", "assistant"]
    assert history[-1]["content"] == "newest answer"


def test_compact_needs_a_full_batch(monkeypatch):
    monkeypatch.setattr(config, "COMPACT_KEEP_MESSAGES", 2)

    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (1, 't', 'm')")
        for role, body in [("user", "a"), ("assistant", "b"), ("user", "c")]:
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content) VALUES (1, ?, ?)",
                (role, body),
            )

    assert asyncio.run(supervisor.compact_campaign(1)) == 0


def test_restore_resumes_only_auto_running(monkeypatch):
    started: list[int] = []
    monkeypatch.setattr(supervisor, "start", lambda campaign_id: started.append(campaign_id) or True)

    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model, auto_running) VALUES (1, 't', 'm', 1)")
        conn.execute("INSERT INTO campaigns (id, target, model, auto_running) VALUES (2, 't', 'm', 0)")

    assert supervisor.restore() == 1
    assert started == [1]

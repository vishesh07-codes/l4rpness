import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import agent, config, db, mcp, supervisor, tools, worker


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "l4rpness.db")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(agent, "provider_key", lambda provider: "test-key")
    worker.WORKERS.clear()
    db.init()
    with db.db() as conn:
        conn.execute(
            "INSERT INTO campaigns (id, target, model, effort) VALUES (1, 'target.example', 'm', 'high')"
        )


def _seed(n: int, status: str = "new") -> list[int]:
    ids = []
    with db.db() as conn:
        for i in range(n):
            cur = conn.execute(
                "INSERT INTO hypotheses (campaign_id, title, angle, vuln_class, rationale, status) "
                "VALUES (1, ?, 'angle', 'xss', 'because', ?)",
                (f"H{i + 1}", status),
            )
            ids.append(cur.lastrowid)
    return ids


def _dispatch(ids: list[int], **kwargs):
    args = {"hypothesis_ids": ids, **kwargs}
    out, ok, _ = asyncio.run(
        tools.execute("spawn_hunter", args, 1, allow=tools.allowed_for("supervisor"))
    )
    return out, ok


def _drain(campaign_id: int = 1) -> None:
    async def go():
        tasks = [
            runtime["task"]
            for runtime in worker.WORKERS.get(campaign_id, {}).values()
            if runtime["task"] is not None
        ]
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5)

    asyncio.run(go())


def _hypothesis(hid: int):
    with db.db() as conn:
        return conn.execute("SELECT * FROM hypotheses WHERE id = ?", (hid,)).fetchone()


def _worker(wid: int):
    with db.db() as conn:
        return conn.execute("SELECT * FROM workers WHERE id = ?", (wid,)).fetchone()


def _stub_run(tool_calls: list[tuple[str, dict]]):
    """Stand-in for agent.run that just executes the given worker tools."""

    async def fake_run(campaign, attachments, history, campaign_id, stop=None, **kwargs):
        for index, (name, args) in enumerate(tool_calls):
            call_id = f"c{index}"
            yield "tool_call", {"id": call_id, "name": name, "args": args}
            output, ok, ms = await tools.execute(
                name,
                args,
                campaign_id,
                allow=tools.allowed_for("hunter"),
                work=kwargs.get("work"),
                scope=kwargs.get("scope") or "",
                worker_id=kwargs.get("worker_id"),
            )
            yield "tool_result", {
                "id": call_id,
                "name": name,
                "output": output,
                "ok": ok,
                "ms": ms,
            }

    return fake_run


def test_dispatch_stamps_work_and_ownership(monkeypatch):
    ids = _seed(2)
    monkeypatch.setattr(agent, "run", _stub_run([]))

    async def go():
        worker_id = worker.spawn(1, ids)
        with db.db() as conn:
            rows = conn.execute(
                "SELECT id, status, worker_id FROM hypotheses WHERE campaign_id = 1 ORDER BY id"
            ).fetchall()
        # let the (empty) chain finish before the loop closes
        runtime = worker.WORKERS[1][worker_id]
        runtime["stop"].set()
        await asyncio.gather(runtime["task"], return_exceptions=True)
        return worker_id, [(r["status"], r["worker_id"]) for r in rows]

    worker_id, stamps = asyncio.run(go())
    assert worker_id == 1
    assert stamps == [("working", 1), ("working", 1)]


def test_dead_hypothesis_is_never_dispatched_again(monkeypatch):
    ids = _seed(2)
    with db.db() as conn:
        conn.execute("UPDATE hypotheses SET status = 'dead' WHERE id = ?", (ids[1],))
    monkeypatch.setattr(agent, "run", _stub_run([]))

    out, ok = _dispatch(ids)
    assert not ok
    assert "#2 is dead" in out
    # nothing was started or stamped
    assert worker.running_count(1) == 0
    assert _hypothesis(ids[0])["status"] == "new"


def test_review_hypothesis_only_returns_to_its_own_worker(monkeypatch):
    ids = _seed(1)
    with db.db() as conn:
        conn.execute("INSERT INTO workers (id, campaign_id, title) VALUES (7, 1, 'hunter')")
        conn.execute(
            "UPDATE hypotheses SET status = 'review', worker_id = 7 WHERE id = ?", (ids[0],)
        )
    monkeypatch.setattr(agent, "run", _stub_run([]))

    out, ok = _dispatch(ids)
    assert not ok and "not dispatchable" in out

    # the owning worker may take it back for a deeper dive
    async def go():
        assert worker.spawn(1, ids, worker_id=7) == 7
        runtime = worker.WORKERS[1][7]
        runtime["stop"].set()
        await asyncio.gather(runtime["task"], return_exceptions=True)

    asyncio.run(go())


async def _never_finishes(campaign_id: int, worker_id: int) -> None:
    await asyncio.Event().wait()


def test_batch_cap_is_enforced(monkeypatch):
    monkeypatch.setattr(agent, "run", _stub_run([]))
    out, ok = _dispatch(_seed(4))
    assert not ok and "at most 3" in out


def test_worker_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(config, "MAX_WORKERS", 1)
    monkeypatch.setattr(worker, "_run", _never_finishes)

    async def go():
        assert worker.spawn(1, _seed(1)) == 1
        return worker.running_count(1)

    assert asyncio.run(go()) == 1
    out, ok = _dispatch(_seed(1))
    assert not ok and "limit of 1 running hunters" in out
    worker.WORKERS.clear()


def test_hunt_report_flows_to_review(monkeypatch, tmp_path):
    ids = _seed(1)
    poc = config.WORK_DIR / "1" / "hunters" / "1" / "poc"
    poc.mkdir(parents=True)
    (poc / "run.sh").write_text("#!/bin/sh\necho pwned\n")
    monkeypatch.setattr(
        agent,
        "run",
        _stub_run([
            ("hunt_report", {
                "hypothesis_id": ids[0],
                "verdict": "confirmed",
                "severity": "high",
                "impact": "stored XSS on every viewer",
                "poc_path": "hunters/1/poc/run.sh",
                "repro": "open /search?q=<svg>",
                "evidence": "HTTP 200 with the payload reflected unescaped",
            }),
            ("hunter_done", {"summary": "1 confirmed"}),
        ]),
    )

    _dispatch(ids)
    _drain()

    row = _hypothesis(ids[0])
    assert row["status"] == "review"
    assert row["result"].startswith("confirmed [high]")
    with db.db() as conn:
        hunt = conn.execute("SELECT * FROM hunts").fetchone()
    assert hunt["verdict"] == "confirmed"
    assert hunt["poc_path"] == "hunters/1/poc/run.sh"
    assert hunt["worker_id"] == 1


def test_hunt_report_requires_a_real_poc_for_confirmed(monkeypatch):
    ids = _seed(1)
    args = {"hypothesis_id": ids[0], "verdict": "confirmed", "impact": "rce"}
    out, ok, _ = asyncio.run(
        tools.execute("hunt_report", args, 1, allow=tools.allowed_for("hunter"), worker_id=1)
    )
    assert not ok and "poc_path" in out

    args["poc_path"] = "hunters/1/poc/run.sh"
    out, ok, _ = asyncio.run(
        tools.execute("hunt_report", args, 1, allow=tools.allowed_for("hunter"), worker_id=1)
    )
    assert not ok and "not an existing file" in out


def test_hunt_report_is_denied_to_the_supervisor(monkeypatch):
    ids = _seed(1)
    out, ok, _ = asyncio.run(
        tools.execute(
            "hunt_report",
            {"hypothesis_id": ids[0], "verdict": "not_vulnerable"},
            1,
            allow=tools.allowed_for("supervisor"),
        )
    )
    assert not ok and "not available in this context" in out


def test_worker_requeues_unfinished_hypotheses(monkeypatch):
    ids = _seed(1)
    monkeypatch.setattr(agent, "run", _stub_run([]))  # never reports

    _dispatch(ids)
    _drain()

    row = _hypothesis(ids[0])
    assert row["status"] == "new"
    assert row["worker_id"] is None
    assert row["attempts"] == 1
    assert _worker(1)["status"] == "blocked"


def test_attempts_exhaust_into_dead(monkeypatch):
    ids = _seed(1)
    monkeypatch.setattr(agent, "run", _stub_run([]))
    monkeypatch.setattr(worker, "MAX_ATTEMPTS", 2)

    _dispatch(ids)
    _drain()
    assert _hypothesis(ids[0])["status"] == "new"

    _dispatch(ids)
    _drain()
    row = _hypothesis(ids[0])
    assert row["status"] == "dead"
    assert "never reported" in row["result"]


def test_reconcile_requeues_and_closes_the_run(monkeypatch):
    ids = _seed(1)
    with db.db() as conn:
        conn.execute("INSERT INTO workers (id, campaign_id, title, status) VALUES (4, 1, 'h', 'running')")
        conn.execute(
            "UPDATE hypotheses SET status = 'working', worker_id = 4 WHERE id = ?", (ids[0],)
        )

    worker.reconcile(1)

    assert _worker(4)["status"] == "interrupted"
    row = _hypothesis(ids[0])
    assert row["status"] == "new" and row["worker_id"] is None


def test_reactivation_keeps_the_transcript(monkeypatch):
    ids = _seed(1)
    poc = config.WORK_DIR / "1" / "hunters" / "1" / "poc"
    poc.mkdir(parents=True)
    (poc / "run.sh").write_text("#!/bin/sh\n")
    monkeypatch.setattr(
        agent,
        "run",
        _stub_run([
            ("hunt_report", {
                "hypothesis_id": ids[0],
                "verdict": "confirmed",
                "impact": "auth bypass",
                "poc_path": "hunters/1/poc/run.sh",
            }),
            ("hunter_done", {"summary": "v1"}),
        ]),
    )
    _dispatch(ids)
    _drain()

    assert _hypothesis(ids[0])["status"] == "review"
    with db.db() as conn:
        before = conn.execute(
            "SELECT count(*) AS c FROM worker_messages WHERE worker_id = 1"
        ).fetchone()["c"]

    out, ok = _dispatch(ids, worker_id=1, note="go deeper on the token storage")
    assert ok, out
    _drain()

    with db.db() as conn:
        rows = conn.execute(
            "SELECT role, content FROM worker_messages WHERE worker_id = 1 ORDER BY id"
        ).fetchall()
    assert len(rows) > before
    assert any("go deeper on the token storage" in row["content"] for row in rows)
    # same worker, no new one
    with db.db() as conn:
        assert conn.execute("SELECT count(*) AS c FROM workers").fetchone()["c"] == 1


def test_worker_history_replays_tool_calls():
    with db.db() as conn:
        conn.execute("INSERT INTO workers (id, campaign_id, title) VALUES (9, 1, 'h')")
        conn.execute(
            "INSERT INTO worker_messages (worker_id, role, content) VALUES (9, 'user', 'go')",
        )
        conn.execute(
            """
            INSERT INTO worker_messages (worker_id, role, content, tool_calls, parts)
            VALUES (9, 'assistant', 'checking', ?, ?)
            """,
            (
                json.dumps([{"id": "c1", "name": "bash", "args": {"cmd": "id"}}]),
                json.dumps([{"type": "tool", "id": "c1", "name": "bash", "output": "uid=0"}]),
            ),
        )
    history = worker._history(1, 9)
    assert history[0] == {"role": "user", "content": "go"}
    assert history[1]["tool_calls"][0]["function"]["name"] == "bash"
    assert history[2]["role"] == "tool" and "uid=0" in history[2]["content"]


def test_compact_worker_folds_older_turns(monkeypatch):
    monkeypatch.setattr(config, "COMPACT_KEEP_MESSAGES", 2)
    with db.db() as conn:
        conn.execute("INSERT INTO workers (id, campaign_id, title) VALUES (5, 1, 'h')")
        for body in ["one", "two", "three", "four", "five", "six"]:
            conn.execute(
                "INSERT INTO worker_messages (worker_id, role, content) VALUES (5, 'assistant', ?)",
                (body,),
            )

    response = MagicMock()
    response.choices = [MagicMock(message=SimpleNamespace(content="MEMORY: found the token"))]

    async def create(**kwargs):
        assert "Hunt memory so far" in kwargs["messages"][1]["content"]
        return response

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(agent, "_client", lambda provider: client)

    folded = asyncio.run(worker.compact_worker(1, 5))
    assert folded == 4

    with db.db() as conn:
        row = conn.execute("SELECT memory_summary, compacted_until FROM workers WHERE id = 5").fetchone()
    assert "token" in row["memory_summary"]
    assert row["compacted_until"] > 0

    history = worker._history(1, 5)
    assert "WORKER MEMORY" in history[0]["content"]


def test_dispatch_instruction_carries_the_hypotheses():
    ids = _seed(2)
    with db.db() as conn:
        conn.execute("INSERT INTO workers (id, campaign_id, title) VALUES (3, 1, 'h')")
        for hid in ids:
            conn.execute(
                "UPDATE hypotheses SET status = 'working', worker_id = 3 WHERE id = ?", (hid,)
            )
    text = worker._dispatch_instruction(1, 3)
    assert f"#{ids[0]}" in text and f"#{ids[1]}" in text
    assert "hunters/3/" in text
    assert "hunt_report" in text


# --- reporting reserve -----------------------------------------------------


def test_report_phase_withdraws_everything_but_reporting_tools():
    hunter_report = {s["function"]["name"] for s in tools.schemas_for("hunter", None, "report")}
    assert hunter_report == {"write_file", "hunt_report", "hunter_done"}
    assert tools.allowed_for("hunter", None, "report") == hunter_report

    supervisor_report = tools.allowed_for("supervisor", "continuous", "report")
    assert "spawn_hunter" not in supervisor_report
    assert "write_file" in supervisor_report


def test_report_phase_window(monkeypatch):
    monkeypatch.setattr(config, "MAX_TOOL_STEPS", 300)
    monkeypatch.setattr(config, "REPORT_RESERVE_STEPS", 20)
    assert agent._report_phase("hunter", 279) is False
    assert agent._report_phase("hunter", 280) is True
    # a plain chat turn must keep all of its tools
    assert agent._report_phase(None, 10_000) is False
    # the reserve can never swallow a small budget
    monkeypatch.setattr(config, "MAX_TOOL_STEPS", 2)
    assert agent._report_phase("hunter", 0) is False
    assert agent._report_phase("hunter", 1) is True


# --- browser isolation -----------------------------------------------------


def test_playwright_is_the_scoped_server():
    assert mcp._is_scoped({"name": "playwright", "command": ["playwright-mcp", "--headless"]})
    assert mcp._is_scoped({"name": "pw", "command": ["/usr/bin/playwright-mcp"]})
    assert not mcp._is_scoped({"name": "frank", "command": ["python", "/frank/server.py"]})
    assert not mcp._is_scoped({"name": "remote", "url": "https://x/mcp"})
    assert mcp._session_key("playwright", "worker-3") == "playwright#worker-3"
    assert mcp._session_key("playwright") == "playwright"


def test_open_and_close_scope_sessions(monkeypatch):
    specs = [
        {"name": "frank", "command": ["python", "/frank/server.py"], "tools": []},
        {"name": "playwright", "command": ["playwright-mcp"], "tools": []},
    ]
    monkeypatch.setattr(mcp, "definitions", lambda: specs)
    started: list[str] = []

    async def fake_connect(spec, key=None):
        started.append(key or spec["name"])
        return SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=[])))

    monkeypatch.setattr(mcp, "_connect", fake_connect)
    monkeypatch.setattr(mcp, "schemas", lambda: [])
    saved_sessions = dict(mcp._sessions)
    saved_stacks = dict(mcp._stacks)
    mcp._sessions.clear()
    mcp._stacks.clear()
    try:
        async def go():
            await mcp.open_scope("worker-3")
            return set(mcp._sessions)

        assert asyncio.run(go()) == {"playwright#worker-3"}
        # only the stateful server gets a private copy
        assert started == ["playwright#worker-3"]

        async def close():
            await mcp.close_scope("worker-3")
            return set(mcp._sessions)

        assert asyncio.run(close()) == set()
    finally:
        mcp._sessions.clear()
        mcp._sessions.update(saved_sessions)
        mcp._stacks.clear()
        mcp._stacks.update(saved_stacks)

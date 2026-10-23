import asyncio

import pytest
from fastapi.testclient import TestClient

from app import agent, config, db, routes, supervisor, tools
from app.main import app


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "l4rpness.db")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(agent, "provider_key", lambda provider: "test-key")
    routes.RUNNING.clear()
    db.init()
    yield
    routes.RUNNING.clear()
    for task in list(supervisor.SUPERVISORS.values()):
        task.cancel()


def _names(schemas):
    return [s["function"]["name"] for s in schemas]


def test_schemas_hide_complete_in_continuous():
    assert "complete_engagement" in _names(tools.schemas_for("supervisor", "goal"))
    assert "complete_engagement" not in _names(tools.schemas_for("supervisor", "continuous"))
    # unknown/missing mode fails closed, matching the continuous prompt default
    assert "complete_engagement" not in _names(tools.schemas_for("supervisor"))
    assert "complete_engagement" not in _names(tools.schemas_for("supervisor", None))
    # campaign mappings can be passed directly
    assert "complete_engagement" in _names(tools.schemas_for("supervisor", {"mode": "goal"}))
    assert "complete_engagement" not in _names(
        tools.schemas_for("supervisor", {"mode": "continuous"})
    )
    # generator set is unaffected
    assert "complete_engagement" not in _names(tools.schemas_for("generator", "goal"))


def test_allowed_hide_complete_in_continuous():
    assert "complete_engagement" in tools.allowed_for("supervisor", "goal")
    assert "complete_engagement" not in tools.allowed_for("supervisor", "continuous")
    assert "complete_engagement" not in tools.allowed_for("supervisor")
    assert "hypothesis_list" in tools.allowed_for("supervisor", "continuous")


def test_complete_engagement_rejects_continuous():
    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model, mode) VALUES (1, 't', 'm', 'continuous')")
        conn.execute("INSERT INTO campaigns (id, target, model, mode) VALUES (2, 't', 'm', 'goal')")

    out, ok = asyncio.run(tools.complete_engagement({"summary": "x"}, 1))
    assert not ok
    assert "only available" in out

    out, ok = asyncio.run(tools.complete_engagement({"summary": "found flag"}, 2))
    assert ok
    assert "found flag" in out


def test_complete_engagement_rejects_missing_campaign():
    out, ok = asyncio.run(tools.complete_engagement({"summary": "x"}, 999))
    assert not ok
    assert "not found" in out


def test_complete_engagement_blocked_by_allow_list():
    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model, mode) VALUES (1, 't', 'm', 'continuous')")
    out, ok, _ = asyncio.run(
        tools.execute(
            "complete_engagement",
            {"summary": "x"},
            1,
            allow=tools.allowed_for("supervisor", "continuous"),
        )
    )
    assert not ok
    assert "not available" in out


def test_api_mode_validation(monkeypatch):
    async def fake_models():
        return ["m"]

    monkeypatch.setattr(agent, "models", fake_models)
    monkeypatch.setattr(supervisor, "start", lambda campaign_id: True)

    with TestClient(app) as client:
        res = client.post("/api/campaigns", data={"target": "t", "model": "m", "mode": "goal"})
        assert res.status_code == 201, res.text
        assert res.json()["mode"] == "goal"

        res = client.post("/api/campaigns", data={"target": "t", "model": "m", "mode": "bogus"})
        assert res.json()["mode"] == "continuous"

        res = client.post("/api/campaigns", data={"target": "t", "model": "m"})
        assert res.json()["mode"] == "continuous"


def test_build_prompt_mentions_mode():
    base = {"target": "t", "info": "", "instructions": "steal the flag", "description": "", "scope": ""}
    goal = agent.build_prompt({**base, "mode": "goal"}, [])
    assert "until goal" in goal
    assert "complete_engagement" in goal

    cont = agent.build_prompt({**base, "mode": "continuous"}, [])
    assert "continuous" in cont


def test_generator_prompt_has_no_run_mode():
    # generators share build_prompt but must not be told about
    # complete_engagement — their allow-list blocks it
    from app import generator

    base = {
        "target": "t",
        "info": "",
        "instructions": "steal the flag",
        "description": "",
        "scope": "",
        "mode": "goal",
    }
    prompt = generator._system_prompt(base, [], "xss")
    assert "complete_engagement" not in prompt
    assert "Run mode:" not in prompt
    assert "generator_done" in prompt


def test_supervisor_goal_completes(monkeypatch):
    async def fake_run(campaign_id, campaign, attachments, history, content, run, files=(), store_user=True, keep=False, tool_kind=None):
        # goal campaigns are offered the completion tool
        names = _names(tools.schemas_for(tool_kind, campaign["mode"]))
        assert "complete_engagement" in names
        run["parts"] = [
            {"type": "tool", "name": "complete_engagement", "ok": True, "output": "done"}
        ]
        return {"ok": True, "stopped": False, "empty": False, "error": ""}

    monkeypatch.setattr(routes, "_run", fake_run)

    with db.db() as conn:
        conn.execute(
            "INSERT INTO campaigns (id, target, model, mode) VALUES (1, 't', 'm', 'goal')"
        )

    async def drive():
        assert supervisor.start(1)
        await asyncio.wait_for(supervisor.SUPERVISORS[1], timeout=2)

    asyncio.run(drive())

    with db.db() as conn:
        row = conn.execute("SELECT auto_running, status FROM campaigns WHERE id = 1").fetchone()
    assert row["auto_running"] == 0
    assert row["status"] == "done"


def test_supervisor_continuous_ignores_complete(monkeypatch):
    chains: list = []

    async def fake_run(campaign_id, campaign, attachments, history, content, run, files=(), store_user=True, keep=False, tool_kind=None):
        names = _names(tools.schemas_for(tool_kind, campaign["mode"]))
        assert "complete_engagement" not in names
        chains.append(1)
        # even if the tool somehow ran, the loop must not complete
        run["parts"] = [
            {"type": "tool", "name": "complete_engagement", "ok": True, "output": "done"}
        ]
        return {"ok": True, "stopped": False, "empty": False, "error": ""}

    monkeypatch.setattr(routes, "_run", fake_run)

    with db.db() as conn:
        conn.execute(
            "INSERT INTO campaigns (id, target, model, mode) VALUES (1, 't', 'm', 'continuous')"
        )

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
    with db.db() as conn:
        row = conn.execute("SELECT status FROM campaigns WHERE id = 1").fetchone()
    # stopped manually -> idle, never auto-completed
    assert row["status"] != "done"

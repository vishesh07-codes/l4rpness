import asyncio
import json
import os
import tempfile
from pathlib import Path

os.environ["L4RP_DATA_DIR"] = tempfile.mkdtemp()
os.environ["DEEPSEEK_API_KEY"] = "test-key"

import pytest
from fastapi.testclient import TestClient

from app import agent, config, db, routes, tools
from app.main import app


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    for suffix in ("", "-wal", "-shm"):
        Path(str(config.DB_PATH) + suffix).unlink(missing_ok=True)
    db.init()

    async def fake_models():
        return ["deepseek-chat", "deepseek-flash"]

    monkeypatch.setattr(agent, "models", fake_models)
    monkeypatch.setattr(config, "SEED_HISTORY", None)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def create(client, target="https://example.com", **extra):
    payload = {"target": target, "model": "deepseek-chat", **extra}
    res = client.post("/api/campaigns", data=payload)
    assert res.status_code == 201, res.text
    return res.json()


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"


def test_create_list_get(client):
    created = create(client, target="10.10.10.5", info="lab box")
    listed = client.get("/api/campaigns").json()
    assert [c["id"] for c in listed] == [created["id"]]
    assert listed[0]["message_count"] == 0

    got = client.get(f"/api/campaigns/{created['id']}").json()
    assert got["info"] == "lab box"
    assert got["attachments"] == []


def test_target_required(client):
    res = client.post("/api/campaigns", data={"target": "   "})
    assert res.status_code == 400


def test_unknown_model_falls_back(client):
    campaign = create(client, model="gpt-12")
    assert campaign["model"] == config.DEFAULT_MODEL


def test_config_lists_models(client):
    body = client.get("/api/config").json()
    assert "deepseek-flash" in body["models"]
    assert body["default_model"] in body["models"]


def test_effort_default_and_fallback(client):
    assert create(client)["effort"] == config.DEFAULT_EFFORT
    assert create(client, effort="max")["effort"] == "max"
    assert create(client, effort="ultra")["effort"] == config.DEFAULT_EFFORT


def test_attachment_roundtrip(client):
    res = client.post(
        "/api/campaigns",
        data={"target": "portal.local"},
        files={"info_files": ("notes.txt", b"hello world", "text/plain")},
    )
    assert res.status_code == 201, res.text
    campaign = res.json()
    attachment = campaign["attachments"][0]
    assert attachment["filename"] == "notes.txt"

    download = client.get(f"/api/attachments/{attachment['id']}")
    assert download.content == b"hello world"


def test_prompt_includes_attachment_text(client):
    res = client.post(
        "/api/campaigns",
        data={"target": "portal.local", "scope": "only /app"},
        files={"info_files": ("notes.txt", b"hello world", "text/plain")},
    )
    campaign = res.json()
    with db.db() as conn:
        rows = conn.execute(
            "SELECT * FROM attachments WHERE campaign_id = ?", (campaign["id"],)
        ).fetchall()

    prompt = agent.build_prompt(campaign, rows)
    assert "hello world" in prompt
    assert "only /app" in prompt
    assert "bash" in prompt


def test_parse_args_handles_control_chars():
    args, error = agent._parse_args('{"cmd": "echo a\nb"}')
    assert args == {"cmd": "echo a\nb"}
    assert error == ""

    args, error = agent._parse_args("{not json")
    assert args == {}
    assert error


def test_invalid_tool_arguments_reported_not_executed(client, monkeypatch):
    state = {"steps": 0, "executed": 0}

    async def fake_step(http, campaign, messages, calls, usage=None):
        state["steps"] += 1
        if state["steps"] > 1:
            yield "content", "done"
            return
        calls.append({
            "id": "c1",
            "name": "bash",
            "arguments": "{not json",
            "args": {},
            "raw": "{not json",
            "parse_error": "Expecting property name enclosed in double quotes",
        })
        yield "content", "calling"

    async def fake_execute(name, args, campaign_id, stop=None):
        state["executed"] += 1
        return "should not run", True, 1

    monkeypatch.setattr(agent, "_step", fake_step)
    monkeypatch.setattr(tools, "execute", fake_execute)
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", data={"content": "go"})
    assert "not valid JSON" in res.text
    assert state["executed"] == 0


def test_chat_streams_and_stores_messages(client, monkeypatch):
    async def fake_run(campaign, attachments, history, campaign_id, stop=None):
        yield "thinking", "weighing it"
        yield "content", "ack: " + history[-1]["content"]

    monkeypatch.setattr(agent, "run", fake_run)
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", data={"content": "hello"})
    assert res.status_code == 200
    assert "event: thinking" in res.text
    assert "event: content" in res.text
    assert "event: done" in res.text

    messages = client.get(f"/api/campaigns/{campaign['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "ack: hello"
    assert messages[1]["reasoning"] == "weighing it"
    assert messages[1]["tool_calls"] == "[]"


def test_chat_failure_keeps_partial_reply(client, monkeypatch):
    async def broken_run(campaign, attachments, history, campaign_id, stop=None):
        yield "content", "partial"
        raise RuntimeError("boom")

    monkeypatch.setattr(agent, "run", broken_run)
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", data={"content": "hi"})
    assert "event: error" in res.text
    assert '"saved": true' in res.text

    messages = client.get(f"/api/campaigns/{campaign['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "partial"


def test_chat_failure_before_output_keeps_user_message(client, monkeypatch):
    async def instant_failure(campaign, attachments, history, campaign_id, stop=None, usage=None):
        raise RuntimeError("boom")
        yield

    monkeypatch.setattr(agent, "run", instant_failure)
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", data={"content": "hi"})
    assert "event: error" in res.text
    assert '"saved": true' in res.text

    messages = client.get(f"/api/campaigns/{campaign['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user"]


def test_tool_loop_logs_calls(client, monkeypatch):
    state = {"steps": 0}

    async def fake_step(http, campaign, messages, calls):
        state["steps"] += 1
        if state["steps"] > 1:
            yield "content", "done"
            return
        yield "tool_start", {"id": "c1", "name": "bash"}
        calls.append({"id": "c1", "name": "bash", "arguments": '{"cmd": "echo hi"}', "args": {"cmd": "echo hi"}})
        yield "content", "running"

    async def fake_execute(name, args, campaign_id, stop=None):
        return "hi\n[exit code 0]", True, 7

    monkeypatch.setattr(agent, "_step", fake_step)
    monkeypatch.setattr(tools, "execute", fake_execute)
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", data={"content": "run it"})
    assert "event: tool_start" in res.text
    assert "event: tool_call" in res.text
    assert "event: tool_result" in res.text
    assert "event: done" in res.text

    messages = client.get(f"/api/campaigns/{campaign['id']}/messages").json()
    calls = json.loads(messages[1]["tool_calls"])
    assert calls[0]["name"] == "bash"
    assert calls[0]["ok"] is True
    assert "hi" in calls[0]["output"]

    parts = json.loads(messages[1]["parts"])
    assert [p["type"] for p in parts] == ["content", "tool", "content"]


def test_tool_step_cap(client, monkeypatch):
    async def always_calls(http, campaign, messages, calls):
        calls.append({"id": "c1", "name": "bash", "arguments": "{}", "args": {}})
        yield "content", "step"

    async def fake_execute(name, args, campaign_id, stop=None):
        return "ok", True, 1

    monkeypatch.setattr(agent, "_step", always_calls)
    monkeypatch.setattr(tools, "execute", fake_execute)
    monkeypatch.setattr(config, "MAX_TOOL_STEPS", 2)
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", data={"content": "go"})
    assert "stopped after 2 tool steps" in res.text

    messages = client.get(f"/api/campaigns/{campaign['id']}/messages").json()
    assert len(json.loads(messages[1]["tool_calls"])) == 2


def test_delete_campaign(client, monkeypatch):
    async def fake_run(campaign, attachments, history, campaign_id, stop=None):
        yield "content", "ok"

    monkeypatch.setattr(agent, "run", fake_run)
    res = client.post(
        "/api/campaigns",
        data={"target": "delete.me"},
        files={"info_files": ("notes.txt", b"data", "text/plain")},
    )
    cid = res.json()["id"]
    client.post(f"/api/campaigns/{cid}/messages", data={"content": "hi"})

    upload_dir = config.UPLOAD_DIR / str(cid)
    assert upload_dir.is_dir()

    res = client.delete(f"/api/campaigns/{cid}")
    assert res.status_code == 200
    assert res.json()["deleted"] == cid

    assert client.get(f"/api/campaigns/{cid}").status_code == 404
    assert client.get("/api/campaigns").json() == []
    assert not upload_dir.exists()


def test_prompt_lists_skills(client, monkeypatch, tmp_path):
    builtin = tmp_path / "skills"
    directory = builtin / "demo"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: demo\ndescription: Use when demo\n---\n\nBody.\n")
    monkeypatch.setattr(config, "SKILL_DIRS", [builtin])

    campaign = create(client)
    prompt = agent.build_prompt(campaign, [])
    assert "<available_skills>" in prompt
    assert "- demo: Use when demo" in prompt


def test_prompt_includes_instruction_files(client):
    campaign = create(client)
    prompt = agent.build_prompt(campaign, [])

    assert prompt.startswith("Instructions from:")
    assert "Creating a campaign is the authorization" in prompt
    assert prompt.index("Instructions from:") < prompt.index("You are the agent attached")


def test_skills_endpoints(client, monkeypatch, tmp_path):
    builtin = tmp_path / "skills"
    directory = builtin / "demo"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: demo\ndescription: Use when demo\n---\n\nBuilt-in body.\n")

    monkeypatch.setattr(config, "SKILL_DIRS", [builtin])
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")

    listed = client.get("/api/skills").json()
    assert [entry["name"] for entry in listed] == ["demo"]
    assert listed[0]["source"] == "built-in"

    detail = client.get("/api/skills/demo").json()
    assert detail["body"] == "Built-in body."
    assert client.get("/api/skills/nope").status_code == 404

    created = client.post(
        "/api/skills",
        json={"name": "custom-one", "description": "Use when custom", "body": "Custom."},
    )
    assert created.status_code == 201
    assert "custom-one" in [entry["name"] for entry in client.get("/api/skills").json()]

    res = client.put("/api/skills/demo", json={"description": "new", "body": "new"})
    assert res.status_code == 403

    res = client.put(
        "/api/skills/custom-one",
        json={"description": "updated desc", "body": "updated body"},
    )
    assert res.status_code == 200
    assert client.get("/api/skills/custom-one").json()["body"] == "updated body"

    res = client.post(
        "/api/skills",
        json={"name": "custom-one", "description": "again", "body": "x"},
    )
    assert res.status_code == 409

    res = client.post("/api/skills", json={"name": "Bad Name", "description": "x", "body": ""})
    assert res.status_code == 400


def test_seed_history_creates_messages(client, monkeypatch, tmp_path):
    path = tmp_path / "seed.json"
    path.write_text(json.dumps([
        {"role": "user", "content": "Begin {{target}}."},
        {
            "role": "assistant",
            "content": "Initialized {{target}}.",
            "parts": [{"type": "content", "text": "Initialized {{target}}."}],
        },
    ]))
    monkeypatch.setattr(config, "SEED_HISTORY", path)

    campaign = create(client, target="seed.example")
    messages = client.get(f"/api/campaigns/{campaign['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "Begin seed.example."
    assert "Initialized seed.example." in messages[1]["parts"]


def test_split_model():
    assert agent.split_model("deepseek/deepseek-flash") == ("deepseek", "deepseek-flash")
    assert agent.split_model("9router/ag/gemini-3.8-flash-high") == (
        "9router",
        "ag/gemini-3.8-flash-high",
    )
    assert agent.split_model("deepseek-chat") == ("deepseek", "deepseek-chat")
    assert agent.split_model("some-random") == (config.PROVIDER, "some-random")


def test_chat_without_key(client, monkeypatch):
    monkeypatch.setattr(agent, "provider_key", lambda provider: "")
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", data={"content": "hi"})
    assert res.status_code == 400
    assert client.get(f"/api/campaigns/{campaign['id']}/messages").json() == []


def test_stop_idle_campaign(client):
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/stop")
    assert res.status_code == 200
    assert res.json() == {"stopped": False}


def test_running_flag_and_409(client):
    campaign = create(client)
    routes.RUNNING[campaign["id"]] = {"stop": asyncio.Event(), "parts": [], "pending": None}
    try:
        listed = client.get("/api/campaigns").json()
        assert listed[0]["running"] is True

        res = client.post(f"/api/campaigns/{campaign['id']}/messages", data={"content": "hi"})
        assert res.status_code == 409
    finally:
        routes.RUNNING.pop(campaign["id"], None)


def test_live_snapshot(client):
    campaign = create(client)
    idle = client.get(f"/api/campaigns/{campaign['id']}/live").json()
    assert idle == {"running": False, "parts": [], "pending": None}

    routes.RUNNING[campaign["id"]] = {
        "stop": asyncio.Event(),
        "parts": [{"type": "content", "text": "hi"}],
        "pending": "bash",
    }
    try:
        body = client.get(f"/api/campaigns/{campaign['id']}/live").json()
        assert body["running"] is True
        assert body["parts"] == [{"type": "content", "text": "hi"}]
        assert body["pending"] == "bash"
    finally:
        routes.RUNNING.pop(campaign["id"], None)


def test_stopped_run_stores_partial(client, monkeypatch):
    async def stopped_run(campaign, attachments, history, campaign_id, stop=None, usage=None):
        yield "content", "half"
        yield "stopped", None

    monkeypatch.setattr(agent, "run", stopped_run)
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", data={"content": "go"})
    assert "stopped by user" in res.text
    assert '"saved": true' in res.text

    messages = client.get(f"/api/campaigns/{campaign['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "half"


def test_agent_run_honours_stop(monkeypatch):
    stop = asyncio.Event()

    async def fake_step(http, campaign, messages, calls, usage=None):
        yield "content", "partial"
        stop.set()

    monkeypatch.setattr(agent, "_step", fake_step)
    campaign = {
        "target": "x",
        "info": "",
        "instructions": "",
        "description": "",
        "scope": "",
        "model": "deepseek-flash",
        "effort": "high",
    }

    async def collect():
        return [
            event
            async for event in agent.run(
                campaign, [], [{"role": "user", "content": "hi"}], 1, stop
            )
        ]

    events = asyncio.run(collect())
    assert events[-1] == ("stopped", None)
    assert ("content", "partial") in events


def test_run_persists_without_a_watcher(client, monkeypatch):
    campaign = create(client)

    async def fake_run(*args, **kwargs):
        yield "content", "kept going"

    monkeypatch.setattr(agent, "run", fake_run)

    async def drive():
        run = {
            "stop": asyncio.Event(),
            "parts": [],
            "pending": None,
            "queue": asyncio.Queue(),
            "consumer": False,
        }
        routes.RUNNING[campaign["id"]] = run
        await routes._run(campaign["id"], {}, [], [], "hello", run)

    asyncio.run(drive())

    messages = client.get(f"/api/campaigns/{campaign['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "kept going"
    assert campaign["id"] not in routes.RUNNING


def test_run_sets_running_then_idle(client, monkeypatch):
    seen = {}

    async def fake_run(campaign, attachments, history, campaign_id, stop=None):
        with db.db() as conn:
            seen["status"] = conn.execute(
                "SELECT status FROM campaigns WHERE id = ?", (campaign_id,)
            ).fetchone()["status"]
            seen["roles"] = [
                row["role"]
                for row in conn.execute(
                    "SELECT role FROM messages WHERE campaign_id = ? ORDER BY id",
                    (campaign_id,),
                ).fetchall()
            ]
        yield "content", "done"

    monkeypatch.setattr(agent, "run", fake_run)
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", data={"content": "go"})
    assert "event: done" in res.text
    assert seen["status"] == "running"
    assert seen["roles"] == ["user", "assistant"]
    assert client.get(f"/api/campaigns/{campaign['id']}").json()["status"] == "idle"


def test_run_checkpoints_mid_stream(client, monkeypatch):
    seen = {}

    async def fake_run(campaign, attachments, history, campaign_id, stop=None):
        yield "content", "working"
        yield "tool_call", {"id": "c1", "name": "bash", "args": {"cmd": "id"}}
        yield "tool_result", {"id": "c1", "name": "bash", "output": "uid=0", "ok": True, "ms": 3}
        with db.db() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE campaign_id = ? AND role = 'assistant'",
                (campaign_id,),
            ).fetchone()
            seen["content"] = row["content"]
            seen["parts"] = json.loads(row["parts"])
        yield "content", " done"

    monkeypatch.setattr(agent, "run", fake_run)
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", data={"content": "go"})
    assert "event: done" in res.text

    tool = next(part for part in seen["parts"] if part["type"] == "tool")
    assert tool["output"] == "uid=0"
    assert tool["ok"] is True
    assert seen["content"] == "working"


def test_startup_recovers_a_running_campaign(client):
    campaign = create(client)
    calls = [
        {"id": "c1", "name": "bash", "args": {"cmd": "sleep 99"}, "output": None, "ok": None, "ms": None}
    ]
    parts = [
        {"type": "thinking", "text": "weighing the sleep"},
        {"type": "tool", "id": "c1", "name": "bash", "args": {"cmd": "sleep 99"}, "output": None, "ok": None, "ms": None},
    ]
    with db.db() as conn:
        conn.execute(
            "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'user', 'go')",
            (campaign["id"],),
        )
        conn.execute(
            """
            INSERT INTO messages (campaign_id, role, content, reasoning, tool_calls, parts)
            VALUES (?, 'assistant', '', 'weighing the sleep', ?, ?)
            """,
            (campaign["id"], json.dumps(calls), json.dumps(parts)),
        )
        conn.execute("UPDATE campaigns SET status = 'running' WHERE id = ?", (campaign["id"],))

    with TestClient(app) as fresh:
        assert fresh.get(f"/api/campaigns/{campaign['id']}").json()["status"] == "interrupted"
        messages = fresh.get(f"/api/campaigns/{campaign['id']}/messages").json()
        saved = json.loads(messages[-1]["parts"])
        assert saved[-1]["type"] == "notice"
        assert "interrupted" in messages[-1]["content"]
        tool = next(part for part in saved if part["type"] == "tool")
        assert tool["ok"] is False
        assert "interrupted" in tool["output"]

    with TestClient(app) as again:
        assert again.get(f"/api/campaigns/{campaign['id']}").json()["status"] == "interrupted"


def test_recovery_drops_an_empty_placeholder(client):
    campaign = create(client)
    with db.db() as conn:
        conn.execute(
            "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'user', 'go')",
            (campaign["id"],),
        )
        conn.execute(
            "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'assistant', '')",
            (campaign["id"],),
        )
        conn.execute("UPDATE campaigns SET status = 'running' WHERE id = ?", (campaign["id"],))

    with TestClient(app) as fresh:
        messages = fresh.get(f"/api/campaigns/{campaign['id']}/messages").json()
        assert [m["role"] for m in messages] == ["user"]
        assert fresh.get(f"/api/campaigns/{campaign['id']}").json()["status"] == "interrupted"


def test_message_upload(client, monkeypatch):
    campaign = create(client)

    async def fake_run(*args, **kwargs):
        yield "content", "ok"

    monkeypatch.setattr(agent, "run", fake_run)

    res = client.post(
        f"/api/campaigns/{campaign['id']}/messages",
        data={"content": "look at this"},
        files={"files": ("note.txt", b"hello file", "text/plain")},
    )
    assert res.status_code == 200
    assert "event: done" in res.text

    messages = client.get(f"/api/campaigns/{campaign['id']}/messages").json()
    user = messages[0]
    assert user["role"] == "user"
    assert len(user["attachments"]) == 1
    assert user["attachments"][0]["filename"] == "note.txt"

    stored = config.UPLOAD_DIR / user["attachments"][0]["path"]
    assert stored.read_text() == "hello file"

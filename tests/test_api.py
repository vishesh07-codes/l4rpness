import asyncio
import json
import os

os.environ["DEEPSEEK_API_KEY"] = "test-key"

import pytest
from fastapi.testclient import TestClient

from app import agent, config, db, routes, supervisor, tools, vm
from app.main import app


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch, tmp_path):
    # Isolate every path per test: before this, uploads (and the DB) landed in
    # the repo's data/ dir and leaked across runs, so the attachment test
    # failed on any second run.
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "l4rpness.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    routes.RUNNING.clear()
    db.init()

    async def fake_models():
        return ["deepseek-chat", "deepseek-flash"]

    monkeypatch.setattr(agent, "models", fake_models)
    monkeypatch.setattr(config, "SEED_HISTORY", None)
    # campaigns auto-start the supervisor in production; tests drive it by hand
    monkeypatch.setattr(supervisor, "start", lambda campaign_id: False)


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


def test_runtime_patch_accepts_known_prefix_without_listing(client, monkeypatch):
    campaign = create(client)
    # Empty live listing must not block configuration changes.
    monkeypatch.setattr(agent, "_models", [])
    res = client.patch(
        f"/api/campaigns/{campaign['id']}/runtime", json={"model": "deepseek/deepseek-chat"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["model"] == "deepseek/deepseek-chat"


def test_runtime_patch_accepts_unchanged_model_when_listing_empty(client, monkeypatch):
    campaign = create(client)
    monkeypatch.setattr(agent, "_models", [])
    res = client.patch(
        f"/api/campaigns/{campaign['id']}/runtime", json={"model": campaign["model"]}
    )
    assert res.status_code == 200, res.text


def test_runtime_patch_rejects_unknown_effort_and_empty(client):
    campaign = create(client)
    assert (
        client.patch(
            f"/api/campaigns/{campaign['id']}/runtime", json={"effort": "bogus"}
        ).status_code
        == 400
    )
    assert client.patch(f"/api/campaigns/{campaign['id']}/runtime", json={}).status_code == 400
    assert (
        client.patch(
            f"/api/campaigns/{campaign['id']}/runtime", json={"model": "   "}
        ).status_code
        == 400
    )
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

    async def fake_execute(name, args, campaign_id, stop=None, on_output=None, **kwargs):
        state["executed"] += 1
        return "should not run", True, 1

    monkeypatch.setattr(agent, "_step", fake_step)
    monkeypatch.setattr(tools, "execute", fake_execute)
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", data={"content": "go"})
    assert "not valid JSON" in res.text
    assert state["executed"] == 0


def test_chat_streams_and_stores_messages(client, monkeypatch):
    async def fake_run(campaign, attachments, history, campaign_id, stop=None, on_tool_output=None, include_attachments=True):
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
    async def broken_run(campaign, attachments, history, campaign_id, stop=None, on_tool_output=None, include_attachments=True):
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
    async def instant_failure(campaign, attachments, history, campaign_id, stop=None, usage=None, on_tool_output=None, include_attachments=True):
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

    async def fake_execute(name, args, campaign_id, stop=None, on_output=None, **kwargs):
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
    # output text lives in parts only; tool_calls keeps metadata so the
    # transcript does not store every stdout twice
    assert "output" not in calls[0]

    parts = json.loads(messages[1]["parts"])
    assert [p["type"] for p in parts] == ["content", "tool", "content"]
    assert "hi" in next(p for p in parts if p["type"] == "tool")["output"]


def test_tool_step_cap(client, monkeypatch):
    async def always_calls(http, campaign, messages, calls):
        calls.append({"id": "c1", "name": "bash", "arguments": "{}", "args": {}})
        yield "content", "step"

    async def fake_execute(name, args, campaign_id, stop=None, on_output=None, **kwargs):
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
    async def fake_run(campaign, attachments, history, campaign_id, stop=None, on_tool_output=None, include_attachments=True):
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


def test_new_campaign_starts_empty(client, monkeypatch, tmp_path):
    # the supervisor owns the first turn; campaigns are never seeded with
    # fabricated assistant history
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
    assert messages == []


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


def test_steer_stores_message_for_the_supervisor(client):
    campaign = create(client)

    res = client.post(
        f"/api/campaigns/{campaign['id']}/steer", data={"content": "try the admin panel"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["role"] == "user"
    assert res.json()["content"] == "try the admin panel"

    messages = client.get(f"/api/campaigns/{campaign['id']}/messages").json()
    assert [m["content"] for m in messages] == ["try the admin panel"]


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
    assert idle == {"running": False, "parts": [], "pending": None, "seq": 0, "usage": None}

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
    async def stopped_run(campaign, attachments, history, campaign_id, stop=None, usage=None, on_tool_output=None, include_attachments=True):
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

    async def fake_run(campaign, attachments, history, campaign_id, stop=None, on_tool_output=None, include_attachments=True):
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

    async def fake_run(campaign, attachments, history, campaign_id, stop=None, on_tool_output=None, include_attachments=True):
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


def test_live_events_fan_out_to_subscribers(client, monkeypatch):
    campaign = create(client)
    queue: asyncio.Queue = asyncio.Queue()
    run = {
        "stop": asyncio.Event(),
        "parts": [],
        "pending": None,
        "queue": asyncio.Queue(),
        "consumer": False,
        "subscribers": {queue},
        "seq": 0,
    }
    routes.RUNNING[campaign["id"]] = run

    async def fake_run(*args, **kwargs):
        yield "content", "live text"

    monkeypatch.setattr(agent, "run", fake_run)

    async def drive():
        await routes._run(campaign["id"], {}, [], [], "go", run)

    asyncio.run(drive())

    names = []
    while not queue.empty():
        names.append(queue.get_nowait()[0])
    assert "content" in names
    assert "done" in names
    assert names[-1] == "__end__"


def test_supervised_chain_end_bumps_seq(client, monkeypatch):
    campaign = create(client)
    queue: asyncio.Queue = asyncio.Queue()
    run = {
        "stop": asyncio.Event(),
        "parts": [],
        "pending": None,
        "queue": asyncio.Queue(),
        "consumer": False,
        "subscribers": {queue},
        "supervised": True,
        "seq": 0,
    }
    routes.RUNNING[campaign["id"]] = run

    async def fake_run(*args, **kwargs):
        yield "content", "chain"

    monkeypatch.setattr(agent, "run", fake_run)

    async def drive():
        await routes._run(campaign["id"], {}, [], [], "", run, store_user=False, keep=True)

    asyncio.run(drive())

    names = []
    while not queue.empty():
        names.append(queue.get_nowait()[0])
    assert "chain_end" in names
    assert "__end__" not in names
    assert run["seq"] == 1


def test_close_run_notifies_subscribers(client):
    campaign = create(client)
    queue: asyncio.Queue = asyncio.Queue()
    routes.RUNNING[campaign["id"]] = {
        "stop": asyncio.Event(),
        "parts": [],
        "pending": None,
        "queue": asyncio.Queue(),
        "consumer": False,
        "subscribers": {queue},
    }

    routes.close_run(campaign["id"])
    assert queue.get_nowait() == ("closed", {})


async def _first_sse_chunk(response) -> str:
    iterator = response.body_iterator.__aiter__()
    chunk = await getattr(iterator, "__anext__")()
    close = getattr(iterator, "aclose", None)
    if close is not None:
        await close()
    return str(chunk)


def test_stream_endpoint_snapshots_live_parts(client):
    campaign = create(client)
    routes.RUNNING[campaign["id"]] = {
        "stop": asyncio.Event(),
        "parts": [{"type": "content", "text": "hello"}],
        "pending": None,
        "queue": asyncio.Queue(),
        "consumer": False,
        "subscribers": set(),
        "seq": 3,
    }

    async def drive():
        return await _first_sse_chunk(await routes.stream_campaign(campaign["id"]))

    chunk = asyncio.run(drive())
    assert "event: snapshot" in chunk
    assert '"text": "hello"' in chunk
    assert '"seq": 3' in chunk


def test_stream_endpoint_idles_when_not_running(client):
    campaign = create(client)

    async def drive():
        return await _first_sse_chunk(await routes.stream_campaign(campaign["id"]))

    chunk = asyncio.run(drive())
    assert "event: idle" in chunk


def test_resume_endpoint(client, monkeypatch):
    campaign = create(client)

    monkeypatch.setattr(supervisor, "start", lambda campaign_id: True)
    res = client.post(f"/api/campaigns/{campaign['id']}/resume")
    assert res.status_code == 200
    assert res.json()["status"] == "running"

    monkeypatch.setattr(supervisor, "start", lambda campaign_id: False)
    res = client.post(f"/api/campaigns/{campaign['id']}/resume")
    assert res.status_code == 400


def test_tool_output_streams_live(client, monkeypatch):
    seen = {}

    async def fake_run(campaign, attachments, history, campaign_id, stop=None, on_tool_output=None, include_attachments=True):
        assert on_tool_output is not None
        yield "tool_call", {"id": "c1", "name": "bash", "args": {"cmd": "scan"}}
        on_tool_output("c1", "first ")
        seen["live"] = routes.RUNNING[campaign_id]["parts"][0].get("output")
        on_tool_output("c1", "second")
        yield "tool_result", {
            "id": "c1",
            "name": "bash",
            "output": "first second\n[exit code 0]",
            "ok": True,
            "ms": 7,
        }

    monkeypatch.setattr(agent, "run", fake_run)
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", data={"content": "go"})
    assert "event: tool_output" in res.text
    assert '"chunk": "first "' in res.text
    # while the tool still runs, the live view already shows the output
    assert seen["live"] == "first "

    messages = client.get(f"/api/campaigns/{campaign['id']}/messages").json()
    parts = json.loads(messages[1]["parts"])
    tool = next(part for part in parts if part["type"] == "tool")
    assert tool["output"] == "first second\n[exit code 0]"
    assert tool["ok"] is True


def test_messages_pagination(client):
    campaign = create(client)
    with db.db() as conn:
        for i in range(6):
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'user', ?)",
                (campaign["id"], f"m{i}"),
            )

    res = client.get(f"/api/campaigns/{campaign['id']}/messages?limit=2")
    assert res.headers.get("x-has-more") == "1"
    tail = res.json()
    assert [m["content"] for m in tail] == ["m4", "m5"]

    res = client.get(
        f"/api/campaigns/{campaign['id']}/messages?before={tail[0]['id']}&limit=3"
    )
    assert res.headers.get("x-has-more") == "1"
    assert [m["content"] for m in res.json()] == ["m1", "m2", "m3"]

    res = client.get(f"/api/campaigns/{campaign['id']}/messages?after={tail[-1]['id']}")
    assert res.json() == []

    res = client.get(f"/api/campaigns/{campaign['id']}/messages?after={tail[0]['id']}")
    assert [m["content"] for m in res.json()] == ["m5"]

    # without params the whole history is still returned
    assert len(client.get(f"/api/campaigns/{campaign['id']}/messages").json()) == 6


def test_generator_and_hypothesis_endpoints(client):
    campaign = create(client)
    with db.db() as conn:
        cur = conn.execute(
            "INSERT INTO generators (campaign_id, focus, status) VALUES (?, 'auth', 'done')",
            (campaign["id"],),
        )
        generator_id = cur.lastrowid
        assert generator_id is not None
        conn.execute(
            "INSERT INTO generator_messages (generator_id, content, parts) VALUES (?, 'hi', ?)",
            (generator_id, json.dumps([{"type": "content", "text": "hi"}])),
        )
        conn.execute(
            """
            INSERT INTO hypotheses (campaign_id, generator_id, title, angle, vuln_class, rationale, status)
            VALUES (?, ?, 'T1', 'a', 'xss', 'r', 'new')
            """,
            (campaign["id"], generator_id),
        )

    generators = client.get(f"/api/campaigns/{campaign['id']}/generators").json()
    assert generators[0]["focus"] == "auth"
    assert generators[0]["hypothesis_count"] == 1
    assert generators[0]["last_snippet"] == "hi"

    messages = client.get(f"/api/generators/{generator_id}/messages").json()
    assert messages[0]["parts"][0]["text"] == "hi"

    with db.db() as conn:
        for i in range(3):
            conn.execute(
                "INSERT INTO generator_messages (generator_id, content, parts) VALUES (?, ?, ?)",
                (generator_id, f"g{i}", "[]"),
            )

    res = client.get(f"/api/generators/{generator_id}/messages?limit=2")
    assert res.headers.get("x-has-more") == "1"
    tail = res.json()
    assert [m["content"] for m in tail] == ["g1", "g2"]

    res = client.get(
        f"/api/generators/{generator_id}/messages?before={tail[0]['id']}&limit=2"
    )
    assert res.headers.get("x-has-more") == "0"
    assert [m["content"] for m in res.json()] == ["hi", "g0"]

    res = client.get(f"/api/generators/{generator_id}/messages?after={tail[-1]['id']}")
    assert res.json() == []

    hypotheses = client.get(f"/api/campaigns/{campaign['id']}/hypotheses").json()
    assert hypotheses[0]["title"] == "T1"
    assert hypotheses[0]["generator_id"] == generator_id


def test_workers_and_hunts_api(client):
    campaign = create(client)
    cid = campaign["id"]
    with db.db() as conn:
        cur = conn.execute(
            "INSERT INTO workers (campaign_id, title, note, status) VALUES (?, 'SQLi in search', 'go deep', 'done')",
            (cid,),
        )
        worker_id = cur.lastrowid
        assert worker_id is not None
        conn.execute(
            "INSERT INTO worker_messages (worker_id, role, content, parts) VALUES (?, 'assistant', 'hi', ?)",
            (worker_id, json.dumps([{"type": "content", "text": "hi"}])),
        )
        hyp = conn.execute(
            "INSERT INTO hypotheses (campaign_id, worker_id, title, angle, vuln_class, rationale, status) "
            "VALUES (?, ?, 'T1', 'a', 'sqli', 'r', 'review')",
            (cid, worker_id),
        )
        conn.execute(
            """
            INSERT INTO hunts (campaign_id, worker_id, hypothesis_id, verdict, severity,
                               impact, poc_path, repro, evidence)
            VALUES (?, ?, ?, 'confirmed', 'high', 'dump the users table', 'hunters/1/poc/run.sh', 'run it', 'HTTP 200')
            """,
            (cid, worker_id, hyp.lastrowid),
        )

    workers = client.get(f"/api/campaigns/{cid}/workers").json()
    assert workers[0]["title"] == "SQLi in search"
    assert workers[0]["note"] == "go deep"
    assert workers[0]["hypothesis_count"] == 1
    assert workers[0]["result_count"] == 1
    assert workers[0]["last_snippet"] == "hi"

    messages = client.get(f"/api/workers/{worker_id}/messages").json()
    assert messages[0]["parts"][0]["text"] == "hi"
    assert client.get("/api/workers/99999/messages").status_code == 404

    hunts = client.get(f"/api/campaigns/{cid}/hunts").json()
    assert hunts[0]["verdict"] == "confirmed"
    assert hunts[0]["poc_path"] == "hunters/1/poc/run.sh"


def test_create_campaign_kernel_vm_flags(client, monkeypatch):
    plain = create(client, target="plain.example")
    assert plain["kernel_enabled"] == 0 and plain["kernel_root_access"] == 0
    assert client.get(f"/api/campaigns/{plain['id']}/vm").json() == {"enabled": False}

    res = client.post(
        "/api/campaigns",
        data={
            "target": "kernel.example",
            "model": "deepseek-chat",
            "kernel": "true",
            "kernel_root_access": "true",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["kernel_enabled"] == 1 and body["kernel_root_access"] == 1

    # the sub-options are meaningless without the VM, and must not be stored
    res = client.post(
        "/api/campaigns",
        data={
            "target": "nope.example",
            "model": "deepseek-chat",
            "kernel_root_access": "true",
            "kernel_flag": "true",
        },
    )
    assert res.json()["kernel_root_access"] == 0
    assert res.json()["kernel_root_flag"] == 0

    res = client.post(
        "/api/campaigns",
        data={"target": "flag.example", "model": "deepseek-chat", "kernel": "true", "kernel_flag": "true"},
    )
    body = res.json()
    assert body["kernel_enabled"] == 1 and body["kernel_root_flag"] == 1
    assert body["kernel_root_access"] == 0


def test_campaign_vm_endpoint_reports_the_control_plane(client, monkeypatch):
    from app import vmctl

    async def down(*args, **kwargs):
        raise vmctl.Unavailable("no socket")

    monkeypatch.setattr(vmctl, "call", down)

    campaign = create(client, target="kernel.example")
    with db.db() as conn:
        conn.execute("UPDATE campaigns SET kernel_enabled = 1 WHERE id = ?", (campaign["id"],))

    body = client.get(f"/api/campaigns/{campaign['id']}/vm").json()
    assert body["enabled"] is True
    assert body["state"] == "unavailable"
    assert "no socket" in body["error"]


def test_delete_campaign_tears_the_vm_down(client, monkeypatch):
    destroyed = []

    async def fake_destroy(campaign_id):
        destroyed.append(campaign_id)

    monkeypatch.setattr(vm, "destroy", fake_destroy)
    campaign = create(client, target="kernel.example")
    assert client.delete(f"/api/campaigns/{campaign['id']}").status_code == 200
    assert destroyed == [campaign["id"]]


def test_usage_is_tracked_and_streamed(client, monkeypatch):
    async def fake_run(campaign, attachments, history, campaign_id, stop=None, on_tool_output=None, include_attachments=True):
        yield "usage", {"prompt_tokens": 1200, "completion_tokens": 300}
        yield "content", "step one"
        yield "usage", {"prompt_tokens": 5000, "completion_tokens": 100}

    monkeypatch.setattr(agent, "run", fake_run)
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", data={"content": "go"})
    assert "event: usage" in res.text
    assert '"in": 1200' in res.text

    row = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert row["usage_in"] == 6200
    assert row["usage_out"] == 400
    # the newest request's prompt size is the context gauge compaction reads
    assert row["last_prompt_tokens"] == 5000


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

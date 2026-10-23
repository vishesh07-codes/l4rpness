import os
import tempfile
from pathlib import Path

os.environ["L4RP_DATA_DIR"] = tempfile.mkdtemp()
os.environ["DEEPSEEK_API_KEY"] = "test-key"

import pytest
from fastapi.testclient import TestClient

from app import agent, config, db
from app.main import app


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    for suffix in ("", "-wal", "-shm"):
        Path(str(config.DB_PATH) + suffix).unlink(missing_ok=True)
    db.init()

    async def fake_models():
        return ["deepseek-chat", "deepseek-flash"]

    monkeypatch.setattr(agent, "models", fake_models)


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
    assert "no tools" in prompt


def test_chat_streams_and_stores_messages(client, monkeypatch):
    async def fake_stream(campaign, attachments, history):
        yield "thinking", "weighing it"
        yield "content", "ack: " + history[-1]["content"]

    monkeypatch.setattr(agent, "stream", fake_stream)
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", json={"content": "hello"})
    assert res.status_code == 200
    assert "event: thinking" in res.text
    assert "event: content" in res.text
    assert "event: done" in res.text

    messages = client.get(f"/api/campaigns/{campaign['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "ack: hello"
    assert messages[1]["reasoning"] == "weighing it"


def test_chat_stream_failure_stores_nothing(client, monkeypatch):
    async def broken_stream(campaign, attachments, history):
        yield "content", "partial"
        raise RuntimeError("boom")

    monkeypatch.setattr(agent, "stream", broken_stream)
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", json={"content": "hi"})
    assert "event: error" in res.text
    assert client.get(f"/api/campaigns/{campaign['id']}/messages").json() == []


def test_chat_without_key(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "")
    campaign = create(client)

    res = client.post(f"/api/campaigns/{campaign['id']}/messages", json={"content": "hi"})
    assert res.status_code == 400
    assert client.get(f"/api/campaigns/{campaign['id']}/messages").json() == []

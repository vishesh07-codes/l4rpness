import asyncio
import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("L4RP_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")

import pytest
from fastapi.testclient import TestClient

from app import config, mcp
from app.main import app
# conftest replaces mcp.stop with a no-op for every test; keep a direct
# reference to the real teardown so the ordering can be exercised
from app.mcp import stop as _real_stop


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    mcp._sessions.clear()
    mcp._stacks.clear()
    mcp._catalog.clear()
    mcp._routes.clear()
    mcp._errors.clear()
    return tmp_path


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_defaults_without_file():
    servers = mcp.definitions()
    assert [server["name"] for server in servers] == ["frank", "playwright"]

    frank = servers[0]
    assert "net_scan" in frank["tools"]

    playwright = servers[1]
    assert playwright["command"][0] == "playwright-mcp"
    assert "--headless" in playwright["command"]
    assert "--no-sandbox" in playwright["command"]
    assert playwright["tools"] == []


def test_save_and_load():
    mcp.save([{"name": "x", "command": ["python", "s.py"], "tools": ["a"]}])
    assert mcp.definitions() == [{"name": "x", "command": ["python", "s.py"], "tools": ["a"]}]


def test_schemas_namespace_and_allowlist():
    mcp.save([{"name": "frank", "command": ["python", "/frank/server.py"], "tools": ["net_scan"]}])
    mcp._catalog["frank"] = [
        SimpleNamespace(name="net_scan", description="scan", inputSchema={"type": "object", "properties": {}}),
        SimpleNamespace(name="dns_lookup", description="dns", inputSchema={"type": "object", "properties": {}}),
    ]

    names = [schema["function"]["name"] for schema in mcp.schemas()]
    assert names == ["frank_net_scan"]
    assert mcp.is_tool("frank_net_scan")
    assert not mcp.is_tool("frank_dns_lookup")


def test_call_routes_to_server():
    mcp.save([{"name": "frank", "command": ["python", "/frank/server.py"], "tools": ["dns_lookup"]}])
    mcp._catalog["frank"] = [
        SimpleNamespace(name="dns_lookup", description="dns", inputSchema={"type": "object", "properties": {}})
    ]

    class FakeSession:
        async def call_tool(self, name, arguments):
            assert name == "dns_lookup"
            assert arguments == {"hostname": "example.com"}
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="1.2.3.4")], isError=False
            )

    mcp._sessions["frank"] = FakeSession()
    mcp.schemas()

    text, ok = asyncio.run(mcp.call("frank_dns_lookup", {"hostname": "example.com"}))
    assert ok
    assert text == "1.2.3.4"


def test_call_unknown_tool():
    text, ok = asyncio.run(mcp.call("frank_nope", {}))
    assert not ok
    assert "unknown mcp tool" in text


def test_call_disconnected_server():
    mcp.save([{"name": "frank", "command": ["python", "/frank/server.py"], "tools": ["dns_lookup"]}])
    mcp._catalog["frank"] = [
        SimpleNamespace(name="dns_lookup", description="dns", inputSchema={"type": "object", "properties": {}})
    ]
    mcp.schemas()

    text, ok = asyncio.run(mcp.call("frank_dns_lookup", {}))
    assert not ok
    assert "not connected" in text


def test_tool_error_is_not_ok():
    mcp.save([{"name": "frank", "command": ["python", "/frank/server.py"], "tools": ["net_scan"]}])
    mcp._catalog["frank"] = [
        SimpleNamespace(name="net_scan", description="scan", inputSchema={"type": "object", "properties": {}})
    ]

    class FakeSession:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="scan failed")], isError=True
            )

    mcp._sessions["frank"] = FakeSession()
    mcp.schemas()

    text, ok = asyncio.run(mcp.call("frank_net_scan", {"host": "example.com"}))
    assert not ok
    assert text == "scan failed"


def test_stop_closes_sessions_in_reverse_order():
    # the stdio sessions nest anyio cancel scopes; tearing them down in
    # insertion order raises and leaves the rest of the teardown broken
    closed: list[str] = []

    class FakeStack:
        def __init__(self, name):
            self.name = name

        async def aclose(self):
            closed.append(self.name)

    mcp._stacks.clear()
    mcp._stacks["first"] = FakeStack("first")
    mcp._stacks["second"] = FakeStack("second")

    asyncio.run(_real_stop())
    assert closed == ["second", "first"]


def test_mcp_endpoints(client, tmp_path):
    res = client.put(
        "/api/mcp",
        json=[
            {
                "name": "frank",
                "command": ["python", "/frank/server.py"],
                "enabled": ["dns_lookup", "web_fetch"],
            }
        ],
    )
    assert res.status_code == 200
    assert res.json()["servers"][0]["enabled"] == ["dns_lookup", "web_fetch"]

    saved = (tmp_path / "mcp.json").read_text()
    assert "dns_lookup" in saved
    assert "web_fetch" in saved

    res = client.put("/api/mcp", json=[{"name": "bad"}])
    assert res.status_code == 400

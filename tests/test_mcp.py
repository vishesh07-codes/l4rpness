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
    assert servers[0]["name"] == "frank"
    assert "net_scan" in servers[0]["tools"]


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

import pytest

from app import mcp


@pytest.fixture(autouse=True)
def _disable_mcp(monkeypatch):
    """Tests must not spawn MCP subprocesses: the stdio client's cancel scope
    cannot be closed cleanly across the test client's task boundaries, which
    shows up as teardown errors. The mcp tests drive the session map directly."""

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(mcp, "start", _noop)
    monkeypatch.setattr(mcp, "stop", _noop)

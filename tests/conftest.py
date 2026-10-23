import pytest

from app import agent, mcp


@pytest.fixture(autouse=True)
def _disable_mcp(monkeypatch):
    """Tests must not spawn MCP subprocesses: the stdio client's cancel scope
    cannot be closed cleanly across the test client's task boundaries, which
    shows up as teardown errors. The mcp tests drive the session map directly."""

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(mcp, "start", _noop)
    monkeypatch.setattr(mcp, "stop", _noop)


@pytest.fixture(autouse=True)
def _offline_providers(monkeypatch):
    """No test may reach a real provider.

    Warm-up paths (model listing on startup, in the supervisor loop, after
    host config edits) construct clients even in tests. The stand-in is
    constructible — tests that fake `agent._step` or `agent.run` never touch
    it — but any real use (listing, chat calls, compaction summaries) fails
    loudly. Anything that wants provider behavior patches `agent._client`
    itself and overrides this guard."""
    from types import SimpleNamespace

    def _boom(*args, **kwargs):
        raise RuntimeError("provider network is disabled in tests")

    def _offline(provider, *args, **kwargs):
        completions = SimpleNamespace(create=_boom)
        return SimpleNamespace(
            models=SimpleNamespace(list=_boom),
            chat=SimpleNamespace(completions=completions),
        )

    monkeypatch.setattr(agent, "_client", _offline)

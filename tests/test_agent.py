import asyncio
from types import SimpleNamespace

from app import agent, config


def _list(items, error=None):
    """Stand-in for the openai models paginator: a fresh async iterator."""

    def call(**kwargs):
        async def gen():
            if error:
                raise error
            for item in items:
                yield SimpleNamespace(id=item)

        return gen()

    return SimpleNamespace(list=call)


def _providers(monkeypatch, providers):
    monkeypatch.setattr(config, "PROVIDERS", providers)
    for conf in providers.values():
        monkeypatch.setenv(conf["api_key_env"], "test-key")


def test_models_merges_providers_and_survives_failures(monkeypatch):
    monkeypatch.setattr(agent, "_models", [])
    _providers(
        monkeypatch,
        {"up": {"api_key_env": "UP_KEY"}, "down": {"api_key_env": "DOWN_KEY"}},
    )
    calls = []

    def fake_client(name, timeout=None, retries=None):
        calls.append((name, timeout, retries))
        if name == "up":
            return SimpleNamespace(models=_list(["m2", "m1"]))
        return SimpleNamespace(models=_list([], error=RuntimeError("connection timed out")))

    monkeypatch.setattr(agent, "_client", fake_client)
    assert asyncio.run(agent.models()) == ["up/m1", "up/m2"]
    # an unreachable provider is capped and not retried, never given the full
    # request timeout
    assert len(calls) == 2
    assert all(timeout == agent.MODELS_TIMEOUT and retries == 0 for _, timeout, retries in calls)


def test_models_skips_providers_without_a_key(monkeypatch):
    monkeypatch.setattr(agent, "_models", [])
    monkeypatch.setattr(
        config,
        "PROVIDERS",
        {"keyed": {"api_key_env": "KEYED"}, "unkeyed": {"api_key_env": "UNKEYED"}},
    )
    monkeypatch.setenv("KEYED", "test-key")
    monkeypatch.delenv("UNKEYED", raising=False)
    called = []

    def fake_client(name, timeout=None, retries=None):
        called.append(name)
        return SimpleNamespace(models=_list(["m1"]))

    monkeypatch.setattr(agent, "_client", fake_client)
    assert asyncio.run(agent.models()) == ["keyed/m1"]
    assert called == ["keyed"]


def test_models_cached_after_success(monkeypatch):
    monkeypatch.setattr(agent, "_models", [])
    _providers(monkeypatch, {"up": {"api_key_env": "UP_KEY"}})
    calls = []

    def fake_client(name, timeout=None, retries=None):
        calls.append(name)
        return SimpleNamespace(models=_list(["m1"]))

    monkeypatch.setattr(agent, "_client", fake_client)
    assert asyncio.run(agent.models()) == ["up/m1"]
    assert asyncio.run(agent.models()) == ["up/m1"]
    assert calls == ["up"]

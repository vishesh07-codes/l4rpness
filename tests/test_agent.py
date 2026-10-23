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


def _stream_client(chunks):
    """Fake OpenAI client yielding prebuilt stream chunks."""

    async def create(**kwargs):
        async def gen():
            for chunk in chunks:
                yield chunk

        return gen()

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )


def _chunk(reasoning=None, content=None):
    delta = SimpleNamespace(
        reasoning_content=reasoning, content=content, tool_calls=None
    )
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)


def _collect_step(monkeypatch, provider_conf, model):
    _providers(monkeypatch, {"gw": provider_conf})
    monkeypatch.setattr(agent.tools, "schemas_for", lambda *a: [])
    chunks = [
        _chunk(content="I'll run a "),
        _chunk(reasoning="batch."),
        _chunk(content=" done"),
    ]
    monkeypatch.setattr(agent, "_client", lambda *a, **k: _stream_client(chunks))
    campaign = {"model": model, "effort": "high"}
    calls = []
    client = _stream_client(chunks)

    async def run():
        return [
            (kind, data)
            async for kind, data in agent._step(client, campaign, [], calls)
        ]

    return asyncio.run(run())


def test_step_folds_reasoning_into_content_for_plain_chat_endpoints(monkeypatch):
    events = _collect_step(
        monkeypatch,
        {"api_key_env": "GW_KEY", "thinking": False},
        "gw/deepseek-chat",
    )
    kinds = [kind for kind, _ in events if kind in ("thinking", "content")]
    assert "thinking" not in kinds
    assert "".join(data for kind, data in events if kind == "content") == (
        "I'll run a batch. done"
    )


def test_step_keeps_reasoning_separate_for_thinking_endpoints(monkeypatch):
    events = _collect_step(
        monkeypatch,
        {"api_key_env": "GW_KEY", "thinking": True},
        "gw/deepseek-reasoner",
    )
    kinds = [kind for kind, _ in events if kind in ("thinking", "content")]
    assert kinds == ["content", "thinking", "content"]

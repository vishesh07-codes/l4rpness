import asyncio
import json

import pytest

from app import agent, config, db, routes, supervisor, tools


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "l4rpness.db")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(agent, "provider_key", lambda provider: "test-key")
    monkeypatch.setattr(agent, "_models", [])
    monkeypatch.setattr(agent, "_model_limits", {})
    routes.RUNNING.clear()
    db.init()
    yield
    routes.RUNNING.clear()
    for task in list(supervisor.SUPERVISORS.values()):
        task.cancel()


def _seed_tool_history(campaign_id, outputs):
    """Assistant turns each carrying one tool result of the given size."""
    with db.db() as conn:
        conn.execute(
            "INSERT INTO campaigns (id, target, model) VALUES (?, 't', 'm')",
            (campaign_id,),
        )
        for i, output in enumerate(outputs):
            calls = [{"id": f"c{i}", "name": "bash", "args": {"cmd": f"cmd{i}"}, "output": output, "ok": True, "ms": 1}]
            parts = [{"type": "tool", "id": f"c{i}", "name": "bash", "args": {"cmd": f"cmd{i}"}, "output": output, "ok": True, "ms": 1}]
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'user', 'go')",
                (campaign_id,),
            )
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content, tool_calls, parts) VALUES (?, 'assistant', ?, ?, ?)",
                (campaign_id, f"reply {i}", json.dumps(calls), json.dumps(parts)),
            )


def _tool_contents(history):
    return [m["content"] for m in history if m.get("role") == "tool"]


def test_short_history_is_byte_identical():
    _seed_tool_history(1, ["uid=0", "root"])
    history = supervisor._history(1)
    assert _tool_contents(history) == ["uid=0", "root"]


def test_old_outputs_cleared_past_budget_metadata_intact():
    # 20 outputs x 40k stored chars. Newest 8 stay complete; the older tail
    # clips to 20k each, and the oldest 4 exceed the 40k protection budget.
    _seed_tool_history(1, [f"out{i}-" + "x" * 40_000 for i in range(20)])
    history = supervisor._history(1)
    contents = _tool_contents(history)
    assert len(contents) == 20
    # newest 8 stay complete
    for i in range(12, 20):
        assert contents[i].startswith(f"out{i}-")
        assert len(contents[i]) > 40000
    # next 8 older are clipped but protected by the budget
    for i in range(4, 12):
        assert contents[i].startswith(f"out{i}-")
        assert len(contents[i]) == 20000
    # oldest 4 cleared, metadata (call pairing) intact
    for i in range(4):
        assert contents[i] == "[old tool output cleared.]"
    # assistant tool_calls still reference every call
    calls = [c for m in history if m.get("tool_calls") for c in m["tool_calls"]]
    assert len(calls) == 20


def test_budget_uses_post_clip_strings_not_stored_blobs():
    # 100k-char stored outputs clip to 20k (= 5k tokens) model-facing.
    # 12 outputs: newest 8 stay full, older 4 clip to 20k and remain under budget.
    # If the budget read stored sizes (25k each), everything old would clear.
    _seed_tool_history(1, ["y" * 100_000 for _ in range(12)])
    history = supervisor._history(1)
    contents = _tool_contents(history)
    assert len(contents) == 12
    assert all(len(c) == 20000 for c in contents[:4])
    assert all(len(c) == 100_000 for c in contents[4:])


def test_spill_path_survives_clearing():
    big = "z" * 40_000 + "\n[output truncated: 99,999 chars total, full output saved to tmp/20240101-dump.txt]"
    _seed_tool_history(1, [big for _ in range(20)])
    history = supervisor._history(1)
    contents = _tool_contents(history)
    assert contents[0].startswith("[old tool output cleared.")
    assert "tmp/20240101-dump.txt" in contents[0]


def test_prune_leaves_db_rows_untouched():
    _seed_tool_history(1, ["q" * 40_000 for _ in range(15)])
    supervisor._history(1)
    with db.db() as conn:
        row = conn.execute("SELECT parts FROM messages WHERE role = 'assistant' ORDER BY id LIMIT 1").fetchone()
    assert "q" * 100 in row["parts"]


def test_model_limit_cache_table_fallback_order(monkeypatch):
    agent._model_limits["9router/fast"] = 500_000
    assert agent.model_limit("9router/fast") == 500_000

    monkeypatch.setattr(config, "LIMITS", {"deepseek-flash": 1_000_000})
    assert agent.model_limit("deepseek/deepseek-flash") == 1_000_000
    assert agent.model_limit("mystery-model") == agent.FALLBACK_LIMIT


def test_limit_pin_overrides_api_cache(monkeypatch):
    """An explicit [llm.limits] pin wins over a (possibly wrong or stale)
    value from the provider listing: it is the operator's way to set the
    window when the API reports the wrong one or none at all."""
    monkeypatch.setattr(agent, "_model_limits", {"p/a": 128_000})
    monkeypatch.setattr(agent, "_limit_sources", {"p/a": "api"})
    monkeypatch.setattr(config, "LIMITS", {"a": 1_000_000})
    assert agent.model_limit("p/a") == 1_000_000
    assert agent.limit_source("p/a") == "table"


def test_limit_from_entry_shapes():
    assert agent._limit_from_entry({"capabilities": {"contextWindow": 262144}}) == 262144
    assert agent._limit_from_entry({"context_length": 1048576}) == 1048576
    assert agent._limit_from_entry({"id": "x"}) is None
    assert agent._limit_from_entry(object()) is None
    assert agent._limit_from_entry({"capabilities": {"contextWindow": True}}) is None


def test_usable_and_trigger_math(monkeypatch):
    monkeypatch.setattr(config, "LIMITS", {"big": 1_000_000})
    assert agent.usable_context("p/big") == 980_000

    monkeypatch.setattr(config, "COMPACT_AT_TOKENS", 0)
    assert agent.compact_trigger("p/big") == int(980_000 * 0.8)
    monkeypatch.setattr(config, "COMPACT_AT_TOKENS", 5000)
    assert agent.compact_trigger("p/big") == 5000
    monkeypatch.setattr(config, "COMPACT_AT_TOKENS", -1)
    assert agent.compact_trigger("p/big") is None


def test_models_captures_limits(monkeypatch):
    class FakeModel:
        def __init__(self, id, extra):
            self.id = id
            self._extra = extra

        def __getattr__(self, name):
            try:
                return self._extra[name]
            except KeyError:
                raise AttributeError(name)

    async def fake_list():
        yield FakeModel("a", {"capabilities": {"contextWindow": 200000}})
        yield FakeModel("b", {})

    class FakeModels:
        def list(self):
            return fake_list()

    class FakeClient:
        def __init__(self, provider):
            self.models = FakeModels()

    monkeypatch.setattr(agent, "_client", lambda provider, *a, **k: FakeClient(provider))
    monkeypatch.setattr(agent, "provider_key", lambda provider: "k")

    got = asyncio.run(agent.models())
    assert {"deepseek/a", "deepseek/b"} <= set(got)
    assert agent.model_limit("deepseek/a") == 200000
    assert agent.model_limit("deepseek/b") == agent.FALLBACK_LIMIT


def test_loop_compacts_past_derived_trigger(monkeypatch):
    seen = []

    async def fake_run(campaign_id, campaign, attachments, history, content, run, files=(), store_user=True, keep=False, tool_kind=None, include_attachments=True):
        return {"ok": True, "stopped": False, "empty": False, "error": ""}

    async def fake_compact(campaign_id, keep_override=None):
        seen.append(campaign_id)
        return 0

    monkeypatch.setattr(routes, "_run", fake_run)
    monkeypatch.setattr(supervisor, "compact_campaign", fake_compact)
    monkeypatch.setattr(config, "LIMITS", {"m": 100_000})  # trigger = 64k
    monkeypatch.setattr(config, "COMPACT_AT_TOKENS", 0)

    with db.db() as conn:
        conn.execute(
            "INSERT INTO campaigns (id, target, model, last_prompt_tokens) VALUES (1, 't', 'm', 99999)"
        )

    async def drive():
        assert supervisor.start(1)
        task = supervisor.SUPERVISORS[1]
        for _ in range(200):
            if seen:
                break
            await asyncio.sleep(0.01)
        supervisor.stop(1)
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(drive())
    # the loop compacts on every chain while over the derived trigger
    assert seen and all(s == 1 for s in seen)


def test_loop_skips_compact_below_trigger(monkeypatch):
    seen = []

    async def fake_run(campaign_id, campaign, attachments, history, content, run, files=(), store_user=True, keep=False, tool_kind=None, include_attachments=True):
        return {"ok": True, "stopped": False, "empty": False, "error": ""}

    async def fake_compact(campaign_id, keep_override=None):
        seen.append(campaign_id)
        return 0

    monkeypatch.setattr(routes, "_run", fake_run)
    monkeypatch.setattr(supervisor, "compact_campaign", fake_compact)
    monkeypatch.setattr(config, "LIMITS", {"m": 100_000})
    monkeypatch.setattr(config, "COMPACT_AT_TOKENS", 0)

    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model, last_prompt_tokens) VALUES (1, 't', 'm', 100)")

    async def drive():
        assert supervisor.start(1)
        task = supervisor.SUPERVISORS[1]
        await asyncio.sleep(0.15)
        supervisor.stop(1)
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(drive())
    assert seen == []


def _request_tokens(campaign_id):
    """Complete serialized request the way agent.run assembles it."""
    with db.db() as conn:
        campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        attachments = conn.execute("SELECT * FROM attachments WHERE campaign_id = ?", (campaign_id,)).fetchall()
    system = agent.build_prompt(campaign, attachments)
    history = supervisor._history(campaign_id)
    schemas = tools.schemas_for("supervisor", campaign["mode"])
    blob = system + json.dumps(history) + json.dumps(schemas)
    return len(blob) // 4


def test_serialized_request_plateaus_as_history_grows():
    # Pathological: every output maxed at 40k stored chars. 4x the history
    # must not grow the request: newest 8 + 40k budget is all that rides along.
    _seed_tool_history(1, ["k" * 40_000 for _ in range(15)])
    small = _request_tokens(1)
    _seed_tool_history(2, ["k" * 40_000 for _ in range(60)])
    big = _request_tokens(2)
    assert big <= small * 1.2


def test_typical_request_stays_under_30k():
    # Realistic turns: short replies, small tool outputs, no attachments.
    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (3, 't', 'm')")
        for i in range(30):
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content) VALUES (3, 'user', 'go')"
            )
            calls = [{"id": f"t{i}", "name": "bash", "args": {"cmd": "id"}, "output": "uid=0", "ok": True, "ms": 1}]
            parts = [{"type": "tool", "id": f"t{i}", "name": "bash", "args": {"cmd": "id"}, "output": "uid=0", "ok": True, "ms": 1}]
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content, tool_calls, parts) VALUES (3, 'assistant', ?, ?, ?)",
                (f"reply {i} with a short finding", json.dumps(calls), json.dumps(parts)),
            )
    assert _request_tokens(3) < 30_000


def test_newest_eight_tool_outputs_restore_full_spills():
    work = config.WORK_DIR / "4"
    (work / "tmp").mkdir(parents=True)
    full = "FULL-OUTPUT-" + "x" * 30_000
    (work / "tmp" / "latest.txt").write_text(full)
    marker = "head\n[output truncated: 30,012 chars total, full output saved to tmp/latest.txt."
    _seed_tool_history(4, ["old-output"] * 8 + [marker])

    history = supervisor._history(4)
    tool_messages = [message for message in history if message.get("role") == "tool"]
    assert tool_messages[-1]["content"].startswith(full)
    assert "full output saved to tmp/latest.txt" in tool_messages[-1]["content"]


def test_spill_restore_bounded_by_shared_budget():
    # Worst case: 8 newest tools each pointing at a max-size spill file.
    # Total restored context must stay within the shared budget, far below
    # the 128k fallback window — never 8x per-file caps.
    work = config.WORK_DIR / "5"
    (work / "tmp").mkdir(parents=True)
    for i in range(8):
        (work / "tmp" / f"spill{i}.txt").write_text("X" * 200_000)
    markers = [
        f"head\n[output truncated: 200,000 chars total, full output saved to tmp/spill{i}.txt.]"
        for i in range(8)
    ]
    _seed_tool_history(5, markers)
    history = supervisor._history(5)
    total = sum(len(m["content"]) for m in history if m.get("role") == "tool")
    assert total <= supervisor.RESTORE_TOTAL_BUDGET_CHARS + 8 * 500
    assert total < agent.FALLBACK_LIMIT


def test_spill_restore_rejects_arbitrary_campaign_files():
    work = config.WORK_DIR / "6"
    (work / "tmp").mkdir(parents=True)
    (work / "FINDINGS.md").write_text("SECRET-CREDS")
    out = supervisor._restore_spilled_output(6, "x full output saved to FINDINGS.md]")
    assert "SECRET-CREDS" not in out
    out = supervisor._restore_spilled_output(6, "x full output saved to notes/secret.md]")
    assert out == "x full output saved to notes/secret.md]"


def test_spill_restore_bounded_read_per_file():
    work = config.WORK_DIR / "7"
    (work / "tmp").mkdir(parents=True)
    (work / "tmp" / "big.txt").write_text("Y" * 200_000)
    out = supervisor._restore_spilled_output(
        7, "head [output truncated: 1, full output saved to tmp/big.txt.]"
    )
    assert len(out) <= supervisor.RESTORE_MAX_CHARS + 500
    assert "full output saved to tmp/big.txt" in out


def test_spill_hint_prefers_delegation(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "WORK_DIR", tmp_path)
    monkeypatch.setattr(config, "TOOL_OUTPUT_CHARS", 100)
    out, ok, _ = asyncio.run(tools.execute("bash", {"cmd": "python3 -c \"print('x' * 500)\""}, 1))
    assert ok
    assert "full output saved to tmp/" in out
    assert "read_file" in out
    assert "hypothesis generator" in out


def test_attachments_stub_after_first_chain():
    rows = [{"filename": "notes.txt"}, {"filename": "scope.pdf"}]
    base = {"target": "t", "info": "", "instructions": "", "description": "", "scope": "", "mode": "continuous"}

    import app.uploads as uploads_mod
    orig = uploads_mod.prompt_text
    uploads_mod.prompt_text = lambda rs: "\n".join(f"### {r['filename']}\nSECRET" for r in rs)
    try:
        full = agent.build_prompt(base, rows)
        stubbed = agent.build_prompt(base, rows, include_attachments=False)
    finally:
        uploads_mod.prompt_text = orig

    assert "SECRET" in full
    assert "SECRET" not in stubbed
    assert "notes.txt" in stubbed and "scope.pdf" in stubbed


def test_loop_sends_attachments_only_on_first_chain(monkeypatch):
    seen = []

    async def fake_run(campaign_id, campaign, attachments, history, content, run, files=(), store_user=True, keep=False, tool_kind=None, include_attachments=True):
        seen.append(include_attachments)
        with db.db() as conn:
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'assistant', 'did stuff')",
                (campaign_id,),
            )
        return {"ok": True, "stopped": False, "empty": False, "error": ""}

    monkeypatch.setattr(routes, "_run", fake_run)

    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (1, 't', 'm')")

    async def drive():
        assert supervisor.start(1)
        task = supervisor.SUPERVISORS[1]
        for _ in range(200):
            if len(seen) >= 2:
                break
            await asyncio.sleep(0.01)
        supervisor.stop(1)
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(drive())
    # first chain: whole attachments; later chains: stub only
    assert seen[0] is True
    assert seen[1] is False


def _seed_fat_args(campaign_id, count, size):
    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (?, 't', 'm')", (campaign_id,))
        for i in range(count):
            args = {"cmd": f"run {i} " + "P" * size}
            calls = [{"id": f"a{i}", "name": "bash", "args": args, "output": "ok", "ok": True, "ms": 1}]
            parts = [{"type": "tool", "id": f"a{i}", "name": "bash", "args": args, "output": "ok", "ok": True, "ms": 1}]
            conn.execute("INSERT INTO messages (campaign_id, role, content) VALUES (?, 'user', 'go')", (campaign_id,))
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content, tool_calls, parts) VALUES (?, 'assistant', ?, ?, ?)",
                (campaign_id, f"r{i}", json.dumps(calls), json.dumps(parts)),
            )


def _replayed_args(history):
    out = []
    for m in history:
        for call in m.get("tool_calls") or []:
            out.append(json.loads(call["function"]["arguments"])["cmd"])
    return out


def test_old_fat_args_truncated_newest_five_whole():
    _seed_fat_args(1, 8, 5000)
    args = _replayed_args(supervisor._history(1))
    assert len(args) == 8
    for cmd in args[:3]:
        assert cmd.endswith("chars truncated]")
        assert len(cmd) < 2500
    for cmd in args[3:]:
        assert cmd.endswith("P" * 10)
        assert len(cmd) > 5000


def test_short_histories_keep_args_byte_identical():
    _seed_fat_args(1, 3, 5000)
    args = _replayed_args(supervisor._history(1))
    assert all(len(cmd) > 5000 and "truncated" not in cmd for cmd in args)


def test_arg_clipping_leaves_db_untouched():
    _seed_fat_args(1, 8, 5000)
    supervisor._history(1)
    with db.db() as conn:
        row = conn.execute("SELECT tool_calls FROM messages WHERE role = 'assistant' ORDER BY id LIMIT 1").fetchone()
    assert "P" * 100 in row["tool_calls"]
    assert "truncated" not in row["tool_calls"]


def test_reset_model_cache_clears_limits():
    agent._models.append("p/m")
    agent._model_limits["p/m"] = 999
    agent._limit_sources["p/m"] = "api"
    agent.reset_model_cache()
    assert agent._models == []
    assert agent._model_limits == {}
    assert agent._limit_sources == {}
    assert agent.limit_source("p/m") == "fallback"


def test_limit_source_order(monkeypatch):
    agent._model_limits["p/a"] = 500_000
    agent._limit_sources["p/a"] = "api"
    assert agent.limit_source("p/a") == "api"
    monkeypatch.setattr(config, "LIMITS", {"b": 1_000_000})
    assert agent.limit_source("p/b") == "table"
    assert agent.limit_source("nope") == "fallback"


def test_spill_path_has_no_trailing_period():
    big = "z" * 40_000 + "\n[output truncated: 99,999 chars total, full output saved to tmp/20240101-dump.txt.]"
    _seed_tool_history(1, [big for _ in range(20)])
    history = supervisor._history(1)
    contents = [m["content"] for m in history if m.get("role") == "tool"]
    assert contents[0].startswith("[old tool output cleared.")
    assert "tmp/20240101-dump.txt" in contents[0]
    assert "tmp/20240101-dump.txt." not in contents[0]


def test_estimated_usage_never_lowers_ctx_gauge(monkeypatch):
    async def fake_run(campaign, attachments, history, campaign_id, stop=None, on_tool_output=None, include_attachments=True):
        yield "usage", {"prompt_tokens": 5000, "completion_tokens": 10}
        yield "usage", {"prompt_tokens": 100, "completion_tokens": 1, "estimated": True}
        yield "content", "done"

    monkeypatch.setattr(agent, "run", fake_run)
    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (1, 't', 'm')")

    async def drive():
        run = {"stop": asyncio.Event(), "parts": [], "pending": None, "queue": asyncio.Queue(), "consumer": False}
        routes.RUNNING[1] = run
        await routes._run(1, {"model": "m"}, [], [], "go", run)

    asyncio.run(drive())
    with db.db() as conn:
        row = conn.execute("SELECT last_prompt_tokens, usage_in FROM campaigns WHERE id = 1").fetchone()
    assert row["last_prompt_tokens"] == 5000
    assert row["usage_in"] == 5100


def test_compaction_prompt_preserves_attachment_facts(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "COMPACT_KEEP_MESSAGES", 2)
    upload_dir = config.UPLOAD_DIR / "1"
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "scope.txt").write_text("SCOPE-SECRET in scope: only /admin is fair game")
    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (1, 't', 'm')")
        conn.execute(
            "INSERT INTO attachments (campaign_id, field, filename, content_type, size, path) VALUES (1, 'scope', 'scope.txt', 'text/plain', 44, '1/scope.txt')"
        )
        for role, body in [("user", "a"), ("assistant", "b"), ("user", "c"), ("assistant", "d"), ("user", "e"), ("assistant", "f")]:
            conn.execute("INSERT INTO messages (campaign_id, role, content) VALUES (1, ?, ?)", (role, body))

    seen = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            seen["prompt"] = kwargs["messages"][-1]["content"]
            from types import SimpleNamespace
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="MEMORY ok"))])

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr("app.agent._client", lambda provider: FakeClient())
    assert asyncio.run(supervisor.compact_campaign(1)) == 4
    assert "SCOPE-SECRET" in seen["prompt"]

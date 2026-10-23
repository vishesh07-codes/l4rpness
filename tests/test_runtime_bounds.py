"""Runtime reliability invariants: bounded context, compaction progress,
evidence preservation, worker permissions, MCP error signaling.

Each test drives the actual execution path (agent.run, compact_campaign,
_render_transcript, tools.execute, mcp.call, supervisor._history), not just
helpers — hostile inputs and provider failures included.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app import agent, config, db, mcp, routes, supervisor, tools


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


def _campaign(model="m"):
    return {
        "target": "t",
        "info": "",
        "instructions": "",
        "description": "",
        "scope": "",
        "model": model,
        "effort": config.DEFAULT_EFFORT,
        "mode": "continuous",
    }


def test_in_chain_budget_never_calls_provider_over_budget(monkeypatch):
    """50 consecutive maximum-size tool results through agent.run: the
    provider is either never called (truncate path) or called only with an
    estimate under budget (prune-when-safe path)."""
    monkeypatch.setattr(config, "LIMITS", {"m": 128_000})
    usable = agent.usable_context("m")
    history = [
        {"role": "tool", "tool_call_id": f"c{i}", "content": "X" * 20_000}
        for i in range(50)
    ]
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)

            async def empty():
                if False:
                    yield None

            return empty()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(agent, "_client", lambda provider: FakeClient())

    kinds = []

    async def drive():
        async for kind, data in agent.run(_campaign(), [], history, 1):
            kinds.append(kind)

    asyncio.run(drive())
    for kwargs in calls:
        schemas = agent.measured_schema_tokens(None, _campaign())
        assert (
            agent.estimate_messages_tokens(kwargs["messages"]) + schemas
            < usable - agent.SAFETY_MARGIN_TOKENS
        )
    assert "truncated" in kinds or calls, kinds


def test_in_chain_budget_truncates_when_unprunable(monkeypatch):
    """A tiny window where even fully pruned history exceeds budget: run()
    yields 'truncated' and never touches the provider."""
    monkeypatch.setattr(config, "LIMITS", {"m": 8_000})
    history = [
        {"role": "tool", "tool_call_id": f"c{i}", "content": "X" * 20_000}
        for i in range(50)
    ]
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError("provider must not be called over budget")

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(agent, "_client", lambda provider: FakeClient())

    kinds = []

    async def drive():
        async for kind, data in agent.run(_campaign(), [], history, 1):
            kinds.append(kind)

    asyncio.run(drive())
    assert "truncated" in kinds
    assert calls == []


def test_failed_compaction_makes_no_progress_and_holds_checkpoint():
    """Summarizer failure returns 0 and never advances compacted_until, so a
    retry cannot mistake failure for completion."""
    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (1, 't', 'm')")
        for i in range(12):
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content) VALUES (1, 'user', ?)",
                (f"msg {i}",),
            )

    class ExplodingCompletions:
        async def create(self, **kwargs):
            raise RuntimeError("provider down")

    class FakeChat:
        completions = ExplodingCompletions()

    class FakeClient:
        chat = FakeChat()

    import app.agent as agent_mod

    orig_client = agent_mod._client
    agent_mod._client = lambda provider: FakeClient()
    try:
        with pytest.raises(RuntimeError, match="provider down"):
            asyncio.run(supervisor.compact_campaign(1))
    finally:
        agent_mod._client = orig_client
    with db.db() as conn:
        row = conn.execute("SELECT compacted_until FROM campaigns WHERE id = 1").fetchone()
    assert row["compacted_until"] == 0


def test_compaction_retains_newest_evidence():
    """An oversized fold keeps the newest exploit output and marks the cut."""
    rows = [
        {"role": "assistant", "content": "", "tool_calls": "[]", "parts": "[]"},
    ]
    for i in range(20):
        calls = [{"id": f"k{i}", "name": "bash", "args": {"cmd": "id"}, "output": f"old-{i}-" + "x" * 5000, "ok": True, "ms": 1}]
        rows.append({
            "role": "assistant",
            "content": f"turn {i}",
            "tool_calls": json.dumps(calls),
            "parts": json.dumps([{"type": "tool", **calls[0]}]),
        })
    calls = [{"id": "new", "name": "bash", "args": {"cmd": "exploit"}, "output": "NEWEST-EXPLOIT-PROOF", "ok": True, "ms": 1}]
    rows.append({
        "role": "assistant",
        "content": "final",
        "tool_calls": json.dumps(calls),
        "parts": json.dumps([{"type": "tool", **calls[0]}]),
    })
    out = supervisor._render_transcript(1, rows, cap_chars=2_000)
    assert "NEWEST-EXPLOIT-PROOF" in out
    assert "older transcript lines dropped" in out


def test_generator_cannot_invoke_disallowed_mcp_tool():
    """Worker permission bypass through MCP: frank_net_scan is supervisor-only."""
    mcp._routes["frank_net_scan"] = ("frank", "net_scan")
    try:
        gen_allow = tools.allowed_for("generator", {"mode": "continuous"})
        out, ok, _ = asyncio.run(
            tools.execute("frank_net_scan", {"host": "example.com"}, 1, allow=gen_allow)
        )
        assert not ok
        assert "not available in this context" in out

        sup_allow = tools.allowed_for("supervisor", {"mode": "continuous"})
        out, ok, _ = asyncio.run(
            tools.execute("frank_net_scan", {"host": "example.com"}, 1, allow=sup_allow)
        )
        # passes the permission layer (fails later: no connected server)
        assert "not available in this context" not in out
    finally:
        mcp._routes.pop("frank_net_scan", None)


def test_full_request_fits_usable_window_production_worst_case(monkeypatch):
    """Eight maximum stored outputs + spills + brief + schemas fit the usable
    window: the complete request, not just one message class."""
    monkeypatch.setattr(config, "LIMITS", {"m": 128_000})
    work = config.WORK_DIR / "9"
    (work / "tmp").mkdir(parents=True, exist_ok=True)
    for i in range(8):
        (work / "tmp" / f"w{i}.txt").write_text("Z" * 40_000)
    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (9, 't', 'm')")
        for i in range(8):
            out = (
                f"head-{i}\n[output truncated: 40,000 chars total, "
                f"full output saved to tmp/w{i}.txt.]"
            )
            calls = [{"id": f"c{i}", "name": "bash", "args": {"cmd": "id"}, "output": out, "ok": True, "ms": 1}]
            parts = [{"type": "tool", "id": f"c{i}", "name": "bash", "args": {"cmd": "id"}, "output": out, "ok": True, "ms": 1}]
            conn.execute("INSERT INTO messages (campaign_id, role, content) VALUES (9, 'user', 'go')")
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content, tool_calls, parts) VALUES (9, 'assistant', ?, ?, ?)",
                (f"reply {i}", json.dumps(calls), json.dumps(parts)),
            )
    campaign = {"target": "t", "info": "", "instructions": "", "description": "",
                "scope": "", "mode": "continuous"}
    system = agent.build_prompt(campaign, [], tool_kind="supervisor")
    history = supervisor._history(9)
    schemas = tools.schemas_for("supervisor", "continuous")
    total_tokens = (len(system) + len(json.dumps(history)) + len(json.dumps(schemas))) // 4
    assert total_tokens < agent.usable_context("m"), total_tokens


def test_mcp_error_payload_is_failure_not_finding():
    """A server returning '{"error": "timeout"}' as success payload must read
    as failure through the real mcp.call path."""
    mcp._routes["frank_web_search"] = ("frank", "web_search")

    class FakeSession:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text='{"error": "timeout"}')],
                isError=False,
            )

    mcp._sessions["frank"] = FakeSession()
    try:
        text, ok = asyncio.run(mcp.call("frank_web_search", {"q": "x"}))
    finally:
        mcp._sessions.pop("frank", None)
        mcp._routes.pop("frank_web_search", None)
    assert not ok
    assert "timeout" in text


def test_context_error_pins_gauge_for_compaction(monkeypatch):
    """A context-length provider rejection forces last_prompt_tokens to the
    trigger through routes._run, so the next chain compacts instead of
    replaying the same oversized prompt."""
    monkeypatch.setattr(config, "LIMITS", {"m": 128_000})
    with db.db() as conn:
        conn.execute(
            "INSERT INTO campaigns (id, target, model, last_prompt_tokens) VALUES (1, 't', 'm', 0)"
        )

    async def blown_run(*args, **kwargs):
        raise agent.ContextTooLargeError("maximum context length exceeded")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(agent, "run", blown_run)
    run = {"stop": asyncio.Event(), "parts": [], "pending": None,
           "queue": asyncio.Queue(), "consumer": False}
    routes.RUNNING[1] = run
    try:
        async def drive():
            await routes._run(1, {"model": "m"}, [], [], "go", run)
        asyncio.run(drive())
    finally:
        routes.RUNNING.pop(1, None)
    with db.db() as conn:
        row = conn.execute("SELECT last_prompt_tokens FROM campaigns WHERE id = 1").fetchone()
    assert row["last_prompt_tokens"] >= agent.compact_trigger("m")


def _seed_fat_history(campaign_id, sizes):
    """Assistant turns each carrying one tool result of the given size."""
    with db.db() as conn:
        conn.execute(
            "INSERT INTO campaigns (id, target, model) VALUES (?, 't', 'm')",
            (campaign_id,),
        )
        for i, size in enumerate(sizes):
            output = "X" * size
            calls = [{"id": f"c{i}", "name": "bash", "args": {"cmd": "id"}, "output": output, "ok": True, "ms": 1}]
            parts = [{"type": "tool", "id": f"c{i}", "name": "bash", "args": {"cmd": "id"}, "output": output, "ok": True, "ms": 1}]
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'user', 'go')",
                (campaign_id,),
            )
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content, tool_calls, parts) VALUES (?, 'assistant', ?, ?, ?)",
                (campaign_id, f"reply {i}", json.dumps(calls), json.dumps(parts)),
            )


async def _drive_loop_to_end(campaign_id, timeout=15):
    task = supervisor.SUPERVISORS[campaign_id]
    await asyncio.wait_for(task, timeout=timeout)


def test_truncated_single_oversized_result_stops_without_second_run(monkeypatch):
    """One 500k tool result (newest, exempt from pruning): the truncated chain
    forces recovery; failed recovery stops the loop idle instead of running
    the same history again. Pairing and the newest user message survive."""
    monkeypatch.setattr(config, "LIMITS", {"m": 128_000})
    _seed_fat_history(1, [500_000])

    # Real agent.run (no fake): the in-chain guard must truncate before any
    # provider call; the exploding client guards both paths.
    class ExplodingCompletions:
        async def create(self, **kwargs):
            raise RuntimeError("summarizer down")

    class ExplodingChat:
        completions = ExplodingCompletions()

    class ExplodingClient:
        chat = ExplodingChat()

    monkeypatch.setattr(agent, "_client", lambda provider: ExplodingClient())
    run_calls = {"n": 0}
    orig_run = routes._run

    async def counting_run(*args, **kwargs):
        run_calls["n"] += 1
        return await orig_run(*args, **kwargs)

    monkeypatch.setattr(routes, "_run", counting_run)
    import app.supervisor as supervisor_mod

    async def main():
        assert supervisor_mod.start(1)
        await _drive_loop_to_end(1)

    asyncio.run(main())
    assert run_calls["n"] == 1
    with db.db() as conn:
        row = conn.execute("SELECT status, auto_running FROM campaigns WHERE id = 1").fetchone()
        assert row["status"] == "idle"
        assert row["auto_running"] == 0
        pairs = conn.execute(
            "SELECT tool_calls, parts FROM messages WHERE campaign_id = 1 AND role = 'assistant'"
        ).fetchall()
    assert pairs
    for pair in pairs:
        calls = json.loads(pair["tool_calls"] or "[]")
        parts = json.loads(pair["parts"] or "[]")
        part_ids = {p.get("id") for p in parts if isinstance(p, dict)}
        for call in calls:
            assert call.get("id") in part_ids
    # newest user message untouched
    with db.db() as conn:
        users = conn.execute(
            "SELECT content FROM messages WHERE campaign_id = 1 AND role = 'user' ORDER BY id"
        ).fetchall()
    assert users and users[-1]["content"] == "go"
    # recovery bookkeeping recorded equal estimates (no progress -> stop)
    run = routes.RUNNING.get(1)
    assert run is None  # loop popped the run on exit


def test_truncated_chain_with_fitting_input_keeps_rolling(monkeypatch):
    """Eight 100k newest outputs: the first recovery shrinks the input, and a
    later chain whose input fits the window (schemas and system prompt on
    top) rolls over instead of durably stopping. The old rule demanded a
    strictly smaller input on every recovery, which stopped healthy campaigns
    whose chains truncate from in-chain growth."""
    monkeypatch.setattr(config, "LIMITS", {"m": 128_000})
    _seed_fat_history(2, [100_000] * 8)

    async def fake_run(*args, **kwargs):
        yield "truncated", "[context budget reached mid-chain]"

    monkeypatch.setattr(agent, "run", fake_run)
    run_calls = {"n": 0}
    orig_run = routes._run

    async def counting_run(*args, **kwargs):
        run_calls["n"] += 1
        return await orig_run(*args, **kwargs)

    monkeypatch.setattr(routes, "_run", counting_run)

    async def advancing_compact(campaign_id, keep_override=None):
        with db.db() as conn:
            max_id = conn.execute(
                "SELECT MAX(id) AS m FROM messages WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()["m"]
            conn.execute(
                "UPDATE campaigns SET memory_summary = 'M', compacted_until = ? WHERE id = ?",
                (max_id - 1, campaign_id),
            )
        return 2

    monkeypatch.setattr(supervisor, "compact_campaign", advancing_compact)
    import app.supervisor as supervisor_mod

    async def main():
        assert supervisor_mod.start(2)
        task = supervisor_mod.SUPERVISORS[2]
        for _ in range(400):
            if run_calls["n"] >= 4:
                break
            await asyncio.sleep(0.01)
        supervisor_mod.stop(2)
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(main())
    # well past the one-recovery stop the old invariant imposed
    assert run_calls["n"] >= 4
    with db.db() as conn:
        row = conn.execute(
            "SELECT compacted_until FROM campaigns WHERE id = 2"
        ).fetchone()
    assert row["compacted_until"] > 0


def _blown_client(message):
    from unittest.mock import MagicMock

    from openai import BadRequestError

    class FakeCompletions:
        async def create(self, **kwargs):
            response = MagicMock()
            response.status_code = 400
            raise BadRequestError(message, response=response, body=None)

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    return FakeClient()


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("This model's maximum context length is 128000 tokens", True),
        ("maximum context_length exceeded (400)", True),
        ("too many tokens in request", True),
        ("token limit exceeded", True),
        ("out of context for this turn", True),
        ("invalid max_tokens: must be >= 1", False),
        ("invalid context parameter: foo", False),
        ("401 Unauthorized: bad api key", False),
        ("model not found", False),
    ],
)
def test_context_error_classifier_shapes(message, expected):
    assert agent._looks_like_context_error(Exception(message)) is expected


def test_step_level_invalid_param_not_context_overflow():
    """An invalid-parameter 400 through the real _step path must propagate as
    BadRequestError, never ContextTooLargeError (which would pin the gauge)."""

    async def drive(kind_message, exc_type):
        client = _blown_client(kind_message)
        calls = []
        gen = agent._step(client, _campaign(), [{"role": "user", "content": "hi"}], calls)
        with pytest.raises(exc_type):
            async for _ in gen:
                pass

    from openai import BadRequestError

    asyncio.run(drive("invalid max_tokens: must be >= 1", BadRequestError))
    asyncio.run(
        drive("This model's maximum context length is 128000 tokens", agent.ContextTooLargeError)
    )


def test_repeated_context_errors_stop_without_progress(monkeypatch):
    """Provider 400s + failing compaction: the loop stops after the second
    identical attempt (no-progress rule) instead of retrying forever."""
    monkeypatch.setattr(config, "LIMITS", {"m": 128_000})
    monkeypatch.setattr(config, "SUPERVISOR_BACKOFF", 0)
    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (3, 't', 'm')")
        conn.execute("INSERT INTO messages (campaign_id, role, content) VALUES (3, 'user', 'go')")

    async def nosleep(stop, seconds):
        return None

    monkeypatch.setattr(supervisor, "_sleep", nosleep)
    monkeypatch.setattr(
        agent, "_client",
        lambda provider: _blown_client("maximum context length exceeded"),
    )

    async def failing_compact(campaign_id, keep_override=None):
        return 0

    monkeypatch.setattr(supervisor, "compact_campaign", failing_compact)
    run_calls = {"n": 0}
    orig_run = routes._run

    async def counting_run(*args, **kwargs):
        run_calls["n"] += 1
        return await orig_run(*args, **kwargs)

    monkeypatch.setattr(routes, "_run", counting_run)
    import app.supervisor as supervisor_mod

    async def main():
        assert supervisor_mod.start(3)
        await _drive_loop_to_end(3, timeout=20)

    asyncio.run(main())
    assert run_calls["n"] == 2
    with db.db() as conn:
        row = conn.execute(
            "SELECT status, auto_running FROM campaigns WHERE id = 3"
        ).fetchone()
    assert row["status"] == "idle"
    assert row["auto_running"] == 0


def test_repeated_context_errors_bounded_at_max(monkeypatch):
    """Shrinking-but-still-failing input reaches the finite retry cap:
    exactly MAX_CONTEXT_ERRORS provider attempts, then a durable stop."""
    monkeypatch.setattr(config, "LIMITS", {"m": 128_000})
    monkeypatch.setattr(config, "SUPERVISOR_BACKOFF", 0)
    _seed_fat_history(4, [30000] * 6)

    async def nosleep(stop, seconds):
        return None

    monkeypatch.setattr(supervisor, "_sleep", nosleep)
    monkeypatch.setattr(
        agent, "_client",
        lambda provider: _blown_client("maximum context length exceeded"),
    )

    async def shrinking_compact(campaign_id, keep_override=None):
        with db.db() as conn:
            max_id = conn.execute(
                "SELECT MAX(id) AS m FROM messages WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()["m"]
            cur = conn.execute(
                "SELECT compacted_until FROM campaigns WHERE id = ?", (campaign_id,)
            ).fetchone()["compacted_until"]
            # advance past two oldest rows per attempt: strictly shrinking input
            rows = conn.execute(
                "SELECT id FROM messages WHERE campaign_id = ? AND id > ? ORDER BY id LIMIT 2",
                (campaign_id, cur),
            ).fetchall()
            if not rows:
                return 0
            conn.execute(
                "UPDATE campaigns SET memory_summary = 'M', compacted_until = ? WHERE id = ?",
                (rows[-1]["id"], campaign_id),
            )
        return len(rows)

    monkeypatch.setattr(supervisor, "compact_campaign", shrinking_compact)
    run_calls = {"n": 0}
    orig_run = routes._run

    async def counting_run(*args, **kwargs):
        run_calls["n"] += 1
        return await orig_run(*args, **kwargs)

    monkeypatch.setattr(routes, "_run", counting_run)
    import app.supervisor as supervisor_mod

    async def main():
        assert supervisor_mod.start(4)
        await _drive_loop_to_end(4, timeout=20)

    asyncio.run(main())
    assert run_calls["n"] == supervisor.MAX_CONTEXT_ERRORS
    with db.db() as conn:
        row = conn.execute(
            "SELECT status, auto_running FROM campaigns WHERE id = 4"
        ).fetchone()
    assert row["status"] == "idle"
    assert row["auto_running"] == 0


def test_usable_context_never_exceeds_limit():
    assert agent.usable_context("nope") == agent.FALLBACK_LIMIT - agent.COMPACT_RESERVE
    import app.config as config_mod

    old, config_mod.LIMITS = config_mod.LIMITS, {"tiny": 8_000}
    try:
        assert agent.usable_context("tiny") == 8_000
        assert agent.usable_context("tiny") <= 8_000
    finally:
        config_mod.LIMITS = old


def test_estimate_counts_reasoning_content():
    base = [{"role": "assistant", "content": "hi" * 1000}]
    thinking = [{"role": "assistant", "content": "hi" * 1000, "reasoning_content": "R" * 8_000}]
    assert agent.estimate_messages_tokens(thinking) - agent.estimate_messages_tokens(base) == 2_000


def test_estimate_divisor_is_documented_heuristic():
    # chars//4 is NOT an upper bound: dense unicode tokenizes far denser.
    # Guards must rely on SAFETY_MARGIN + measured schemas + recoverable
    # truncation, never on the estimate alone.
    assert agent.EST_CHARS_PER_TOKEN == 4
    assert agent.estimate_messages_tokens([{"role": "user", "content": "中" * 10_000}]) == 2_500
    assert agent.SAFETY_MARGIN_TOKENS > 0


def test_schema_growth_forces_truncation(monkeypatch):
    """Measured schemas (not the old fixed reserve): 100k tokens of schemas
    with a small history must truncate without calling the provider. Captures
    the exact tools kwarg passed to the adapter."""
    monkeypatch.setattr(config, "LIMITS", {"m": 128_000})
    history = [{"role": "tool", "tool_call_id": "c0", "content": "ok"}]
    big_schemas = [
        {"type": "function", "function": {"name": f"tool_{i}", "description": "d" * 2_000,
                                          "parameters": {"type": "object", "properties": {}}}}
        for i in range(250)
    ]
    monkeypatch.setattr(tools, "schemas_for", lambda *a, **k: big_schemas)
    assert agent.measured_schema_tokens(None, _campaign()) > 100_000
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError("schemas alone exceed budget: provider must not be called")

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(agent, "_client", lambda provider: FakeClient())
    kinds = []

    async def drive():
        async for kind, data in agent.run(_campaign(), [], history, 1):
            kinds.append(kind)

    asyncio.run(drive())
    assert "truncated" in kinds
    assert calls == []


def test_small_window_model_truncates(monkeypatch):
    monkeypatch.setattr(config, "LIMITS", {"m": 32_000})
    assert agent.usable_context("m") == 16_000
    assert agent.compact_trigger("m") == 12_800
    history = [
        {"role": "tool", "tool_call_id": f"c{i}", "content": "X" * 20_000}
        for i in range(10)
    ]
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError("provider must not be called over budget")

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(agent, "_client", lambda provider: FakeClient())
    kinds = []

    async def drive():
        async for kind, data in agent.run(_campaign(), [], history, 1):
            kinds.append(kind)

    asyncio.run(drive())
    assert "truncated" in kinds
    assert calls == []


def _seed_user_rows(campaign_id, n, prefix="msg"):
    with db.db() as conn:
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (?, 't', 'm')", (campaign_id,))
        for i in range(n):
            conn.execute(
                "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'user', ?)",
                (campaign_id, f"{prefix} {i}"),
            )


def _fake_summarizer(text):
    class FakeCompletions:
        async def create(self, **kwargs):
            FakeCompletions.captured = kwargs
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    return FakeClient()


def test_compact_rejects_blank_summaries(monkeypatch):
    """Blank and whitespace-only outputs are malformed: 0 folded, checkpoint
    frozen, prior good summary preserved. (Short-but-nonempty summaries are
    accepted: small folds can legitimately summarize briefly.)"""
    _seed_user_rows(10, 12)
    with db.db() as conn:
        conn.execute("UPDATE campaigns SET memory_summary = 'GOOD PRIOR' WHERE id = 10")
    import app.agent as agent_mod

    orig = agent_mod._client
    for bad in ["", "   \n  "]:
        agent_mod._client = lambda provider, _b=bad: _fake_summarizer(_b)
        try:
            assert asyncio.run(supervisor.compact_campaign(10)) == 0
        finally:
            agent_mod._client = orig
    with db.db() as conn:
        row = conn.execute(
            "SELECT compacted_until, memory_summary FROM campaigns WHERE id = 10"
        ).fetchone()
    assert row["compacted_until"] == 0
    assert row["memory_summary"] == "GOOD PRIOR"


def test_compact_caps_oversized_prior_summary(monkeypatch):
    """A 100k inherited summary is capped in the prompt and in storage."""
    _seed_user_rows(11, 12)
    big = "S" * 100_000
    with db.db() as conn:
        conn.execute("UPDATE campaigns SET memory_summary = ? WHERE id = 11", (big,))
    import app.agent as agent_mod

    orig = agent_mod._client
    summarizer = _fake_summarizer("FRESH SUMMARY WITH ENOUGH WORDS HERE")
    agent_mod._client = lambda provider: summarizer
    try:
        folded = asyncio.run(supervisor.compact_campaign(11))
    finally:
        agent_mod._client = orig
    assert folded > 0
    prompt = summarizer.chat.completions.captured
    assert len(prompt["messages"][1]["content"]) < 100_000
    assert "prior summary truncated" in prompt["messages"][1]["content"]
    with db.db() as conn:
        row = conn.execute(
            "SELECT memory_summary FROM campaigns WHERE id = 11"
        ).fetchone()
    assert len(row["memory_summary"]) <= supervisor.SUMMARY_MAX_CHARS + 64


def test_compact_three_failures_then_success_sequence():
    """Three failing attempts keep the checkpoint frozen with shrinking folds;
    the fourth (working summarizer) advances it and preserves newest evidence."""
    _seed_user_rows(12, 60)
    import app.agent as agent_mod

    orig = agent_mod._client

    class ExplodingCompletions:
        async def create(self, **kwargs):
            raise RuntimeError("down")

    class ExplodingChat:
        completions = ExplodingCompletions()

    class ExplodingClient:
        chat = ExplodingChat()

    folds = []
    try:
        agent_mod._client = lambda provider: ExplodingClient()
        for keep in [None, supervisor._preserve_keep(8, 1), supervisor._preserve_keep(8, 2)]:
            with pytest.raises(RuntimeError):
                asyncio.run(supervisor.compact_campaign(12, keep_override=keep))
            with db.db() as conn:
                cu = conn.execute(
                    "SELECT compacted_until FROM campaigns WHERE id = 12"
                ).fetchone()["compacted_until"]
            assert cu == 0
            folds.append(keep)
        assert folds == [None, 16, 32]
        agent_mod._client = lambda provider: _fake_summarizer("EVIDENCE: finding at a/b.py:10 proven by test X")
        folded = asyncio.run(supervisor.compact_campaign(12))
        assert folded > 0
        with db.db() as conn:
            row = conn.execute(
                "SELECT compacted_until, memory_summary FROM campaigns WHERE id = 12"
            ).fetchone()
        assert row["compacted_until"] > 0
        assert "a/b.py:10" in row["memory_summary"]
    finally:
        agent_mod._client = orig


def test_compact_failures_stop_loop_after_max(monkeypatch):
    """Gauge pinned high with a permanently failing summarizer: five compact
    attempts with growing preserve-keeps, four chains run, then a durable
    stop (the fifth failure trips MAX before another chain starts)."""
    monkeypatch.setattr(config, "LIMITS", {"m": 128_000})
    monkeypatch.setattr(config, "SUPERVISOR_BACKOFF", 0)
    _seed_user_rows(13, 12)
    with db.db() as conn:
        conn.execute("UPDATE campaigns SET last_prompt_tokens = 99999 WHERE id = 13")

    async def nosleep(stop, seconds):
        return None

    monkeypatch.setattr(supervisor, "_sleep", nosleep)

    async def empty_run(*args, **kwargs):
        if False:
            yield None

    monkeypatch.setattr(agent, "run", empty_run)
    seen_keeps = []

    async def recording_fail(campaign_id, keep_override=None):
        seen_keeps.append(keep_override)
        raise RuntimeError("summarizer down")

    monkeypatch.setattr(supervisor, "compact_campaign", recording_fail)
    run_calls = {"n": 0}
    orig_run = routes._run

    async def counting_run(*args, **kwargs):
        run_calls["n"] += 1
        return await orig_run(*args, **kwargs)

    monkeypatch.setattr(routes, "_run", counting_run)
    import app.supervisor as supervisor_mod

    async def main():
        assert supervisor_mod.start(13)
        await _drive_loop_to_end(13, timeout=20)

    asyncio.run(main())
    assert run_calls["n"] == supervisor.MAX_COMPACT_FAILURES - 1
    assert seen_keeps == [None, 16, 32, 64, 128]
    with db.db() as conn:
        row = conn.execute(
            "SELECT status, auto_running FROM campaigns WHERE id = 13"
        ).fetchone()
    assert row["status"] == "idle"
    assert row["auto_running"] == 0


def test_loop_ignores_noop_compaction(monkeypatch):
    """A pinned gauge plus compaction passes that report NOTHING_TO_FOLD must
    not trip the failure stop: nothing-to-fold is not a failure. The old code
    counted it, so a healthy campaign stopped after five rounds."""
    monkeypatch.setattr(config, "LIMITS", {"m": 128_000})
    monkeypatch.setattr(config, "SUPERVISOR_BACKOFF", 0)
    with db.db() as conn:
        conn.execute(
            "INSERT INTO campaigns (id, target, model, last_prompt_tokens) "
            "VALUES (1, 't', 'm', 99999)"
        )

    async def nosleep(stop, seconds):
        return None

    monkeypatch.setattr(supervisor, "_sleep", nosleep)

    async def fake_run(campaign_id, campaign, attachments, history, content, run, files=(), store_user=True, keep=False, tool_kind=None, include_attachments=True):
        return {"ok": True, "stopped": False, "empty": False, "error": ""}

    noops = {"n": 0}

    async def nothing_compact(campaign_id, keep_override=None):
        noops["n"] += 1
        return supervisor.NOTHING_TO_FOLD

    monkeypatch.setattr(routes, "_run", fake_run)
    monkeypatch.setattr(supervisor, "compact_campaign", nothing_compact)
    import app.supervisor as supervisor_mod

    target = supervisor.MAX_COMPACT_FAILURES + 2

    async def main():
        assert supervisor_mod.start(1)
        task = supervisor_mod.SUPERVISORS[1]
        for _ in range(400):
            if noops["n"] >= target:
                break
            await asyncio.sleep(0.01)
        supervisor.stop(1)
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(main())
    assert noops["n"] >= target


class _HangingSession:
    calls = 0

    async def call_tool(self, name, arguments):
        type(self).calls += 1
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def _register_hanging_server(monkeypatch, timeout=0.2):
    monkeypatch.setattr(mcp, "MCP_CALL_TIMEOUT", timeout)
    mcp._routes["frank_stall"] = ("frank", "stall")
    mcp._sessions["frank"] = _HangingSession()
    mcp._locks.pop("frank", None)
    _HangingSession.calls = 0


def test_mcp_concurrent_timeouts_deterministic_no_orphans(monkeypatch):
    """Two simultaneous hanging calls: both fail with timeouts, total time is
    bounded, the stale session is evicted exactly once, no stacks orphaned."""
    _register_hanging_server(monkeypatch, timeout=0.2)
    import time

    async def main():
        started = time.monotonic()
        results = await asyncio.gather(
            mcp.call("frank_stall", {}), mcp.call("frank_stall", {})
        )
        return time.monotonic() - started, results

    try:
        elapsed, results = asyncio.run(main())
    finally:
        mcp._routes.pop("frank_stall", None)
        mcp._sessions.pop("frank", None)
        mcp._locks.pop("frank", None)
    assert elapsed < 1.5, elapsed
    for text, ok in results:
        assert not ok
        assert "timed out" in text
    assert "frank" not in mcp._sessions
    assert "frank" not in mcp._stacks


def test_mcp_timeout_evicts_session(monkeypatch):
    _register_hanging_server(monkeypatch, timeout=0.2)
    try:
        text, ok = asyncio.run(mcp.call("frank_stall", {}))
    finally:
        mcp._routes.pop("frank_stall", None)
    assert not ok
    assert "timed out" in text
    assert "frank" not in mcp._sessions
    # next call fails cleanly (not connected) instead of hanging
    mcp._routes["frank_stall"] = ("frank", "stall")
    try:
        text, ok = asyncio.run(mcp.call("frank_stall", {}))
    finally:
        mcp._routes.pop("frank_stall", None)
        mcp._locks.pop("frank", None)
    assert not ok
    assert "not connected" in text


def test_mcp_cancel_propagates_and_evicts(monkeypatch):
    """User-initiated cancellation surfaces as CancelledError (never a tool
    result) and the interrupted transport is evicted."""
    _register_hanging_server(monkeypatch, timeout=5.0)
    import contextlib

    async def main():
        task = asyncio.ensure_future(mcp.call("frank_stall", {}))
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return task

    try:
        task = asyncio.run(main())
    finally:
        mcp._routes.pop("frank_stall", None)
        mcp._locks.pop("frank", None)
    assert task.cancelled()
    assert "frank" not in mcp._sessions


def test_mcp_reconnect_bounded(monkeypatch):
    """A hanging _connect during reconnect returns False within the timeout
    instead of wedging the caller."""

    async def hanging_connect(spec):
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(mcp, "MCP_CALL_TIMEOUT", 0.2)
    monkeypatch.setattr(mcp, "_connect", hanging_connect)
    monkeypatch.setattr(
        mcp, "definitions", lambda: [{"name": "frank", "command": ["x"]}]
    )
    import time

    started = time.monotonic()
    result = asyncio.run(mcp._reconnect("frank"))
    elapsed = time.monotonic() - started
    assert result is False
    assert elapsed < 1.5, elapsed

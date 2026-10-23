# Phase 1 context-bloat fix — design spec

Date: 2026-10-07 | Branch: `spike/model-limits` | Issue: l4rps3c/l4rpness#7
Scope decision: phased — prune + compaction trigger + max_steps now;
delegate-first spill hint + attach-uploads-once deferred to phase 2.

## Problem

Steady-state chains send ~77k prompt tokens/request (vs 1.6k-config output cap).
`_history()` replays every tool output (≤20k chars each) on every request;
compaction at 950k never fires; `max_steps = -1` is unbounded. See issue #7
for the measured budget table.

## 1. Model limit plumbing (`app/agent.py`, `config.toml`)

- Extend `agent.models()` to also fill `_model_limits: dict["provider/bare", int]`
  from the same `models.list()` response objects:
  `capabilities.contextWindow ?? context_length`. Malformed shapes are skipped
  with a warning — extraction must never break model listing.
- Same cache lifecycle as `_models` (cleared on key save). No new invalidation.
- New `[llm.limits]` table for what APIs don't advertise, with source URL in a
  comment: `deepseek-flash / deepseek-v4-pro / deepseek-chat / deepseek-reasoner
  = 1_000_000` (https://api-docs.deepseek.com/quick_start/pricing).
- `agent.model_limit("provider/model") -> int`: cache → config table (bare id,
  then full id) → `128_000`. Never raises.
- `agent.usable_context(model) -> int`: `max(limit - 20_000, 16_000)`.
  The 20k reserves output + the compaction summary itself (opencode
  `COMPACTION_BUFFER` equivalent).

## 2. Prune old tool outputs (`app/supervisor.py::_history`)

- Read-time only. Stored `parts` keep full outputs: transcript UI and crash
  recovery (same rows) are unaffected. Only the model-facing replay shrinks.
- Walk tool outputs newest→oldest, accumulate `len // 4` tokens **over the
  actual model-facing strings after the existing 20k-char `_history()` clip**,
  not over stored `parts[].output` — the budget must be computed from what is
  really sent, otherwise pruning and payload can disagree. Past a **40k-token
  tool-output protection budget** (opencode `PRUNE_PROTECT` parity), replace
  output with `[old tool output cleared]`; keep name/args/ok/ms. Prune engages
  once >20k tokens are prunable (opencode `PRUNE_MINIMUM` parity). 40k is the
  protection budget for tool outputs, not a whole-prompt guarantee: the newest
  5 alone can contribute ~25k worst-case, plus system/schemas/attachments on
  top — acceptance therefore measures the complete serialized request (below).
- **Count floor of 5** (pentest accommodation): newest 5 tool outputs stay
  verbatim even over budget. Worst case bounded by the 20k-char per-output
  clip (5 × ~5k tokens). Exploit chains keep recent evidence whole.
- Spill path survives: if cleared output mentions `full output saved to ...`,
  that line is preserved so the model can page it back via `read_file`.
- Single choke point: manual chat (`send_message`) carries role/content only,
  so `_history()` is the only function that needs this.
- Out of scope (phase 2): fat *inputs* (big `bash` cmd args in `tool_calls`).

## 3. Auto compaction trigger (`app/supervisor.py`, `app/agent.py`, `config.toml`)

Repurposed semantics for `supervisor.compact_at_tokens`:
- `0` (new default) → auto: trigger = 80% of `usable_context(model)`.
  80% (not opencode's 100%) because our signal (`last_prompt_tokens`) is
  coarser than their session-total counter — firing at 100% discovers
  overflow one request too late.
- `> 0` → explicit threshold, today's behavior. Tuned operators keep theirs.
- `< 0` → disabled (moves today's "0 disables" here; config comment says so).
- Single resolver `agent.compact_trigger(model) -> int | None` (`None` =
  disabled), consumed by both the loop check and `compact_campaign`'s early
  return. Folding logic itself untouched.
- Net effect: 128k-fallback campaigns fold ~86k; 1M-window campaigns ~780k.

## 4. Finite `max_steps` (`config.toml` only)

- `tools.max_steps`: `-1` → `50`. `agent.run` already honors `> 0` and yields
  `[stopped after N tool steps]`; supervisor treats it as a normal chain end
  and rolls into the next chain. Zero code change. `-1` remains the uncapped
  escape hatch.

## 5. Tests

- Prune: old outputs cleared past budget with metadata intact, budget computed
  over post-clip strings (a test with stored outputs larger than the clip must
  still agree with the sent payload); newest 5 kept verbatim over budget;
  spill-path line preserved; DB rows untouched; short histories byte-identical.
- Limits: cache → table → 128k fallback order; malformed API shapes never
  raise; trigger math incl. disabled/explicit branches.
- Integration: supervisor loop with fake `last_prompt_tokens` above/below the
  derived trigger; summarizer mocked, assert on invocation.
- No-regression: checkpointed content/reasoning untouched; goal-mode
  `complete_engagement` detection (reads live `run["parts"]`) unaffected;
  full `pytest -q` green.

## Acceptance (from #7, tightened in review)
- Measured on the **complete serialized request** (system + schemas +
  attachments + pruned history + prompt), not on the tool-output portion
  alone: steady-state chains <30k prompt tokens for typical campaigns without
  massive uploads. The 40k figure is the tool-output protection budget, not a
  whole-prompt guarantee.
- Tokens plateau instead of growing monotonically (prune + compaction
  observable in logs).
- The compaction trigger operates on the actual model-facing prompt size
  (`last_prompt_tokens`, i.e. provider-reported usage of what was really
  sent): pruning must never make the trigger blind to real growth.
- All tests above green. Output cap (1.6k) is proxy policy — out of scope.

## Phase 3 (follow-up, same branch): fat input args

Payloads the model itself typed (base64 blobs, exploit bodies in `bash` cmd
args) replay inside `tool_calls` on every request. Same treatment as outputs,
read-time only in `_history()` via `_prune_call_args`: string args over
`HISTORY_ARG_CHARS` (2000) become head + `[…N chars truncated]`; newest 5 tool
calls exempt (same `PRUNE_KEEP_COUNT` floor — recent commands stay whole while
iterating). No budget walk: inputs are bounded by what the model typed, so a
flat per-arg cap suffices. DB untouched.

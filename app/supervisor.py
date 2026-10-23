"""The 24x7 supervisor loop.

One task per campaign. It runs the same agent chain the chat uses, forever,
until the operator stops it. A chain that asked the model "are you done?" never
ends the campaign: after it returns, the loop rebuilds the conversation from
the database, appends a continuation nudge, and starts the next chain. Messages
the operator sends while a chain is running land in the database and are picked
up by the next chain.
"""

import asyncio
import json
import logging
import random
import re
import time
from pathlib import Path

from . import agent, config, db, generator, tools, uploads

log = logging.getLogger("l4rpness")

SUPERVISORS: dict[int, asyncio.Task] = {}

MAX_BACKOFF_SECONDS = 120
# Consecutive context-length failures before the loop stops instead of
# retrying. Notices and backoff are not progress; only a clean chain resets.
MAX_CONTEXT_ERRORS = 3
# Consecutive failed compactions before the loop stops instead of retrying
# the same fold forever.
MAX_COMPACT_FAILURES = 5

# compact_campaign() sentinel: nothing worth folding this pass (a batch that
# small has nothing older to fold, or everything already is). A no-op, never
# counted against the failure budget — counting it stopped healthy campaigns.
NOTHING_TO_FOLD = -1

# Provider model listings live in an in-process cache; once cold (startup,
# key/endpoint edits) every context window silently resolves to the fallback.
# The loop re-warms at most this often so a dead provider cannot stall every
# iteration.
MODELS_WARM_RETRY_SECONDS = 60

# The supervisor's job is the worker pipeline, not hunting. If it has not
# spawned a generator or a hunter for this long, the next turn is told so
# explicitly (and the operator sees a notice) — the policy prompt alone is not
# always enough to stop a model from doing the work itself.
DELEGATION_NUDGE_SECONDS = 300

DELEGATION_NUDGE = (
    f"[DELEGATION CHECK] You have not spawned a generator or a hunter for "
    f"{DELEGATION_NUDGE_SECONDS // 60} minutes. You are the supervisor: your job is to run "
    "the worker pipeline, not to test the target yourself. Do one of these now, before any "
    "more of your own testing:\n"
    "- no `new` hypotheses in the backlog -> spawn 3 to 5 hypothesis generators "
    "with different evidence-based focuses (spawn_hypothesis_generator);\n"
    "- `new` hypotheses exist -> dispatch them to hunters with spawn_hunter (up "
    "to 3 per worker, up to 5 workers);\n"
    "- `review` results exist -> rule on them with worker_status then "
    "hypothesis_update (done or dead).\n"
    "A turn spent driving exploits yourself instead of spawning or dispatching "
    "is a wasted turn."
)

BEGIN_PROMPT = (
    "Begin the engagement against {target}. Work continuously until the "
    "operator stops you. You are the SUPERVISOR: you do not test the target "
    "yourself, you run the worker pipeline. Spend at most about five tool "
    "calls establishing what the target is, then in THIS SAME TURN spawn 3 to "
    "5 hypothesis generators with different evidence-based focuses "
    "(spawn_hypothesis_generator) — do not explore the whole target first. The "
    "moment hypotheses exist, dispatch them to hunter workers with "
    "spawn_hunter (up to 3 per worker) and rule on the results they file. "
    "Never declare the engagement complete."
)

CONTINUE_PROMPT = (
    "Your turn ended, but the engagement is not finished. This campaign is "
    "never \"done\": every software has vulnerabilities that have not been "
    "found yet, and a clean surface only means the coverage was shallow. Your "
    "next action is a PIPELINE action, not your own testing: (1) spawn 3 to 5 "
    "generators if the backlog has no 'new' hypotheses, (2) dispatch every "
    "'new' hypothesis to hunters with spawn_hunter, (3) rule on every 'review' "
    "result with worker_status then hypothesis_update. Do those before running "
    "any exploit yourself — hunting is the workers' job, dispatching is yours."
)

GOAL_BEGIN_PROMPT = (
    "Begin the engagement against {target}. Pursue the operator's stated goal "
    "to a concrete, evidence-backed result. Keep working with real tools until "
    "the goal is genuinely met, then call complete_engagement with a one-line "
    "summary. If one angle fails, try another. First, use the available tools "
    "to establish what the target is, inspect the codebase or reachable surface, "
    "and record concrete scope and technology facts. Only after that "
    "reconnaissance, spawn several hypothesis generators with different "
    "evidence-based focus areas (spawn_hypothesis_generator), then pull work "
    "from hypothesis_list and record results with hypothesis_update."
)

GOAL_CONTINUE_PROMPT = (
    "Your turn ended but the goal is not achieved yet. Prefer working the "
    "hypothesis backlog (hypothesis_list) over inventing angles from scratch, "
    "and spawn more generators when the backlog runs dry. Review your notes and "
    "results, pick the next angle, and keep working with real tools. When the "
    "goal is genuinely met and the evidence is recorded in your work "
    "directory, call complete_engagement with a one-line summary."
)


def is_running(campaign_id: int) -> bool:
    task = SUPERVISORS.get(campaign_id)
    return task is not None and not task.done()


def _mark_running(campaign_id: int) -> None:
    with db.db() as conn:
        conn.execute(
            "UPDATE campaigns SET auto_running = 1, status = 'running', updated_at = datetime('now') WHERE id = ?",
            (campaign_id,),
        )


def start(campaign_id: int) -> bool:
    """Start the loop for a campaign. Idempotent; returns True if it is running."""
    from . import routes

    task = SUPERVISORS.get(campaign_id)
    if task is not None and not task.done():
        run = routes.RUNNING.get(campaign_id)
        if run is not None and run["stop"].is_set():
            # a stop was requested but the loop is still winding down; a steer
            # cancels the stop so the live loop keeps going and picks it up
            _mark_running(campaign_id)
            run["stop"].clear()
        return True

    with db.db() as conn:
        campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if campaign is None:
        return False

    provider, _ = agent.split_model(campaign["model"])
    if not agent.provider_key(provider):
        log.warning("supervisor not started for campaign %d: no key for %s", campaign_id, provider)
        return False

    run = routes.RUNNING.get(campaign_id)
    if run is not None and not run.get("supervised"):
        # a manual chat run owns the campaign right now; try again later
        log.warning("supervisor not started for campaign %d: a chat run is active", campaign_id)
        return False

    _mark_running(campaign_id)

    if run is None:
        run = {
            "stop": asyncio.Event(),
            "parts": [],
            "pending": None,
            "queue": asyncio.Queue(),
            "consumer": False,
            "supervised": True,
            "seq": 0,
        }
        routes.RUNNING[campaign_id] = run
    else:
        run["stop"].clear()

    SUPERVISORS[campaign_id] = asyncio.create_task(_loop(campaign_id))
    log.info("supervisor started for campaign %d", campaign_id)
    return True


def stop(campaign_id: int) -> bool:
    """Stop the loop. The current chain winds down gracefully; the loop exits."""
    from . import routes

    with db.db() as conn:
        conn.execute(
            "UPDATE campaigns SET auto_running = 0, updated_at = datetime('now') WHERE id = ?",
            (campaign_id,),
        )

    run = routes.RUNNING.get(campaign_id)
    if run is None or not run.get("supervised"):
        return False

    run["stop"].set()
    task = SUPERVISORS.get(campaign_id)
    if task is None or task.done():
        routes.RUNNING.pop(campaign_id, None)
    return True


def restore() -> int:
    """Resume every campaign that was running before the process restarted.

    A manual stop clears auto_running, so those campaigns stay stopped.
    """
    with db.db() as conn:
        rows = conn.execute("SELECT id FROM campaigns WHERE auto_running = 1").fetchall()

    started = 0
    for row in rows:
        if start(row["id"]):
            started += 1
    if rows:
        log.info("supervisor restore: resumed %d of %d campaigns", started, len(rows))
    return started


def _replay(rows, root: Path, by_message: dict | None = None) -> list[dict]:
    """Turn stored turn rows into model messages.

    Assistant turns keep their tool calls and the tool outputs, not just the
    final text, so restarting a chain does not wipe how a result was produced.
    `by_message` supplies per-user-message uploads (campaign history only).
    """
    history: list[dict] = []
    first_attachments_rendered = False
    for row in rows:
        content = row["content"] or ""
        if row["role"] == "user":
            attached = (by_message or {}).get(row["id"])
            if attached:
                # Mirror the system-prompt brief policy (full once, filenames
                # after): the first replayed user message with attachments
                # carries bodies; later ones only names so a 120KB brief is
                # not resent every chain via history.
                if not first_attachments_rendered:
                    context = uploads.prompt_text(attached)
                    if context:
                        content = f"{content}\n\n{context}".strip()
                    first_attachments_rendered = True
                else:
                    try:
                        names = ", ".join(a["filename"] for a in attached)
                    except Exception:
                        names = "attachments"
                    if names:
                        content = (
                            f"{content}\n\nAttachments (contents provided at "
                            f"campaign start; see early context): {names}"
                        ).strip()
            history.append({"role": "user", "content": content})
            continue

        try:
            calls = json.loads(row["tool_calls"] or "[]")
        except json.JSONDecodeError:
            calls = []
        try:
            parts = json.loads(row["parts"] or "[]")
        except json.JSONDecodeError:
            parts = []

        outputs = {
            part.get("id"): part.get("output")
            for part in parts
            if isinstance(part, dict) and part.get("type") == "tool" and part.get("id")
        }

        valid = [call for call in calls if isinstance(call, dict) and call.get("id") and call.get("name")]
        if not valid:
            if content:
                history.append({"role": "assistant", "content": content})
            continue

        history.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call.get("args") or {}),
                    },
                }
                for call in valid
            ],
        })
        for call in valid:
            output = outputs.get(call["id"])
            if output is None:
                output = call.get("output")
            # Stored outputs are already clipped to TOOL_OUTPUT_CHARS by
            # tools._clip; the newest RECENT_OUTPUT_KEEP_COUNT stay verbatim
            # per the prune exemption. Spill restoration happens after pruning
            # under a shared budget (see below).
            history.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": str(output if output is not None else "(no output recorded)"),
            })

    history = _prune_call_args(_prune_tool_outputs(history))
    # Restore spill evidence newest-first within a shared budget. Older tools
    # keep their clipped head + spill path for paging via read_file.
    budget = [RESTORE_TOTAL_BUDGET_CHARS]
    for message in reversed(history):
        if message.get("role") != "tool":
            continue
        if budget[0] <= 0:
            break
        message["content"] = _restore_spilled_output(
            root, str(message.get("content") or ""), budget
        )
    return history


def _history(campaign_id: int) -> list[dict]:
    """Rebuild the model conversation from the database."""
    with db.db() as conn:
        campaign = conn.execute(
            "SELECT memory_summary, compacted_until FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        compacted_until = campaign["compacted_until"] if campaign else 0
        rows = conn.execute(
            "SELECT * FROM messages WHERE campaign_id = ? AND id > ? ORDER BY id",
            (campaign_id, compacted_until),
        ).fetchall()
        files = conn.execute(
            "SELECT * FROM message_attachments WHERE campaign_id = ? ORDER BY id", (campaign_id,)
        ).fetchall()

    by_message: dict[int, list] = {}
    for file in files:
        by_message.setdefault(file["message_id"], []).append(file)

    history: list[dict] = []
    if campaign is not None and campaign["memory_summary"]:
        history.append({
            "role": "user",
            "content": (
                "[CAMPAIGN MEMORY] Earlier turns were compacted into this summary. "
                "Treat it as established context.\n\n" + campaign["memory_summary"]
            ),
        })
    return history + _replay(rows, _work_root(campaign_id), by_message)


# Tool-output protection budget (opencode PRUNE_PROTECT parity): the newest
# RECENT_OUTPUT_KEEP_COUNT tool results stay verbatim no matter what, older ones stay
# verbatim until this many tokens past them, then are cleared to a placeholder.
PRUNE_PROTECT_TOKENS = 40_000
# Don't churn small histories: pruning engages only past this much prunable
# output (opencode PRUNE_MINIMUM parity).
PRUNE_MINIMUM_TOKENS = 20_000
# Pentest accommodation: exploit chains live on recent evidence, so the newest
# RECENT_OUTPUT_KEEP_COUNT results are exempt from the budget even when outputs run fat.
PRUNE_KEEP_COUNT = 5
RECENT_OUTPUT_KEEP_COUNT = 8
# Upper bound for a single restored spill file, and a shared budget across one
# history rebuild. Per-file reads are bounded; the total keeps eight fat spills
# from exceeding small context windows on their own.
RESTORE_MAX_CHARS = 40_000
RESTORE_TOTAL_BUDGET_CHARS = 60_000
# Spill files are always written by tools._spill as tmp/*.txt under the
# campaign work dir. Only those paths are eligible for restoration — never an
# arbitrary campaign file named by tool output.
_SPILL_RE = re.compile(r"^tmp/[\w\-. ]+\.txt$")


def _cleared_output(content: str) -> str:
    """Placeholder for a pruned tool result.

    The call metadata (tool name, args, ok) already rides along in the
    assistant message, so only the stdout goes. A spill path survives so the
    model can page the evidence back with read_file if it truly needs it.
    """
    match = re.search(r"full output saved to ([^\s\]]+)", content)
    spill = match.group(1).rstrip(".,;:!?)") if match else ""
    suffix = f" Full output saved to {spill}; read it with read_file if truly needed." if spill else ""
    return f"[old tool output cleared.{suffix}]"


# Model-facing width of one tool result in history replay. The spill note
# ("full output saved to ...") is appended past the cut by tools._clip, so it
# is re-attached here — otherwise neither replay nor prune placeholders could
# ever point the model at the full evidence.
HISTORY_OUTPUT_CHARS = 20000


def _work_root(campaign_id: int) -> Path:
    """The campaign work directory: the root every spill and evidence path is
    relative to (a worker's own root is a subdirectory of this)."""
    return config.WORK_DIR / str(campaign_id)


def _spill_path_for(root: Path, spill: str):
    """Validated spill path under `root`/tmp, or None.

    Rejects absolute paths, parent escapes, non-tmp locations, and non-.txt
    names so arbitrary tool output cannot pull arbitrary campaign files
    (prompts, findings, secrets) into model context.
    """
    if ".." in spill or spill.startswith(("/", "~")):
        return None
    if not _SPILL_RE.match(spill):
        return None
    try:
        path = (root / spill).resolve()
        root_resolved = root.resolve()
    except OSError:
        return None
    if not path.is_relative_to(root_resolved) or not path.is_file():
        return None
    # Belt and suspenders: must live under root/tmp/ even after symlinks.
    try:
        path.relative_to(root_resolved / "tmp")
    except ValueError:
        return None
    return path


def _read_spill_head(path, limit: int) -> str | None:
    """Read at most limit+1 chars without loading a multi-MB spill file."""
    try:
        with path.open(errors="replace") as fh:
            data = fh.read(limit + 1)
    except OSError:
        return None
    return data


def _restore_spilled_output(
    root: Path, content: str, budget: list[int] | None = None
) -> str:
    """Restore a clipped tool result from its spill file under `root`.

    Only `tmp/*.txt` spills are eligible, reads are bounded, and an optional
    mutable single-item `budget` caps total restored chars across one history
    rebuild: when exhausted, the clipped head is returned unchanged.
    Cleared placeholders are never re-expanded: they exist precisely because
    the tail exceeded budget.
    """
    if content.startswith("[old tool output cleared"):
        return content
    match = re.search(r"full output saved to ([^\s\]]+)", content)
    if not match:
        return content
    spill = match.group(1).rstrip(".,;:!?)")
    path = _spill_path_for(root, spill)
    if path is None:
        return content
    if budget is not None and budget[0] <= 0:
        return content
    data = _read_spill_head(path, RESTORE_MAX_CHARS)
    if data is None:
        return content
    truncated = len(data) > RESTORE_MAX_CHARS
    snippet = data[:RESTORE_MAX_CHARS] if truncated else data
    if budget is not None:
        if len(snippet) > budget[0]:
            snippet = snippet[: budget[0]]
            truncated = True
        budget[0] -= len(snippet)
    if truncated:
        try:
            total = path.stat().st_size
            note = f"\n[restored output truncated: ~{total:,} bytes on disk]"
        except OSError:
            note = "\n[restored output truncated]"
        snippet += note
    # Always re-pin the spill path so later pruning can point the model
    # back at the full evidence even after the in-context copy is clipped.
    return f"{snippet}\n[full output saved to {spill}]"


def _clip_history_output(text: str) -> str:
    if len(text) <= HISTORY_OUTPUT_CHARS:
        return text
    clipped = text[:HISTORY_OUTPUT_CHARS]
    match = re.search(r"full output saved to ([^\s\]]+)", text)
    spill = match.group(1).rstrip(".,;:!?)") if match else ""
    if spill and spill not in clipped:
        clipped += f"\n[spill: full output saved to {spill}]"
    return clipped


# Model-facing width of one argument value in replayed tool calls. Payloads
# the model itself typed (base64 blobs, exploit bodies) replay on every
# request; beyond this they become head + note. Same exemption as outputs:
# recent commands stay whole while iterating.
HISTORY_ARG_CHARS = 2000


def _clip_call_args(args: dict) -> dict:
    clipped = {}
    for key, value in args.items():
        if isinstance(value, str) and len(value) > HISTORY_ARG_CHARS:
            clipped[key] = value[:HISTORY_ARG_CHARS] + f"[…{len(value):,} chars truncated]"
        else:
            clipped[key] = value
    return clipped


def _prune_call_args(history: list[dict]) -> list[dict]:
    """Truncate fat args in old replayed tool calls. Read-time only."""
    calls = []
    for message in history:
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict):
                calls.append(call)
    for call in calls[:-PRUNE_KEEP_COUNT] if len(calls) > PRUNE_KEEP_COUNT else []:
        try:
            function = call.get("function") or {}
            data = json.loads(function.get("arguments") or "{}")
        except (json.JSONDecodeError, AttributeError):
            continue
        if isinstance(data, dict):
            function["arguments"] = json.dumps(_clip_call_args(data))
    return history


def _prune_tool_outputs(history: list[dict]) -> list[dict]:
    """Clear old tool outputs to bound replay growth. Read-time only.

    The database rows are untouched (transcript UI and crash recovery read the
    same rows), so this only shrinks what the model sees. The token budget is
    computed over the actual model-facing strings after the 20k clip above,
    never over stored blobs, so pruning and payload cannot disagree.
    """
    tool_idx = [i for i, message in enumerate(history) if message.get("role") == "tool"]
    older = (
        tool_idx[:-RECENT_OUTPUT_KEEP_COUNT]
        if len(tool_idx) > RECENT_OUTPUT_KEEP_COUNT
        else []
    )
    for i in older:
        history[i] = {
            **history[i],
            "content": _clip_history_output(str(history[i].get("content") or "")),
        }
    # newest RECENT_OUTPUT_KEEP_COUNT are exempt; the rest is the prunable tail
    candidates = older
    if not candidates:
        return history
    sizes = [len(str(history[i].get("content") or "")) // 4 for i in candidates]
    if sum(sizes) <= PRUNE_MINIMUM_TOKENS:
        return history

    # newest-first within the tail: keep until the budget runs out, clear older
    running = 0
    for pos in range(len(candidates) - 1, -1, -1):
        running += sizes[pos]
        if running <= PRUNE_PROTECT_TOKENS:
            continue
        i = candidates[pos]
        history[i] = {
            **history[i],
            "content": _cleared_output(str(history[i].get("content") or "")),
        }
    return history


COMPACT_SYSTEM = (
    "You compact the memory of an autonomous penetration-testing campaign. "
    "Write a dense, factual handoff of everything that matters and nothing that does not. "
    "Preserve: the target and scope, discovered endpoints, hosts and tech stack, credentials "
    "or tokens found, confirmed findings with file/line and how they were proven, hypotheses "
    "tried and their outcomes, dead ends and rejected ideas, current access, and the open plan. "
    "Keep exact values (paths, hosts, tokens, commands) where they matter. "
    "No filler, no apologies, no caveats."
)


def _render_transcript(root: Path, rows, cap_chars: int = 240_000) -> str:
    lines: list[str] = []
    for row in rows:
        content = (row["content"] or "").strip()
        if row["role"] == "user":
            lines.append(f"USER: {content[:4000]}")
            continue

        try:
            calls = json.loads(row["tool_calls"] or "[]")
        except json.JSONDecodeError:
            calls = []
        try:
            parts = json.loads(row["parts"] or "[]")
        except json.JSONDecodeError:
            parts = []
        outputs = {
            part.get("id"): part.get("output")
            for part in parts
            if isinstance(part, dict) and part.get("type") == "tool" and part.get("id")
        }

        text = content[:4000]
        for call in calls:
            if not isinstance(call, dict):
                continue
            output = outputs.get(call.get("id"))
            if output is None:
                output = call.get("output")
            args = json.dumps(call.get("args") or {})
            rendered = str(output) if output is not None else "(none)"
            # Compaction only keeps a 1500-char head: read a bounded slice of
            # an eligible spill file instead of restoring up to 30k.
            head = rendered[:1500]
            match = re.search(r"full output saved to ([^\s\]]+)", rendered)
            if match:
                spill = match.group(1).rstrip(".,;:!?)")
                spill_path = _spill_path_for(root, spill)
                if spill_path is not None:
                    data = _read_spill_head(spill_path, 1500)
                    if data is not None:
                        head = data[:1500]
            text += (
                f"\n\nTOOL {call.get('name', '?')} {args[:400]}"
                f"\nOUTPUT {head}"
            )
        if text.strip():
            lines.append(f"ASSISTANT: {text}")

    joined = "\n\n".join(lines)
    if len(joined) <= cap_chars:
        return joined
    # Keep the newest evidence: the summarizer must see what just happened,
    # not just how the fold started. Mark the cut so it cannot pose as complete.
    return "[older transcript lines dropped]\n" + joined[-cap_chars:]


def _preserve_keep(base_keep: int, failures: int) -> int:
    """Tail rows to preserve after consecutive compaction failures: doubling
    per failure shrinks the folded span, so a failing compaction retries
    strictly smaller inputs (gate A ends the sequence when nothing remains)."""
    return base_keep * (2 ** failures)


# Stored memory summaries are re-sent on every request (history[0]) and
# re-embedded in every compaction prompt: cap both directions so a long
# campaign cannot grow either input forever.
SUMMARY_MAX_CHARS = 20_000


def _row_tokens(row) -> int:
    """Rough model-facing token estimate for one stored message row.

    Mirrors the read-time replay shape: content and tool-call arguments count
    as stored (argument clipping happens later), tool outputs count at their
    replay clip so a fat stored blob cannot force an over-aggressive fold.
    """
    chars = len(row["content"] or "") + len(row["tool_calls"] or "")
    try:
        parts = json.loads(row["parts"] or "[]")
    except json.JSONDecodeError:
        parts = []
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "tool":
            chars += min(len(str(part.get("output") or "")), HISTORY_OUTPUT_CHARS)
    return chars // agent.EST_CHARS_PER_TOKEN


def _usable_or_fallback(model: str) -> int:
    """Usable context for a model, degrading to the conservative default."""
    try:
        return agent.usable_context(model)
    except Exception:
        return agent.FALLBACK_LIMIT - agent.COMPACT_RESERVE


def _fold_caps(usable: int, keep_override: int | None = None) -> tuple[int, int, int, int]:
    """(eff_keep, transcript_cap, brief_cap, summary_cap) for one fold.

    Transcript gets ~half the window in chars (4 chars/token), brief a bounded
    slice; the rest is headroom for the system prompt + the summary itself.
    After consecutive failures the caller raises keep_override, which scales
    every cap down, so each retry is provably smaller input rather than the
    same impossible request.
    """
    base_keep = config.COMPACT_KEEP_MESSAGES
    try:
        base_keep = max(1, int(base_keep))
    except (TypeError, ValueError):
        base_keep = 8
    eff_keep = keep_override if keep_override is not None else base_keep
    cap_divisor = max(1, eff_keep // base_keep)
    return (
        eff_keep,
        max(8_000, usable * 2 // cap_divisor),
        max(2_000, min(20_000, usable) // cap_divisor),
        max(4_000, SUMMARY_MAX_CHARS // cap_divisor),
    )


def _size_aware_fold(rows, compacted_until: int, eff_keep: int, usable: int):
    """Pick the fold: (boundary_id, rows) or None when nothing is worth folding.

    Keeps the newest messages verbatim and folds everything older; the kept
    tail shrinks by size until it fits the usable window (down to the newest
    single row), because one row here is a whole chain and counting rows alone
    says nothing about bytes.
    """
    if len(rows) < 2:
        return None
    eff_keep = max(1, eff_keep)
    target_keep = max(1, min(eff_keep, len(rows) - 1))
    keep = target_keep
    budget = max(16_000, usable - agent.COMPACT_RESERVE)
    while keep > 1 and sum(_row_tokens(row) for row in rows[-keep:]) > budget:
        keep -= 1

    if keep == target_keep and len(rows) <= eff_keep + 1:
        # A small benign batch whose tail fits: there is no older history to
        # win by folding, so do not burn a summarizer call on it.
        return None

    boundary = rows[-keep - 1]["id"]
    fold = [row for row in rows if compacted_until < row["id"] <= boundary]
    if not fold:
        return None
    return boundary, fold


async def _summarize_fold(
    campaign,
    fold,
    root: Path,
    transcript_cap: int,
    brief_cap: int,
    summary_cap: int,
    brief_text: str = "",
    prior_summary: str | None = None,
    label: str = "Campaign",
) -> str | None:
    """Ask the campaign model to fold `fold` into the rolling summary.

    Returns the new summary, or None when the model produced nothing usable —
    callers must not advance their checkpoint in that case, so a bad summary
    can never displace a good one.
    """
    if prior_summary is None:
        prior_summary = campaign["memory_summary"] or ""
    prompt = (
        f"{label} memory so far:\n{prior_summary or '(none yet)'}\n\n"
        f"Transcript to fold in:\n{_render_transcript(root, fold, cap_chars=transcript_cap)}"
    )
    if len(prior_summary) > summary_cap:
        # Cap the inherited summary in the prompt as well: without this, one
        # oversized summary makes every future compaction input oversized.
        prompt = prompt.replace(
            prior_summary,
            prior_summary[:summary_cap] + "\n[prior summary truncated]",
            1,
        )
    # Bound the summarizer input for small-window models: scope facts matter,
    # full brief bytes do not need to ride every compaction.
    if len(brief_text) > brief_cap:
        brief_text = brief_text[:brief_cap] + "\n[brief truncated for compaction]"
    if brief_text.strip():
        prompt += (
            "\n\nBrief attachments (their contents stop being re-sent after "
            "the first chain — preserve the key scope/target facts from them "
            "in the summary):\n" + brief_text
        )

    provider, bare_model = agent.split_model(campaign["model"])
    client = agent._client(provider)
    response = await client.chat.completions.create(
        model=bare_model,
        messages=[
            {"role": "system", "content": COMPACT_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    if not response or not getattr(response, "choices", None):
        return None
    summary = (response.choices[0].message.content or "").strip()
    # Blank or whitespace-only outputs are malformed. Short-but-nonempty
    # summaries are accepted: small folds can legitimately summarize briefly.
    if not summary:
        return None
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[:SUMMARY_MAX_CHARS] + "\n[summary truncated]"
    return summary


async def compact_campaign(campaign_id: int, keep_override: int | None = None) -> int:
    """Fold older messages into the rolling memory summary.

    Returns how many messages were folded. NOTHING_TO_FOLD means the pass
    found nothing worth folding (small benign batch, everything already
    folded, compaction disabled) — a no-op callers must not count as a
    failure. 0 means a real attempt failed to produce a summary (the
    checkpoint stays frozen, so a retry cannot mistake failure for
    completion). Transcript and brief caps derive from the campaign model's
    usable context so the summarizer call itself fits the window performing
    it. Failure is never fatal: the loop retries with a smaller fold (see
    _loop).
    """
    with db.db() as conn:
        campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        rows = conn.execute(
            "SELECT * FROM messages WHERE campaign_id = ? ORDER BY id", (campaign_id,)
        ).fetchall()
    if campaign is None:
        return NOTHING_TO_FOLD
    if agent.compact_trigger(campaign["model"]) is None:
        return NOTHING_TO_FOLD

    usable = _usable_or_fallback(campaign["model"])
    eff_keep, transcript_cap, brief_cap, summary_cap = _fold_caps(usable, keep_override)
    picked = _size_aware_fold(rows, campaign["compacted_until"], eff_keep, usable)
    if picked is None:
        return NOTHING_TO_FOLD
    boundary, fold = picked

    # Brief uploads stop riding along after the first chain (filenames only),
    # so the summary is their only long-term home: give the summarizer the
    # attachment text and an explicit instruction to preserve scope facts.
    with db.db() as conn:
        attachment_rows = conn.execute(
            "SELECT * FROM attachments WHERE campaign_id = ?", (campaign_id,)
        ).fetchall()
    summary = await _summarize_fold(
        campaign,
        fold,
        _work_root(campaign_id),
        transcript_cap,
        brief_cap,
        summary_cap,
        brief_text=uploads.prompt_text(attachment_rows),
    )
    if summary is None:
        return 0

    with db.db() as conn:
        conn.execute(
            """
            UPDATE campaigns SET memory_summary = ?, compacted_until = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (summary, boundary, campaign_id),
        )
    return len(fold)


def _degrade_over_budget(campaign, history: list[dict]) -> int:
    """Clear older in-memory tool outputs when the request would not fit the
    usable window. Read-path only: the database rows stay intact for the
    transcript and crash recovery. Returns how many outputs were cleared.

    The next chain gets the same treatment before it starts, so callers that
    judge whether a recovery actually reduced the input must apply it too —
    otherwise a perfectly recoverable history reads as no progress.
    """
    from . import routes

    usable = _usable_or_fallback(campaign["model"])
    if agent.estimate_messages_tokens(history) < usable:
        return 0

    degraded = 0
    tool_idx = [
        i for i, message in enumerate(history) if message.get("role") == "tool"
    ]
    for i in tool_idx[:-RECENT_OUTPUT_KEEP_COUNT] if len(
        tool_idx
    ) > RECENT_OUTPUT_KEEP_COUNT else []:
        content = history[i].get("content")
        if isinstance(content, str) and not content.startswith(
            "[old tool output cleared"
        ):
            history[i] = {
                **history[i],
                "content": _cleared_output(content),
            }
            degraded += 1
    routes.push_event(
        campaign["id"],
        "notice",
        {"text": "context over budget — older tool outputs cleared for this chain…"},
    )
    log.warning(
        "campaign %d history over usable context; degraded %d outputs in-memory",
        campaign["id"],
        degraded,
    )
    return degraded


def _backlog_line(campaign_id: int) -> str:
    """Compact status so the supervisor knows what the backlog, the hypothesis
    generators and the hunter workers are doing, without pulling full
    transcripts in."""
    with db.db() as conn:
        counts = {
            row["status"]: row["c"]
            for row in conn.execute(
                "SELECT status, count(*) AS c FROM hypotheses WHERE campaign_id = ? GROUP BY status",
                (campaign_id,),
            ).fetchall()
        }
        gens = conn.execute(
            "SELECT id, focus FROM generators WHERE campaign_id = ? AND status = 'running'",
            (campaign_id,),
        ).fetchall()
        campaign_row = conn.execute(
            "SELECT kernel_enabled, kernel_root_access FROM campaigns WHERE id = ?",
            (campaign_id,),
        ).fetchone()
        hunting = conn.execute(
            "SELECT count(*) AS c FROM workers WHERE campaign_id = ? AND status = 'running'",
            (campaign_id,),
        ).fetchone()["c"]
        spotlight = None
        if gens:
            pick = random.choice(gens)
            message = conn.execute(
                "SELECT content, parts FROM generator_messages WHERE generator_id = ? ORDER BY id DESC LIMIT 1",
                (pick["id"],),
            ).fetchone()
            if message is not None:
                spotlight = (
                    f"GEN #{pick['id']} ({pick['focus'] or 'general'}): "
                    f"{tools._agent_snippet(message)}"
                )

    running = len(gens)
    vmline = ""
    if campaign_row is not None and campaign_row["kernel_enabled"]:
        root = "root password set" if campaign_row["kernel_root_access"] else "root locked"
        vmline = (
            f"\nKERNEL VM: on, {root}. Hunters test in it with vm_exec/vm_status/vm_console; "
            "you own vm_start/vm_stop/vm_restart/vm_reset. Kernel exploits belong in there, "
            "never in the container."
        )

    if not counts and not running and not hunting:
        # The nudge matters most when nothing exists yet: without it the
        # supervisor invents its own angles instead of spawning workers.
        return (
            "HYPOTHESES: none yet — spawn a few generators with different "
            "focuses (spawn_hypothesis_generator), then dispatch the backlog to "
            "hunter workers with spawn_hunter." + vmline
        )

    parts = [
        f"{counts.get(name, 0)} {name}"
        for name in ("new", "working", "review", "done", "dead")
    ]
    line = "HYPOTHESES: " + ", ".join(parts)
    if running:
        line += f" | generators running: {running}"
    if hunting:
        line += f" | hunters running: {hunting}"
    line += (
        " (hypothesis_list for the backlog; dispatch 'new' with spawn_hunter; "
        "rule on 'review' with worker_status then hypothesis_update; "
        "generator_status for a generator)"
    )
    if vmline:
        line += vmline
    if spotlight:
        line += "\n" + spotlight
    return line


def _delegation_idle_seconds(campaign_id: int) -> float | None:
    """Seconds since this campaign last spawned a generator or a hunter.

    None means nothing was ever spawned. Read from the database rather than
    tracked in memory so resumed campaigns and rows that predate this check are
    covered too.
    """
    with db.db() as conn:
        row = conn.execute(
            """
            SELECT (julianday('now') - julianday(max(created_at))) * 86400.0 AS age
            FROM (
                SELECT created_at FROM generators WHERE campaign_id = ?
                UNION ALL
                SELECT created_at FROM workers WHERE campaign_id = ?
            )
            """,
            (campaign_id, campaign_id),
        ).fetchone()
    if row is None or row["age"] is None:
        return None
    return float(row["age"])


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def _loop(campaign_id: int) -> None:
    from . import routes, vm, vmctl, worker

    log.info("supervisor loop started for campaign %d", campaign_id)
    run = routes.RUNNING.get(campaign_id)
    if run is None:
        return

    # tasks do not survive a restart; close their rows out
    generator.reconcile(campaign_id)
    worker.reconcile(campaign_id)

    stop: asyncio.Event = run["stop"]
    backoff = config.SUPERVISOR_BACKOFF
    cancelled = False
    context_errors = 0
    compact_failures = 0
    # delegation backstop: five minutes from this point with nothing spawned
    loop_started_at = time.monotonic()
    last_nudge_at = 0.0
    # kernel VM: brought up on the first chain that can have one, and retried
    # until it works, so a control plane that was not running yet heals without
    # anyone restarting the campaign. Once it has succeeded an explicit vm_stop
    # is left alone.
    vm_ready = False
    last_vm_notice = 0.0
    # last opportunistic model-listing re-warm (see the loop body)
    warm_attempted_at = 0.0

    try:
        while True:
            # a stop with `restart` set is an interrupt: end the current chain,
            # then keep looping so the queued operator message lands next chain
            if stop.is_set():
                if run.pop("restart", False):
                    stop.clear()
                else:
                    break

            # let stop requests, pollers and other campaigns get a turn; a
            # chain that returns without awaiting would otherwise starve them
            await asyncio.sleep(0)

            with db.db() as conn:
                campaign = conn.execute(
                    "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
                ).fetchone()
                attachments = conn.execute(
                    "SELECT * FROM attachments WHERE campaign_id = ?", (campaign_id,)
                ).fetchall()
            if campaign is None:
                break

            mode = campaign["mode"] or "continuous"

            if not vm_ready and vm.enabled(campaign):
                try:
                    state = await vm.ensure(campaign_id, vm.as_root(campaign))
                    vm_ready = True
                    log.info("campaign %d: vm ready (%s)", campaign_id, vm.describe(state))
                except vmctl.Unavailable as exc:
                    # Rate-limited: a control plane that is down produces one
                    # notice every few minutes, not one per chain.
                    if time.monotonic() - last_vm_notice > 300:
                        last_vm_notice = time.monotonic()
                        log.warning("campaign %d: vm unavailable: %s", campaign_id, exc)
                        routes.push_event(
                            campaign_id,
                            "notice",
                            {
                                "text": (
                                    f"kernel VM not available: {exc} — start vmctl on the host, "
                                    "or use vm_start once it is up"
                                )
                            },
                        )

            # Provider model listings are cached in-process and go cold on
            # startup and on provider/key edits; without them every context
            # window silently resolves to the 128k fallback, which mis-sizes
            # both the trigger and the in-chain budget. Re-warm here so a
            # running campaign heals without a restart. The cooldown keeps an
            # unreachable provider from stalling every iteration.
            if (
                not agent._models
                and time.monotonic() - warm_attempted_at > MODELS_WARM_RETRY_SECONDS
            ):
                warm_attempted_at = time.monotonic()
                try:
                    await agent.models()
                except Exception:
                    log.warning("campaign %d: model listing warm-up failed", campaign_id)

            # compact against the model-derived trigger (zero config = auto):
            # last_prompt_tokens is provider-reported usage of what was really
            # sent, so pruning can never hide real growth from this check
            trigger = agent.compact_trigger(campaign["model"])
            if trigger is not None and campaign["last_prompt_tokens"] >= trigger:
                routes.push_event(
                    campaign_id,
                    "notice",
                    {"text": "context is large — compacting older turns into memory…"},
                )
                # Shrink the fold after consecutive failures so a failing
                # compaction cannot retry the same impossible input forever.
                keep_override = None
                if compact_failures:
                    try:
                        base_keep = max(1, config.COMPACT_KEEP_MESSAGES)
                    except Exception:
                        base_keep = 8
                    keep_override = _preserve_keep(base_keep, compact_failures)
                try:
                    folded = await compact_campaign(campaign_id, keep_override=keep_override)
                except Exception as exc:
                    if agent.summarizer_unreachable(exc):
                        # The summarizer socket failed: nothing about the fold
                        # was even attempted, so this must not burn the failure
                        # budget (nor freeze the checkpoint). A provider that
                        # comes back should heal the campaign with no restart,
                        # so back off and retry like any other provider outage.
                        log.warning(
                            "campaign %d: summarizer unreachable — retrying after backoff: %s",
                            campaign_id,
                            exc,
                        )
                        await _sleep(stop, backoff)
                        backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                        continue
                    log.exception("compaction failed for campaign %d", campaign_id)
                    folded = 0
                if folded > 0:
                    compact_failures = 0
                    log.info("compacted %d messages for campaign %d", folded, campaign_id)
                    routes.push_event(campaign_id, "compacted", {"messages": folded})
                elif folded == NOTHING_TO_FOLD:
                    # No-op: nothing foldable this pass (small batch or
                    # everything already folded). Not a failure — counting it
                    # used to stop healthy campaigns after five rounds.
                    log.info(
                        "campaign %d: nothing to fold this pass (gauge %d tokens)",
                        campaign_id,
                        campaign["last_prompt_tokens"],
                    )
                else:
                    # Progress invariant: an unsuccessful attempt must not leave
                    # a monotonically growing input for the next attempt.
                    compact_failures += 1
                    if compact_failures >= MAX_COMPACT_FAILURES:
                        # Finite failure policy: repeated compaction failures
                        # across iterations stop durably instead of retrying
                        # the same fold forever.
                        log.warning(
                            "campaign %d: compaction failed %d times in a row — stopping",
                            campaign_id,
                            compact_failures,
                        )
                        routes.push_event(
                            campaign_id,
                            "notice",
                            {"text": "compaction repeatedly failed — stopping; resume or steer to retry…"},
                        )
                        with db.db() as conn:
                            conn.execute(
                                """
                                UPDATE campaigns SET auto_running = 0, status = 'idle',
                                    updated_at = datetime('now')
                                WHERE id = ?
                                """,
                                (campaign_id,),
                            )
                        break

            history = _history(campaign_id)
            touched = any(message.get("role") == "assistant" for message in history)
            if touched:
                prompt = GOAL_CONTINUE_PROMPT if mode == "goal" else CONTINUE_PROMPT
            else:
                begin = GOAL_BEGIN_PROMPT if mode == "goal" else BEGIN_PROMPT
                prompt = begin.format(target=campaign["target"].strip())

            backlog = _backlog_line(campaign_id)
            if backlog:
                prompt += "\n\n" + backlog

            # Delegation backstop. The policy prompt asks for this, but a model
            # that starts hunting on its own will keep going, so the condition
            # is enforced here: five minutes into the run (and every five
            # minutes after) with nothing spawned, the turn itself is told to
            # delegate. Checked between chains — a chain already in flight
            # cannot be interrupted.
            since_start = time.monotonic() - loop_started_at
            idle = _delegation_idle_seconds(campaign_id)
            if (
                since_start >= DELEGATION_NUDGE_SECONDS
                and (idle is None or idle >= DELEGATION_NUDGE_SECONDS)
                and time.monotonic() - last_nudge_at >= DELEGATION_NUDGE_SECONDS
            ):
                last_nudge_at = time.monotonic()
                prompt += "\n\n" + DELEGATION_NUDGE
                routes.push_event(
                    campaign_id,
                    "notice",
                    {
                        "text": (
                            f"no generator or hunter spawned for "
                            f"{DELEGATION_NUDGE_SECONDS // 60} minutes — the supervisor "
                            "must delegate, not hunt alone"
                        )
                    },
                )
                log.warning(
                    "campaign %d: no delegation in %.0fs — telling the supervisor to delegate",
                    campaign_id,
                    since_start,
                )

            history.append({"role": "user", "content": prompt})

            # Full-request budget before the provider call: history + prompt
            # must fit usable context (the system prompt and schemas ride on
            # top inside agent.run, which enforces its own in-chain budget).
            # Degrade in-memory only — DB rows stay intact for the transcript.
            _degrade_over_budget(campaign, history)

            run["parts"] = []
            run["pending"] = None

            try:
                outcome = await routes._run(
                    campaign_id,
                    campaign,
                    attachments,
                    history,
                    "",
                    run,
                    (),
                    store_user=False,
                    keep=True,
                    tool_kind="supervisor",
                    # brief uploads go out whole on the first chain and live on
                    # in early context; later chains get filenames only
                    include_attachments=not touched,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("supervisor chain crashed for campaign %d", campaign_id)
                outcome = {"error": "chain crashed"}

            # goal mode ends when the agent declares the objective met
            if mode == "goal" and any(
                part.get("type") == "tool"
                and part.get("name") == "complete_engagement"
                and part.get("ok")
                for part in run.get("parts") or []
            ):
                routes.push_event(
                    campaign_id,
                    "notice",
                    {"text": "goal achieved — engagement complete"},
                )
                with db.db() as conn:
                    conn.execute(
                        """
                        UPDATE campaigns SET auto_running = 0, status = 'done',
                            updated_at = datetime('now')
                        WHERE id = ?
                        """,
                        (campaign_id,),
                    )
                break

            if stop.is_set() or outcome.get("stopped"):
                if run.pop("restart", False):
                    stop.clear()
                    continue
                break

            if outcome.get("truncated"):
                # A truncated chain is NOT successful completion: the provider
                # was never called with the full history. Force recovery
                # independently of last_prompt_tokens/compact_trigger, then
                # decide from a like-for-like comparison whether the next
                # chain can run.
                est_before = agent.estimate_messages_tokens(history)
                try:
                    folded = await compact_campaign(campaign_id)
                except Exception as exc:
                    if agent.summarizer_unreachable(exc):
                        # No fold was attempted, so "est_after == est_before" is
                        # not evidence that recovery is impossible — it is only
                        # evidence the summarizer was unreachable. Stopping here
                        # killed campaigns on a startup blip; retry with backoff
                        # so the campaign heals when the provider returns.
                        log.warning(
                            "campaign %d: summarizer unreachable during recovery — "
                            "retrying after backoff: %s",
                            campaign_id,
                            exc,
                        )
                        await _sleep(stop, backoff)
                        backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                        continue
                    log.exception(
                        "recovery compaction failed for campaign %d", campaign_id
                    )
                    folded = 0
                new_history = _history(campaign_id)
                # Compare like with like: the pre-recovery estimate covered
                # history+prompt, so the post-recovery one must too — and it
                # must include the same in-memory output degrade the next
                # chain would apply, or a recoverable input reads as no
                # progress.
                new_history.append({"role": "user", "content": prompt})
                _degrade_over_budget(campaign, new_history)
                est_after = agent.estimate_messages_tokens(new_history)
                # Did the truncated chain have any chance? If its own input
                # already filled the window (schemas and system prompt on
                # top), the provider was never called at all and retrying the
                # same size is a spin: only a strictly smaller next input may
                # continue. Otherwise the chain did real work, the next one
                # rolls over, and the loop keeps going.
                immediate = False
                try:
                    usable = agent.usable_context(campaign["model"])
                    schemas = agent.measured_schema_tokens("supervisor", campaign)
                    system = agent.build_prompt(
                        campaign,
                        attachments,
                        include_attachments=not touched,
                        tool_kind="supervisor",
                    )
                    immediate = (
                        est_before
                        + schemas
                        + len(system) // agent.EST_CHARS_PER_TOKEN
                        >= usable - agent.SAFETY_MARGIN_TOKENS
                    )
                except Exception:
                    immediate = False
                (run.setdefault("context_recovery", [])).append({
                    "est_before": est_before,
                    "est_after": est_after,
                    "folded": folded,
                    "immediate": immediate,
                })
                if est_after < est_before or not immediate:
                    log.info(
                        "campaign %d truncated-chain recovery: %d -> %d "
                        "(folded %s, immediate %s)",
                        campaign_id, est_before, est_after, folded, immediate,
                    )
                    continue
                # No progress: durable recoverable stop. The truncated text and
                # all prior tool-call/result pairs are already persisted by
                # _run; the newest user message is untouched. Clearing
                # auto_running stops the exit-time restart (same as goal-done
                # and manual stop); steer or resume restarts the loop.
                log.warning(
                    "campaign %d: truncated-chain recovery made no progress "
                    "(estimate %d -> %d, folded %s) — stopping",
                    campaign_id, est_before, est_after, folded,
                )
                routes.push_event(
                    campaign_id,
                    "notice",
                    {"text": "context budget exceeded and recovery made no progress — stopping; resume or steer to retry…"},
                )
                with db.db() as conn:
                    conn.execute(
                        """
                        UPDATE campaigns SET auto_running = 0, status = 'idle',
                            updated_at = datetime('now')
                        WHERE id = ?
                        """,
                        (campaign_id,),
                    )
                break

            if outcome.get("ok"):
                # Recovery-success condition: a chain returned from the
                # provider without context errors and without truncation.
                # Only this resets the counter — notices, backoff, or
                # unrelated errors must not.
                backoff = config.SUPERVISOR_BACKOFF
                context_errors = 0
                continue

            # Context-length errors escalate with a FINITE retry limit.
            # Notices and backoff are not progress: the counter resets only on
            # the recovery-success condition above.
            if outcome.get("error") == "context too large for model window":
                context_errors += 1
                est = agent.estimate_messages_tokens(history)
                rec = run.setdefault("context_recovery", [])
                prev = rec[-1] if rec else {}
                prev_est = prev.get("est_after", prev.get("est"))
                rec.append({
                    "event": "context_error",
                    "est": est,
                    "attempt": context_errors,
                })
                if context_errors >= MAX_CONTEXT_ERRORS or (
                    prev_est is not None and est >= prev_est and context_errors > 1
                ):
                    # Bounded stop: compaction and pruning did not reduce the
                    # input, so retrying the identical request is futile.
                    # Durable recoverable stop, same as truncated no-progress.
                    log.warning(
                        "campaign %d: context errors persist after %d attempts "
                        "(estimate %d) — stopping",
                        campaign_id, context_errors, est,
                    )
                    routes.push_event(
                        campaign_id,
                        "notice",
                        {"text": "context errors persist after recovery attempts — stopping; resume or steer to retry…"},
                    )
                    with db.db() as conn:
                        conn.execute(
                            """
                            UPDATE campaigns SET auto_running = 0, status = 'idle',
                                updated_at = datetime('now')
                            WHERE id = ?
                            """,
                            (campaign_id,),
                        )
                    break
                routes.push_event(
                    campaign_id,
                    "notice",
                    {"text": "context too large — compacting before retry…"},
                )
                await _sleep(stop, backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue

            # errored or empty: wait before the retry so a broken provider is
            # not hammered in a tight loop (counter retained: only a clean
            # chain is recovery success).
            await _sleep(stop, backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
    except asyncio.CancelledError:
        cancelled = True
        log.info("supervisor loop cancelled for campaign %d", campaign_id)
    finally:
        # stop the hypothesis generators and hunters before the loop disappears
        await generator.stop_campaign(campaign_id)
        await worker.stop_campaign(campaign_id)
        # close the live SSE before the run disappears so watchers fall back to
        # the saved transcript
        routes.close_run(campaign_id)
        routes.RUNNING.pop(campaign_id, None)
        SUPERVISORS.pop(campaign_id, None)

        restart = False
        with db.db() as conn:
            row = conn.execute(
                "SELECT auto_running FROM campaigns WHERE id = ?", (campaign_id,)
            ).fetchone()
            if row is not None and row["auto_running"] and not stop.is_set() and not cancelled:
                # a steer raced the exit: the operator wants the loop alive
                restart = True
            else:
                conn.execute(
                    """
                    UPDATE campaigns SET status = 'idle', updated_at = datetime('now')
                    WHERE id = ? AND status = 'running'
                    """,
                    (campaign_id,),
                )

        if restart:
            log.info("supervisor restarting for campaign %d after a steer raced the stop", campaign_id)
            start(campaign_id)
        log.info("supervisor loop exited for campaign %d", campaign_id)

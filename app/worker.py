"""Hunter workers.

The supervisor dispatches a hunter with a small batch of hypotheses; the
worker exploits them, writes a runnable PoC into its own directory and files a
verdict with `hunt_report`. The supervisor then reviews and accepts or denies
it. Workers drive the normal agent turn (`agent.run`) in their own task with
their own work directory and their own browser, so the supervisor's run,
stream and context are untouched.
"""

import asyncio
import json
import logging
from pathlib import Path

from . import agent, config, db, mcp, supervisor

log = logging.getLogger("l4rpness")

# campaign id -> worker id -> runtime state (task, stop event)
WORKERS: dict[int, dict[int, dict]] = {}

PROMPT_FILE = config.ROOT / "prompts" / "hunter_worker.md"

# How many times a chain may compact itself and continue after the context
# budget stops it. Workers have no outer loop to roll over for them.
MAX_CHAIN_ROLLS = 5

# A hypothesis that keeps coming back from workers that never reported is
# closed instead of being retried forever.
MAX_ATTEMPTS = 3


def _work(campaign_id: int, worker_id: int) -> Path:
    """The worker's own directory, inside the campaign's work root."""
    return supervisor._work_root(campaign_id) / "hunters" / str(worker_id)


def running_count(campaign_id: int) -> int:
    return len(WORKERS.get(campaign_id, {}))


def is_running(campaign_id: int, worker_id: int) -> bool:
    runtime = WORKERS.get(campaign_id, {}).get(worker_id)
    return runtime is not None and not runtime["task"].done()


def _finish(worker_id: int, status: str) -> None:
    with db.db() as conn:
        conn.execute(
            "UPDATE workers SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, worker_id),
        )


def _push(campaign_id: int, worker_id: int, status: str, message: dict | None = None) -> None:
    from . import routes

    payload = {"worker_id": worker_id, "status": status}
    if message is not None:
        payload["message"] = message
    routes.push_event(campaign_id, "worker", payload)


def _requeue(conn, worker_id: int, reason: str) -> int:
    """Send a worker's unreported hypotheses back to the queue.

    Without this a crashed or cancelled worker leaves them 'working' forever
    and the backlog silently drains. After MAX_ATTEMPTS they are closed as
    dead so a broken hypothesis cannot spin the queue.
    """
    rows = conn.execute(
        "SELECT id, attempts FROM hypotheses WHERE worker_id = ? AND status = 'working'",
        (worker_id,),
    ).fetchall()
    for row in rows:
        attempts = int(row["attempts"] or 0) + 1
        if attempts >= MAX_ATTEMPTS:
            conn.execute(
                "UPDATE hypotheses SET status = 'dead', attempts = ?, result = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (attempts, f"closed after {attempts} worker attempts that never reported ({reason})"[:1000], row["id"]),
            )
        else:
            conn.execute(
                "UPDATE hypotheses SET status = 'new', worker_id = NULL, attempts = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (attempts, row["id"]),
            )
    return len(rows)


def reconcile(campaign_id: int) -> None:
    """A process restart kills the tasks; close their rows out and requeue."""
    with db.db() as conn:
        rows = conn.execute(
            "SELECT id FROM workers WHERE campaign_id = ? AND status = 'running'",
            (campaign_id,),
        ).fetchall()
        for row in rows:
            _requeue(conn, row["id"], "interrupted by a restart")
            conn.execute(
                "UPDATE workers SET status = 'interrupted', updated_at = datetime('now') "
                "WHERE id = ?",
                (row["id"],),
            )


def _dispatch_instruction(campaign_id: int, worker_id: int) -> str:
    """The user turn that hands the worker its hypotheses.

    The supervisor chooses the work; the worker never picks from the backlog.
    """
    with db.db() as conn:
        rows = conn.execute(
            "SELECT id, title, angle, vuln_class, rationale FROM hypotheses "
            "WHERE worker_id = ? AND status = 'working' ORDER BY id",
            (worker_id,),
        ).fetchall()

    lines = [
        f"Test the {len(rows)} hypotheses below. They are already recorded in the "
        "campaign backlog — do not re-file them.",
        "",
        f"You work inside hunters/{worker_id}/ under the campaign work directory. Put every "
        f"PoC, log and note there, and refer to them to the supervisor by their "
        f"campaign-relative path (for example 'hunters/{worker_id}/poc/run.sh').",
        "",
    ]
    for row in rows:
        lines.append(f"#{row['id']} [{row['vuln_class']}] {row['title']}")
        lines.append(f"  angle: {row['angle']}")
        if row["rationale"]:
            lines.append(f"  why: {row['rationale'][:800]}")
        lines.append("")
    lines.append(
        "Pursue each one with real tools until you can prove it or rule it out, then file "
        "the result with hunt_report. When every hypothesis has a result, call hunter_done."
    )
    return "\n".join(lines)


def _insert_message(worker_id: int, role: str, content: str) -> int:
    with db.db() as conn:
        cur = conn.execute(
            "INSERT INTO worker_messages (worker_id, role, content, parts) VALUES (?, ?, ?, '[]')",
            (worker_id, role, content),
        )
        conn.execute(
            "UPDATE workers SET updated_at = datetime('now') WHERE id = ?", (worker_id,)
        )
        return cur.lastrowid


def _delete_message(message_id: int) -> None:
    """Drop a turn row that never produced anything (dead chains must not
    leave empty bubbles in the transcript)."""
    with db.db() as conn:
        conn.execute("DELETE FROM worker_messages WHERE id = ?", (message_id,))


def _store_message(
    worker_id: int, message_id: int, content: list[str], thinking: list[str], calls: list, parts: list
) -> dict | None:
    with db.db() as conn:
        conn.execute(
            """
            UPDATE worker_messages SET content = ?, reasoning = ?, tool_calls = ?, parts = ?
            WHERE id = ?
            """,
            (
                "".join(content).strip(),
                "".join(thinking).strip(),
                json.dumps(calls),
                json.dumps(parts),
                message_id,
            ),
        )
        conn.execute(
            "UPDATE workers SET updated_at = datetime('now') WHERE id = ?", (worker_id,)
        )
        row = conn.execute(
            "SELECT * FROM worker_messages WHERE id = ?", (message_id,)
        ).fetchone()
    if row is None:
        return None
    stored = {key: row[key] for key in row.keys()}
    stored["parts"] = parts
    return stored


def spawn(campaign_id: int, hypothesis_ids: list[int], worker_id: int = 0, note: str = "") -> int:
    """Start a hunter, or reactivate one that keeps its history.

    dispatchable status is 'new', or 'review' when it belongs to the worker
    being reactivated. Returns the worker id, or 0 when the caller must not
    dispatch (worker limit reached, worker already running, or a hypothesis is
    no longer dispatchable). The assignment and the status stamp happen in one
    transaction so a hypothesis can never end up on two workers.
    """
    reactivate = bool(worker_id)
    if reactivate:
        with db.db() as conn:
            row = conn.execute(
                "SELECT id FROM workers WHERE id = ? AND campaign_id = ?",
                (worker_id, campaign_id),
            ).fetchone()
        if row is None or is_running(campaign_id, worker_id):
            return 0
    elif running_count(campaign_id) >= config.MAX_WORKERS:
        return 0

    with db.db() as conn:
        placeholders = ",".join("?" for _ in hypothesis_ids)
        rows = conn.execute(
            f"SELECT id, title, status, worker_id FROM hypotheses "
            f"WHERE campaign_id = ? AND id IN ({placeholders})",
            [campaign_id, *hypothesis_ids],
        ).fetchall()
        dispatchable = {row["id"] for row in rows if row["status"] == "new"}
        if worker_id:
            dispatchable |= {
                row["id"]
                for row in rows
                if row["status"] == "review" and row["worker_id"] == worker_id
            }
        if dispatchable != set(hypothesis_ids):
            return 0

        if worker_id:
            conn.execute(
                "UPDATE workers SET status = 'running', note = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (note[:500], worker_id),
            )
        else:
            titles = [row["title"] for row in rows]
            title = (titles[0] if titles else f"{len(titles)} hypotheses")[:200]
            cur = conn.execute(
                "INSERT INTO workers (campaign_id, title, note, status) VALUES (?, ?, ?, 'running')",
                (campaign_id, title, note[:500]),
            )
            worker_id = cur.lastrowid

        for hypothesis_id in hypothesis_ids:
            conn.execute(
                "UPDATE hypotheses SET status = 'working', worker_id = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (worker_id, hypothesis_id),
            )

    instruction = _dispatch_instruction(campaign_id, worker_id)
    if reactivate and note:
        instruction = f"Deep dive request from the supervisor: {note}\n\n{instruction}"
    _insert_message(worker_id, "user", instruction)

    runtime = {"task": None, "stop": asyncio.Event()}
    WORKERS.setdefault(campaign_id, {})[worker_id] = runtime
    runtime["task"] = asyncio.create_task(_run(campaign_id, worker_id))
    log.info(
        "hunter #%d started for campaign %d on %d hypothesis(es)",
        worker_id,
        campaign_id,
        len(hypothesis_ids),
    )
    return worker_id


def stop_worker(campaign_id: int, worker_id: int) -> bool:
    runtime = WORKERS.get(campaign_id, {}).get(worker_id)
    if runtime is None:
        return False
    runtime["stop"].set()
    return True


async def stop_campaign(campaign_id: int) -> None:
    runtimes = WORKERS.pop(campaign_id, {})
    for runtime in runtimes.values():
        runtime["stop"].set()
        task = runtime["task"]
        if task is not None and not task.done():
            task.cancel()
    tasks = [r["task"] for r in runtimes.values() if r["task"] is not None]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _history(campaign_id: int, worker_id: int) -> list[dict]:
    """Rebuild this worker's conversation from its own rows."""
    with db.db() as conn:
        worker = conn.execute(
            "SELECT memory_summary, compacted_until FROM workers WHERE id = ?", (worker_id,)
        ).fetchone()
        compacted_until = worker["compacted_until"] if worker else 0
        rows = conn.execute(
            "SELECT * FROM worker_messages WHERE worker_id = ? AND id > ? ORDER BY id",
            (worker_id, compacted_until),
        ).fetchall()

    history: list[dict] = []
    if worker is not None and worker["memory_summary"]:
        history.append({
            "role": "user",
            "content": (
                "[WORKER MEMORY] Earlier turns of this hunt were compacted into this "
                "summary. Treat it as established context.\n\n" + worker["memory_summary"]
            ),
        })
    return history + supervisor._replay(rows, _work(campaign_id, worker_id))


async def compact_worker(campaign_id: int, worker_id: int, keep_override: int | None = None) -> int:
    """Fold this worker's older turns into its own rolling summary.

    Same contract as supervisor.compact_campaign: >0 folded, 0 failed (do not
    advance the checkpoint), NOTHING_TO_FOLD nothing to do.
    """
    with db.db() as conn:
        campaign = conn.execute(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        worker = conn.execute("SELECT * FROM workers WHERE id = ?", (worker_id,)).fetchone()
        rows = conn.execute(
            "SELECT * FROM worker_messages WHERE worker_id = ? ORDER BY id", (worker_id,)
        ).fetchall()
    if campaign is None or worker is None:
        return supervisor.NOTHING_TO_FOLD

    usable = supervisor._usable_or_fallback(campaign["model"])
    eff_keep, transcript_cap, brief_cap, summary_cap = supervisor._fold_caps(usable, keep_override)
    picked = supervisor._size_aware_fold(rows, worker["compacted_until"], eff_keep, usable)
    if picked is None:
        return supervisor.NOTHING_TO_FOLD
    boundary, fold = picked

    summary = await supervisor._summarize_fold(
        campaign,
        fold,
        _work(campaign_id, worker_id),
        transcript_cap,
        brief_cap,
        summary_cap,
        prior_summary=worker["memory_summary"] or "",
        label="Hunt",
    )
    if summary is None:
        return 0

    with db.db() as conn:
        conn.execute(
            "UPDATE workers SET memory_summary = ?, compacted_until = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (summary, boundary, worker_id),
        )
    return len(fold)


def _extra_system(worker_id: int) -> str:
    try:
        text = PROMPT_FILE.read_text()
    except OSError:
        text = ""
    return text.replace("{{worker_id}}", str(worker_id))


async def _run(campaign_id: int, worker_id: int) -> None:
    status = "done"
    # The worker's browser scope. Its private session is opened lazily by
    # mcp.call on the first browser tool and closed in this same task by the
    # finally below (an MCP session nests a cancel scope that cannot cross
    # tasks), so a worker that never browses costs nothing.
    scope = f"worker-{worker_id}"
    work = _work(campaign_id, worker_id)
    log.info("hunter #%d running for campaign %d", worker_id, campaign_id)
    try:
        work.mkdir(parents=True, exist_ok=True)

        with db.db() as conn:
            campaign = conn.execute(
                "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
            ).fetchone()
        if campaign is None:
            status = "failed"
            return

        runtime = WORKERS.get(campaign_id, {}).get(worker_id)
        stop = runtime["stop"] if runtime else asyncio.Event()
        extra_system = _extra_system(worker_id)
        rolls = 0

        while True:
            history = _history(campaign_id, worker_id)
            message_id = _insert_message(worker_id, "assistant", "")
            content: list[str] = []
            thinking: list[str] = []
            calls: list[dict] = []
            parts: list = []
            truncated = ""

            try:
                stream = agent.run(
                    campaign,
                    [],
                    history,
                    campaign_id,
                    stop=stop,
                    tool_kind="hunter",
                    work=work,
                    scope=scope,
                    extra_system=extra_system,
                    worker_id=worker_id,
                )
                async for kind, data in stream:
                    if kind == "thinking":
                        thinking.append(data)
                        parts.append({"type": "thinking", "text": data})
                    elif kind == "content":
                        content.append(data)
                        parts.append({"type": "content", "text": data})
                    elif kind == "tool_call":
                        calls.append({"id": data["id"], "name": data["name"], "args": data["args"]})
                        parts.append({
                            "type": "tool",
                            "id": data["id"],
                            "name": data["name"],
                            "args": data["args"],
                            "output": None,
                            "ok": None,
                            "ms": None,
                        })
                    elif kind == "tool_result":
                        for call in calls:
                            if call["id"] == data["id"]:
                                call.update(ok=data["ok"], ms=data["ms"])
                        for part in parts:
                            if part.get("type") == "tool" and part.get("id") == data["id"]:
                                part.update(output=data["output"], ok=data["ok"], ms=data["ms"])
                        _store_message(worker_id, message_id, content, thinking, calls, parts)
                        _push(campaign_id, worker_id, "running")
                    elif kind == "usage":
                        tokens = int(data.get("prompt_tokens") or 0)
                        with db.db() as conn:
                            conn.execute(
                                "UPDATE workers SET last_prompt_tokens = ? WHERE id = ?",
                                (tokens, worker_id),
                            )
                    elif kind == "truncated":
                        truncated = str(data)
            except agent.ContextTooLargeError as exc:
                # The provider refused before any usage chunk: treat it like a
                # budget stop so the loop compacts and continues instead of
                # dying mid-hunt.
                log.warning("hunter #%d exceeded the model window: %s", worker_id, exc)
                truncated = str(exc)

            if not content and not calls and not parts:
                _delete_message(message_id)
            else:
                stored = _store_message(worker_id, message_id, content, thinking, calls, parts)
                if stored is not None:
                    _push(campaign_id, worker_id, "running", stored)


            if stop.is_set():
                status = "stopped"
                break
            if truncated and rolls < MAX_CHAIN_ROLLS:
                folded = await compact_worker(campaign_id, worker_id)
                if folded <= 0:
                    log.warning(
                        "hunter #%d could not compact (%s) — ending the run",
                        worker_id,
                        folded,
                    )
                    status = "blocked"
                    break
                rolls += 1
                log.info(
                    "hunter #%d compacted %d turns, continuing the hunt",
                    worker_id,
                    folded,
                )
                continue
            break
    except asyncio.CancelledError:
        status = "cancelled"
        log.info("hunter #%d cancelled for campaign %d", worker_id, campaign_id)
    except Exception:
        status = "failed"
        log.exception("hunter #%d failed for campaign %d", worker_id, campaign_id)
    finally:
        WORKERS.get(campaign_id, {}).pop(worker_id, None)
        try:
            await mcp.close_scope(scope)
        except Exception:
            log.warning("hunter #%d: closing its browser session failed", worker_id)
        if status == "done":
            # Every dispatched hypothesis must have a result, otherwise the
            # run stopped short and the work goes back to the queue.
            with db.db() as conn:
                pending = conn.execute(
                    "SELECT count(*) AS c FROM hypotheses "
                    "WHERE worker_id = ? AND status = 'working'",
                    (worker_id,),
                ).fetchone()["c"]
            if pending:
                status = "blocked"
        with db.db() as conn:
            if status != "done":
                _requeue(conn, worker_id, status)
        _finish(worker_id, status)
        _push(campaign_id, worker_id, status)
        log.info("hunter #%d finished for campaign %d: %s", worker_id, campaign_id, status)

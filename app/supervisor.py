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

from . import agent, config, db, uploads

log = logging.getLogger("l4rpness")

SUPERVISORS: dict[int, asyncio.Task] = {}

MAX_BACKOFF_SECONDS = 120

BEGIN_PROMPT = (
    "Begin the engagement against {target}. Work continuously until the "
    "operator stops you. Never declare the engagement complete."
)

CONTINUE_PROMPT = (
    "Your turn ended, but the engagement is not finished. This campaign is "
    "never \"done\": every software has vulnerabilities that have not been "
    "found yet, and a clean surface only means the coverage was shallow. "
    "Review your notes and your previous results, form the next hypothesis, "
    "and keep testing with real tools now."
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


def _history(campaign_id: int) -> list[dict]:
    """Rebuild the model conversation from the database.

    Assistant turns keep their tool calls and the tool outputs, not just the
    final text, so restarting a chain does not wipe how a result was produced.
    """
    with db.db() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE campaign_id = ? ORDER BY id", (campaign_id,)
        ).fetchall()
        files = conn.execute(
            "SELECT * FROM message_attachments WHERE campaign_id = ? ORDER BY id", (campaign_id,)
        ).fetchall()

    by_message: dict[int, list] = {}
    for file in files:
        by_message.setdefault(file["message_id"], []).append(file)

    history: list[dict] = []
    for row in rows:
        content = row["content"] or ""
        if row["role"] == "user":
            attached = by_message.get(row["id"])
            if attached:
                context = uploads.prompt_text(attached)
                if context:
                    content = f"{content}\n\n{context}".strip()
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
            history.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": str(output if output is not None else "(no output recorded)")[:20000],
            })

    return history


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def _loop(campaign_id: int) -> None:
    from . import routes

    log.info("supervisor loop started for campaign %d", campaign_id)
    run = routes.RUNNING.get(campaign_id)
    if run is None:
        return

    stop: asyncio.Event = run["stop"]
    backoff = config.SUPERVISOR_BACKOFF
    cancelled = False

    try:
        while not stop.is_set():
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

            history = _history(campaign_id)
            touched = any(message.get("role") == "assistant" for message in history)
            if touched:
                history.append({"role": "user", "content": CONTINUE_PROMPT})
            else:
                history.append({
                    "role": "user",
                    "content": BEGIN_PROMPT.format(target=campaign["target"].strip()),
                })

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
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("supervisor chain crashed for campaign %d", campaign_id)
                outcome = {"error": "chain crashed"}

            if stop.is_set() or outcome.get("stopped"):
                break

            if outcome.get("ok"):
                # no pause: a chain that ended is immediately followed by the
                # next chain with the same context
                backoff = config.SUPERVISOR_BACKOFF
                continue

            # errored or empty: wait before the retry so a broken provider is
            # not hammered in a tight loop
            await _sleep(stop, backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
    except asyncio.CancelledError:
        cancelled = True
        log.info("supervisor loop cancelled for campaign %d", campaign_id)
    finally:
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

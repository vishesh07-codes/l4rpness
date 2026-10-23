"""Background hypothesis workers (generators).

The supervisor spawns these with `spawn_hypothesis_generator`. Each one studies
the target for its focus area and files concrete, testable hypotheses through
the `hypothesis_generate` tool. Every step is persisted and pushed to the
campaign stream so the supervisor and the dashboard can watch them work.
"""

import asyncio
import json
import logging
from typing import Any, cast

from . import agent, config, db, tools

log = logging.getLogger("l4rpness")

GENERATORS: dict[int, dict[int, asyncio.Task]] = {}

PROMPT_FILE = config.ROOT / "prompts" / "hypothesis_generator.md"

OUTPUT_CHARS = 4000


def running_count(campaign_id: int) -> int:
    return len(GENERATORS.get(campaign_id, {}))


def reconcile(campaign_id: int) -> None:
    """A process restart kills the tasks; close their rows out."""
    with db.db() as conn:
        conn.execute(
            """
            UPDATE generators SET status = 'interrupted', updated_at = datetime('now')
            WHERE campaign_id = ? AND status = 'running'
            """,
            (campaign_id,),
        )


def spawn(campaign_id: int, focus: str = "") -> int:
    """Start one generator. Returns its id, or 0 when the limit is reached."""
    if running_count(campaign_id) >= config.MAX_GENERATORS:
        return 0

    with db.db() as conn:
        cur = conn.execute(
            "INSERT INTO generators (campaign_id, focus, status) VALUES (?, ?, 'running')",
            (campaign_id, focus[:200]),
        )
        generator_id = cur.lastrowid
    if generator_id is None:
        return 0

    task = asyncio.create_task(_run(campaign_id, generator_id, focus))
    GENERATORS.setdefault(campaign_id, {})[generator_id] = task
    return generator_id


async def stop_campaign(campaign_id: int) -> None:
    tasks = GENERATORS.pop(campaign_id, {})
    for task in tasks.values():
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks.values(), return_exceptions=True)


def _finish(generator_id: int, status: str) -> None:
    with db.db() as conn:
        conn.execute(
            "UPDATE generators SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, generator_id),
        )


def _push(campaign_id: int, payload: dict) -> None:
    from . import routes

    routes.push_event(campaign_id, "generator", payload)


def _store_message(generator_id: int, content: str, parts: list) -> dict:
    with db.db() as conn:
        cur = conn.execute(
            "INSERT INTO generator_messages (generator_id, content, parts) VALUES (?, ?, ?)",
            (generator_id, content[:8000], json.dumps(parts)),
        )
        message_id = cur.lastrowid
        conn.execute(
            "UPDATE generators SET updated_at = datetime('now') WHERE id = ?", (generator_id,)
        )
        row = conn.execute(
            "SELECT * FROM generator_messages WHERE id = ?", (message_id,)
        ).fetchone()
    stored = {key: row[key] for key in row.keys()}
    stored["parts"] = parts
    return stored


def _system_prompt(campaign, attachments, focus: str) -> str:
    base = agent.build_prompt(campaign, attachments, include_run_mode=False, tool_kind="generator")
    try:
        extra = PROMPT_FILE.read_text()
    except OSError:
        extra = ""
    return base + "\n\n---\n\n" + extra.replace("{{focus}}", focus or "general")


def _step_parts(message, calls: list) -> list:
    parts: list = []
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning:
        parts.append({"type": "thinking", "text": str(reasoning)})
    if message.content:
        parts.append({"type": "content", "text": str(message.content)})
    for call in calls:
        function = getattr(call, "function", None)
        try:
            args = json.loads(getattr(function, "arguments", "") or "{}")
        except json.JSONDecodeError:
            args = {}
        parts.append({
            "type": "tool",
            "id": call.id,
            "name": getattr(function, "name", "") if function else "",
            "args": args,
            "output": None,
            "ok": None,
            "ms": None,
        })
    return parts


async def _run(campaign_id: int, generator_id: int, focus: str) -> None:
    filed = 0
    status = "done"
    log.info("generator #%d started for campaign %d (focus: %s)", generator_id, campaign_id, focus or "general")
    try:
        with db.db() as conn:
            campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
            attachments = conn.execute(
                "SELECT * FROM attachments WHERE campaign_id = ?", (campaign_id,)
            ).fetchall()
        if campaign is None:
            status = "failed"
            return

        provider, bare_model = agent.split_model(campaign["model"])
        client = agent._client(provider)
        conf = config.PROVIDERS.get(provider, {})
        extra: dict = {}
        if conf.get("thinking"):
            extra["reasoning_effort"] = campaign["effort"] or config.DEFAULT_EFFORT
            extra["extra_body"] = {"thinking": {"type": "enabled"}}

        messages: list[dict] = [
            {"role": "system", "content": _system_prompt(campaign, attachments, focus)},
            {
                "role": "user",
                "content": (
                    "Generate the hypotheses now. Study the target with your tools first; "
                    "file each hypothesis with hypothesis_generate, then call generator_done."
                ),
            },
        ]
        idle = 0

        while True:
            response = await client.chat.completions.create(
                model=bare_model,
                messages=cast(Any, messages),
                tools=cast(Any, tools.schemas_for("generator")),
                temperature=config.TEMPERATURE,
                **extra,
            )
            if not response or not getattr(response, "choices", None):
                log.warning("generator #%d got an empty model response", generator_id)
                status = "failed"
                break

            message = response.choices[0].message
            calls = message.tool_calls or []
            assistant: dict = {"role": "assistant", "content": message.content or ""}
            if calls:
                assistant["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": getattr(getattr(call, "function", None), "name", ""),
                            "arguments": getattr(getattr(call, "function", None), "arguments", ""),
                        },
                    }
                    for call in calls
                ]
            messages.append(assistant)

            if not calls:
                idle += 1
                if idle >= 2:
                    log.warning("generator #%d stopped replying with tool calls", generator_id)
                    status = "blocked"
                    break
                messages.append({
                    "role": "user",
                    "content": "Keep going: file at least 4 hypotheses with hypothesis_generate, then call generator_done.",
                })
                continue
            idle = 0

            parts = _step_parts(message, calls)
            finished = False
            for index, call in enumerate(calls):
                function = getattr(call, "function", None)
                name = getattr(function, "name", "") if function else ""
                try:
                    args = json.loads(getattr(function, "arguments", "") or "{}")
                except json.JSONDecodeError:
                    args = {}

                if name == "generator_done":
                    finished = True
                    output, ok = "generator finished", True
                    status = str(args.get("status") or "done")
                elif name == "hypothesis_generate":
                    output, ok = await tools.hypothesis_generate(
                        args, campaign_id, generator_id=generator_id
                    )
                    if ok and "filed" in output:
                        filed += 1
                else:
                    output, ok, _ = await tools.execute(
                        name, args, campaign_id, stop=None, allow=tools.allowed_for("generator")
                    )

                part = parts[index]
                part["output"] = output[:OUTPUT_CHARS]
                part["ok"] = ok
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output[:OUTPUT_CHARS]})

            stored = _store_message(generator_id, message.content or "", parts)
            _push(campaign_id, {"generator_id": generator_id, "focus": focus, "status": "running", "message": stored})

            if finished:
                break
    except asyncio.CancelledError:
        status = "cancelled"
        log.info("generator #%d cancelled for campaign %d", generator_id, campaign_id)
    except Exception:
        status = "failed"
        log.exception("generator #%d failed for campaign %d", generator_id, campaign_id)
    finally:
        GENERATORS.get(campaign_id, {}).pop(generator_id, None)
        _finish(generator_id, status)
        _push(campaign_id, {"generator_id": generator_id, "focus": focus, "status": status})

    log.info("generator #%d finished for campaign %d: %d hypotheses filed", generator_id, campaign_id, filed)

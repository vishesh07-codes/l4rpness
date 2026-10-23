import json
import logging
from collections.abc import AsyncIterator
from string import Template
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from . import config, tools, uploads

log = logging.getLogger("l4rpness")

SYSTEM = Template((config.ROOT / "prompts" / "campaign_agent.md").read_text())
_models: list[str] = []


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=config.API_KEY, base_url=config.BASE_URL, timeout=config.REQUEST_TIMEOUT)


async def models() -> list[str]:
    global _models
    if _models:
        return _models

    try:
        page = await _client().models.list()
    except Exception:
        log.exception("model list failed")
        return []

    _models = sorted(model.id for model in page.data)
    return _models


def build_prompt(campaign, attachment_rows) -> str:
    sections = []
    for label, value in [
        ("Target", campaign["target"]),
        ("Info", campaign["info"]),
        ("Instructions", campaign["instructions"]),
        ("Description", campaign["description"]),
        ("Scope", campaign["scope"]),
    ]:
        if value.strip():
            sections.append(f"{label}:\n{value.strip()}")

    return SYSTEM.substitute(
        campaign="\n\n".join(sections),
        attachments=uploads.prompt_text(attachment_rows),
    )


async def _step(client, campaign, messages, calls: list) -> AsyncIterator[tuple[str, Any]]:
    response = await client.chat.completions.create(
        model=campaign["model"],
        messages=cast(Any, messages),
        tools=cast(Any, tools.SCHEMAS),
        stream=True,
        temperature=config.TEMPERATURE,
        reasoning_effort=cast(Any, campaign["effort"] or config.DEFAULT_EFFORT),
        extra_body={"thinking": {"type": "enabled"}},
    )

    assembled: dict[int, dict] = {}
    announced: set[int] = set()
    async for chunk in response:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            yield "thinking", reasoning
        if delta.content:
            yield "content", delta.content

        for call in delta.tool_calls or []:
            entry = assembled.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
            if call.id:
                entry["id"] = call.id
            if call.function and call.function.name:
                entry["name"] = call.function.name
                if call.index not in announced:
                    announced.add(call.index)
                    yield "tool_start", {"id": entry["id"], "name": entry["name"]}
            if call.function and call.function.arguments:
                entry["arguments"] += call.function.arguments

    for index in sorted(assembled):
        entry = assembled[index]
        try:
            args = json.loads(entry["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {}
        calls.append({**entry, "args": args})


async def run(campaign, attachment_rows, history, campaign_id, stop=None) -> AsyncIterator[tuple[str, Any]]:
    client = _client()
    messages: list[dict] = [{"role": "system", "content": build_prompt(campaign, attachment_rows)}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]

    calls: list[dict] = []
    steps = 0

    while True:
        if stop is not None and stop.is_set():
            yield "stopped", None
            return

        calls.clear()
        thinking: list[str] = []
        content: list[str] = []

        async for kind, data in _step(client, campaign, messages, calls):
            if stop is not None and stop.is_set():
                yield "stopped", None
                return
            if kind == "thinking":
                thinking.append(data)
            elif kind == "content":
                content.append(data)
            yield kind, data

        if stop is not None and stop.is_set():
            yield "stopped", None
            return

        if not calls:
            return

        if content:
            yield "content", "\n\n"

        messages.append({
            "role": "assistant",
            "content": "".join(content) or None,
            "reasoning_content": "".join(thinking),
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": call["arguments"]},
                }
                for call in calls
            ],
        })

        for call in calls:
            yield "tool_call", {"id": call["id"], "name": call["name"], "args": call["args"]}
            output, ok, ms = await tools.execute(call["name"], call["args"], campaign_id, stop)
            yield "tool_result", {"id": call["id"], "name": call["name"], "output": output, "ok": ok, "ms": ms}
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})

        steps += 1
        if config.MAX_TOOL_STEPS > 0 and steps >= config.MAX_TOOL_STEPS:
            yield "notice", f"[stopped after {steps} tool steps]"
            return

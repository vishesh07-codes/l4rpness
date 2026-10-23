import json
import logging
import os
from collections.abc import AsyncIterator
from string import Template
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from . import config, mcp, skills, tools, uploads

log = logging.getLogger("l4rpness")

SYSTEM = Template((config.ROOT / "prompts" / "campaign_agent.md").read_text())
_models: list[str] = []


def provider_key(provider: str) -> str:
    conf = config.PROVIDERS.get(provider, {})
    return os.getenv(conf.get("api_key_env", ""), "").strip()


def _client(provider: str | None = None) -> AsyncOpenAI:
    name = provider or config.PROVIDER
    conf = config.PROVIDERS.get(name, {})
    return AsyncOpenAI(
        api_key=provider_key(name) or config.API_KEY,
        base_url=conf.get("base_url", config.BASE_URL),
        timeout=config.REQUEST_TIMEOUT,
    )


def split_model(model: str) -> tuple[str, str]:
    name, sep, bare = model.partition("/")
    if sep and name in config.PROVIDERS:
        return name, bare
    if model.startswith("deepseek-"):
        return "deepseek", model
    return config.PROVIDER, model


async def models() -> list[str]:
    global _models
    if _models:
        return _models

    merged = []
    for name in config.PROVIDERS:
        if not provider_key(name):
            continue
        try:
            page = await _client(name).models.list()
        except Exception:
            log.warning("model list failed for provider %s", name)
            continue
        merged += [f"{name}/{model.id}" for model in page.data]

    _models = sorted(set(merged))
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

    prompt = SYSTEM.substitute(
        campaign="\n\n".join(sections),
        attachments=uploads.prompt_text(attachment_rows),
    )

    parts = []
    for file in config.INSTRUCTION_FILES:
        try:
            content = file.read_text(errors="replace").strip()
        except OSError:
            log.warning("instruction file missing: %s", file)
            continue
        parts.append(f"Instructions from: {file}\n\n{content}")

    parts.append(prompt)

    block = skills.index()
    if block:
        parts.append(block)

    return "\n\n---\n\n".join(parts)


def _parse_args(raw: str) -> tuple[dict, str]:
    try:
        return json.loads(raw), ""
    except json.JSONDecodeError as first:
        try:
            return json.loads(raw, strict=False), ""
        except json.JSONDecodeError:
            return {}, str(first)


async def _step(client, campaign, messages, calls: list) -> AsyncIterator[tuple[str, Any]]:
    provider, model = split_model(campaign["model"])
    conf = config.PROVIDERS.get(provider, {})

    extra: dict = {}
    if conf.get("thinking"):
        extra["reasoning_effort"] = cast(Any, campaign["effort"] or config.DEFAULT_EFFORT)
        extra["extra_body"] = {"thinking": {"type": "enabled"}}

    response = await client.chat.completions.create(
        model=model,
        messages=cast(Any, messages),
        tools=cast(Any, tools.SCHEMAS + mcp.schemas()),
        stream=True,
        temperature=config.TEMPERATURE,
        **extra,
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
        raw = entry["arguments"] or "{}"
        args, parse_error = _parse_args(raw)
        calls.append({**entry, "args": args, "raw": raw, "parse_error": parse_error})


async def run(campaign, attachment_rows, history, campaign_id, stop=None, on_tool_output=None) -> AsyncIterator[tuple[str, Any]]:
    provider, _ = split_model(campaign["model"])
    client = _client(provider)
    messages: list[dict] = [{"role": "system", "content": build_prompt(campaign, attachment_rows)}]
    for message in history:
        # chat history is role/content; the supervisor replays tool calls too
        entry = {
            key: message[key]
            for key in ("role", "content", "tool_calls", "tool_call_id")
            if key in message
        }
        messages.append(entry)

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
            if call.get("parse_error"):
                output = (
                    f"error: arguments were not valid JSON, {call['name']} did not run. "
                    f"{call['parse_error']}. Send the call again with valid JSON. "
                    f"Raw arguments: {call.get('raw', '')[:400]}"
                )
                ok, ms = False, 0
            else:
                def stream_chunk(chunk: str, call_id=call["id"]):
                    if on_tool_output is not None:
                        on_tool_output(call_id, chunk)

                output, ok, ms = await tools.execute(
                    call["name"], call["args"], campaign_id, stop, on_output=stream_chunk
                )
            yield "tool_result", {"id": call["id"], "name": call["name"], "output": output, "ok": ok, "ms": ms}
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})

        steps += 1
        if config.MAX_TOOL_STEPS > 0 and steps >= config.MAX_TOOL_STEPS:
            yield "notice", f"[stopped after {steps} tool steps]"
            return

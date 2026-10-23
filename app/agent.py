import json
import logging
import os
from collections.abc import AsyncIterator
from string import Template
from typing import Any, cast

from openai import AsyncOpenAI, BadRequestError
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
            # openai>=2 returns an async paginator from models.list(), not an
            # awaitable page, so iterate it instead of awaiting it
            async for model in _client(name).models.list():
                merged.append(f"{name}/{model.id}")
        except Exception as exc:
            log.warning("model list failed for provider %s: %s", name, exc)
            continue

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


async def _step(client, campaign, messages, calls: list, tool_kind: str | None = None) -> AsyncIterator[tuple[str, Any]]:
    provider, model = split_model(campaign["model"])
    conf = config.PROVIDERS.get(provider, {})

    extra: dict = {}
    if conf.get("thinking"):
        extra["reasoning_effort"] = cast(Any, campaign["effort"] or config.DEFAULT_EFFORT)
        extra["extra_body"] = {"thinking": {"type": "enabled"}}

    request: dict = dict(
        model=model,
        messages=cast(Any, messages),
        tools=cast(Any, tools.schemas_for(tool_kind)),
        stream=True,
        temperature=config.TEMPERATURE,
        **extra,
    )
    try:
        response = await client.chat.completions.create(
            **request, stream_options={"include_usage": True}
        )
    except BadRequestError:
        # the provider refuses stream_options; fall back to the estimate below
        log.info("provider %s rejected stream_options, usage will be estimated", provider)
        response = await client.chat.completions.create(**request)

    assembled: dict[int, dict] = {}
    announced: set[int] = set()
    reported = False
    output_chars = 0
    async for chunk in response:
        # the final usage chunk carries no choices, so read it before skipping
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            reported = True
            yield "usage", {
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            }

        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            output_chars += len(reasoning)
            yield "thinking", reasoning
        if delta.content:
            output_chars += len(delta.content)
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

    if not reported:
        # rough fallback (~4 chars per token) so compaction still gets a signal
        prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
        yield "usage", {
            "prompt_tokens": max(prompt_chars // 4, 1),
            "completion_tokens": max(output_chars // 4, 1),
            "estimated": True,
        }


async def run(campaign, attachment_rows, history, campaign_id, stop=None, on_tool_output=None, tool_kind=None) -> AsyncIterator[tuple[str, Any]]:
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

        step = (
            _step(client, campaign, messages, calls)
            if tool_kind is None
            else _step(client, campaign, messages, calls, tool_kind)
        )
        async for kind, data in step:
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

                if tool_kind is None:
                    output, ok, ms = await tools.execute(
                        call["name"], call["args"], campaign_id, stop, on_output=stream_chunk
                    )
                else:
                    output, ok, ms = await tools.execute(
                        call["name"],
                        call["args"],
                        campaign_id,
                        stop,
                        on_output=stream_chunk,
                        allow=tools.allowed_for(tool_kind),
                    )
            yield "tool_result", {"id": call["id"], "name": call["name"], "output": output, "ok": ok, "ms": ms}
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})

        steps += 1
        if config.MAX_TOOL_STEPS > 0 and steps >= config.MAX_TOOL_STEPS:
            yield "notice", f"[stopped after {steps} tool steps]"
            return

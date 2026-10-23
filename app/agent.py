import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncIterator
from string import Template
from typing import Any, cast

from openai import AsyncOpenAI, BadRequestError
from openai.types.chat import ChatCompletionMessageParam

from . import config, mcp, skills, tools, uploads

log = logging.getLogger("l4rpness")

SYSTEM = Template((config.ROOT / "prompts" / "campaign_agent.md").read_text())
_models: list[str] = []
_model_limits: dict[str, int] = {}
_limit_sources: dict[str, str] = {}


def reset_model_cache() -> None:
    """Clear IDs and limits together.

    Key rotation or provider changes refetch the listing; limits cached from
    the old listing must not survive it, or model_limit() and the compaction
    trigger silently use the wrong window until process restart.
    """
    _models.clear()
    _model_limits.clear()
    _limit_sources.clear()

# Last resort when neither the provider API nor the config table knows a
# model. Conservative on purpose: an unknown model gets early compaction.
FALLBACK_LIMIT = 128_000

# Headroom kept below the context limit: room for the next output plus the
# compaction summary itself (opencode COMPACTION_BUFFER equivalent).
COMPACT_RESERVE = 20_000

# model listing talks to every configured provider; an unreachable one must
# never stall the whole list (or the UI), so each fetch gets its own short cap
# with no SDK retries — a dead endpoint is skipped after one attempt
MODELS_TIMEOUT = 5.0


def provider_key(provider: str) -> str:
    conf = config.PROVIDERS.get(provider, {})
    return os.getenv(conf.get("api_key_env", ""), "").strip()


def _client(
    provider: str | None = None,
    timeout: float | None = None,
    retries: int | None = None,
) -> AsyncOpenAI:
    name = provider or config.PROVIDER
    conf = config.PROVIDERS.get(name, {})
    kwargs = {"max_retries": retries} if retries is not None else {}
    return AsyncOpenAI(
        api_key=provider_key(name) or config.API_KEY,
        base_url=conf.get("base_url", config.BASE_URL),
        timeout=timeout or config.REQUEST_TIMEOUT,
        **kwargs,
    )


def split_model(model: str) -> tuple[str, str]:
    name, sep, bare = model.partition("/")
    if sep and name in config.PROVIDERS:
        return name, bare
    if model.startswith("deepseek-"):
        return "deepseek", model
    return config.PROVIDER, model


async def _provider_models(name: str) -> list[str]:
    out: list[str] = []
    try:
        # openai>=2 returns an async paginator from models.list(), not an
        # awaitable page, so iterate it instead of awaiting it
        async for model in _client(name, timeout=MODELS_TIMEOUT, retries=0).models.list():
            fid = f"{name}/{model.id}"
            out.append(fid)
            limit = _limit_from_entry(model)
            if limit is not None:
                _model_limits[fid] = limit
                _limit_sources[fid] = "api"
    except Exception as exc:
        log.warning("model list failed for provider %s: %s", name, exc)
    return out


async def models() -> list[str]:
    global _models
    if _models:
        return _models

    names = [name for name in config.PROVIDERS if provider_key(name)]
    merged = await asyncio.gather(*(_provider_models(name) for name in names))
    _models = sorted({model for part in merged for model in part})
    return _models


def _limit_from_entry(entry) -> int | None:
    """Context window off a provider's model listing, any shape.

    9router exposes capabilities.contextWindow (falling back to top-level
    context_length); OpenAI-style listings carry neither. Anything
    unrecognized yields None so limit plumbing never breaks model listing.
    """
    try:
        caps = entry.get("capabilities") if isinstance(entry, dict) else getattr(entry, "capabilities", None)
        window = caps.get("contextWindow") if isinstance(caps, dict) else getattr(caps, "contextWindow", None)
        if isinstance(window, bool):
            window = None
        if isinstance(window, (int, float)) and window > 0:
            return int(window)
        length = (
            entry.get("context_length") if isinstance(entry, dict) else getattr(entry, "context_length", None)
        )
        if isinstance(length, bool):
            length = None
        if isinstance(length, (int, float)) and length > 0:
            return int(length)
    except Exception:
        log.warning("unreadable model limit entry: %r", type(entry))
    return None


def model_limit(model: str) -> int:
    """Context window for a campaign model string. Never raises."""
    return _limit_with_source(model)[0]


def limit_source(model: str) -> str:
    """Where model_limit's answer came from: api, table, or fallback."""
    return _limit_with_source(model)[1]


def _limit_with_source(model: str) -> tuple[int, str]:
    try:
        if model in _model_limits:
            return _model_limits[model], _limit_sources.get(model, "api")
        table = config.LIMITS or {}
        _, sep, bare = model.partition("/")
        for key in (model, bare if sep else model):
            try:
                value = int(table.get(key, 0))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value, "table"
    except Exception:
        log.warning("model limit lookup failed for %r", model)
    return FALLBACK_LIMIT, "fallback"


def usable_context(model: str) -> int:
    """Tokens we are willing to fill before compacting or erroring."""
    limit = model_limit(model)
    # Never exceed the resolved window, even for tiny/unknown models: the
    # 16k floor keeps small windows usable but must not overshoot the limit.
    return min(limit, max(limit - COMPACT_RESERVE, 16_000))


def compact_trigger(model: str) -> int | None:
    """Prompt-token threshold that folds history into memory.

    None means disabled. A positive config threshold wins verbatim (operator
    override); zero (the default) derives 80% of usable context so the valve
    opens before the provider errors do; negative disables.
    """
    try:
        setting = int(config.COMPACT_AT_TOKENS)
    except (TypeError, ValueError):
        setting = 0
    if setting < 0:
        return None
    if setting > 0:
        # Clamp operator overrides to the model's usable context: a larger
        # threshold would never fire before the provider rejects the request.
        try:
            return min(setting, usable_context(model))
        except Exception:
            return setting
    return int(usable_context(model) * 0.8)


def build_prompt(
    campaign,
    attachment_rows,
    include_run_mode: bool = True,
    include_attachments: bool = True,
    tool_kind: str | None = None,
) -> str:
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

    if attachment_rows and not include_attachments:
        # Later chains: contents went out on the first chain and live on in
        # early context and the memory summary. Re-sending up to 120KB every
        # chain is pure overhead, so only filenames ride along from here.
        try:
            names = ", ".join(row["filename"] for row in attachment_rows)
        except Exception:
            names = "attachments"
        sections.append(
            f"Attachments (contents provided at campaign start; see early context): {names}"
        )
        attachments_text = ""
    else:
        attachments_text = uploads.prompt_text(attachment_rows)

    # generators share this brief but have their own lifecycle — telling them
    # about complete_engagement would point at a tool their allow-list blocks
    if include_run_mode:
        mode = _campaign_mode(campaign) or "continuous"
        if mode == "goal":
            sections.append(
                "Run mode: until goal — pursue the objective stated in Instructions/Description "
                "to a concrete, evidence-backed result, then call complete_engagement once "
                "with a one-line summary of what was achieved and where the evidence lives. "
                "The tool is only available in this mode; a continuous campaign never completes."
            )
        else:
            sections.append(
                "Run mode: continuous (24x7) — keep testing until the operator stops you. "
                "This campaign never completes; complete_engagement does not exist here."
            )

    prompt = SYSTEM.substitute(
        campaign="\n\n".join(sections),
        attachments=attachments_text,
    )

    parts = []
    for file in config.INSTRUCTION_FILES:
        if file.name == "supervisor_policy.md" and tool_kind != "supervisor":
            continue
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


def _campaign_mode(campaign) -> str | None:
    return tools._extract_mode(campaign)


class ContextTooLargeError(Exception):
    """The provider rejected the request as exceeding the model window."""


def _looks_like_context_error(exc: Exception) -> bool:
    # Substring matching over "ClassName: message" only — provider SDKs differ
    # in shape, so this is a heuristic, not a complete classifier (see the
    # invariant matrix). Bare "max_tokens"/"context" are deliberately NOT
    # markers: parameter-validation 400s ("invalid max_tokens", "invalid
    # context parameter") must never pin the compaction gauge.
    text = f"{type(exc).__name__}: {exc}".lower()
    if (
        re.search(r"invalid\s+(max_tokens|\S+\s+parameter)", text)
        and "context length" not in text
        and "context_length" not in text
    ):
        return False
    return any(
        marker in text
        for marker in (
            "context_length",
            "context length",
            "maximum context",
            "too many tokens",
            "token limit",
            "out of context",
            "context window",
            "context too large",
            "context exceeds",
        )
    )


# chars-per-token divisor for estimates. This is a HEURISTIC, not an upper
# bound: dense unicode/code payloads tokenize far denser than 4 chars/token,
# and no tokenizer is available here. Guards compensate with SAFETY_MARGIN
# plus measured schemas, and mis-estimation degrades into the recoverable
# truncated/context-error paths — never into an unbounded provider call.
EST_CHARS_PER_TOKEN = 4
# Extra headroom below usable context after accounting for measured schemas.
SAFETY_MARGIN_TOKENS = 2_000


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Rough prompt size over every token-bearing field actually sent.

    Counts message content, reasoning_content (thinking models send it
    verbatim in the request), serialized tool calls, and tool_call ids.
    Tool schemas are measured separately at the guard site. Heuristic —
    see EST_CHARS_PER_TOKEN; never treat the result as an upper bound.
    """
    chars = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif content is not None:
            chars += len(str(content))
        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str):
            chars += len(reasoning)
        elif reasoning is not None:
            chars += len(str(reasoning))
        for call in message.get("tool_calls") or []:
            try:
                chars += len(json.dumps(call))
            except (TypeError, ValueError):
                pass
        if message.get("tool_call_id"):
            chars += len(str(message["tool_call_id"]))
    return max(chars // EST_CHARS_PER_TOKEN, 1)


def measured_schema_tokens(tool_kind: str | None, campaign) -> int:
    """Serialized tool schemas for this run, in tokens (same heuristic)."""
    try:
        schemas = tools.schemas_for(tool_kind, _campaign_mode(campaign))
        return max(len(json.dumps(schemas)) // EST_CHARS_PER_TOKEN, 0)
    except Exception:
        return 0


def _prune_in_memory(messages: list[dict], keep_newest: int = 8) -> bool:
    """Replace older in-chain tool outputs with placeholders (mutates).

    Returns True if anything was pruned. The first (system) message is never
    touched; the newest `keep_newest` tool results stay verbatim. Tails are
    kept so spill paths survive for paging via read_file.
    """
    idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    targets = idx[:-keep_newest] if len(idx) > keep_newest else []
    pruned = False
    for i in targets:
        content = messages[i].get("content")
        if not isinstance(content, str):
            continue
        if content.startswith("[in-chain output pruned"):
            continue
        tail = content[-1000:] if len(content) > 1000 else content
        messages[i]["content"] = (
            "[in-chain output pruned to bound the request; "
            "page the full evidence with read_file if needed.]\n" + tail
        )
        pruned = True
    return pruned


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
        tools=cast(Any, tools.schemas_for(tool_kind, _campaign_mode(campaign))),
        stream=True,
        temperature=config.TEMPERATURE,
        **extra,
    )
    try:
        response = await client.chat.completions.create(
            **request, stream_options={"include_usage": True}
        )
    except BadRequestError as exc:
        # the provider refuses stream_options; fall back to the estimate below
        if _looks_like_context_error(exc):
            raise ContextTooLargeError(str(exc)) from exc
        log.info("provider %s rejected stream_options, usage will be estimated", provider)
        try:
            response = await client.chat.completions.create(**request)
        except BadRequestError as exc2:
            if _looks_like_context_error(exc2):
                raise ContextTooLargeError(str(exc2)) from exc2
            raise
    except Exception as exc:
        if _looks_like_context_error(exc):
            raise ContextTooLargeError(str(exc)) from exc
        raise

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


async def run(campaign, attachment_rows, history, campaign_id, stop=None, on_tool_output=None, tool_kind=None, include_attachments: bool = True) -> AsyncIterator[tuple[str, Any]]:
    provider, _ = split_model(campaign["model"])
    client = _client(provider)
    messages: list[dict] = [{"role": "system", "content": build_prompt(campaign, attachment_rows, include_attachments=include_attachments, tool_kind=tool_kind)}]
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

        # Hard in-chain budget: tool results accumulate verbatim in `messages`
        # and compaction only runs between chains. Estimate the pending
        # request against usable context before every provider call; prune
        # older in-memory outputs when safe, otherwise end the chain with an
        # explicit recoverable event instead of letting the provider 400.
        # Schemas are measured (not reserved blindly); SAFETY_MARGIN covers
        # estimation error since no tokenizer is available. A single tool
        # result larger than the budget cannot be pruned (newest are exempt)
        # and terminates here — the loop then compacts or durably stops.
        try:
            usable = usable_context(campaign["model"])
        except Exception:
            usable = FALLBACK_LIMIT - COMPACT_RESERVE
        schemas = measured_schema_tokens(tool_kind, campaign)
        if estimate_messages_tokens(messages) + schemas >= usable - SAFETY_MARGIN_TOKENS:
            _prune_in_memory(messages)
            schemas = measured_schema_tokens(tool_kind, campaign)
            if estimate_messages_tokens(messages) + schemas >= usable - SAFETY_MARGIN_TOKENS:
                yield (
                    "truncated",
                    "[context budget reached mid-chain: ending this chain so "
                    "older turns can compact; continue next chain.]",
                )
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
                        allow=tools.allowed_for(tool_kind, _campaign_mode(campaign)),
                    )
            yield "tool_result", {"id": call["id"], "name": call["name"], "output": output, "ok": ok, "ms": ms}
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})

        steps += 1
        if config.MAX_TOOL_STEPS > 0 and steps >= config.MAX_TOOL_STEPS:
            yield "notice", f"[stopped after {steps} tool steps]"
            return

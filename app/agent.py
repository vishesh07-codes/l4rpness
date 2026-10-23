from collections.abc import AsyncIterator
from string import Template
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from . import config, uploads

SYSTEM = Template((config.ROOT / "prompts" / "campaign_agent.md").read_text())


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


async def stream(campaign, attachment_rows, history) -> AsyncIterator[tuple[str, str]]:
    client = AsyncOpenAI(api_key=config.API_KEY, base_url=config.BASE_URL, timeout=180)
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": build_prompt(campaign, attachment_rows)}
    ]
    messages += [
        cast(ChatCompletionMessageParam, {"role": m["role"], "content": m["content"]})
        for m in history
    ]

    response = await client.chat.completions.create(
        model=campaign["model"],
        messages=messages,
        stream=True,
        reasoning_effort=cast(Any, campaign["effort"] or config.DEFAULT_EFFORT),
        extra_body={"thinking": {"type": "enabled"}},
    )

    async for chunk in response:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            yield "thinking", reasoning
        if delta.content:
            yield "content", delta.content

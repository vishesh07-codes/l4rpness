from string import Template
from typing import cast

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


async def reply(campaign, attachment_rows, history) -> str:
    client = AsyncOpenAI(api_key=config.API_KEY, base_url=config.BASE_URL, timeout=180)
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": build_prompt(campaign, attachment_rows)}
    ]
    messages += [
        cast(ChatCompletionMessageParam, {"role": m["role"], "content": m["content"]})
        for m in history
    ]

    resp = await client.chat.completions.create(model=campaign["model"], messages=messages)
    return (resp.choices[0].message.content or "").strip()

import asyncio
import os
import re
import time
from itertools import islice
from pathlib import Path

from . import config

SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Run a shell command in the campaign work directory. "
                "Long output is saved to a file and the result says where."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Shell command to run."},
                    "timeout": {
                        "type": "integer",
                        "description": f"Seconds before the command is killed, max {config.BASH_TIMEOUT}.",
                    },
                },
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a text file from the campaign work directory. "
                "Use offset and limit to page through a large file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the work directory."},
                    "offset": {"type": "integer", "description": "First line to read, 1 based."},
                    "limit": {"type": "integer", "description": "How many lines, default 500."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a text file in the campaign work directory, creating parent directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the work directory."},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
]


def _work_dir(campaign_id: int) -> Path:
    path = config.WORK_DIR / str(campaign_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve(work: Path, name: str) -> Path:
    path = (work / name).resolve()
    if not path.is_relative_to(work.resolve()):
        raise ValueError("path escapes the work dir")
    return path


def _spill(work: Path, text: str, name: str) -> str:
    tmp = work / "tmp"
    tmp.mkdir(exist_ok=True)
    slug = re.sub(r"[^\w.-]+", "-", name).strip("-")[:40] or "output"
    base = f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}"

    file = tmp / f"{base}.txt"
    n = 1
    while file.exists():
        file = tmp / f"{base}-{n}.txt"
        n += 1

    file.write_text(text)
    return str(file.relative_to(work))


def _clip(work: Path, text: str, name: str) -> str:
    if len(text) <= config.TOOL_OUTPUT_CHARS:
        return text

    path = _spill(work, text, name)
    return (
        text[: config.TOOL_OUTPUT_CHARS]
        + f"\n[output truncated: {len(text):,} chars total, full output saved to {path}]"
    )


async def bash(args: dict, work: Path) -> tuple[str, bool]:
    cmd = str(args.get("cmd", "")).strip()
    if not cmd:
        raise ValueError("cmd is required")

    try:
        timeout = max(1, min(int(args["timeout"]), config.BASH_TIMEOUT))
    except (KeyError, TypeError, ValueError):
        timeout = config.BASH_TIMEOUT

    env = {k: v for k, v in os.environ.items() if k != "DEEPSEEK_API_KEY"}
    proc = await asyncio.create_subprocess_shell(
        cmd,
        cwd=work,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    stdout = proc.stdout

    async def drain() -> tuple[bytes, bool]:
        out = bytearray()
        capped = False
        while True:
            block = await stdout.read(65536)
            if not block:
                break
            room = config.BASH_OUTPUT_BYTES - len(out)
            if room > 0:
                out.extend(block[:room])
            if len(block) > room:
                capped = True
        await proc.wait()
        return bytes(out), capped

    note = ""
    try:
        out, capped = await asyncio.wait_for(drain(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        out, capped = await drain()
        note = f"\n[killed after {timeout}s]"

    if capped:
        note += f"\n[output limit reached, kept the first {config.BASH_OUTPUT_BYTES:,} bytes]"

    text = out.decode(errors="replace")
    body = _clip(work, text, cmd)
    return f"{body}\n[exit code {proc.returncode}]{note}".strip(), proc.returncode == 0 and not capped


async def read_file(args: dict, work: Path) -> tuple[str, bool]:
    path = _resolve(work, str(args.get("path", "")))
    if not path.is_file():
        raise ValueError("file not found")

    offset = max(int(args.get("offset") or 1), 1)
    limit = max(1, min(int(args.get("limit") or 500), config.READ_MAX_LINES))

    with path.open(errors="replace") as fh:
        for _ in range(offset - 1):
            if not fh.readline():
                return f"{path.relative_to(work)}: nothing at offset {offset}", True

        chunk = [line.rstrip("\n") for line in islice(fh, limit)]
        if not chunk:
            return f"{path.relative_to(work)}: nothing at offset {offset}", True

        remaining = sum(1 for _ in fh)

    end = offset - 1 + len(chunk)
    total = end + remaining
    body = "\n".join(f"{number:5d}  {line}" for number, line in enumerate(chunk, start=offset))
    tail = "" if remaining == 0 else f"\n[{remaining} more lines, use offset={end + 1}]"
    return f"{path.relative_to(work)} (lines {offset}-{end} of {total})\n{body}{tail}", True


async def write_file(args: dict, work: Path) -> tuple[str, bool]:
    path = _resolve(work, str(args.get("path", "")))
    path.parent.mkdir(parents=True, exist_ok=True)

    content = str(args.get("content", ""))
    path.write_text(content)
    return f"wrote {len(content)} chars to {path.relative_to(work)}", True


HANDLERS = {"bash": bash, "read_file": read_file, "write_file": write_file}


async def execute(name: str, args: dict, campaign_id: int) -> tuple[str, bool, int]:
    work = _work_dir(campaign_id)
    handler = HANDLERS.get(name)
    if handler is None:
        return f"error: unknown tool {name}", False, 0

    started = time.monotonic()
    try:
        text, ok = await handler(args or {}, work)
    except Exception as exc:
        text, ok = f"error: {exc}", False
    ms = int((time.monotonic() - started) * 1000)
    return text, ok, ms

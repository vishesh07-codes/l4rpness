import asyncio
import codecs
import contextlib
import os
import re
import signal
import time
from itertools import islice
from pathlib import Path

from . import config, mcp, skills

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
                    "cmd": {
                        "type": "string",
                        "description": "The shell command as one string. Required.",
                    },
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
            "name": "skill",
            "description": (
                "Load a skill by name and follow it. "
                "The available skills and when to use them are listed in the system prompt."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name from the available skills list."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a text file from the campaign work directory, or from a skill directory "
                "when a skill points at extra files. Use offset and limit to page through a large file."
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
    {
        "type": "function",
        "function": {
            "name": "write_todos",
            "description": (
                "Publish or update your task plan for this campaign. Pass the full "
                "list every time; it replaces the previous plan. Use it to track "
                "multi-step work and keep one item in_progress."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Short imperative task, e.g. 'Enumerate open ports'.",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed", "cancelled"],
                                },
                            },
                            "required": ["title"],
                        },
                    },
                },
                "required": ["todos"],
            },
        },
    },
]


def _work_dir(campaign_id: int) -> Path:
    path = config.WORK_DIR / str(campaign_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _str_arg(args: dict, *keys: str) -> str:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _cmd_arg(args: dict) -> str:
    cmd = _str_arg(args, "cmd", "command", "cmdline", "script", "shell", "shell_command")
    if cmd:
        return cmd
    # models occasionally drop a positional-style key ({"__arg1": "..."});
    # with exactly one candidate string there is nothing to be ambiguous about
    extras = {
        key: value.strip()
        for key, value in args.items()
        if key != "timeout" and isinstance(value, str) and value.strip()
    }
    if len(extras) == 1:
        return next(iter(extras.values()))
    return ""


def _resolve(roots: list[Path], name: str) -> Path:
    fallback = None
    for root in roots:
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()):
            continue
        if fallback is None:
            fallback = path
        if path.exists():
            return path
    if fallback is not None:
        return fallback
    raise ValueError("path escapes the allowed dirs")


def _label(path: Path, work: Path) -> str:
    return str(path.relative_to(work)) if path.is_relative_to(work) else str(path)


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


def _kill_tree(proc) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _kill_when_stopped(proc, stop):
    await stop.wait()
    if proc.returncode is None:
        _kill_tree(proc)


async def _wait_for_exit(proc, seconds: float) -> bool:
    """Poll for process exit and return True when it happened.

    asyncio's Process.wait() only resolves once every stdout/stderr pipe is
    disconnected (base_subprocess._call_connection_lost). A detached process
    can hold the pipe open after the shell exits, which makes wait() hang
    forever even though the process is already reaped. The returncode is set
    the moment the process exits, so poll that instead.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while proc.returncode is None:
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.05)
    return True


async def bash(args: dict, work: Path, stop=None, on_output=None) -> tuple[str, bool]:
    cmd = _cmd_arg(args)
    if not cmd:
        got = ", ".join(sorted(args)) or "none"
        raise ValueError(
            f'cmd is required: call bash with {{"cmd": "the shell command"}} (got keys: {got})'
        )

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
        start_new_session=True,
    )
    assert proc.stdout is not None
    stdout = proc.stdout

    out = bytearray()
    capped = False

    async def read_output() -> None:
        nonlocal capped
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        streamed = 0
        while True:
            block = await stdout.read(65536)
            if not block:
                break
            room = config.BASH_OUTPUT_BYTES - len(out)
            if room > 0:
                out.extend(block[:room])
            if len(block) > room:
                capped = True
            if on_output is not None:
                text = decoder.decode(block)
                if text and streamed < config.TOOL_OUTPUT_CHARS:
                    piece = text[: config.TOOL_OUTPUT_CHARS - streamed]
                    streamed += len(piece)
                    on_output(piece)
        if on_output is not None:
            tail = decoder.decode(b"", final=True)
            if tail and streamed < config.TOOL_OUTPUT_CHARS:
                on_output(tail[: config.TOOL_OUTPUT_CHARS - streamed])

    watcher = asyncio.ensure_future(_kill_when_stopped(proc, stop)) if stop else None
    reader = asyncio.ensure_future(read_output())
    note = ""
    try:
        # wait for the shell process, never for EOF: a detached background
        # process can hold the stdout pipe open after the shell is gone
        if not await _wait_for_exit(proc, timeout):
            note = f"\n[killed after {timeout}s]"
            _kill_tree(proc)
            await _wait_for_exit(proc, 5)
    finally:
        if watcher:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        if proc.returncode is None:
            _kill_tree(proc)
        # the process is gone; give the reader a moment to flush what is
        # buffered, then stop waiting on it
        try:
            await asyncio.wait_for(reader, 2)
        except asyncio.TimeoutError:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            with contextlib.suppress(Exception):
                transport.close()

    if stop is not None and stop.is_set():
        note += "\n[stopped by request]"

    if capped:
        note += f"\n[output limit reached, kept the first {config.BASH_OUTPUT_BYTES:,} bytes]"

    text = bytes(out).decode(errors="replace")
    body = _clip(work, text, cmd)
    return f"{body}\n[exit code {proc.returncode}]{note}".strip(), proc.returncode == 0 and not capped


async def read_file(args: dict, work: Path, stop=None) -> tuple[str, bool]:
    name = _str_arg(args, "path", "file_path", "file", "filename")
    if not name:
        got = ", ".join(sorted(args)) or "none"
        raise ValueError(f'path is required: call read_file with {{"path": "relative/path"}} (got keys: {got})')
    path = _resolve([work, *skills.roots()], name)
    if not path.is_file():
        raise ValueError("file not found")

    offset = max(int(args.get("offset") or 1), 1)
    limit = max(1, min(int(args.get("limit") or 500), config.READ_MAX_LINES))

    with path.open(errors="replace") as fh:
        for _ in range(offset - 1):
            if not fh.readline():
                return f"{_label(path, work)}: nothing at offset {offset}", True

        chunk = [line.rstrip("\n") for line in islice(fh, limit)]
        if not chunk:
            return f"{_label(path, work)}: nothing at offset {offset}", True

        remaining = sum(1 for _ in fh)

    end = offset - 1 + len(chunk)
    total = end + remaining
    body = "\n".join(f"{number:5d}  {line}" for number, line in enumerate(chunk, start=offset))
    tail = "" if remaining == 0 else f"\n[{remaining} more lines, use offset={end + 1}]"
    return f"{_label(path, work)} (lines {offset}-{end} of {total})\n{body}{tail}", True


async def write_file(args: dict, work: Path, stop=None) -> tuple[str, bool]:
    name = _str_arg(args, "path", "file_path", "file", "filename")
    if not name:
        got = ", ".join(sorted(args)) or "none"
        raise ValueError(f'path is required: call write_file with {{"path": "relative/path"}} (got keys: {got})')
    path = _resolve([work], name)
    path.parent.mkdir(parents=True, exist_ok=True)

    content = args.get("content")
    if content is None:
        content = args.get("text", "")
    content = str(content)
    path.write_text(content)
    return f"wrote {len(content)} chars to {path.relative_to(work)}", True


async def write_todos(args: dict, work: Path, stop=None) -> tuple[str, bool]:
    todos = args.get("todos") or args.get("plan")
    if not isinstance(todos, list) or not todos:
        raise ValueError("todos must be a non-empty list")

    done = sum(
        1
        for todo in todos
        if isinstance(todo, dict) and todo.get("status") == "completed"
    )
    return f"plan updated: {len(todos)} items, {done} completed", True


async def skill(args: dict, work: Path, stop=None) -> tuple[str, bool]:
    name = _str_arg(args, "name", "skill", "skill_name")
    loaded = skills.load(name)
    if loaded is None:
        names = ", ".join(entry["name"] for entry in skills.discover()) or "none"
        raise ValueError(f"unknown skill {name!r}, available: {names}")

    header = f"# Skill: {loaded['name']}\nDirectory: {loaded['dir']}\n\n"
    return header + loaded["body"], True


HANDLERS = {
    "bash": bash,
    "skill": skill,
    "read_file": read_file,
    "write_file": write_file,
    "write_todos": write_todos,
}


async def execute(name: str, args: dict, campaign_id: int, stop=None, on_output=None) -> tuple[str, bool, int]:
    work = _work_dir(campaign_id)

    if mcp.is_tool(name):
        started = time.monotonic()
        text, ok = await mcp.call(name, args or {})
        ms = int((time.monotonic() - started) * 1000)
        return _clip(work, text, name), ok, ms

    handler = HANDLERS.get(name)
    if handler is None:
        return f"error: unknown tool {name}", False, 0

    started = time.monotonic()
    try:
        if name == "bash":
            text, ok = await handler(args or {}, work, stop, on_output)
        else:
            text, ok = await handler(args or {}, work, stop)
    except Exception as exc:
        text, ok = f"error: {exc}", False
    ms = int((time.monotonic() - started) * 1000)
    return text, ok, ms

import asyncio
import codecs
import contextlib
import json
import logging
import os
import re
import signal
import time
from itertools import islice
from pathlib import Path

from . import config, db, mcp, skills

log = logging.getLogger("l4rpness")

CORE_SCHEMAS = [
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

SCHEMAS = CORE_SCHEMAS  # legacy alias; per-agent sets come from schemas_for()

SUPERVISOR_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "spawn_hypothesis_generator",
            "description": (
                "Start a background hypothesis generator for this campaign. It studies "
                "the target and files concrete, testable attack hypotheses that you can "
                "read with hypothesis_list. Spawn several with different focus areas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "focus": {
                        "type": "string",
                        "description": "What this generator should look at, e.g. 'auth and sessions' or 'file uploads and parsers'.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hypothesis_list",
            "description": "List filed hypotheses with status, angle, class and result. Use it to pick the next thing to work on.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["", "new", "working", "done", "dead"],
                        "description": "Optional status filter; empty lists everything.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hypothesis_update",
            "description": "Update a hypothesis status and optionally record a one-line result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hypothesis_id": {"type": "integer"},
                    "status": {"type": "string", "enum": ["new", "working", "done", "dead"]},
                    "result": {"type": "string", "description": "One-line outcome, e.g. 'confirmed XSS in search' or 'no sink reachable'."},
                },
                "required": ["hypothesis_id", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generator_status",
            "description": (
                "Read the hypothesis workers (generators): what they are doing right now "
                "and their recent chat history. Without an id it lists every generator for "
                "the campaign."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "generator_id": {"type": "integer", "description": "Optional: one generator to inspect in detail."},
                    "limit": {"type": "integer", "description": "How many of its latest messages to return (default 6, max 30)."},
                },
            },
        },
    },
]

SUPERVISOR_SCHEMAS.append(
    {
        "type": "function",
        "function": {
            "name": "complete_engagement",
            "description": (
                "Declare the campaign's objective achieved and end the engagement. "
                "Only use this in a goal-mode campaign, once the stated goal is "
                "genuinely met and the evidence is recorded in your work directory. "
                "A continuous campaign never completes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "One line: what was achieved and where the evidence lives.",
                    },
                },
                "required": ["summary"],
            },
        },
    }
)

GENERATOR_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "hypothesis_generate",
            "description": "File one concrete, testable attack hypothesis. Call it once per hypothesis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short specific title."},
                    "angle": {"type": "string", "description": "The exact attack idea."},
                    "vuln_class": {"type": "string", "description": "e.g. sqli, xss, idor, auth_bypass, ssrf, rce, race, logic."},
                    "rationale": {"type": "string", "description": "Why this should work: what you observed and how the input reaches the sink."},
                },
                "required": ["title", "angle", "vuln_class", "rationale"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generator_done",
            "description": "Finish this generator run. Call it once you filed all hypotheses.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["done", "blocked"]},
                    "summary": {"type": "string", "description": "How many hypotheses were filed and what areas they cover."},
                },
                "required": ["status", "summary"],
            },
        },
    },
]

CORE_TOOLS = {"bash", "skill", "read_file", "write_file", "write_todos"}
SUPERVISOR_TOOLS = {
    "spawn_hypothesis_generator",
    "hypothesis_list",
    "hypothesis_update",
    "generator_status",
    "complete_engagement",
}
GENERATOR_TOOLS = {"hypothesis_generate", "generator_done"}


def _extract_mode(mode_or_campaign) -> str | None:
    if mode_or_campaign is None or isinstance(mode_or_campaign, str):
        return mode_or_campaign
    try:
        return mode_or_campaign["mode"] or None
    except Exception:
        pass
    try:
        return mode_or_campaign.get("mode") or None
    except Exception:
        return None


def schemas_for(kind: str | None = None, mode: str | dict | None = None) -> list[dict]:
    schemas = list(CORE_SCHEMAS)
    if kind == "supervisor":
        # Fail closed: unknown/missing mode hides the tool, matching the
        # prompt default of continuous. Only goal campaigns offer it.
        resolved = _extract_mode(mode)
        if resolved == "goal":
            schemas += SUPERVISOR_SCHEMAS
        else:
            schemas += [s for s in SUPERVISOR_SCHEMAS if s["function"]["name"] != "complete_engagement"]
    elif kind == "generator":
        schemas += GENERATOR_SCHEMAS
    return schemas + mcp.schemas()


def allowed_for(kind: str | None = None, mode: str | dict | None = None) -> set[str] | None:
    resolved = _extract_mode(mode)
    if kind == "supervisor":
        if resolved == "goal":
            return CORE_TOOLS | SUPERVISOR_TOOLS
        return (CORE_TOOLS | SUPERVISOR_TOOLS) - {"complete_engagement"}
    if kind == "generator":
        return CORE_TOOLS | GENERATOR_TOOLS
    return None


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


async def spawn_hypothesis_generator(args: dict, campaign_id: int, stop=None) -> tuple[str, bool]:
    from . import generator

    focus = str(args.get("focus", "")).strip()
    gen_id = generator.spawn(campaign_id, focus)
    if gen_id == 0:
        return f"error: already at the limit of {config.MAX_GENERATORS} running generators", False
    return (
        f"generator #{gen_id} started (focus: {focus or 'general'}). "
        "Read its hypotheses later with hypothesis_list."
    ), True


async def hypothesis_generate(
    args: dict, campaign_id: int, stop=None, generator_id: int | None = None
) -> tuple[str, bool]:
    title = str(args.get("title", "")).strip()
    if not title:
        return "error: title is required", False
    angle = str(args.get("angle", "")).strip()
    vuln_class = str(args.get("vuln_class", "")).strip()
    rationale = str(args.get("rationale", "")).strip()

    with db.db() as conn:
        existing = conn.execute(
            "SELECT id FROM hypotheses WHERE campaign_id = ? AND lower(title) = lower(?)",
            (campaign_id, title),
        ).fetchone()
        if existing:
            return f"hypothesis #{existing['id']} already exists (duplicate title), skipped", True
        cur = conn.execute(
            """
            INSERT INTO hypotheses (campaign_id, generator_id, title, angle, vuln_class, rationale)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (campaign_id, generator_id, title[:200], angle[:500], vuln_class[:60], rationale[:4000]),
        )
        hypothesis_id = cur.lastrowid
    return f"hypothesis #{hypothesis_id} filed: {title}", True


async def hypothesis_list(args: dict, campaign_id: int, stop=None) -> tuple[str, bool]:
    status = str(args.get("status", "")).strip()
    with db.db() as conn:
        counts = {
            row["status"]: row["c"]
            for row in conn.execute(
                "SELECT status, count(*) AS c FROM hypotheses WHERE campaign_id = ? GROUP BY status",
                (campaign_id,),
            ).fetchall()
        }
        sql = "SELECT * FROM hypotheses WHERE campaign_id = ?"
        params: list = [campaign_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        rows = conn.execute(sql + " ORDER BY id DESC LIMIT 30", params).fetchall()

    summary = ", ".join(f"{counts.get(name, 0)} {name}" for name in ("new", "working", "done", "dead"))
    if not rows:
        return f"hypotheses: {summary} (nothing to show)", True

    lines = []
    for row in rows:
        line = f"#{row['id']} [{row['status']}] {row['title']} | {row['angle']} | {row['vuln_class']}"
        if row["result"]:
            line += f" | result: {row['result'][:160]}"
        lines.append(line[:400])
    return f"hypotheses: {summary}\n" + "\n".join(lines), True


async def hypothesis_update(args: dict, campaign_id: int, stop=None) -> tuple[str, bool]:
    try:
        hypothesis_id = int(args.get("hypothesis_id") or 0)
    except (TypeError, ValueError):
        hypothesis_id = 0
    status = str(args.get("status", "")).strip()
    if status not in ("new", "working", "done", "dead"):
        return "error: status must be new, working, done or dead", False
    result = str(args.get("result", "")).strip()

    with db.db() as conn:
        row = conn.execute(
            "SELECT id FROM hypotheses WHERE id = ? AND campaign_id = ?",
            (hypothesis_id, campaign_id),
        ).fetchone()
        if row is None:
            return f"error: hypothesis #{hypothesis_id} not found", False
        if result:
            conn.execute(
                "UPDATE hypotheses SET status = ?, result = ?, updated_at = datetime('now') WHERE id = ?",
                (status, result[:1000], hypothesis_id),
            )
        else:
            conn.execute(
                "UPDATE hypotheses SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (status, hypothesis_id),
            )
    suffix = f" ({result[:160]})" if result else ""
    return f"hypothesis #{hypothesis_id} -> {status}{suffix}", True


def _generator_parts(row) -> list:
    try:
        return json.loads(row["parts"] or "[]")
    except json.JSONDecodeError:
        return []


def _message_snippet(row) -> str:
    text = str(row["content"] or "")
    for part in reversed(_generator_parts(row)):
        if part.get("type") == "tool":
            text = f"{part.get('name')}: {part.get('output') or ''}"
            break
        if part.get("type") == "content" and part.get("text"):
            text = part["text"]
            break
    return text.replace("\n", " ").strip()[:160]


def _format_generator_message(row) -> list[str]:
    lines = []
    for part in _generator_parts(row):
        kind = part.get("type")
        if kind == "thinking" and part.get("text"):
            lines.append(f"  [thinking] {str(part['text'])[:200]}")
        elif kind == "content" and part.get("text"):
            lines.append(f"  [say] {str(part['text'])[:300]}")
        elif kind == "tool":
            args = json.dumps(part.get("args") or {})[:120]
            output = str(part.get("output") or "")[:300]
            lines.append(f"  [tool] {part.get('name')} {args} -> {output}")
    if not lines and row["content"]:
        lines.append(f"  {str(row['content'])[:300]}")
    return lines


async def generator_status(args: dict, campaign_id: int, stop=None) -> tuple[str, bool]:
    try:
        generator_id = int(args.get("generator_id") or 0)
    except (TypeError, ValueError):
        generator_id = 0
    try:
        limit = max(1, min(int(args.get("limit") or 6), 30))
    except (TypeError, ValueError):
        limit = 6

    with db.db() as conn:
        if generator_id:
            gen = conn.execute(
                "SELECT * FROM generators WHERE id = ? AND campaign_id = ?",
                (generator_id, campaign_id),
            ).fetchone()
            if gen is None:
                return f"error: generator #{generator_id} not found", False
            rows = conn.execute(
                "SELECT * FROM generator_messages WHERE generator_id = ? ORDER BY id DESC LIMIT ?",
                (generator_id, limit),
            ).fetchall()
            filed = conn.execute(
                "SELECT id, title, status, vuln_class FROM hypotheses WHERE generator_id = ? ORDER BY id",
                (generator_id,),
            ).fetchall()

            lines = [f"generator #{gen['id']} [{gen['status']}] focus: {gen['focus'] or 'general'}, {gen['updated_at']}"]
            for row in reversed(rows):
                lines.extend(_format_generator_message(row))
            if filed:
                lines.append("filed hypotheses:")
                lines.extend(
                    f"  #{h['id']} [{h['status']}] {h['title']} ({h['vuln_class']})" for h in filed
                )
            return "\n".join(lines)[:12000], True

        rows = conn.execute(
            """
            SELECT g.*,
                   (SELECT count(*) FROM hypotheses h WHERE h.generator_id = g.id) AS hypothesis_count,
                   (SELECT id FROM generator_messages m WHERE m.generator_id = g.id ORDER BY id DESC LIMIT 1) AS last_message_id
            FROM generators g
            WHERE g.campaign_id = ?
            ORDER BY g.id DESC
            LIMIT 10
            """,
            (campaign_id,),
        ).fetchall()
        if not rows:
            return "no generators have run for this campaign yet", True

        lines = []
        for gen in rows:
            snippet = ""
            if gen["last_message_id"]:
                last = conn.execute(
                    "SELECT content, parts FROM generator_messages WHERE id = ?",
                    (gen["last_message_id"],),
                ).fetchone()
                if last is not None:
                    snippet = _message_snippet(last)
            line = (
                f"#{gen['id']} [{gen['status']}] focus: {gen['focus'] or 'general'}"
                f" | hypotheses: {gen['hypothesis_count']}"
            )
            if snippet:
                line += f" | last: {snippet}"
            lines.append(line)
        return "generators:\n" + "\n".join(lines), True


async def complete_engagement(args: dict, campaign_id: int, stop=None) -> tuple[str, bool]:
    summary = _str_arg(args, "summary") or "objective achieved"
    try:
        with db.db() as conn:
            row = conn.execute("SELECT mode FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    except Exception:
        log.exception("complete_engagement: mode lookup failed for campaign %d", campaign_id)
        return "error: could not verify campaign mode", False
    if row is None:
        return "error: campaign not found", False
    try:
        mode = row["mode"] or "continuous"
    except Exception:
        mode = "continuous"
    if mode != "goal":
        return "error: complete_engagement is only available in until-goal campaigns", False
    return f"engagement marked complete: {summary}", True


CAMPAIGN_HANDLERS = {
    "complete_engagement": complete_engagement,
    "spawn_hypothesis_generator": spawn_hypothesis_generator,
    "hypothesis_generate": hypothesis_generate,
    "hypothesis_list": hypothesis_list,
    "hypothesis_update": hypothesis_update,
    "generator_status": generator_status,
}

HANDLERS = {
    "bash": bash,
    "skill": skill,
    "read_file": read_file,
    "write_file": write_file,
    "write_todos": write_todos,
}


async def execute(
    name: str,
    args: dict,
    campaign_id: int,
    stop=None,
    on_output=None,
    allow: set[str] | None = None,
) -> tuple[str, bool, int]:
    work = _work_dir(campaign_id)

    # per-agent permission layer: a run may only call the tools it was given
    if allow is not None and name not in allow and not mcp.is_tool(name):
        return f"error: tool {name} is not available in this context", False, 0

    if mcp.is_tool(name):
        started = time.monotonic()
        text, ok = await mcp.call(name, args or {})
        ms = int((time.monotonic() - started) * 1000)
        return _clip(work, text, name), ok, ms

    if name in CAMPAIGN_HANDLERS:
        started = time.monotonic()
        try:
            text, ok = await CAMPAIGN_HANDLERS[name](args or {}, campaign_id, stop)
        except Exception as exc:
            text, ok = f"error: {exc}", False
        ms = int((time.monotonic() - started) * 1000)
        return text, ok, ms

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

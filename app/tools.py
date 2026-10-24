import asyncio
import codecs
import contextlib
import json
import logging
import os
import re
import signal
import shlex
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
                    "The command starts there; avoid cd or pushd outside it "
                    "(advisory check only — absolute-path I/O is not blocked). "
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
                        "enum": ["new", "working", "review", "done", "dead"],
                        "description": "Optional status filter; omit to list every hypothesis. 'review' = a hunter filed a result awaiting your accept/deny.",
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
    {
        "type": "function",
        "function": {
            "name": "worker_status",
            "description": (
                "Read the hunter workers: what they are doing right now, the hypotheses "
                "they were dispatched with, and the results they filed. Without an id it "
                "lists every worker for the campaign."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "worker_id": {"type": "integer", "description": "Optional: one worker to inspect in detail."},
                    "limit": {"type": "integer", "description": "How many of its latest messages to return (default 6, max 30)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_hunter",
            "description": (
                "Dispatch a hunter worker to test up to 3 hypotheses. The worker exploits "
                "them and files a verdict with a runnable PoC. Pass worker_id to send an "
                "existing worker back for a deeper dive (it keeps its history), otherwise a "
                "fresh worker starts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hypothesis_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Ids of the hypotheses to test. Must be status 'new'.",
                    },
                    "worker_id": {
                        "type": "integer",
                        "description": "Optional: reactivate this worker instead of starting a new one.",
                    },
                    "note": {
                        "type": "string",
                        "description": "Optional extra instruction for this run, e.g. what to dig into.",
                    },
                },
                "required": ["hypothesis_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "worker_stop",
            "description": "Stop a running hunter worker. Its unresolved hypotheses go back to the queue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "worker_id": {"type": "integer", "description": "The worker to stop."},
                },
                "required": ["worker_id"],
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

VM_HUNTER_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "vm_exec",
            "description": (
                "Run one shell command inside the kernel-pentest VM, as the unprivileged "
                "user. The console takes a single line — chain with ; or &&, or use "
                "sh -c, never a multi-line script. Background long jobs (nohup ... &) and "
                "poll them with vm_status/vm_console instead of waiting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "One shell command line."},
                    "timeout": {"type": "integer", "description": "Seconds before the command is abandoned (default 120)."},
                },
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_status",
            "description": (
                "Host-side state of the VM: running or not, how long the guest has been up, "
                "how many times it has restarted, and whether root is locked. Works even "
                "when the guest is wedged or mid-panic, so check it when a command does not "
                "come back."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_console",
            "description": (
                "Tail of the VM's serial console: boot messages, and kernel panics. This is "
                "where a crash shows up — dmesg may be restricted inside the guest, but the "
                "console is captured on the host."
            ),
            "parameters": {
                "type": "object",
                "properties": {"lines": {"type": "integer", "description": "How many lines (default 80)."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_flag",
            "description": (
                "Check a flag value you read from /root/root.txt against the one the host "
                "planted. Returns correct or does not match — the expected value is never "
                "sent here, so a match proves the file was actually read as root. Pass the "
                "value only after escalating; guessing gets nowhere."
            ),
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string", "description": "The flag string you read."}},
                "required": ["value"],
            },
        },
    },
]

VM_SUPERVISOR_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "vm_start",
            "description": "Start this campaign's VM if it is stopped. Uses the campaign's root setting.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_stop",
            "description": "Stop the VM. It stays stopped until you start it again.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_restart",
            "description": "Power-cycle the VM, keeping its disk: files the workers left in it survive.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vm_reset",
            "description": (
                "Throw the VM's disk away and clone a clean one from the base image, then "
                "start it. Destructive: everything the workers had in there is gone."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

HUNTER_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "hunt_report",
            "description": (
                "File the result for one dispatched hypothesis. Call it once per hypothesis, "
                "with a runnable PoC saved in your work directory. The supervisor reviews "
                "your verdict and decides whether to accept it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hypothesis_id": {"type": "integer", "description": "The hypothesis this result is for."},
                    "verdict": {
                        "type": "string",
                        "enum": ["confirmed", "not_vulnerable", "inconclusive", "blocked"],
                        "description": "confirmed = exploited with evidence; not_vulnerable = proven safe; inconclusive = undetermined; blocked = could not test.",
                    },
                    "severity": {"type": "string", "description": "e.g. critical, high, medium, low (for confirmed findings)."},
                    "impact": {"type": "string", "description": "What an attacker gets from this. Required for confirmed."},
                    "poc_path": {
                        "type": "string",
                        "description": "Campaign-relative path to the runnable PoC script/file you wrote, e.g. 'hunters/3/poc/run.sh'.",
                    },
                    "repro": {"type": "string", "description": "Exact steps/commands to reproduce it."},
                    "evidence": {"type": "string", "description": "The raw request/response or tool output proving it."},
                },
                "required": ["hypothesis_id", "verdict"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hunter_done",
            "description": "Finish this hunter run. Call it once every dispatched hypothesis has a result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "One line per hypothesis: verdict and what you proved."},
                },
                "required": ["summary"],
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
    "spawn_hunter",
    "worker_status",
    "worker_stop",
    "complete_engagement",
}
GENERATOR_TOOLS = {"hypothesis_generate", "generator_done"}
HUNTER_TOOLS = {"hunt_report", "hunter_done"}

# Kernel-pentest VM. The guest dies often by design, so the read-only tools
# (and polling) stay available in the reporting phase — otherwise a hunter that
# spent its step budget mid-exploit could not even read back the crash.
VM_HUNTER_TOOLS = {"vm_exec", "vm_status", "vm_console", "vm_flag"}
VM_SUPERVISOR_TOOLS = {"vm_start", "vm_stop", "vm_restart", "vm_reset"}

# The tail of the step budget is reserved for closing out: in this phase the
# run is only offered the tools that persist evidence and end the turn, so a
# worker that ran out of exploration budget can still report what it found.
REPORTING_TOOLS = {
    "supervisor": {"write_file", "write_todos", "complete_engagement"},
    "generator": {"write_file", "hypothesis_generate", "generator_done"},
    # vm_exec is deliberately absent: the reporting phase must not start new
    # work. Polling a command that is already running, reading the crash, and
    # confirming a flag the hunter already read all have to stay possible.
    "hunter": {"write_file", "hunt_report", "hunter_done", "vm_status", "vm_console", "vm_flag"},
}


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


def campaign_flag(mode_or_campaign, key: str) -> bool:
    """Read a boolean campaign column to decide which tools exist.

    agent.run passes the campaign row here; the older callers still pass a mode
    string. Anything unexpected — a string, a row from before the column
    existed, a dict without the key — fails closed, so a campaign that never
    asked for the feature never sees its tools.
    """
    if mode_or_campaign is None or isinstance(mode_or_campaign, str):
        return False
    for get in (lambda c: c[key], lambda c: c.get(key)):
        try:
            return bool(get(mode_or_campaign))
        except Exception:
            continue
    return False


def schemas_for(
    kind: str | None = None, mode: str | dict | None = None, phase: str | None = None
) -> list[dict]:
    schemas = list(CORE_SCHEMAS)
    resolved = _extract_mode(mode)
    if kind == "supervisor":
        # Fail closed: unknown/missing mode hides the tool, matching the
        # prompt default of continuous. Only goal campaigns offer it.
        if resolved == "goal":
            schemas += SUPERVISOR_SCHEMAS
        else:
            schemas += [s for s in SUPERVISOR_SCHEMAS if s["function"]["name"] != "complete_engagement"]
        if campaign_flag(mode, "kernel_enabled"):
            schemas += VM_SUPERVISOR_SCHEMAS
    elif kind == "fast":
        # Fast mode is the pre-hunter harness: one agent does all the work, so
        # it gets the ordinary tools and none of the delegation ones.
        if resolved == "goal":
            schemas += [s for s in SUPERVISOR_SCHEMAS if s["function"]["name"] == "complete_engagement"]
        if campaign_flag(mode, "kernel_enabled"):
            schemas += VM_SUPERVISOR_SCHEMAS + VM_HUNTER_SCHEMAS
    elif kind == "generator":
        schemas += GENERATOR_SCHEMAS
    elif kind == "hunter":
        schemas += HUNTER_SCHEMAS
        if campaign_flag(mode, "kernel_enabled"):
            schemas += VM_HUNTER_SCHEMAS
    exposed = schemas + mcp.schemas()
    if kind == "generator":
        # Presentation layer: generators are only offered read-only MCP tools.
        # execute() enforces the same set, so this filtering is never the
        # only control.
        exposed = [
            s for s in exposed
            if not mcp.is_tool(s["function"]["name"])
            or s["function"]["name"] in MCP_GENERATOR_TOOLS
        ]
    if phase == "report":
        allowed = _reporting_set(kind, mode)
        exposed = [s for s in exposed if s["function"]["name"] in allowed]
    return exposed


def _reporting_set(kind: str | None, mode: str | dict | None = None) -> set[str]:
    """Tools offered in the tail of the step budget.

    The set is static per kind, but the VM tools only exist for campaigns that
    turned the VM on — and execute() enforces `allow`, so this has to subtract
    them or a non-VM campaign could still call vm_status in the reporting phase.
    """
    allowed = set(REPORTING_TOOLS.get(kind or "", set()))
    if not campaign_flag(mode, "kernel_enabled"):
        allowed -= VM_HUNTER_TOOLS
    return allowed


def allowed_for(
    kind: str | None = None, mode: str | dict | None = None, phase: str | None = None
) -> set[str] | None:
    if phase == "report":
        return _reporting_set(kind, mode)
    resolved = _extract_mode(mode)
    kernel = campaign_flag(mode, "kernel_enabled")
    if kind == "supervisor":
        tools = CORE_TOOLS | SUPERVISOR_TOOLS
        if resolved != "goal":
            tools -= {"complete_engagement"}
        if kernel:
            tools |= VM_SUPERVISOR_TOOLS | VM_HUNTER_TOOLS
        return tools
    if kind == "fast":
        # Same enforcement as the schemas: no delegation, and the VM is the
        # solo agent's to run as well as to keep alive.
        tools = CORE_TOOLS | set(mcp.tool_names())
        if resolved == "goal":
            tools |= {"complete_engagement"}
        if kernel:
            tools |= VM_SUPERVISOR_TOOLS | VM_HUNTER_TOOLS
        return tools
    if kind == "generator":
        # Generators can't touch the VM at all: they only file hypotheses.
        return CORE_TOOLS | GENERATOR_TOOLS | set(MCP_GENERATOR_TOOLS)
    if kind == "hunter":
        tools = CORE_TOOLS | HUNTER_TOOLS | set(mcp.tool_names())
        if kernel:
            tools |= VM_HUNTER_TOOLS
        return tools
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
        + f"\n[output truncated: {len(text):,} chars total, full output saved to {path}. "
        "Do not page the whole file into context: skim it with read_file "
        "(offset/limit), or spawn a hypothesis generator to triage it and "
        "report back only the relevant lines.]"
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

    # Cost-saving nudge, not a security boundary: bash starts in the campaign
    # work directory, and absolute-path I/O outside it is otherwise allowed.
    # This only catches the common cd/pushd-away spelling mistakes.
    for match in re.finditer(
        r"(?:^|[;&|\n(]\s*)(?:cd|pushd)\s+(?:--\s+)?(?P<target>\"[^\"]*\"|'[^']*'|[^\s;&|()]+)",
        cmd,
    ):
        target = match.group("target")
        try:
            target = shlex.split(target)[0]
        except (ValueError, IndexError):
            pass
        current_dir = target in {".", "./"}
        if not current_dir:
            try:
                current_dir = Path(target).expanduser().resolve() == work.resolve()
            except (OSError, RuntimeError):
                current_dir = False
        if not current_dir:
            return (
                f"error: bash starts in the campaign work directory ({work}); "
                "prefer relative paths (this check is advisory — absolute-path "
                "tool I/O outside the work dir is not blocked)",
                False,
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
    return (
        f"[campaign work directory: {work}]\n{body}\n[exit code {proc.returncode}]{note}"
    ).strip(), proc.returncode == 0 and not capped


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


RECONNAISSANCE_TOOLS = {
    "bash",
    "read_file",
    "frank_codebase_identify",
    "frank_dns_lookup",
    "frank_net_scan",
    "frank_web_fetch",
    "frank_web_search",
    "frank_web_answer",
    "frank_method_search",
}

MCP_WORKDIR_PATH_TOOLS = {
    "frank_codebase_identify",
    "frank_scan_vulns",
    "frank_scan_secrets",
    "frank_audit_deps",
}

# MCP tools a generator worker may invoke. Read-only triage only: the active
# network scanner stays supervisor-side. Schema filtering is presentation;
# execute() enforces this same set, so exposure can never become permission.
MCP_GENERATOR_TOOLS = frozenset({
    "frank_codebase_identify",
    "frank_scan_vulns",
    "frank_scan_secrets",
    "frank_audit_deps",
    "frank_web_fetch",
    "frank_web_search",
    "frank_web_answer",
    "frank_method_search",
    "frank_dns_lookup",
})


def _reconnaissance_observed(campaign_id: int) -> bool:
    with db.db() as conn:
        rows = conn.execute(
            "SELECT tool_calls, parts FROM messages WHERE campaign_id = ? AND role = 'assistant'",
            (campaign_id,),
        ).fetchall()

    for row in rows:
        try:
            calls = json.loads(row["tool_calls"] or "[]")
        except json.JSONDecodeError:
            calls = []
        try:
            parts = json.loads(row["parts"] or "[]")
        except json.JSONDecodeError:
            parts = []
        successful_ids = {
            part.get("id")
            for part in parts
            if isinstance(part, dict)
            and part.get("type") == "tool"
            and part.get("ok") is True
        }
        for call in calls:
            if (
                isinstance(call, dict)
                and call.get("name") in RECONNAISSANCE_TOOLS
                and (call.get("ok") is True or call.get("id") in successful_ids)
            ):
                return True
    return False


def _mcp_args(name: str, args: dict, work: Path) -> dict:
    if name not in MCP_WORKDIR_PATH_TOOLS:
        return args
    value = _str_arg(args, "path", "target", "dir", "directory") or "."
    try:
        resolved = _resolve([work], value)
    except ValueError as exc:
        raise ValueError(f"{exc}; retry with a work-dir-relative path") from exc
    return {**args, "path": str(resolved)}


async def spawn_hypothesis_generator(args: dict, campaign_id: int, stop=None, **_ctx) -> tuple[str, bool]:
    from . import generator

    if not _reconnaissance_observed(campaign_id):
        return (
            "error: reconnaissance is required before spawning generators; inspect the target "
            "or codebase with bash, read_file, or an MCP reconnaissance tool first",
            False,
        )

    focus = str(args.get("focus", "")).strip()
    gen_id = generator.spawn(campaign_id, focus)
    if gen_id == 0:
        return f"error: already at the limit of {config.MAX_GENERATORS} running generators", False
    return (
        f"generator #{gen_id} started (focus: {focus or 'general'}). "
        "Read its hypotheses later with hypothesis_list."
    ), True


async def hypothesis_generate(
    args: dict, campaign_id: int, stop=None, generator_id: int | None = None, **_ctx
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


async def hypothesis_list(args: dict, campaign_id: int, stop=None, **_ctx) -> tuple[str, bool]:
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

    summary = ", ".join(
        f"{counts.get(name, 0)} {name}" for name in ("new", "working", "review", "done", "dead")
    )
    if not rows:
        return f"hypotheses: {summary} (nothing to show)", True

    lines = []
    for row in rows:
        line = f"#{row['id']} [{row['status']}] {row['title']} | {row['angle']} | {row['vuln_class']}"
        if row["result"]:
            line += f" | result: {row['result'][:160]}"
        lines.append(line[:400])
    return f"hypotheses: {summary}\n" + "\n".join(lines), True


async def hypothesis_update(args: dict, campaign_id: int, stop=None, **_ctx) -> tuple[str, bool]:
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


def _agent_parts(row) -> list:
    try:
        return json.loads(row["parts"] or "[]")
    except json.JSONDecodeError:
        return []


def _agent_snippet(row) -> str:
    text = str(row["content"] or "")
    for part in reversed(_agent_parts(row)):
        if part.get("type") == "tool":
            text = f"{part.get('name')}: {part.get('output') or ''}"
            break
        if part.get("type") == "content" and part.get("text"):
            text = part["text"]
            break
    return text.replace("\n", " ").strip()[:160]


def _format_agent_message(row) -> list[str]:
    lines = []
    for part in _agent_parts(row):
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


async def generator_status(args: dict, campaign_id: int, stop=None, **_ctx) -> tuple[str, bool]:
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
                lines.extend(_format_agent_message(row))
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
                    snippet = _agent_snippet(last)
            line = (
                f"#{gen['id']} [{gen['status']}] focus: {gen['focus'] or 'general'}"
                f" | hypotheses: {gen['hypothesis_count']}"
            )
            if snippet:
                line += f" | last: {snippet}"
            lines.append(line)
        return "generators:\n" + "\n".join(lines), True


async def complete_engagement(args: dict, campaign_id: int, stop=None, **_ctx) -> tuple[str, bool]:
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


HUNT_VERDICTS = ("confirmed", "not_vulnerable", "inconclusive", "blocked")


def _campaign_work_root(campaign_id: int) -> Path:
    return (config.WORK_DIR / str(campaign_id)).resolve()


async def hunt_report(args: dict, campaign_id: int, stop=None, **ctx) -> tuple[str, bool]:
    """Record one worker's verdict. The hypothesis goes to 'review' — the
    supervisor owns the final accept/deny, never the worker."""
    worker_id = ctx.get("worker_id")
    try:
        hypothesis_id = int(args.get("hypothesis_id") or 0)
    except (TypeError, ValueError):
        hypothesis_id = 0
    if not hypothesis_id:
        return "error: hypothesis_id is required", False
    verdict = str(args.get("verdict", "")).strip()
    if verdict not in HUNT_VERDICTS:
        return f"error: verdict must be one of {', '.join(HUNT_VERDICTS)}", False

    with db.db() as conn:
        row = conn.execute(
            "SELECT id, status, worker_id FROM hypotheses WHERE id = ? AND campaign_id = ?",
            (hypothesis_id, campaign_id),
        ).fetchone()
    if row is None:
        return f"error: hypothesis #{hypothesis_id} not found", False
    if row["status"] in ("done", "dead"):
        return f"error: hypothesis #{hypothesis_id} is already closed ({row['status']})", False
    if worker_id and row["worker_id"] not in (None, worker_id):
        return (
            f"error: hypothesis #{hypothesis_id} belongs to worker #{row['worker_id']}",
            False,
        )

    severity = str(args.get("severity", "")).strip()[:40]
    impact = str(args.get("impact", "")).strip()
    poc_path = str(args.get("poc_path", "")).strip()
    repro = str(args.get("repro", "")).strip()
    evidence = str(args.get("evidence", "")).strip()

    if verdict == "confirmed":
        if not impact:
            return "error: a confirmed finding needs an impact statement", False
        if not poc_path:
            return (
                "error: a confirmed finding needs poc_path — write a runnable PoC "
                "first and point at it with the campaign-relative path",
                False,
            )

    # A claimed PoC must actually exist, or the supervisor would be asked to
    # trust a file that is not there.
    if poc_path:
        root = _campaign_work_root(campaign_id)
        try:
            target = (root / poc_path).resolve()
        except OSError:
            target = None
        if target is None or not target.is_relative_to(root) or not target.is_file():
            return (
                f"error: poc_path {poc_path!r} is not an existing file inside the campaign "
                f"work dir (your files live under hunters/{worker_id or '?'}/ — pass that path)",
                False,
            )
        poc_path = str(target.relative_to(root))

    result = verdict
    if severity:
        result += f" [{severity}]"
    if impact:
        result += f": {impact}"
    elif evidence:
        result += f": {evidence}"

    with db.db() as conn:
        conn.execute(
            """
            INSERT INTO hunts (campaign_id, worker_id, hypothesis_id, verdict, severity,
                               impact, poc_path, repro, evidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                campaign_id,
                worker_id,
                hypothesis_id,
                verdict,
                severity,
                impact[:2000],
                poc_path,
                repro[:4000],
                evidence[:8000],
            ),
        )
        conn.execute(
            "UPDATE hypotheses SET status = 'review', result = ?, updated_at = datetime('now') WHERE id = ?",
            (result[:1000], hypothesis_id),
        )
    return (
        f"result filed for hypothesis #{hypothesis_id}: {verdict} — "
        "the supervisor will review and accept or deny it",
        True,
    )


async def hunter_done(args: dict, campaign_id: int, stop=None, **ctx) -> tuple[str, bool]:
    summary = _str_arg(args, "summary") or "worker finished"
    return f"worker finished: {summary}", True


async def spawn_hunter(args: dict, campaign_id: int, stop=None, **ctx) -> tuple[str, bool]:
    """Dispatch up to MAX_HYPOTHESES_PER_WORKER hypotheses to a hunter worker.

    Only the supervisor assigns work, so there is no claim race; the status
    check below is what stops an already reviewed/dead hypothesis from being
    sent out again.
    """
    from . import worker

    raw = args.get("hypothesis_ids")
    ids: list[int] = []
    if isinstance(raw, list):
        for value in raw:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                continue
    ids = list(dict.fromkeys(ids))
    if not ids:
        return "error: hypothesis_ids is required", False
    if len(ids) > config.MAX_HYPOTHESES_PER_WORKER:
        return (
            f"error: at most {config.MAX_HYPOTHESES_PER_WORKER} hypotheses per hunter worker",
            False,
        )
    try:
        worker_id = int(args.get("worker_id") or 0)
    except (TypeError, ValueError):
        worker_id = 0
    note = str(args.get("note", "")).strip()

    with db.db() as conn:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id, status, worker_id FROM hypotheses "
            f"WHERE campaign_id = ? AND id IN ({placeholders})",
            [campaign_id, *ids],
        ).fetchall()
    found = {row["id"]: row for row in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        return f"error: hypotheses not found in this campaign: {missing}", False

    # Dispatchable = brand new, or a reviewed hypothesis sent back to the very
    # worker that owns it for a deeper dive. Never a closed or foreign one.
    refused = []
    for i in ids:
        row = found[i]
        if row["status"] == "new":
            continue
        if worker_id and row["status"] == "review" and row["worker_id"] == worker_id:
            continue
        owner = f" (worker #{row['worker_id']})" if row["worker_id"] else ""
        refused.append(f"#{i} is {row['status']}{owner}")
    if refused:
        return "error: not dispatchable: " + ", ".join(refused), False

    started = worker.spawn(campaign_id, ids, worker_id=worker_id, note=note)
    if started == 0:
        if worker_id:
            return f"error: worker #{worker_id} is already running", False
        return f"error: already at the limit of {config.MAX_WORKERS} running hunters", False
    listed = ", ".join(f"#{i}" for i in ids)
    return (
        f"hunter worker #{started} dispatched on {len(ids)} hypothesis(es): {listed}. "
        "Read results later with worker_status.",
        True,
    )


async def worker_status(args: dict, campaign_id: int, stop=None, **ctx) -> tuple[str, bool]:
    try:
        worker_id = int(args.get("worker_id") or 0)
    except (TypeError, ValueError):
        worker_id = 0
    try:
        limit = max(1, min(int(args.get("limit") or 6), 30))
    except (TypeError, ValueError):
        limit = 6

    with db.db() as conn:
        if worker_id:
            row = conn.execute(
                "SELECT * FROM workers WHERE id = ? AND campaign_id = ?",
                (worker_id, campaign_id),
            ).fetchone()
            if row is None:
                return f"error: worker #{worker_id} not found", False
            messages = conn.execute(
                "SELECT * FROM worker_messages WHERE worker_id = ? ORDER BY id DESC LIMIT ?",
                (worker_id, limit),
            ).fetchall()
            dispatched = conn.execute(
                "SELECT id, title, status, vuln_class, result FROM hypotheses "
                "WHERE worker_id = ? ORDER BY id",
                (worker_id,),
            ).fetchall()
            results = conn.execute(
                "SELECT * FROM hunts WHERE worker_id = ? ORDER BY id", (worker_id,)
            ).fetchall()

            lines = [
                f"worker #{row['id']} [{row['status']}] {row['title'] or 'hunter'}, {row['updated_at']}"
            ]
            if row["note"]:
                lines.append(f"  note: {row['note'][:200]}")
            for message in reversed(messages):
                lines.extend(_format_agent_message(message))
            if dispatched:
                lines.append("hypotheses:")
                for hyp in dispatched:
                    line = f"  #{hyp['id']} [{hyp['status']}] {hyp['title']} ({hyp['vuln_class']})"
                    if hyp["result"]:
                        line += f" -> {hyp['result'][:200]}"
                    lines.append(line)
            for hunt in results:
                line = f"  result for hyp #{hunt['hypothesis_id']}: {hunt['verdict']}"
                if hunt["severity"]:
                    line += f" [{hunt['severity']}]"
                if hunt["poc_path"]:
                    line += f" poc: {hunt['poc_path']}"
                lines.append(line)
                if hunt["impact"]:
                    lines.append(f"    impact: {hunt['impact'][:300]}")
                if hunt["repro"]:
                    lines.append(f"    repro: {hunt['repro'][:300]}")
            return "\n".join(lines)[:12000], True

        rows = conn.execute(
            """
            SELECT w.*,
                   (SELECT count(*) FROM hypotheses h WHERE h.worker_id = w.id) AS hypothesis_count,
                   (SELECT count(*) FROM hunts x WHERE x.worker_id = w.id) AS result_count,
                   (SELECT id FROM worker_messages m WHERE m.worker_id = w.id ORDER BY id DESC LIMIT 1) AS last_message_id
            FROM workers w
            WHERE w.campaign_id = ?
            ORDER BY w.id DESC
            LIMIT 10
            """,
            (campaign_id,),
        ).fetchall()
        if not rows:
            return "no hunter workers have run for this campaign yet", True

        lines = []
        for row in rows:
            snippet = ""
            if row["last_message_id"]:
                last = conn.execute(
                    "SELECT content, parts FROM worker_messages WHERE id = ?",
                    (row["last_message_id"],),
                ).fetchone()
                if last is not None:
                    snippet = _agent_snippet(last)
            line = (
                f"#{row['id']} [{row['status']}] {row['title'] or 'hunter'}"
                f" | dispatched: {row['hypothesis_count']} | results: {row['result_count']}"
            )
            if snippet:
                line += f" | last: {snippet}"
            lines.append(line)
        return "workers:\n" + "\n".join(lines), True


async def worker_stop(args: dict, campaign_id: int, stop=None, **ctx) -> tuple[str, bool]:
    from . import worker

    try:
        worker_id = int(args.get("worker_id") or 0)
    except (TypeError, ValueError):
        worker_id = 0
    if not worker_id:
        return "error: worker_id is required", False
    if not worker.stop_worker(campaign_id, worker_id):
        return f"error: worker #{worker_id} is not running", False
    return (
        f"worker #{worker_id} is stopping; its unresolved hypotheses go back to the queue",
        True,
    )


async def vm_exec(args: dict, campaign_id: int, stop=None, **ctx) -> tuple[str, bool]:
    from . import vm as vm_mod, vmctl

    cmd = _str_arg(args, "cmd")
    if not cmd:
        return "error: cmd is required", False
    try:
        text, ok = await vm_mod.exec_command(campaign_id, cmd, args.get("timeout"))
    except vmctl.Unavailable as exc:
        return f"error: {exc}", False
    return _clip(ctx.get("work") or _work_dir(campaign_id), text, "vm-exec"), ok


async def vm_status(args: dict, campaign_id: int, stop=None, **ctx) -> tuple[str, bool]:
    from . import vm as vm_mod, vmctl

    try:
        return vm_mod.describe(await vm_mod.status(campaign_id)), True
    except vmctl.Unavailable as exc:
        return f"error: {exc}", False


async def vm_console(args: dict, campaign_id: int, stop=None, **ctx) -> tuple[str, bool]:
    from . import vm as vm_mod, vmctl

    try:
        lines = max(1, min(int(args.get("lines") or 80), 400))
    except (TypeError, ValueError):
        lines = 80
    try:
        text, ok = await vm_mod.console(campaign_id, lines)
    except vmctl.Unavailable as exc:
        return f"error: {exc}", False
    return _clip(ctx.get("work") or _work_dir(campaign_id), text, "vm-console"), ok


async def vm_flag(args: dict, campaign_id: int, stop=None, **ctx) -> tuple[str, bool]:
    from . import vm as vm_mod, vmctl

    value = _str_arg(args, "value")
    if not value:
        return "error: value is required", False
    try:
        return await vm_mod.verify_flag(campaign_id, value)
    except vmctl.Unavailable as exc:
        return f"error: {exc}", False


async def _vm_lifecycle(action: str, campaign_id: int) -> tuple[str, bool]:
    """Lifecycle always uses the campaign's own root setting.

    An agent asking for `vm_start` must not be able to hand itself root by
    passing an argument, so the argument does not exist.
    """
    from . import vm as vm_mod, vmctl

    with db.db() as conn:
        row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if row is None:
        return "error: campaign not found", False
    try:
        return await vm_mod.lifecycle(
            action, campaign_id, root=vm_mod.as_root(row), flag=vm_mod.with_flag(row)
        )
    except vmctl.Unavailable as exc:
        return f"error: {exc}", False


async def vm_start(args: dict, campaign_id: int, stop=None, **ctx) -> tuple[str, bool]:
    return await _vm_lifecycle("start", campaign_id)


async def vm_stop(args: dict, campaign_id: int, stop=None, **ctx) -> tuple[str, bool]:
    return await _vm_lifecycle("stop", campaign_id)


async def vm_restart(args: dict, campaign_id: int, stop=None, **ctx) -> tuple[str, bool]:
    return await _vm_lifecycle("restart", campaign_id)


async def vm_reset(args: dict, campaign_id: int, stop=None, **ctx) -> tuple[str, bool]:
    return await _vm_lifecycle("reset", campaign_id)


CAMPAIGN_HANDLERS = {
    "complete_engagement": complete_engagement,
    "spawn_hypothesis_generator": spawn_hypothesis_generator,
    "hypothesis_generate": hypothesis_generate,
    "hypothesis_list": hypothesis_list,
    "hypothesis_update": hypothesis_update,
    "generator_status": generator_status,
    "hunt_report": hunt_report,
    "hunter_done": hunter_done,
    "vm_exec": vm_exec,
    "vm_status": vm_status,
    "vm_console": vm_console,
    "vm_flag": vm_flag,
    "vm_start": vm_start,
    "vm_stop": vm_stop,
    "vm_restart": vm_restart,
    "vm_reset": vm_reset,
    "spawn_hunter": spawn_hunter,
    "worker_status": worker_status,
    "worker_stop": worker_stop,
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
    work: Path | None = None,
    scope: str = "",
    worker_id: int | None = None,
) -> tuple[str, bool, int]:
    """Run one tool call.

    ``work`` is the directory the file/bash tools operate in — the campaign
    root by default, or a hunter worker's own folder. ``scope`` selects which
    MCP session to use ('' is the shared one, a worker id gets its own
    browser). ``worker_id`` is passed through for the worker-side handlers.
    """
    work = work or _work_dir(campaign_id)

    # Fast mode has no subagents. The schemas and the allow-list already hide
    # the spawn tools, but a manual chat turn runs with no allow-list at all,
    # so refuse here too.
    if name in ("spawn_hunter", "spawn_hypothesis_generator"):
        with db.db() as conn:
            row = conn.execute(
                "SELECT fast_mode FROM campaigns WHERE id = ?", (campaign_id,)
            ).fetchone()
        if row is not None and row["fast_mode"]:
            return "error: fast mode runs without subagents — do the work yourself", False, 0

    # Per-agent permission layer: a run may only call the tools it was given.
    # Schema filtering is presentation only — this check is the enforcement,
    # including for MCP tools. Supervisor contexts (allow contains
    # spawn_hypothesis_generator) may use any connected MCP tool; generator
    # contexts are confined to MCP_GENERATOR_TOOLS.
    if allow is not None and name not in allow:
        if not mcp.is_tool(name):
            return f"error: tool {name} is not available in this context", False, 0
        is_supervisor = "spawn_hypothesis_generator" in allow
        if not is_supervisor and name not in MCP_GENERATOR_TOOLS:
            return f"error: tool {name} is not available in this context", False, 0

    if mcp.is_tool(name):
        started = time.monotonic()
        try:
            mcp_args = _mcp_args(name, args or {}, work)
        except Exception as exc:
            return f"error: {exc}", False, 0
        text, ok = await mcp.call(name, mcp_args, scope=scope)
        ms = int((time.monotonic() - started) * 1000)
        return _clip(work, text, name), ok, ms

    if name in CAMPAIGN_HANDLERS:
        started = time.monotonic()
        try:
            text, ok = await CAMPAIGN_HANDLERS[name](
                args or {}, campaign_id, stop, work=work, worker_id=worker_id
            )
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

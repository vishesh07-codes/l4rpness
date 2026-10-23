import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from . import config

log = logging.getLogger("l4rpness")

# Bound for one MCP tool call. Frank's heaviest tools (semgrep/specter)
# run up to 600s internally; the harness must not hang a chain on them.
try:
    MCP_CALL_TIMEOUT = float(getattr(config, "HTTP_TIMEOUT", 30) or 30)
except (TypeError, ValueError):
    MCP_CALL_TIMEOUT = 30.0

# Chromium's stock UA string says "HeadlessChrome", which WAFs block on
# sight; override it with the same Chrome string minus that marker. The
# version tracks the chromium build that the pinned @playwright/mcp in the
# Dockerfile installs (`playwright-mcp` reports which with `browser.version`).
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.8059.12 Safari/537.36"
)


def defaults() -> list[dict]:
    """The built-in MCP servers, built fresh so paths follow the live data
    dir (the browser's output dir must match where start() creates it)."""
    return [
        {
            "name": "frank",
            "command": ["python", "/frank/server.py"],
            "tools": [
                "dns_lookup",
                "web_fetch",
                "web_search",
                "web_answer",
                "method_search",
                "codebase_identify",
                "scan_vulns",
                "scan_secrets",
                "audit_deps",
                "net_scan",
            ],
        },
        {
            # headless Chromium the agent drives through the playwright_*
            # tools. --no-sandbox is required as root; --isolated keeps the
            # profile in memory so nothing leaks between campaigns; the rest
            # is the difference from curl: page JavaScript runs, sessions
            # stick, and the traffic looks like Chrome. An empty tool list
            # exposes every tool the server offers.
            "name": "playwright",
            "command": [
                "playwright-mcp",
                "--headless",
                "--browser",
                "chromium",
                "--no-sandbox",
                "--isolated",
                "--ignore-https-errors",
                "--file-paths",
                "absolute",
                "--user-agent",
                BROWSER_UA,
                "--output-dir",
                str(config.DATA_DIR / "browser"),
            ],
            "tools": [],
        },
    ]


_sessions: dict[str, Any] = {}
_stacks: dict[str, AsyncExitStack] = {}
_catalog: dict[str, list] = {}
_routes: dict[str, tuple[str, str]] = {}
_errors: dict[str, str] = {}
# Only reconnect/teardown operations need to be serialized. Normal tool
# execution should remain concurrent across a shared session so one stalled
# request does not block unrelated work on the same server.
_reconnect_locks: dict[str, asyncio.Lock] = {}
# Backward-compatible alias used by the runtime-bound tests and any callers
# still expecting the older single registry name.
_locks: dict[str, asyncio.Lock] = _reconnect_locks


def _session_key(server: str, scope: str = "") -> str:
    """Session registry key. '' is the shared session every agent uses; a
    worker scope gets a private instance of the stateful servers."""
    return f"{server}#{scope}" if scope else server


def _spec_for(server: str) -> dict | None:
    return next((s for s in definitions() if s.get("name") == server), None)


def _is_scoped(spec: dict) -> bool:
    """Servers that carry per-agent state need their own process per scope:
    the browser keeps cookies and navigation state, so two hunters sharing one
    would stomp each other's session and produce false negatives."""
    command = spec.get("command") or []
    if not command:
        return False
    return os.path.basename(str(command[0])).startswith("playwright")


def tool_names() -> set[str]:
    return set(_routes)


def _reconnect_lock_for(server: str) -> asyncio.Lock:
    lock = _reconnect_locks.get(server)
    if lock is None:
        lock = asyncio.Lock()
        _reconnect_locks[server] = lock
    return lock


def _lock_for(server: str) -> asyncio.Lock:
    """Backward-compatible alias for reconnect serialization."""
    return _reconnect_lock_for(server)


async def _close_server(key: str) -> None:
    """Drop a suspect session and its stack. Used after timeouts and
    cancellations, where the transport state can no longer be trusted."""
    stack = _stacks.pop(key, None)
    if stack is not None:
        try:
            await stack.aclose()
        except Exception:
            pass
    _sessions.pop(key, None)


def _file():
    return config.DATA_DIR / "mcp.json"


def definitions() -> list[dict]:
    if not _file().is_file():
        return defaults()

    try:
        data = json.loads(_file().read_text())
    except (OSError, json.JSONDecodeError):
        log.warning("mcp.json is not valid json: %s", _file())
        return []

    servers = data.get("servers", [])
    return [server for server in servers if isinstance(server, dict) and server.get("name")]


def save(servers: list[dict]) -> None:
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps({"servers": servers}, indent=2) + "\n")


async def _connect(spec: dict, key: str | None = None) -> ClientSession:
    key = key or spec["name"]
    stack = AsyncExitStack()
    command = spec.get("command")

    if command:
        # fail before spawning anything if a referenced file is missing, so we
        # do not leave a half-open stdio client behind (used to raise a cancel
        # scope error on shutdown). Frank lives at /frank inside the image.
        missing = next(
            (arg for arg in command[1:] if arg.startswith("/") and not os.path.exists(arg)),
            None,
        )
        if missing:
            raise ValueError(f"server file not found: {missing}")

        env = {**os.environ, **(spec.get("env") or {})}
        params = StdioServerParameters(command=command[0], args=list(command[1:]), env=env)
        read, write = await stack.enter_async_context(stdio_client(params))
    elif spec.get("url"):
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        http_client = await stack.enter_async_context(
            create_mcp_http_client(headers=spec.get("headers") or None)
        )
        read, write = await stack.enter_async_context(
            streamable_http_client(spec["url"], http_client=http_client)
        )
    else:
        raise ValueError("server needs a command or url")

    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    _stacks[key] = stack
    return session


async def start() -> None:
    # the playwright server writes auto-named screenshots and traces into
    # this directory; it must exist before _connect's guard checks the
    # absolute --output-dir argument
    (config.DATA_DIR / "browser").mkdir(parents=True, exist_ok=True)

    for spec in definitions():
        name = spec["name"]
        log.info("mcp server %s: starting %s", name, spec.get("command") or spec.get("url"))
        try:
            # Bound discovery: a hanging server must not stall lifespan.
            session = await asyncio.wait_for(_connect(spec), timeout=MCP_CALL_TIMEOUT)
            listing = await asyncio.wait_for(session.list_tools(), timeout=MCP_CALL_TIMEOUT)
        except asyncio.TimeoutError:
            await _close_server(name)
            _errors[name] = f"startup timed out after {MCP_CALL_TIMEOUT:g}s"
            log.warning("mcp server %s startup timed out", name)
            continue
        except Exception as exc:
            _errors[name] = str(exc)[:200]
            log.warning("mcp server %s failed to start: %s", name, exc)
            continue

        _sessions[name] = session
        _catalog[name] = list(listing.tools)
        _errors.pop(name, None)
        log.info("mcp server %s connected with %d tools", name, len(_catalog[name]))

    schemas()


def schemas() -> list[dict]:
    _routes.clear()
    exposed = []

    for spec in definitions():
        name = spec["name"]
        allowed = spec.get("tools") or []
        for tool in _catalog.get(name, []):
            if allowed and tool.name not in allowed:
                continue
            key = f"{name}_{tool.name}"
            _routes[key] = (name, tool.name)
            exposed.append(
                {
                    "type": "function",
                    "function": {
                        "name": key,
                        "description": (tool.description or f"{tool.name} on {name}")[:1024],
                        "parameters": tool.inputSchema or {"type": "object", "properties": {}},
                    },
                }
            )

    return exposed


def is_tool(name: str) -> bool:
    return name in _routes


async def _reconnect(key: str) -> bool:
    """Reconnect one session key after a failed call. Bounded and serialized
    by the caller-held lock. Returns True if usable. Works for both the shared
    session (key == server name) and a worker's private one (server#scope)."""
    server = key.split("#", 1)[0]
    spec = _spec_for(server)
    if spec is None:
        return False
    await _close_server(key)
    try:
        session = await asyncio.wait_for(_connect(spec, key), timeout=MCP_CALL_TIMEOUT)
        listing = await asyncio.wait_for(session.list_tools(), timeout=MCP_CALL_TIMEOUT)
    except asyncio.TimeoutError:
        _errors[key] = f"reconnect timed out after {MCP_CALL_TIMEOUT:g}s"
        log.warning("mcp session %s reconnect timed out", key)
        return False
    except Exception as exc:
        _errors[key] = str(exc)[:200]
        log.warning("mcp session %s reconnect failed: %s", key, exc)
        return False
    _sessions[key] = session
    _catalog[server] = list(listing.tools)
    _errors.pop(key, None)
    schemas()
    return True


async def open_scope(scope: str) -> None:
    """Start a private session for each stateful server under this scope.

    Must be awaited from the task that will use it and closed with
    close_scope() from that same task: every stdio session nests an anyio
    cancel scope, which cannot be exited from a different task.
    """
    if not scope:
        return
    for spec in definitions():
        if not _is_scoped(spec):
            continue
        key = _session_key(spec["name"], scope)
        if key in _sessions:
            continue
        try:
            session = await asyncio.wait_for(_connect(spec, key), timeout=MCP_CALL_TIMEOUT)
            await asyncio.wait_for(session.list_tools(), timeout=MCP_CALL_TIMEOUT)
        except Exception as exc:
            _errors[key] = str(exc)[:200]
            log.warning("mcp scope %s: %s failed to start: %s", scope, spec["name"], exc)
            await _close_server(key)
            continue
        _sessions[key] = session
        _errors.pop(key, None)
        log.info("mcp scope %s: %s ready", scope, spec["name"])


async def close_scope(scope: str) -> None:
    """Close the private sessions opened for this scope (same-task rule)."""
    if not scope:
        return
    suffix = f"#{scope}"
    keys = {key for key in (*_stacks, *_sessions) if key.endswith(suffix)}
    for key in keys:
        await _close_server(key)


def _is_error_payload(text: str) -> bool:
    """Frank servers return '{"error": ...}' as a normal (isError=False)
    string. Treat that as failure so the model retries instead of citing it."""
    stripped = text.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return False
    try:
        payload = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(payload, dict) and "error" in payload and len(payload) <= 2


async def call(name: str, args: dict, scope: str = "") -> tuple[str, bool]:
    target = _routes.get(name)
    if target is None:
        return f"error: unknown mcp tool {name}", False

    server, tool = target
    key = _session_key(server, scope)
    session = _sessions.get(key)
    if session is None:
        # A stateful server under a worker scope gets its own process, opened
        # lazily on first use so a worker that never browses costs nothing.
        spec = _spec_for(server)
        if scope and spec is not None and _is_scoped(spec):
            await open_scope(scope)
            session = _sessions.get(key)
        if session is None:
            session = _sessions.get(server)
        if session is None:
            reason = _errors.get(key) or _errors.get(server, "startup failed")
            return f"error: mcp server {server} is not connected: {reason}", False

    try:
        result = await asyncio.wait_for(
            session.call_tool(tool, arguments=args or {}), timeout=MCP_CALL_TIMEOUT
        )
    except asyncio.TimeoutError:
        # Only the reconnect/teardown path is serialized. If another call has
        # already replaced this session, do not tear it down out from under it.
        async with _reconnect_lock_for(key):
            if _sessions.get(key) is session:
                await _close_server(key)
                _errors[key] = f"call timed out after {MCP_CALL_TIMEOUT:g}s"
        return (
            f"error: mcp tool {name} timed out after {MCP_CALL_TIMEOUT:g}s; "
            "retry once, then fall back to bash/read_file",
            False,
        )
    except asyncio.CancelledError:
        async with _reconnect_lock_for(key):
            if _sessions.get(key) is session:
                await _close_server(key)
        raise
    except Exception as exc:
        # One reconnect + retry before failing: a restarted frank server must
        # not wedge every later chain. Serialize just this transition.
        log.warning("mcp call %s failed, reconnecting %s: %s", name, key, exc)
        async with _reconnect_lock_for(key):
            current = _sessions.get(key)
            if current is None or current is session:
                if not await _reconnect(key):
                    return (
                        f"error: mcp call failed: {exc}; "
                        "retry once, then fall back to bash/read_file",
                        False,
                    )
            session = _sessions.get(key)
            if session is None:
                return f"error: mcp server {server} unreachable", False
        try:
            result = await asyncio.wait_for(
                session.call_tool(tool, arguments=args or {}), timeout=MCP_CALL_TIMEOUT
            )
        except asyncio.TimeoutError:
            return (
                f"error: mcp tool {name} timed out after {MCP_CALL_TIMEOUT:g}s; "
                "retry once, then fall back to bash/read_file",
                False,
            )
        except Exception as exc2:
            return (
                f"error: mcp call failed: {exc2}; "
                "retry once, then fall back to bash/read_file",
                False,
            )

    text = "\n".join(
        block.text for block in (result.content or []) if getattr(block, "type", "") == "text"
    ).strip() or "(no output)"
    if result.isError or _is_error_payload(text):
        return f"{text}\n(retry with corrected arguments, or fall back to bash/read_file)", False
    return text, True


async def stop() -> None:
    # every stdio session nests an anyio cancel scope, so they must be
    # exited in reverse order of entry; closing the first one first corrupts
    # the rest of the teardown (RuntimeError, then stray CancelledError).
    # Scoped (worker) sessions are skipped on purpose: they were entered in
    # the worker's task and can only be exited there.
    for key, stack in reversed(list(_stacks.items())):
        if "#" in key:
            continue
        try:
            await stack.aclose()
        except Exception:
            log.warning("closing mcp server %s failed", key)

    _stacks.clear()
    _sessions.clear()
    _catalog.clear()
    _routes.clear()
    _reconnect_locks.clear()


def status() -> dict[str, Any]:
    servers = []
    for spec in definitions():
        name = spec["name"]
        servers.append(
            {
                "name": name,
                "command": spec.get("command") or [],
                "url": spec.get("url") or "",
                "env": spec.get("env") or {},
                "headers": spec.get("headers") or {},
                "enabled": spec.get("tools") or [],
                "connected": name in _sessions,
                "error": _errors.get(name, ""),
                "available": [tool.name for tool in _catalog.get(name, [])],
            }
        )
    return {"servers": servers}

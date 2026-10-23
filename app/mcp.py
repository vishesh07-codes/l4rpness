import json
import logging
import os
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from . import config

log = logging.getLogger("l4rpness")

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


async def _connect(spec: dict) -> ClientSession:
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
    _stacks[spec["name"]] = stack
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
            session = await _connect(spec)
            listing = await session.list_tools()
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


async def call(name: str, args: dict) -> tuple[str, bool]:
    target = _routes.get(name)
    if target is None:
        return f"error: unknown mcp tool {name}", False

    server, tool = target
    session = _sessions.get(server)
    if session is None:
        reason = _errors.get(server, "startup failed")
        return f"error: mcp server {server} is not connected: {reason}", False

    try:
        result = await session.call_tool(tool, arguments=args or {})
    except Exception as exc:
        return f"error: mcp call failed: {exc}", False

    text = "\n".join(
        block.text for block in (result.content or []) if getattr(block, "type", "") == "text"
    ).strip()
    return text or "(no output)", not result.isError


async def stop() -> None:
    # every stdio session nests an anyio cancel scope, so they must be
    # exited in reverse order of entry; closing the first one first corrupts
    # the rest of the teardown (RuntimeError, then stray CancelledError)
    for name, stack in reversed(list(_stacks.items())):
        try:
            await stack.aclose()
        except Exception:
            log.warning("closing mcp server %s failed", name)

    _stacks.clear()
    _sessions.clear()
    _catalog.clear()
    _routes.clear()


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

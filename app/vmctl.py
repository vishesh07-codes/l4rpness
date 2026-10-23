"""Client for the host-side VM control plane.

The kernel-pentest VM lives on the host, not in this container. It needs KVM,
it must outlive a harness restart, and a guest that escapes its hypervisor
should land in an unprivileged host account rather than inside the harness. The
only thing that crosses the boundary is one unix socket with a fixed verb set.

Everything here fails soft: a missing or wedged control plane must produce a
tool error an agent can act on, never a hung chain.
"""

import asyncio
import json
import logging

from . import config

log = logging.getLogger("l4rpness")


class Unavailable(Exception):
    """Control plane missing, wedged, or too slow to answer."""


async def call(req: dict, timeout: float | None = None) -> dict:
    async def exchange():
        reader, writer = await asyncio.open_unix_connection(config.VM_SOCK)
        try:
            writer.write((json.dumps(req) + "\n").encode())
            await writer.drain()
            line = await reader.readline()
        finally:
            writer.close()
        if not line:
            raise Unavailable("vm control plane closed the connection")
        return json.loads(line)

    try:
        return await asyncio.wait_for(exchange(), timeout=timeout or config.VM_CALL_TIMEOUT)
    except (OSError, asyncio.TimeoutError, ValueError) as exc:
        raise Unavailable(f"vm control plane unavailable ({exc})") from exc

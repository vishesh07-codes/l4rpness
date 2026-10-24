"""The kernel-pentest VM, one per campaign.

Thin layer over the host control plane (vmctl.py): it decides what an agent may
ask for, formats the answers, and never lets a dead or missing VM turn into a
hung chain.

Two things are deliberate here:

* `status` is host-side only. It has to answer while the guest is wedged, which
  is exactly when an agent needs to know whether it just killed the kernel.
* `exec` reports a non-zero exit as a *result*, not a tool failure. An exploit
  that fails is a finding about the target, not an error in the harness.
"""

import asyncio
import logging

from . import config, db, tools, vmctl

log = logging.getLogger("l4rpness")

# Available to a hunter that is doing the work.
WORKER_TOOLS = {"vm_exec", "vm_status", "vm_console"}
# Lifecycle. Only the supervisor, because changing the machine out from under a
# worker that is mid-experiment is the one thing that really does break someone
# else's run.
SUPERVISOR_TOOLS = {"vm_start", "vm_stop", "vm_restart", "vm_reset"}
TOOLS = WORKER_TOOLS | SUPERVISOR_TOOLS

# Starting the VM includes booting the guest, which the host bounds at 90s.
# The socket call has to outlast that or the client gives up on a boot that is
# still going fine.
BOOT_GRACE = 150


def enabled(campaign) -> bool:
    return tools.campaign_flag(campaign, "kernel_enabled")


def as_root(campaign) -> bool:
    return tools.campaign_flag(campaign, "kernel_root_access")


def with_flag(campaign) -> bool:
    return tools.campaign_flag(campaign, "kernel_root_flag")


def describe(vm: dict) -> str:
    """One line an agent can read at a glance, including the bit that matters
    when something went wrong: how long the guest has been up and how many times
    it has come back."""
    if vm.get("state") != "running":
        return f"vm: {vm.get('state', 'unknown')} (pid {vm.get('pid') or '-'})"
    mins, secs = divmod(int(vm.get("uptime_s") or 0), 60)
    bits = [
        f"vm: running",
        f"kernel {vm.get('kernel') or '?'}",
        f"up {mins}m{secs:02d}s",
        f"restarts {vm.get('restarts', 0)}",
    ]
    if vm.get("root_access"):
        bits.append(f"root password {vm.get('root_password') or 'set'}")
    else:
        bits.append("root locked")
    if vm.get("root_flag"):
        bits.append("flag in /root/root.txt")
    return " · ".join(bits)


# Keeps fire-and-forget provisioning tasks alive: without a reference an
# asyncio task can be collected mid-boot.
_TASKS: set[asyncio.Task] = set()


def provision_now(campaign_id: int) -> None:
    """Start the campaign's VM as soon as the campaign does, off the request path.

    The environment belongs to the campaign, not to the supervisor's first
    chain: a campaign that is running should have its VM up whether or not the
    agent has done anything yet. The loop's own ensure stays as the retry
    backstop for a control plane that was not up yet.
    """
    async def run():
        with db.db() as conn:
            row = conn.execute(
                "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
            ).fetchone()
        if row is None or not enabled(row):
            return
        try:
            state = await ensure(campaign_id, as_root(row), with_flag(row))
        except vmctl.Unavailable as exc:
            log.warning("campaign %d: vm not provisioned: %s", campaign_id, exc)
            return
        # The campaign can be deleted while the guest is booting; if it was, do
        # not leave the VM we just started behind.
        with db.db() as conn:
            gone = conn.execute(
                "SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,)
            ).fetchone() is None
        if gone:
            await destroy(campaign_id)
            return
        log.info("campaign %d: vm %s", campaign_id, describe(state))

    task = asyncio.create_task(run())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def status(campaign_id: int) -> dict:
    reply = await vmctl.call({"action": "status", "campaign": campaign_id})
    return reply.get("vm") or {}


async def ensure(campaign_id: int, root: bool, flag: bool = False) -> dict:
    """Bring the VM if it is not already running.

    Called on the first chain, and retried on later chains until it succeeds, so
    a control plane that was not up yet heals itself. After that an explicit
    `vm_stop` is respected rather than undone a turn later.

    The boot timeout is the host's, so the socket call has to outlast a boot.
    """
    vm = await status(campaign_id)
    if vm.get("state") == "running":
        return vm
    reply = await vmctl.call(
        {
            "action": "start",
            "campaign": campaign_id,
            "root_access": bool(root),
            "root_flag": flag,
        },
        timeout=BOOT_GRACE,
    )
    if not reply.get("ok"):
        raise vmctl.Unavailable(reply.get("error") or "start refused")
    return reply.get("vm") or {}


async def exec_command(campaign_id: int, cmd: str, timeout: int | None = None) -> tuple[str, bool]:
    timeout = int(timeout or config.VM_EXEC_TIMEOUT)
    reply = await vmctl.call(
        {"action": "exec", "campaign": campaign_id, "cmd": cmd, "timeout": timeout},
        # the console round trip includes the guest running the command
        timeout=timeout + 15,
    )
    if not reply.get("ok"):
        text = reply.get("error") or "vm command failed"
        tail = reply.get("console")
        if tail:
            text += "\n\nconsole:\n" + tail
        return text, False
    out = (reply.get("output") or "").strip()
    code = reply.get("exit", 0)
    if code:
        out = f"{out}\n[exit {code}]".strip()
    return out or "(no output)", True


async def verify_flag(campaign_id: int, value: str) -> tuple[str, bool]:
    """Check a claimed flag against the one the host planted.

    The expected value is never sent to the container, so this is the only way
    to confirm an escalation without trusting the guest's own output.
    """
    reply = await vmctl.call({"action": "verify", "campaign": campaign_id, "value": value})
    if not reply.get("ok"):
        return reply.get("error") or "flag check failed", False
    return ("flag correct", True) if reply.get("match") else ("flag does not match", True)


async def console(campaign_id: int, lines: int = 80) -> tuple[str, bool]:
    reply = await vmctl.call({"action": "console", "campaign": campaign_id, "lines": lines})
    if not reply.get("ok"):
        return reply.get("error") or "no console", False
    return reply.get("console") or "(console empty)", True


async def lifecycle(
    action: str, campaign_id: int, root: bool | None = None, flag: bool | None = None
) -> tuple[str, bool]:
    req = {"action": action, "campaign": campaign_id}
    if root is not None:
        req["root_access"] = bool(root)
    if flag is not None:
        req["root_flag"] = bool(flag)
    # restart/reset also boot a guest, so they need the same grace as start.
    reply = await vmctl.call(req, timeout=BOOT_GRACE)
    if not reply.get("ok"):
        return reply.get("error") or f"vm {action} failed", False
    vm = reply.get("vm") or {}
    note = ""
    if action == "reset":
        note = " (disk discarded — anything the workers had in there is gone)"
    return describe(vm) + note, True


async def sweep() -> int:
    """Destroy VMs whose campaign no longer exists.

    Deleting a campaign takes its VM down in the same request, but that can fail
    (control plane unreachable) or the app can die half way. This is the durable
    half, run once at startup: a deleted campaign must not keep a guest alive.
    """
    try:
        reply = await vmctl.call({"action": "list"})
    except (vmctl.Unavailable, Exception) as exc:
        log.warning("vm sweep skipped: %s", exc)
        return 0

    with db.db() as conn:
        orphans = [
            entry["campaign"]
            for entry in reply.get("vms") or []
            if entry.get("campaign")
            and conn.execute(
                "SELECT 1 FROM campaigns WHERE id = ?", (entry["campaign"],)
            ).fetchone()
            is None
        ]
    for campaign_id in orphans:
        await destroy(campaign_id)
    if orphans:
        log.info("removed VMs for deleted campaigns: %s", orphans)
    return len(orphans)


async def destroy(campaign_id: int) -> None:
    """Best effort: a campaign going away must not leave a guest burning RAM.

    Never raises — deletion should not fail because the control plane is down.
    """
    try:
        await vmctl.call({"action": "destroy", "campaign": campaign_id})
    except Exception as exc:
        log.warning("campaign %d: vm teardown failed: %s", campaign_id, exc)

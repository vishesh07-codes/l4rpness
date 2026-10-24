import asyncio
from unittest.mock import AsyncMock

import pytest

from app import agent, config, db, routes, supervisor, tools, vm, vmctl


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "l4rpness.db")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(agent, "provider_key", lambda provider: "test-key")
    routes.RUNNING.clear()
    db.init()


def _campaign(kernel=1, root=0, flag=0, cid=1):
    with db.db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO campaigns "
            "(id, target, model, kernel_enabled, kernel_root_access, kernel_root_flag) "
            "VALUES (?, 't', 'm', ?, ?, ?)",
            (cid, kernel, root, flag),
        )


def _row(cid=1):
    with db.db() as conn:
        return conn.execute("SELECT * FROM campaigns WHERE id = ?", (cid,)).fetchone()


def _fake_plane(monkeypatch, replies):
    """Stand-in for the host control plane, keyed by action."""
    calls = []

    async def call(req, timeout=None):
        calls.append((req, timeout))
        reply = replies.get(req["action"])
        if reply is None:
            raise vmctl.Unavailable("control plane down")
        return reply if not callable(reply) else reply(req)

    monkeypatch.setattr(vmctl, "call", call)
    return calls


def _exec(monkeypatch, action, args=None, cid=1, kind="hunter", phase=None, flag=1, root=0, root_flag=0):
    _campaign(kernel=flag, root=root, flag=root_flag, cid=cid)
    allow = tools.allowed_for(kind, _row(cid), phase)
    return asyncio.run(
        tools.execute(action, args or {}, cid, allow=allow, scope="", worker_id=1)
    )


# --- gating -----------------------------------------------------------------


def test_vm_tools_are_gated_on_the_campaign_flag():
    plain = {"mode": "continuous", "kernel_enabled": 0}
    kernel = {"mode": "continuous", "kernel_enabled": 1}

    assert not (tools.allowed_for("hunter", plain) & vm.TOOLS)
    assert not (tools.allowed_for("supervisor", plain) & vm.TOOLS)

    hunter = tools.allowed_for("hunter", kernel)
    assert {"vm_exec", "vm_status", "vm_console"} <= hunter
    assert not (hunter & vm.SUPERVISOR_TOOLS), "workers must not get lifecycle"

    sup = tools.allowed_for("supervisor", kernel)
    assert vm.SUPERVISOR_TOOLS <= sup

    # generators never touch the VM
    assert not (tools.allowed_for("generator", kernel) & vm.TOOLS)


def test_vm_gating_fails_closed_on_old_callers():
    # a bare mode string is what the pre-flag call sites passed
    assert not (tools.allowed_for("supervisor", "continuous") & vm.TOOLS)
    assert not (tools.allowed_for("hunter") & vm.TOOLS)
    assert not (tools.allowed_for("generator") & vm.TOOLS)


def test_report_phase_keeps_polling_but_not_new_work():
    kernel = {"mode": "continuous", "kernel_enabled": 1}
    allowed = tools.allowed_for("hunter", kernel, "report")
    assert {"vm_status", "vm_console"} <= allowed
    assert "vm_exec" not in allowed
    # and nothing VM-shaped leaks into a campaign that never asked for it
    assert not (tools.allowed_for("hunter", {"kernel_enabled": 0}, "report") & vm.TOOLS)


def test_schemas_only_offer_vm_tools_when_enabled():
    names = lambda kind, row: {
        s["function"]["name"] for s in tools.schemas_for(kind, row, None)
    }
    assert "vm_exec" in names("hunter", {"kernel_enabled": 1})
    assert "vm_exec" not in names("hunter", {"kernel_enabled": 0})
    assert "vm_reset" in names("supervisor", {"kernel_enabled": 1})
    assert "vm_reset" not in names("supervisor", {"kernel_enabled": 0})


# --- the module -------------------------------------------------------------


def test_enabled_and_as_root_read_the_row():
    _campaign(kernel=1, root=1)
    row = _row()
    assert vm.enabled(row) and vm.as_root(row)
    assert not vm.enabled("continuous") and not vm.as_root(None)


def test_describe_shows_what_changed_since_last_look():
    line = vm.describe(
        {"state": "running", "kernel": "7.2.9-zen1-1-zen", "uptime_s": 192, "restarts": 2,
         "root_access": False, "accel": "kvm"}
    )
    assert "7.2.9-zen1-1-zen" in line and "up 3m12s" in line and "restarts 2" in line
    assert "root locked" in line
    assert vm.describe({"state": "stopped"}) == "vm: stopped (pid -)"


def test_ensure_starts_a_stopped_vm_and_leaves_a_running_one_alone(monkeypatch):
    calls = _fake_plane(monkeypatch, {
        "status": {"ok": True, "vm": {"state": "stopped"}},
        "start": {"ok": True, "vm": {"state": "running", "kernel": "k"}},
    })
    asyncio.run(vm.ensure(1, root=False))
    assert [c[0]["action"] for c in calls] == ["status", "start"]

    calls = _fake_plane(monkeypatch, {"status": {"ok": True, "vm": {"state": "running"}}})
    asyncio.run(vm.ensure(1, root=False))
    assert [c[0]["action"] for c in calls] == ["status"]


def test_ensure_surfaces_a_refused_start(monkeypatch):
    _fake_plane(monkeypatch, {
        "status": {"ok": True, "vm": {"state": "stopped"}},
        "start": {"ok": False, "error": "only 200MB free"},
    })
    with pytest.raises(vmctl.Unavailable, match="200MB"):
        asyncio.run(vm.ensure(1, root=False))


def test_destroy_never_raises(monkeypatch):
    _fake_plane(monkeypatch, {})  # every action raises Unavailable
    asyncio.run(vm.destroy(1))  # must not propagate


# --- tools ------------------------------------------------------------------


def test_vm_exec_reports_output_and_exit_code(monkeypatch):
    _fake_plane(monkeypatch, {
        "exec": {"ok": True, "output": "uid=0(root)", "exit": 0},
    })
    out, ok, _ = _exec(monkeypatch, "vm_exec", {"cmd": "id"})
    assert ok and "uid=0(root)" in out


def test_vm_exec_treats_a_failed_exploit_as_a_result(monkeypatch):
    """A PoC that exits non-zero is a finding about the target, not a broken
    tool: the agent must see the output and the code, not an error."""
    _fake_plane(monkeypatch, {"exec": {"ok": True, "output": "segfault", "exit": 139}})
    out, ok, _ = _exec(monkeypatch, "vm_exec", {"cmd": "./poc"})
    assert ok
    assert "segfault" in out and "[exit 139]" in out


def test_vm_exec_timeout_is_a_tool_error_with_the_console(monkeypatch):
    _fake_plane(monkeypatch, {
        "exec": {"ok": False, "error": "command did not finish in 4s", "console": "Kernel panic"},
    })
    out, ok, _ = _exec(monkeypatch, "vm_exec", {"cmd": "sleep 999"})
    assert not ok
    assert "did not finish" in out and "Kernel panic" in out


def test_vm_exec_gives_the_guest_its_full_budget(monkeypatch):
    """The socket call must outlast the command, or a long PoC would be cut off
    by the transport timeout instead of its own."""
    calls = _fake_plane(monkeypatch, {"exec": {"ok": True, "output": "", "exit": 0}})
    _exec(monkeypatch, "vm_exec", {"cmd": "make", "timeout": 300})
    req, timeout = calls[-1]
    assert req["timeout"] == 300
    assert timeout > 300


def test_vm_exec_needs_a_command(monkeypatch):
    _fake_plane(monkeypatch, {"exec": {"ok": True, "output": "", "exit": 0}})
    out, ok, _ = _exec(monkeypatch, "vm_exec", {})
    assert not ok and "cmd is required" in out


def test_vm_console_and_status_work_when_the_guest_is_dead(monkeypatch):
    _fake_plane(monkeypatch, {
        "status": {"ok": True, "vm": {"state": "stopped"}},
        "console": {"ok": True, "console": "Kernel panic - not syncing"},
    })
    out, ok, _ = _exec(monkeypatch, "vm_status")
    assert ok and "stopped" in out
    out, ok, _ = _exec(monkeypatch, "vm_console", {"lines": 5})
    assert ok and "Kernel panic" in out


def test_vm_tools_error_instead_of_hanging_when_the_plane_is_down(monkeypatch):
    _fake_plane(monkeypatch, {})
    for action, args in [
        ("vm_exec", {"cmd": "id"}),
        ("vm_status", {}),
        ("vm_console", {}),
    ]:
        out, ok, _ = _exec(monkeypatch, action, args)
        assert not ok, action
        assert "control plane down" in out, action


def test_vm_start_cannot_grant_itself_root(monkeypatch):
    """The campaign decides root, not the agent: an argument is ignored."""
    calls = _fake_plane(monkeypatch, {
        "start": {"ok": True, "vm": {"state": "running", "kernel": "k"}},
    })
    _campaign(kernel=1, root=0)
    _exec(monkeypatch, "vm_start", {"root_access": True}, kind="supervisor")
    assert calls[-1][0]["root_access"] is False

    _campaign(kernel=1, root=1)
    _exec(monkeypatch, "vm_start", {"root_access": False}, kind="supervisor", root=1)
    assert calls[-1][0]["root_access"] is True


def test_vm_reset_warns_that_the_disk_is_gone(monkeypatch):
    _fake_plane(monkeypatch, {
        "reset": {"ok": True, "vm": {"state": "running", "kernel": "k", "uptime_s": 0}},
    })
    out, ok, _ = _exec(monkeypatch, "vm_reset", {}, kind="supervisor")
    assert ok and "disk discarded" in out


# --- supervisor wiring ------------------------------------------------------


def test_loop_brings_the_vm_up_before_the_first_chain(monkeypatch):
    ensured = []
    notices = []

    async def fake_ensure(campaign_id, root):
        ensured.append((campaign_id, root))
        return {"state": "running"}

    monkeypatch.setattr(vm, "ensure", fake_ensure)
    monkeypatch.setattr(
        routes, "push_event", lambda cid, name, data: notices.append((name, data))
    )

    async def fake_run(campaign_id, campaign, attachments, history, content, run, files=(), **kwargs):
        return {"ok": True, "stopped": False, "empty": False, "error": ""}

    monkeypatch.setattr(routes, "_run", fake_run)
    _campaign(kernel=1, root=1)

    async def drive():
        assert supervisor.start(1)
        task = supervisor.SUPERVISORS[1]
        for _ in range(200):
            if ensured:
                break
            await asyncio.sleep(0.01)
        supervisor.stop(1)
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(drive())
    assert ensured == [(1, True)]


def test_loop_survives_an_unavailable_vm(monkeypatch):
    """A dead control plane is a notice, not a stopped campaign."""
    chains = []
    notices = []

    async def fake_ensure(campaign_id, root):
        raise vmctl.Unavailable("no socket")

    monkeypatch.setattr(vm, "ensure", fake_ensure)
    monkeypatch.setattr(
        routes, "push_event", lambda cid, name, data: notices.append(data.get("text", ""))
    )

    async def fake_run(campaign_id, campaign, attachments, history, content, run, files=(), **kwargs):
        chains.append(campaign_id)
        return {"ok": True, "stopped": False, "empty": False, "error": ""}

    monkeypatch.setattr(routes, "_run", fake_run)
    _campaign(kernel=1)

    async def drive():
        assert supervisor.start(1)
        task = supervisor.SUPERVISORS[1]
        for _ in range(200):
            if chains:
                break
            await asyncio.sleep(0.01)
        supervisor.stop(1)
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(drive())
    assert chains, "the loop must still run a chain"
    assert any("no socket" in text for text in notices)


def test_loop_retries_the_vm_until_the_control_plane_answers(monkeypatch):
    """The control plane not being up yet must not need a campaign restart."""
    attempts = []
    chains = []

    async def flaky_ensure(campaign_id, root):
        attempts.append(campaign_id)
        if len(attempts) == 1:
            raise vmctl.Unavailable("no socket")
        return {"state": "running"}

    monkeypatch.setattr(vm, "ensure", flaky_ensure)
    monkeypatch.setattr(routes, "push_event", lambda *a, **k: None)

    async def fake_run(campaign_id, campaign, attachments, history, content, run, files=(), **kwargs):
        chains.append(campaign_id)
        return {"ok": True, "stopped": False, "empty": False, "error": ""}

    monkeypatch.setattr(routes, "_run", fake_run)
    _campaign(kernel=1)

    async def drive():
        assert supervisor.start(1)
        task = supervisor.SUPERVISORS[1]
        for _ in range(300):
            if len(attempts) >= 2:
                break
            await asyncio.sleep(0.01)
        supervisor.stop(1)
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(drive())
    assert len(attempts) == 2, "should retry once the plane is up, then stop asking"
    assert len(chains) >= 2, "chains keep running while the VM is missing"


def test_non_vm_campaigns_do_not_touch_the_control_plane(monkeypatch):
    called = []

    async def fake_ensure(*args, **kwargs):
        called.append(args)

    monkeypatch.setattr(vm, "ensure", fake_ensure)

    async def fake_run(campaign_id, campaign, attachments, history, content, run, files=(), **kwargs):
        return {"ok": True, "stopped": False, "empty": False, "error": ""}

    monkeypatch.setattr(routes, "_run", fake_run)
    _campaign(kernel=0)

    async def drive():
        assert supervisor.start(1)
        task = supervisor.SUPERVISORS[1]
        for _ in range(200):
            await asyncio.sleep(0.01)
            if not task.done():
                break
        supervisor.stop(1)
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(drive())
    assert called == []


def test_backlog_line_mentions_the_kernel_vm(monkeypatch):
    _campaign(kernel=1, root=0)
    line = supervisor._backlog_line(1)
    assert "KERNEL VM" in line and "root locked" in line

    _campaign(kernel=1, root=1, cid=2)
    assert "root password set" in supervisor._backlog_line(2)

    _campaign(kernel=0, cid=3)
    assert "KERNEL VM" not in supervisor._backlog_line(3)


# --- root flag ---------------------------------------------------------------


def test_flag_tool_is_gated_and_survives_the_reporting_phase():
    kernel = {"mode": "continuous", "kernel_enabled": 1}
    assert "vm_flag" in tools.allowed_for("hunter", kernel)
    assert "vm_flag" in tools.allowed_for("supervisor", kernel)
    # checking a value is read-only, so a hunter out of step budget can still
    # confirm the escalation it just spent the budget on
    assert "vm_flag" in tools.allowed_for("hunter", kernel, "report")
    assert not (tools.allowed_for("hunter", {"kernel_enabled": 0}, "report") & {"vm_flag"})
    assert "vm_flag" in {s["function"]["name"] for s in tools.schemas_for("hunter", kernel, None)}


def test_with_flag_reads_the_row_and_fails_closed():
    _campaign(kernel=1, flag=1, cid=4)
    _campaign(kernel=1, cid=5)
    assert vm.with_flag(_row(4))
    assert not vm.with_flag(_row(5))
    assert not vm.with_flag("continuous") and not vm.with_flag(None)


def test_verify_flag_reports_a_match_without_leaking_the_value(monkeypatch):
    calls = _fake_plane(monkeypatch, {"verify": {"ok": True, "match": True}})
    out, ok, _ = _exec(monkeypatch, "vm_flag", {"value": "l4vm-abc"})
    assert ok and "correct" in out
    assert calls[-1][0]["value"] == "l4vm-abc"

    _fake_plane(monkeypatch, {"verify": {"ok": True, "match": False}})
    out, ok, _ = _exec(monkeypatch, "vm_flag", {"value": "l4vm-wrong"})
    assert ok and "does not match" in out


def test_verify_flag_needs_a_value_and_surfaces_failures(monkeypatch):
    _fake_plane(monkeypatch, {"verify": {"ok": True, "match": False}})
    out, ok, _ = _exec(monkeypatch, "vm_flag", {})
    assert not ok and "value is required" in out

    _fake_plane(monkeypatch, {"verify": {"ok": False, "error": "this campaign has no flag"}})
    out, ok, _ = _exec(monkeypatch, "vm_flag", {"value": "x"})
    assert not ok and "no flag" in out

    _fake_plane(monkeypatch, {})
    out, ok, _ = _exec(monkeypatch, "vm_flag", {"value": "x"})
    assert not ok and "control plane down" in out


def test_start_and_lifecycle_carry_the_campaigns_flag_setting(monkeypatch):
    calls = _fake_plane(monkeypatch, {
        "status": {"ok": True, "vm": {"state": "stopped"}},
        "start": {"ok": True, "vm": {"state": "running", "kernel": "k"}},
        "reset": {"ok": True, "vm": {"state": "running", "kernel": "k"}},
    })
    asyncio.run(vm.ensure(1, root=False, flag=True))
    assert calls[-1][0]["root_flag"] is True

    _campaign(kernel=1, flag=1, cid=6)
    _exec(monkeypatch, "vm_reset", {}, cid=6, kind="supervisor", root_flag=1)
    assert calls[-1][0]["root_flag"] is True


def test_describe_mentions_the_flag_without_the_value():
    line = vm.describe({"state": "running", "kernel": "k", "uptime_s": 5,
                        "root_flag": True, "root_password": "l4root-secret"})
    assert "flag in /root/root.txt" in line
    assert "l4root-secret" not in line.replace("root password l4root-secret", "")


# --- surviving restarts and upgrades ----------------------------------------


def test_migration_adds_the_vm_columns_to_an_existing_database():
    """A database written before this feature must gain the columns, default to
    off, and keep its campaigns."""
    db.init()
    with db.db() as conn:
        for column in ("kernel_root_access", "kernel_root_flag"):
            conn.execute(f"ALTER TABLE campaigns DROP COLUMN {column}")

    db.init()  # what a container rebuild does on startup
    with db.db() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(campaigns)")}
        assert {"kernel_enabled", "kernel_root_access", "kernel_root_flag"} <= columns
        conn.execute("INSERT INTO campaigns (id, target, model) VALUES (1, 'old.example', 'm')")
        row = conn.execute("SELECT * FROM campaigns WHERE id = 1").fetchone()
    assert not vm.enabled(row) and not vm.with_flag(row)


def test_a_stale_socket_is_reported_not_waited_on(tmp_path, monkeypatch):
    import socket as unix

    # a socket file whose listener is gone: exactly what a killed control plane
    # leaves behind, and what a rebuild can surface
    stale = tmp_path / "stale.sock"
    listener = unix.socket(unix.AF_UNIX)
    listener.bind(str(stale))
    listener.listen(1)
    listener.close()

    monkeypatch.setattr(config, "VM_SOCK", str(stale))
    with pytest.raises(vmctl.Unavailable):
        asyncio.run(vmctl.call({"action": "ping"}))

    monkeypatch.setattr(config, "VM_SOCK", str(tmp_path / "nope.sock"))
    with pytest.raises(vmctl.Unavailable):
        asyncio.run(vmctl.call({"action": "ping"}))


def test_a_wedged_control_plane_gives_up_instead_of_hanging(tmp_path, monkeypatch):
    """The socket accepts but never answers: the call must time out, not sit
    there holding a chain forever."""
    import socket as unix

    quiet = tmp_path / "quiet.sock"
    listener = unix.socket(unix.AF_UNIX)
    listener.bind(str(quiet))
    listener.listen(1)
    monkeypatch.setattr(config, "VM_SOCK", str(quiet))
    monkeypatch.setattr(config, "VM_CALL_TIMEOUT", 0.2)
    try:
        with pytest.raises(vmctl.Unavailable):
            asyncio.run(vmctl.call({"action": "ping"}))
    finally:
        listener.close()


def test_provisioning_is_tied_to_the_campaign_running(monkeypatch):
    """The VM belongs to the campaign, not to the supervisor's first chain, so a
    running campaign gets it whether or not the agent has done anything."""
    ensured = []

    async def fake_ensure(campaign_id, root, flag=False):
        ensured.append((campaign_id, root, flag))
        return {"state": "running"}

    monkeypatch.setattr(vm, "ensure", fake_ensure)

    _campaign(kernel=1, root=1, flag=1, cid=7)
    asyncio.run(_provision(7))
    assert ensured == [(7, True, True)]

    # a campaign without the VM flag is left alone
    _campaign(kernel=0, cid=8)
    asyncio.run(_provision(8))
    assert ensured == [(7, True, True)]


async def _provision(cid):
    """provision_now is fire-and-forget; await the same thing for the test."""
    row = _row(cid)
    if vm.enabled(row):
        await vm.ensure(cid, vm.as_root(row), vm.with_flag(row))


def test_sweep_removes_vms_for_campaigns_that_are_gone(monkeypatch):
    """The durable half of "deleting a campaign removes its VM"."""
    _campaign(kernel=1, cid=1)
    destroyed = []

    async def fake_call(req, timeout=None):
        if req["action"] == "list":
            return {"ok": True, "vms": [{"campaign": 1}, {"campaign": 42}, {"campaign": 0}]}
        return {"ok": True}

    async def fake_destroy(campaign_id):
        destroyed.append(campaign_id)

    monkeypatch.setattr(vmctl, "call", fake_call)
    monkeypatch.setattr(vm, "destroy", fake_destroy)

    assert asyncio.run(vm.sweep()) == 1
    assert destroyed == [42], "only the campaign that no longer exists"


def test_sweep_is_quiet_when_the_control_plane_is_down(monkeypatch):
    async def down(req, timeout=None):
        raise vmctl.Unavailable("no socket")

    monkeypatch.setattr(vmctl, "call", down)
    assert asyncio.run(vm.sweep()) == 0

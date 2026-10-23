"""Tests for the host-side control plane.

`vm/vmctl.py` runs on the host and the container never imports it, but it is the
piece every VM guarantee rests on — what survives a crash, what a restart
preserves, what a status means. These drive it directly with no QEMU and no
socket: pure decisions about paths, secrets and state.
"""

import importlib.util
import json
import pathlib
import stat
import types

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("vmctl_host", REPO / "vm" / "vmctl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vmctl = _load()


def _config(tmp_path, **overrides):
    args = types.SimpleNamespace(
        base_dir=str(REPO / "vm" / "base"),
        run_dir=str(tmp_path / "run"),
        state_dir=str(tmp_path / "state"),
        images_dir=str(tmp_path / "images"),
        serial_dir=str(tmp_path / "serial"),
        qemu="qemu-system-x86_64",
        qemu_img="qemu-img",
        kvm=False,
        mem_mb=1024,
        smp=2,
        max_vms=2,
        mem_reserve_mb=512,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return vmctl.Config(args)


def test_config_resolves_paths_so_a_relative_base_dir_still_works(tmp_path):
    """A relative --base-dir used to be copied verbatim into the clone's backing
    path, which qemu-img then resolved against the clone's directory and the
    guest never booted."""
    cfg = _config(tmp_path, base_dir="vm/base")
    assert cfg.base.is_absolute()
    assert cfg.state.is_absolute() and cfg.images.is_absolute()


def test_instance_id_is_stable_and_per_state_dir(tmp_path):
    a = _config(tmp_path / "one").instance
    assert a == _config(tmp_path / "one").instance, "same dir, same identity"
    assert a != _config(tmp_path / "two").instance, "different dir, different identity"


def test_status_reports_intent_not_just_liveness(tmp_path):
    """A reboot-killed guest must not read as a deliberate stop: one is coming
    back, the other is not."""
    g = vmctl.Guest(_config(tmp_path), 1)
    g.state = "stopped"
    assert g.status()["state"] == "stopped"

    for recorded in ("ready", "booting", "resetting"):
        g.state = recorded
        assert g.status()["state"] == "interrupted", recorded
    assert g.status()["alive"] is False

    g.state = "failed"
    assert g.status()["state"] == "failed"


def test_configure_absent_means_keep_not_disable(tmp_path):
    """A bare start must not lock root on a campaign that was told otherwise."""
    g = vmctl.Guest(_config(tmp_path), 1)
    g.configure(root_access=True, root_flag=True)
    g.configure()  # e.g. an operator's vm_start with no arguments
    assert g.root_access and g.root_flag
    g.configure(root_access=False)
    assert not g.root_access and g.root_flag


def test_secrets_are_created_once_not_per_boot(tmp_path):
    """The VM restarts itself after every panic; a password or flag that changed
    underneath an agent would be worse than useless."""
    g = vmctl.Guest(_config(tmp_path), 1)
    g.root_access = True
    g.root_flag = True
    g.ensure_secrets()
    pw, flag = g.root_pw, g.flag
    assert pw.startswith("l4root-") and flag.startswith("l4vm-")

    g.ensure_secrets()  # a later boot
    assert (g.root_pw, g.flag) == (pw, flag)


def test_no_secrets_when_the_campaign_did_not_ask(tmp_path):
    g = vmctl.Guest(_config(tmp_path), 1)
    g.ensure_secrets()
    assert g.root_pw == "" and g.flag == ""


def test_seed_is_root_only_and_cleared_when_disabled(tmp_path):
    cfg = _config(tmp_path)
    g = vmctl.Guest(cfg, 7)
    g.root_flag = True
    g.write_seed()

    blob = g.seed.read_bytes()
    assert len(blob) == 4096
    assert blob.startswith(g.flag.encode() + b"\n")
    assert stat.S_IMODE(g.seed.stat().st_mode) == 0o600
    assert blob[40:] == b"\0" * (4096 - 40)

    # turning the flag off must not leave the old value lying around
    g.root_flag = False
    g.write_seed()
    assert not g.seed.exists()


def test_the_flag_never_reaches_the_command_line(tmp_path):
    """The guest's /proc/cmdline is world-readable, so the flag travels on a
    device instead — this guards that it stays that way."""
    g = vmctl.Guest(_config(tmp_path), 7)
    g.root_access = True
    g.root_flag = True
    g.ensure_secrets()
    argv = " ".join(g._argv())
    assert g.flag not in argv
    assert g.root_pw not in argv, "the password goes as a hash, never plaintext"
    assert f"file={g.seed},if=virtio,format=raw" in argv


def test_no_seed_drive_when_the_flag_is_off(tmp_path):
    g = vmctl.Guest(_config(tmp_path), 7)
    assert str(g.seed) not in " ".join(g._argv())


def test_guests_are_named_per_instance_so_sweeps_stay_local(tmp_path):
    """Two control planes on one host must not kill each other's guests: the
    orphan sweep only claims what its own identity launched."""
    mine = vmctl.Guest(_config(tmp_path / "mine"), 3)
    theirs = vmctl.Guest(_config(tmp_path / "theirs"), 3)
    argv_mine = " ".join(mine._argv())
    argv_theirs = " ".join(theirs._argv())
    assert mine.cfg.instance in argv_mine and theirs.cfg.instance in argv_theirs
    assert mine.cfg.instance != theirs.cfg.instance

    server = vmctl.Server(mine.cfg)
    # each sweep recognises only its own name
    assert server._tag_from_cmdline(argv_mine) == "campaign-3"
    assert server._tag_from_cmdline(argv_theirs) is None


def test_keyfile_round_trips_the_things_a_restart_must_preserve(tmp_path):
    g = vmctl.Guest(_config(tmp_path), 5)
    g.root_access = True
    g.root_flag = True
    g.ensure_secrets()
    g.restarts = 3
    g.save("ready", note="test")

    loaded = vmctl.Server(g.cfg)._load_state(5)
    assert loaded.root_access and loaded.root_flag
    assert loaded.root_pw == g.root_pw
    assert loaded.flag == g.flag
    assert loaded.restarts == 3
    assert loaded.state == "ready"


def test_admission_refuses_when_the_budget_is_spent(tmp_path, monkeypatch):
    cfg = _config(tmp_path, max_vms=0)
    server = vmctl.Server(cfg)
    # nobody running, but the slot budget is zero
    with pytest.raises(RuntimeError, match="at 0 running"):
        server._admit(1)

    monkeypatch.setattr(vmctl, "mem_available_mb", lambda: 10)
    cfg.max_vms = 2
    with pytest.raises(RuntimeError, match="free"):
        server._admit(1)


def test_doctor_flags_a_missing_base(tmp_path):
    report = _config(tmp_path, base_dir=str(tmp_path / "not-built-yet")).check()
    assert any("no base image" in problem for problem in report["problems"])
    assert report["base"].endswith("not-built-yet")


# --- build context -----------------------------------------------------------


def test_every_gitignored_directory_is_also_kept_out_of_the_build_context():
    """`.dockerignore` governs what the builder uploads; `.gitignore` only
    governs commits. Drift between them is how the VM's keyfiles — root
    passwords, flag values — ended up being sent to the Docker daemon on every
    build, along with 200MB of base image and sockets the builder cannot tar.
    """
    def entries(path):
        out = set()
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "*" in line or line.startswith("!"):
                continue
            out.add(line.rstrip("/"))
        return out

    ignored, uploaded = entries(REPO / ".gitignore"), entries(REPO / ".dockerignore")
    # tests/ is a deliberate dockerignore-only exclusion; .git is context-only
    missing = sorted(ignored - uploaded - {"tests", ".git"})
    assert not missing, f"gitignored but still sent to the builder: {missing}"

    # and the ones that actually carry secrets, spelled out
    for runtime in ("vm/state", "vm/serial", "vm/base", "vm/images"):
        assert runtime in uploaded, runtime

#!/usr/bin/env python3
"""Host-side VM control plane for kernel-pentest campaigns.

Runs on the *host*, as the operator, never in the container. It owns the QEMU
processes, the per-campaign disk clones, the serial console (which is both the
command channel and the crash log) and the state files.

The container gets exactly one thing: a unix socket. It cannot see the images,
the keyfiles or a guest IP, and it cannot reach the QEMU process. That is the
whole point of splitting it out — a guest that escapes QEMU lands in this
process's privileges, not in the harness's, and not in root.

Commands arrive as newline-delimited JSON: {"action": ..., "campaign": N, ...}.
Everything that mutates a VM is serialized per campaign by an asyncio lock, so
concurrent agents cannot interleave commands on one console.
"""

import argparse
import asyncio
import collections
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Console text kept per VM for `console`/`exec` parsing. Big enough for a long
# boot plus a panic trace, small enough that it can't matter on disk.
CONSOLE_BYTES = 256 * 1024

BOOT_TIMEOUT = 90
WATCHDOG_INTERVAL = 5
# A guest that has come up but wedges (RCU stall, deadlock) never returns, so
# every exec has a hard ceiling regardless of what the caller asked for.
MAX_EXEC_TIMEOUT = 600

CSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _qemu_for(cfg, tag: str) -> int:
    """Pid of a QEMU already running this campaign, if any.

    Whether this process started it or not: a start racing another, or an
    adopted guest whose keyfile went missing, must not put a second QEMU on the
    same disk. (QEMU's own image lock would refuse the second write, but with a
    far less useful error.)
    """
    marker = f"-name l4vm-{cfg.instance}-{tag}"
    for pid, cmdline in Server._qemu_processes():
        if marker in cmdline:
            return pid
    return 0


def log(msg):
    print(f"[vmctl] {msg}", flush=True)


def crypt_hash(password):
    """sha512crypt via openssl. Written straight into /etc/shadow by the guest's
    session script, which is how a per-boot root password reaches a disk we
    can't mount."""
    out = subprocess.run(
        ["openssl", "passwd", "-6", "-salt", secrets.token_hex(8), password],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def mem_available_mb():
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


class Guest:
    """One campaign's VM: QEMU process, serial console, command protocol."""

    def __init__(self, cfg, campaign, tag=None):
        self.cfg = cfg
        self.campaign = campaign
        self._tag = tag or f"campaign-{campaign}"
        self.lock = asyncio.Lock()
        self.buf = bytearray()
        self.conn = None
        self.reader = None
        self.proc = None
        self.capture = None
        self.data_arrived = asyncio.Event()
        self.restarts = 0
        self.root_access = False
        self.started_at = 0.0
        self.root_pw = ""
        self.root_flag = False
        self.flag = ""
        self.fail_count = 0
        self.last_fail = 0.0
        # One VM per campaign. The supervisor's retry and the campaign's own
        # provisioning can race, and they must not both boot one.
        self.lifecycle = asyncio.Lock()
        self.state = "stopped"

    # --- paths ------------------------------------------------------------
    @property
    def tag(self):
        return self._tag

    @property
    def clone(self):
        return self.cfg.images / f"{self.tag}.qcow2"

    @property
    def state_file(self):
        return self.cfg.state / f"{self.tag}.json"

    @property
    def serial_sock(self):
        return self.cfg.serial / f"{self.tag}.sock"

    @property
    def seed(self):
        return self.cfg.state / f"{self.tag}.seed"

    def ensure_secrets(self):
        """Create the campaign's password and flag once, not once per boot.

        The VM restarts itself after every panic. A password that changes
        underneath an agent mid-workflow is worse than useless, and a flag that
        changes would invalidate an escalation the agent already proved.
        """
        if self.root_access and not self.root_pw:
            self.root_pw = "l4root-" + secrets.token_hex(6)
        if self.root_flag and not self.flag:
            self.flag = "l4vm-" + secrets.token_hex(16)

    def write_seed(self):
        """Put the flag on the second disk for the guest's pid 1 to plant.

        The value is generated here and kept in the keyfile — never sent to the
        container, because the whole point is that the only way to learn it is to
        read a root-only file inside the guest. The guest zeroes the seed after
        reading it, so this runs before every boot.
        """
        if not self.root_flag:
            self.seed.unlink(missing_ok=True)
            return
        self.ensure_secrets()
        blob = self.flag.encode() + b"\n"
        self.seed.parent.mkdir(parents=True, exist_ok=True)
        self.seed.write_bytes(blob + b"\0" * (4096 - len(blob)))
        os.chmod(self.seed, 0o600)

    def kernel_image(self):
        meta = self.cfg.base / "meta.json"
        if meta.is_file():
            try:
                return json.loads(meta.read_text())["kernel_image"]
            except (OSError, ValueError, KeyError):
                pass
        for candidate in ("/boot/vmlinuz-linux-zen", "/boot/vmlinuz-linux"):
            if Path(candidate).is_file():
                return candidate
        raise RuntimeError("no kernel image; rebuild the base with build-base.sh")

    # --- state ------------------------------------------------------------
    def save(self, state=None, note=""):
        if state:
            self.state = state
        data = {
            "campaign": self.campaign,
            "state": self.state,
            "pid": self.pid(),
            "started_at": self.started_at,
            "restarts": self.restarts,
            "root_access": self.root_access,
            "root_flag": self.root_flag,
            "root_password": self.root_pw,
            "flag": self.flag,
            "note": note,
            "updated_at": time.time(),
        }
        self.cfg.state.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(data, indent=2) + "\n")

    def pid(self):
        if self.proc is not None and self.proc.returncode is None:
            return self.proc.pid
        try:
            return int(json.loads(self.state_file.read_text())["pid"])
        except (OSError, ValueError, KeyError):
            return 0

    def alive(self):
        pid = self.pid()
        if not pid:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    # --- qemu -------------------------------------------------------------
    def _argv(self):
        accel = ["-accel", "kvm", "-cpu", "host"] if self.cfg.kvm else ["-accel", "tcg", "-cpu", "max"]
        append = "root=/dev/vda rw console=ttyS0 loglevel=4 panic=5"
        # The only privilege knob there is: a root password hash, or nothing.
        # Nothing means root stays locked and su cannot authenticate.
        if self.root_access:
            append += " l4vm.rootpw=" + crypt_hash(self.root_pw)
        return [
            self.cfg.qemu,
            *accel,
            "-name", f"l4vm-{self.cfg.instance}-{self.tag}",
            "-m", str(self.cfg.mem_mb),
            "-smp", str(self.cfg.smp),
            "-kernel", self.kernel_image(),
            "-append", append,
            "-drive", f"file={self.clone},if=virtio,format=qcow2,discard=unmap",
            *(["-drive", f"file={self.seed},if=virtio,format=raw"] if self.root_flag else []),
            "-nic", "none",
            "-display", "none",
            "-serial", f"unix:{self.serial_sock},server=on,wait=off",
            "-monitor", "none",
            "-no-reboot",
        ]

    async def boot(self):
        other = _qemu_for(self.cfg, self.tag)
        if other:
            raise RuntimeError(f"{self.tag} already has a running vm (pid {other})")
        if not self.clone.exists():
            self.clone.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [self.cfg.qimg, "create", "-f", "qcow2", "-F", "qcow2",
                 "-b", str(self.cfg.base / "base.qcow2"), str(self.clone)],
                check=True, capture_output=True,
            )
        self.serial_sock.parent.mkdir(parents=True, exist_ok=True)
        self.serial_sock.unlink(missing_ok=True)
        self.ensure_secrets()
        self.write_seed()

        self.proc = await asyncio.create_subprocess_exec(
            *self._argv(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        self.started_at = time.time()
        self.buf.clear()
        self.save("booting")
        asyncio.create_task(self._reap(self.proc))

        # Wait for the console to answer rather than for the process to exist —
        # QEMU is up long before the guest's session is.
        deadline = time.monotonic() + BOOT_TIMEOUT
        while time.monotonic() < deadline:
            if not self.alive():
                raise RuntimeError(f"qemu exited during boot (see console)")
            try:
                await self.run("true", timeout=5, lock=False)
                self.save("ready")
                log(f"{self.tag}: ready after {time.time() - self.started_at:.1f}s")
                return
            except (TimeoutError, ConnectionError, OSError):
                await asyncio.sleep(1)
        raise TimeoutError(f"guest did not come up within {BOOT_TIMEOUT}s")

    async def _reap(self, proc):
        await proc.wait()

    async def kill(self):
        pid = self.pid()
        if pid:
            # whole process group: qemu spawns no helpers today, but killing the
            # group is the reliable way to be sure the qcow2 lock is released
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            for _ in range(50):
                if not self.alive():
                    break
                await asyncio.sleep(0.1)
            if self.alive():
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        if self.proc is not None:
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except (TimeoutError, asyncio.TimeoutError):
                pass
            self.proc = None
        await self._disconnect()

    # --- console ----------------------------------------------------------
    async def _connect(self):
        if self.conn is not None:
            return
        # QEMU creates the socket before the guest boots, so a short retry
        # covers the tiny window between spawn and bind.
        for _ in range(100):
            try:
                reader, writer = await asyncio.open_unix_connection(str(self.serial_sock))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                await asyncio.sleep(0.1)
        else:
            raise ConnectionError("serial socket never appeared")
        self.conn = writer
        self.reader = asyncio.create_task(self._read_loop(reader))
        # A serial line that was just attached can drop the leading bytes of the
        # first write. That is what made an early probe report success while
        # having run nothing at all, so spend a throwaway line on it.
        try:
            self.conn.write(b"\n")
            await self.conn.drain()
        except OSError:
            pass
        await asyncio.sleep(0.15)

    async def _disconnect(self):
        if self.reader is not None:
            self.reader.cancel()
            try:
                await self.reader
            except (asyncio.CancelledError, Exception):
                pass
            self.reader = None
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    async def _read_loop(self, reader):
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                self._append(chunk)
        except (asyncio.CancelledError, OSError):
            pass

    def _append(self, chunk):
        self.buf.extend(chunk)
        if len(self.buf) > CONSOLE_BYTES:
            del self.buf[: len(self.buf) - CONSOLE_BYTES]
        if self.capture is not None:
            self.capture.extend(chunk)
            self.data_arrived.set()

    def console_tail(self, lines=80):
        text = self.buf.decode("utf-8", "replace")
        # strip CSI sequences: a guest can otherwise paint the operator's log view
        text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
        text = re.sub(r"[^\x09\x0a\x0d\x20-\x7e]", "", text)
        rows = text.splitlines()
        return "\n".join(rows[-lines:])

    async def _recover(self):
        """Take the console back after a command that never finished.

        `su`, `passwd`, a pager and friends block on the terminal; without this
        they also eat the *next* command as their answer. Interrupt the
        foreground process, then kill the line buffer (Ctrl-U) — an interrupt
        alone leaves whatever was half-typed in the buffer, which the shell then
        tries to run.
        """
        if self.conn is None:
            return
        try:
            self.conn.write(b"\x03\x03\x15\n")
            await self.conn.drain()
        except OSError:
            return
        await asyncio.sleep(0.3)
        self.capture = None
        self.data_arrived.clear()

    async def run(self, cmd, timeout=120, lock=True):
        """Run one shell command as the console session.

        Wrapped in nonce markers so the echo of the command, the shell prompt
        and interleaved kernel messages can't be mistaken for its output. The
        trailing printf echoes as `%d` rather than a digit, so the terminator
        can only match the real one.
        """
        if not self.alive():
            raise ConnectionError("vm is not running")
        if lock:
            await self.lock.acquire()
        try:
            # Reconnect if the previous connection died (guest rebooted, QEMU
            # restarted) rather than writing into a closed socket.
            if self.conn is None or self.reader is None or self.reader.done():
                await self._disconnect()
                await self._connect()

            nonce = "l4" + secrets.token_hex(6)
            line = f"printf '\\n{nonce}\\n'; {cmd}; printf '\\n{nonce}:%d\\n' $?\n"

            self.capture = bytearray()
            self.data_arrived.clear()
            try:
                self.conn.write(line.encode())
                await self.conn.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                await self._disconnect()
                await self._connect()
                self.conn.write(line.encode())
                await self.conn.drain()

            end = re.compile(rf"{nonce}:(\d+)")
            # The tty turns \n into \r\n on the way out, so match the start
            # marker loosely. The echoed command line can't match it: there the
            # nonce is followed by a literal backslash from the printf argument.
            start = re.compile(rf"{re.escape(nonce)}\r?\n")

            deadline = time.monotonic() + min(timeout, MAX_EXEC_TIMEOUT)
            while True:
                text = self.capture.decode("utf-8", "replace")
                found = end.search(text)
                if found:
                    head = start.search(text)
                    if head is None:
                        # The end marker only proves the *tail* of the line
                        # survived. Without the start marker the command itself
                        # was mangled, so this is a transport fault, not a
                        # result — never report it as one.
                        raise ConnectionError(
                            "console dropped part of the command line; it was not run — retry"
                        )
                    body = text[head.end() : found.start()]
                    body = CSI.sub("", body)
                    return body.replace("\r\n", "\n").strip("\n"), int(found.group(1))
                left = deadline - time.monotonic()
                if left <= 0:
                    await self._recover()
                    raise TimeoutError(f"command did not finish in {timeout}s (a command waiting on input, e.g. su or a pager, is interrupted)")
                try:
                    await asyncio.wait_for(self.data_arrived.wait(), timeout=left)
                except (TimeoutError, asyncio.TimeoutError):
                    await self._recover()
                    raise TimeoutError(f"command did not finish in {timeout}s (a command waiting on input, e.g. su or a pager, is interrupted)") from None
                self.data_arrived.clear()
        finally:
            self.capture = None
            if lock:
                self.lock.release()

    # --- lifecycle --------------------------------------------------------
    def configure(self, root_access=None, root_flag=None):
        """Apply the requested privilege settings before a boot.

        Absent means "keep what this campaign was configured with", not "turn it
        off": a bare start must not silently lock root on a campaign that was
        told otherwise. All three lifecycle paths go through here so a reset or a
        restart cannot come back with a different setting than the campaign has.
        """
        if root_access is not None:
            self.root_access = bool(root_access)
        if root_flag is not None:
            self.root_flag = bool(root_flag)

    async def start(self, root_access=None, root_flag=None):
        async with self.lifecycle:
            if self.alive():
                return
            self.configure(root_access, root_flag)
            await self.boot()
            self.save("ready")

    async def restart(self, root_access=None, root_flag=None):
        async with self.lifecycle:
            await self.kill()
            self.configure(root_access, root_flag)
            self.restarts += 1
            await self.boot()
            self.save("ready")

    async def reset(self, root_access=None, root_flag=None):
        async with self.lifecycle:
            await self.kill()
            self.clone.unlink(missing_ok=True)
            self.configure(root_access, root_flag)
            self.restarts += 1
            await self.boot()
            self.save("ready", note="disk reset to base")

    async def shutdown(self):
        async with self.lifecycle:
            await self.kill()
            self.save("stopped")

    def status(self):
        alive = self.alive()
        # Report the *intent*, not just liveness: "interrupted" means it was
        # running and will be brought back, "stopped" means someone stopped it on
        # purpose. Flattening both to "stopped" made a reboot look like a
        # deliberate teardown to the agents and the UI.
        if alive:
            state = "running"
        elif self.state in ("ready", "booting", "resetting"):
            state = "interrupted"
        else:
            state = self.state or "stopped"
        return {
            "campaign": self.campaign,
            "state": state,
            "alive": alive,
            "pid": self.pid() if alive else 0,
            "kernel": self.cfg.kernel_release(),
            "uptime_s": round(time.time() - self.started_at, 1) if alive and self.started_at else 0,
            "restarts": self.restarts,
            "root_access": self.root_access,
            "root_password": self.root_pw if self.root_access else "",
            "root_flag": self.root_flag,
            "accel": "kvm" if self.cfg.kvm else "tcg",
            "clone": str(self.clone),
            "clone_bytes": self.clone.stat().st_size if self.clone.exists() else 0,
        }


class Server:
    def __init__(self, cfg):
        self.cfg = cfg
        self.guests = {}
        self.server = None

    def guest(self, campaign):
        g = self.guests.get(campaign)
        if g is None:
            g = Guest(self.cfg, campaign)
            self.guests[campaign] = g
        return g

    def _load_state(self, campaign):
        g = self.guest(campaign)
        try:
            data = json.loads(g.state_file.read_text())
        except (OSError, ValueError):
            return g
        g.restarts = int(data.get("restarts") or 0)
        g.root_access = bool(data.get("root_access"))
        g.root_flag = bool(data.get("root_flag"))
        g.flag = data.get("flag") or ""
        g.root_pw = data.get("root_password") or ""
        g.started_at = float(data.get("started_at") or 0)
        g.state = data.get("state") or "stopped"
        return g

    async def reconcile(self):
        """Adopt what survived a vmctl restart, sweep what didn't get a keyfile.

        QEMU outlives this process, so a restart mid-campaign must not leak
        VMs or, worse, leave two instances fighting over one qcow2 lock.
        """
        adopted = 0
        for path in sorted(self.cfg.state.glob("campaign-*.json")):
            try:
                campaign = int(path.stem.split("-")[1])
            except (IndexError, ValueError):
                continue
            g = self._load_state(campaign)
            if g.alive():
                adopted += 1
                log(f"{g.tag}: adopted (pid {g.pid()})")
            elif g.state == "stopped":
                # the operator stopped this one on purpose: leave it down
                continue
            else:
                # it was supposed to be running, so something took it out from
                # under us. The disk and the secrets survive; the watchdog
                # brings it back.
                g.save("interrupted", note="died with the host")
        if adopted:
            log(f"adopted {adopted} running vm(s)")

        # Anything QEMU-shaped with our naming but no keyfile is an orphan.
        for pid, cmdline in self._qemu_processes():
            tag = self._tag_from_cmdline(cmdline)
            if tag and not (self.cfg.state / f"{tag}.json").is_file():
                log(f"killing orphan {tag} (pid {pid})")
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

    @staticmethod
    def _qemu_processes():
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            except OSError:
                continue
            if "qemu-system" in cmdline:
                yield int(entry.name), cmdline

    def _tag_from_cmdline(self, cmdline):
        """Only guests this instance launched. Another control plane's guests
        are not orphans to us."""
        m = re.search(rf"-name\s+l4vm-{self.cfg.instance}-(campaign-\d+)", cmdline)
        return m.group(1) if m else None

    # --- admission --------------------------------------------------------
    def _admit(self, campaign):
        running = [g for g in self.guests.values() if g.campaign != campaign and g.alive()]
        if len(running) >= self.cfg.max_vms:
            raise RuntimeError(f"at {self.cfg.max_vms} running vm(s); stop one first")
        free = mem_available_mb()
        if free and free < self.cfg.mem_mb + self.cfg.mem_reserve_mb:
            raise RuntimeError(f"only {free}MB free, need {self.cfg.mem_mb + self.cfg.mem_reserve_mb}MB")

    # --- actions ----------------------------------------------------------
    async def handle(self, req):
        action = req.get("action")
        campaign = int(req.get("campaign") or 0)

        if action == "ping":
            return {"ok": True, "kvm": self.cfg.kvm, "vms": len([g for g in self.guests.values() if g.alive()])}

        if action == "list":
            return {"ok": True, "vms": [self._load_state(g.campaign).status() for g in self.guests.values()]}

        if action == "doctor":
            return {"ok": True, **self.cfg.check()}

        if not campaign:
            return {"ok": False, "error": "campaign id required"}

        g = self._load_state(campaign)

        if action == "status":
            return {"ok": True, "vm": g.status()}

        if action == "shutdown":
            # Deliberate teardown of every guest this host knows about. The unit
            # deliberately leaves guests running across a restart, so something
            # has to be able to stop them on purpose.
            stopped = []
            for path in sorted(self.cfg.state.glob("campaign-*.json")):
                try:
                    cid = int(path.stem.split("-")[1])
                except (IndexError, ValueError):
                    continue
                g = self._load_state(cid)
                if g.alive():
                    await g.shutdown()
                    stopped.append(cid)
            log(f"stopped {len(stopped)} vm(s)")
            return {"ok": True, "stopped": stopped}

        if action == "verify":
            claimed = str(req.get("value") or "").strip()
            if not g.root_flag or not g.flag:
                return {"ok": False, "error": "this campaign has no flag"}
            if not claimed:
                return {"ok": False, "error": "value required"}
            match = hmac.compare_digest(claimed, g.flag)
            log(f"{g.tag}: flag claim {'matched' if match else 'did not match'}")
            return {"ok": True, "match": match}

        if action == "console":
            return {"ok": True, "console": g.console_tail(int(req.get("lines") or 80))}

        if action == "start":
            if not g.alive():
                self._admit(campaign)
            await g.start(req.get("root_access"), req.get("root_flag"))
            return {"ok": True, "vm": g.status()}

        if action == "stop":
            await g.shutdown()
            return {"ok": True, "vm": g.status()}

        if action == "restart":
            if not g.alive():
                self._admit(campaign)
            await g.restart(req.get("root_access"), req.get("root_flag"))
            return {"ok": True, "vm": g.status()}

        if action == "reset":
            if not g.alive():
                self._admit(campaign)
            await g.reset(req.get("root_access"), req.get("root_flag"))
            return {"ok": True, "vm": g.status()}

        if action == "destroy":
            await g.kill()
            g.clone.unlink(missing_ok=True)
            g.state_file.unlink(missing_ok=True)
            self.guests.pop(campaign, None)
            return {"ok": True}

        if action == "exec":
            cmd = req.get("cmd")
            if not cmd:
                return {"ok": False, "error": "cmd required"}
            if not g.alive():
                return {"ok": False, "error": "vm is not running", "state": "stopped"}
            try:
                out, code = await g.run(cmd, timeout=int(req.get("timeout") or 120))
            except TimeoutError as exc:
                return {"ok": False, "error": str(exc), "timeout": True, "console": g.console_tail(30)}
            except (ConnectionError, OSError) as exc:
                return {"ok": False, "error": f"vm unreachable: {exc}", "console": g.console_tail(30)}
            return {"ok": True, "output": out, "exit": code}

        return {"ok": False, "error": f"unknown action {action!r}"}

    # --- socket -----------------------------------------------------------
    async def client(self, reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    req = json.loads(line)
                except ValueError:
                    writer.write(b'{"ok": false, "error": "bad json"}\n')
                    await writer.drain()
                    continue
                try:
                    resp = await self.handle(req)
                except Exception as exc:  # never take the server down for one call
                    log(f"action {req.get('action')!r} failed: {exc}")
                    resp = {"ok": False, "error": str(exc)}
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()

    async def watchdog(self):
        """Restart VMs that died on their own.

        A panic is a normal event in kernel work, not an incident: the guest
        goes down, the same disk comes back up, and the agents find out from the
        boot id changing.
        """
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            for g in list(self.guests.values()):
                if not g.state_file.is_file():
                    continue
                if g.alive() or g.state == "stopped":
                    continue
                # back off a guest that keeps failing to come up, so a broken
                # base cannot spawn a QEMU every five seconds
                if time.time() - g.last_fail < min(60, 5 * max(1, g.fail_count)):
                    continue
                try:
                    self._admit(g.campaign)
                except RuntimeError as exc:
                    # over the memory/slot budget: leave it interrupted and try
                    # again next tick rather than marking it failed forever
                    if time.time() - g.last_fail > 60:
                        g.last_fail = time.time()
                        log(f"{g.tag}: holding off restart: {exc}")
                    continue
                try:
                    await g.kill()
                    await g.boot()
                    g.fail_count = 0
                    g.save("ready", note="auto-restart after exit")
                    log(f"{g.tag}: restarted after unexpected exit")
                except Exception as exc:
                    g.fail_count += 1
                    g.last_fail = time.time()
                    g.save("failed", note=str(exc))
                    log(f"{g.tag}: restart failed ({g.fail_count}): {exc}")

    async def serve(self):
        self.cfg.run.mkdir(parents=True, exist_ok=True)
        sock = self.cfg.run / "vm.sock"
        sock.unlink(missing_ok=True)
        self.server = await asyncio.start_unix_server(self.client, path=str(sock))
        os.chmod(sock, 0o666)
        log(f"listening on {sock}")
        await self.reconcile()
        async with self.server:
            await self.watchdog()


class Config:
    def __init__(self, args):
        self.base = Path(args.base_dir or HERE / "base").resolve()
        self.run = Path(args.run_dir or HERE / "run").resolve()
        self.state = Path(args.state_dir or HERE / "state").resolve()
        self.images = Path(args.images_dir or HERE / "images").resolve()
        self.serial = Path(args.serial_dir or HERE / "serial").resolve()
        # Identifies this control plane in the QEMU command line, so the orphan
        # sweep can tell its own guests from another instance's. Without it, a
        # second instance sharing a host kills the first one's VMs.
        self.instance = hashlib.sha1(str(self.state).encode()).hexdigest()[:8]
        self.qemu = args.qemu
        self.qimg = args.qemu_img
        self.kvm = args.kvm and os.access("/dev/kvm", os.R_OK | os.W_OK)
        self.mem_mb = args.mem_mb
        self.smp = args.smp
        self.max_vms = args.max_vms
        self.mem_reserve_mb = args.mem_reserve_mb

    def kernel_release(self):
        meta = self.base / "meta.json"
        try:
            return json.loads(meta.read_text()).get("kernel_release") or "?"
        except (OSError, ValueError):
            return "?"

    def check(self):
        problems = []
        if not (self.base / "base.qcow2").is_file():
            problems.append(f"no base image in {self.base} (run vm/build-base.sh)")
        if shutil.which(self.qemu) is None:
            problems.append(f"qemu not found: {self.qemu}")
        if shutil.which(self.qimg) is None:
            problems.append(f"qemu-img not found: {self.qimg}")
        if shutil.which("openssl") is None:
            problems.append("openssl not found (needed for the root password hash)")
        return {"problems": problems, "kvm": self.kvm, "qemu": self.qemu,
                "base": str(self.base), "mem_available_mb": mem_available_mb()}


async def send(cfg, req, timeout=60):
    """One request over the control socket. Used by the CLI subcommands."""
    reader, writer = await asyncio.open_unix_connection(str(cfg.run / "vm.sock"))
    try:
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        return json.loads(await asyncio.wait_for(reader.readline(), timeout=timeout))
    finally:
        writer.close()


async def boot_check(cfg):
    """Boot the base image once and read the kernel back. Parity with the host
    kernel is the premise of the whole feature, so it gets verified, not
    assumed."""
    g = Guest(cfg, 0, tag="bootcheck")
    try:
        await g.start(root_access=False)
        out, code = await g.run("uname -r; id -un; cat /etc/shadow | head -1", timeout=20)
        await g.kill()
        if code != 0:
            return 2, out
        return 0, out
    except Exception as exc:
        return 2, str(exc)
    finally:
        g.clone.unlink(missing_ok=True)
        (cfg.state / "bootcheck.json").unlink(missing_ok=True)
        g.serial_sock.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(prog="vmctl")
    ap.add_argument("command", choices=["serve", "doctor", "stop-all"])
    ap.add_argument("--base-dir")
    ap.add_argument("--run-dir")
    ap.add_argument("--state-dir")
    ap.add_argument("--images-dir")
    ap.add_argument("--serial-dir")
    ap.add_argument("--qemu", default="qemu-system-x86_64")
    ap.add_argument("--qemu-img", default="qemu-img")
    ap.add_argument("--mem-mb", type=int, default=1024)
    ap.add_argument("--smp", type=int, default=2)
    ap.add_argument("--max-vms", type=int, default=2)
    ap.add_argument("--mem-reserve-mb", type=int, default=2048)
    ap.add_argument("--no-kvm", dest="kvm", action="store_false")
    ap.add_argument("--boot-check", action="store_true")
    args = ap.parse_args()

    cfg = Config(args)

    if args.command == "stop-all":
        try:
            reply = asyncio.run(send(cfg, {"action": "shutdown"}))
        except (OSError, asyncio.TimeoutError) as exc:
            print(f"control plane not reachable ({exc})")
            return 1
        print(f"stopped: {reply.get('stopped') or 'nothing'}")
        return 0

    if args.command == "doctor":
        report = cfg.check()
        for problem in report.pop("problems"):
            print(f"problem: {problem}")
        for key, value in report.items():
            print(f"{key}: {value}")
        if args.boot_check:
            code, out = asyncio.run(boot_check(cfg))
            print(f"boot check: {'ok' if code == 0 else 'FAILED'}")
            print(out)
        return 1 if report.get("problems") or (args.boot_check and code) else 0

    asyncio.run(Server(cfg).serve())
    return 0


if __name__ == "__main__":
    sys.exit(main())

---
name: kernel-lpe
description: >-
  Kernel exploit workflow inside the campaign's kernel-pentest VM: local
  privilege escalation from the unprivileged hunter user to root, or escape
  attempts against QEMU. Covers reading the crash from the serial console,
  building and pushing a PoC, keeping the shared kernel's baseline intact, and
  what evidence makes a kernel finding acceptable. Use for anything touching
  the kernel, LPE, privilege escalation, syscalls, nftables, io_uring, BPF or
  a QEMU escape.
---

# Kernel work in the VM

The VM boots **the same kernel as the operator's machine** (same version, same
modules, same sysctls). A bug you prove in there is a bug on the host — that is
the whole reason it exists. It has no network and no compiler.

You are the `hunter` user. Root is locked unless the campaign set a password.

## The loop

1. `vm_status` — confirm it is up, and note the uptime and restart count so you
   can tell later whether someone rebooted it under you.
2. Look at what you have: `vm_exec` runs one line. `uname -r`, `id`,
   `ls -l /proc/sys/kernel`, `cat /proc/version`, `lsmod | head`,
   `cat /proc/kallsyms | head` (kallsyms is readable, symbols included).
3. Build the PoC **in the container** — `gcc -static`, or `musl-gcc` if you have
   it — and push it in:
   `base64 -w0 ./poc | ...` → in the guest
   `echo <b64> | base64 -d > /work/poc && chmod +x /work/poc`.
   Static matters: the guest is musl, and a dynamically linked glibc binary
   will not run.
4. Run it in the background if it is slow: `nohup /work/poc > /work/out 2>&1 &`,
   then poll with `vm_exec 'tail -5 /work/out'`.
5. When it dies — and it will — `vm_console` is the crash report. Read the
   faulting instruction, the call trace, and the register dump from there.

## Reading a crash

`dmesg` is restricted for `hunter`, exactly as it is on the host, so the serial
console is how you see a panic. `vm_console` gives the last N lines, including
the oops and the panic text. Take the `RIP:`/`Call Trace:` lines — those name the
function and the offset that actually faulted.

The VM restarts itself after a panic with the same disk, so your files survive.
`vm_status` will show uptime reset and the restart count up.

## Getting root

- **Root locked** (the default): there is no password to give `su`, so the only
  way up is an actual bug. Do not spend time on `su`, `sudo` (not installed) or
  password guessing — if it were that easy the campaign would not be testing
  anything.
- **Root password set:** it is in your instructions. `su` reads the password
  from the terminal rather than stdin, so detach the session:
  `printf '%s\n' "$pw" | setsid su -c 'id' root`.
- Once you have root, prove it in a way that survives a fresh boot: the
  acceptance bar is a runnable PoC that takes the VM from `hunter` to `uid 0`,
  not a transcript of you already being root.

## The flag, when the campaign has one

`/root/root.txt` holds a per-campaign string the host planted there at boot. It
is `0600` inside a `0700` `/root`, so getting at it *is* the proof of
escalation: it is not on the kernel command line, not on any device you can
read, and not in this container. `vm_status` will say `flag in
/root/root.txt` when it applies.

Read it as root, then confirm it before you report:

    vm_flag l4vm-<the value you read>

That answers `correct` or `does not match`. The expected value is never sent to
this container, so a match cannot be faked from inside a compromised guest —
which is why it is worth doing rather than just pasting the text.

## Evidence

The supervisor accepts a kernel finding on a **runnable PoC plus the crash**. What
that means concretely:

- The PoC source and binary live under your work directory in the container
  (`hunters/<your id>/...`) so `hunt_report` can point at a real file, and a
  fresh clone of the VM can replay it.
- The reproduction is the exact command sequence, starting from `hunter`.
- The evidence is the console excerpt: the oops, the call trace, and the
  `uid=0` (or equivalent) that shows the boundary was crossed.

A panic on its own is not an escalation — say what it proves.

## Escaping QEMU

Guest → host escape work runs in the same VM. Attempts are fine; understand that
an escape lands in the host account the hypervisor runs as, and the harness is
built so that is a bounded account rather than the operator's shell. Report the
primitive you found (a device, a parser, a port) even if you cannot complete a
full escape — a reachable bug is the finding.

## Shared with everyone

The VM is one machine shared by every hunter and the supervisor, with one
filesystem and no separation inside it.

- Put your files under `/work/<something unique to you>` so you do not collide.
- Loading a module, changing a sysctl or adding a firewall rule changes the
  kernel *everyone* is testing against. Note it, and revert it when you are done.
- A panic ends everyone's session, not just yours. That is expected; the VM comes
  back. If you see an oops in the console that is not yours, check the timestamps
  before you claim it.
- Never touch the VM's configuration — boot parameters, disks, CPU, network,
  power. Those belong to the supervisor.

#!/bin/sh
# Build the kernel-pentest VM base image.
#
# The guest boots the *host's own kernel* (vmlinuz + /lib/modules), so uname -r
# inside the VM matches the operator's machine. That is the whole point: a
# kernel bug found in the guest is a bug in the host kernel too.
#
# Userspace is a stock Alpine minirootfs with a minimal init. Alpine's own
# inittab calls /sbin/openrc, which the minirootfs doesn't ship; openrc is a
# separate package and an unattended testbox doesn't need it.
#
# The console always lands in an unprivileged shell (hunter) and root's
# password is whatever the minirootfs ships — locked. So the only way to root is
# an actual exploit; an agent cannot just su. vmctl can put a root password hash
# on the cmdline for a campaign that legitimately needs privilege, and then the
# normal su path works, with the console still unprivileged.
#
# Unprivileged, no loop mounts: mke2fs -d populates the filesystem from a
# directory and fakeroot fixes up ownership, so no root and no CAP_SYS_ADMIN.
#
# Usage:
#   vm/build-base.sh [--force] [--out DIR] [--alpine VER] [--size-mb N]
#                    [--kernel PATH] [--no-modules] [--no-verify]

set -eu

ALPINE="3.24.0"
OUT="$(cd "$(dirname "$0")" && pwd)/base"
SIZE_MB=2048
KERNEL=""
WITH_MODULES=1
VERIFY=1
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE=1 ;;
        --out) OUT="$2"; shift ;;
        --alpine) ALPINE="$2"; shift ;;
        --size-mb) SIZE_MB="$2"; shift ;;
        --kernel) KERNEL="$2"; shift ;;
        --no-modules) WITH_MODULES=0 ;;
        --no-verify) VERIFY=0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v fakeroot >/dev/null 2>&1 || die "fakeroot is required — without it every file in the image ends up owned by the build user, which hands the guest's filesystem to the unprivileged user"

HOST_RELEASE="$(uname -r)"
MODULES_DIR="/usr/lib/modules/$HOST_RELEASE"

if [ -z "$KERNEL" ]; then
    for candidate in /boot/vmlinuz-linux-zen /boot/vmlinuz-linux "/boot/vmlinuz-$HOST_RELEASE"; do
        [ -f "$candidate" ] && { KERNEL="$candidate"; break; }
    done
fi
[ -n "$KERNEL" ] || die "no kernel image found; pass --kernel /boot/vmlinuz-..."
[ -f "$KERNEL" ] || die "kernel image not found: $KERNEL"
if [ "$WITH_MODULES" = 1 ]; then
    [ -d "$MODULES_DIR" ] || die "no modules for $HOST_RELEASE — the guest kernel must match the running host kernel"
fi

if [ "$FORCE" != 1 ] && [ -f "$OUT/base.qcow2" ]; then
    say "already built: $OUT/base.qcow2 (--force to rebuild)"
    exit 0
fi

mkdir -p "$OUT"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/l4rpness-vmbase.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT INT TERM

TARBALL="alpine-minirootfs-$ALPINE-x86_64.tar.gz"
URL="https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/x86_64/$TARBALL"
say "fetching $TARBALL"
curl -fsSL -o "$WORK/$TARBALL" "$URL" || die "download failed: $URL"

ROOTFS="$WORK/rootfs"
mkdir -p "$ROOTFS"
tar -xzf "$WORK/$TARBALL" -C "$ROOTFS" || die "extract failed"

# The unprivileged account everything runs as. 1000:1000 so it reads like a
# normal first user; ownership is forced through fakeroot below, not inherited
# from whoever happens to run this script.
USER_ID=1000
printf 'hunter:x:%s:%s:Hunter:/home/hunter:/bin/sh\n' "$USER_ID" "$USER_ID" >> "$ROOTFS/etc/passwd"
printf 'hunter:x:%s:\n' "$USER_ID" >> "$ROOTFS/etc/group"
# same field shape as Alpine's own root entry; '*' keeps the password locked
printf 'hunter:*::0:::::\n' >> "$ROOTFS/etc/shadow"
mkdir -p "$ROOTFS/home/hunter" "$ROOTFS/work"

# Root's own entry is whatever the minirootfs ships — '*' (locked) — and stays
# that way here. Privilege is decided by exactly one thing at boot: whether
# vmctl puts a password hash on the cmdline. No lock of mine, no session
# switching, no sudo: root is reachable only through the normal su/shadow
# path, which is the same hierarchy the host uses.

cat > "$ROOTFS/sbin/l4vm-init" <<'INIT'
#!/bin/sh
# pid 1 does the minimum a testbox needs, then hands off to /sbin/l4vm-session
# via inittab. The serial console is the control channel, so there is no
# network, no agent and no login prompt to keep alive.
mount -t proc     proc  /proc 2>/dev/null
mount -t sysfs    sys   /sys  2>/dev/null
mount -t devtmpfs dev   /dev  2>/dev/null
mount -t tmpfs    tmp   /tmp  2>/dev/null
mount -t tmpfs    tmpfs /run  2>/dev/null
hostname l4vm 2>/dev/null
ip link set lo up 2>/dev/null
mkdir -p /work

# The campaign's flag, planted by the host on a second disk. This runs before
# the drop to hunter and /root is 0700, so the unprivileged user cannot read it;
# the seed is then zeroed because otherwise /dev/vdb would hand it straight
# back. The value never touches the console, which the agents can read.
if [ -b /dev/vdb ]; then
    flag=$(tr -d '\000' < /dev/vdb | head -1)
    if [ -n "$flag" ]; then
        mkdir -p /root
        printf '%s\n' "$flag" > /root/root.txt
        chmod 600 /root/root.txt
        chown 0:0 /root/root.txt
        printf 'l4vm: flag planted in /root/root.txt\n'
    fi
    dd if=/dev/zero of=/dev/vdb bs=4096 count=1 2>/dev/null
fi
INIT
chmod 755 "$ROOTFS/sbin/l4vm-init"

# The console always lands here, as hunter. The only privilege knob is whether
# vmctl put a root password hash on the cmdline; with no hash root stays locked
# and su cannot authenticate, so the only way up is an actual exploit.
cat > "$ROOTFS/sbin/l4vm-session" <<'SESSION'
#!/bin/sh
root="locked"
for arg in $(cat /proc/cmdline); do
    case "$arg" in
        l4vm.rootpw=*)
            sed -i "s|^root:[^:]*:|root:${arg#l4vm.rootpw=}:|" /etc/shadow
            root="password set"
            ;;
    esac
done
printf '\nl4vm: kernel %s, console user hunter, root %s\n' "$(uname -r)" "$root"
exec /bin/su -l hunter
SESSION
chmod 755 "$ROOTFS/sbin/l4vm-session"

cat > "$ROOTFS/etc/inittab" <<'INITTAB'
::sysinit:/sbin/l4vm-init
ttyS0::respawn:/sbin/l4vm-session
::ctrlaltdel:/sbin/reboot
::shutdown:/bin/umount -a -r
INITTAB

printf 'l4vm\n' > "$ROOTFS/etc/hostname"

# Modules are copied so the guest can modprobe whatever the host can. Netfilter,
# overlayfs and friends are exactly the LPE surface here, and a lot of that is
# modular — without this the guest is missing half the attack surface.
if [ "$WITH_MODULES" = 1 ]; then
    say "copying modules for $HOST_RELEASE"
    mkdir -p "$ROOTFS/lib/modules"
    cp -a "$MODULES_DIR" "$ROOTFS/lib/modules/" || die "module copy failed"
fi

RAW="$WORK/base.raw"
say "building ${SIZE_MB}M ext4 image"
dd if=/dev/zero of="$RAW" bs=1M count="$SIZE_MB" status=none || die "dd failed"

# Everything under / is root:root; the two writable spots belong to hunter.
# mke2fs writes uid/gid straight into the image, so fakeroot's faked stat is
# enough — nothing needs to be chown'd on the real filesystem.
#
# busybox gets the setuid bit that a stock Alpine install gives it. Without it
# su refuses to switch users, and the agent needs to drop from a root session
# down to hunter to run a PoC the way it would actually run. Applets that don't
# need privilege drop it themselves, so this is not a free root shell.
fakeroot sh -c "
    chown -R 0:0 '$ROOTFS' &&
    chown -R $USER_ID:$USER_ID '$ROOTFS/home/hunter' '$ROOTFS/work' &&
    chmod 4755 '$ROOTFS/bin/busybox' &&
    mke2fs -q -t ext4 -L l4vm -d '$ROOTFS' -F '$RAW'
" || die "mke2fs failed"

say "converting to qcow2"
# Convert beside the real one and move into place only on success: a clone that
# is still running holds a read lock on the current base, and a half-written
# base would break every campaign that depends on it.
NEW="$OUT/base.qcow2.new"
rm -f "$NEW"
if ! qemu-img convert -f raw -O qcow2 -o lazy_refcounts=on "$RAW" "$NEW"; then
    rm -f "$NEW"
    die "convert failed (a running VM holds the current base — stop it first: vmctl.py serve actions stop)"
fi
mv -f "$NEW" "$OUT/base.qcow2"

cat > "$OUT/meta.json" <<EOF
{
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "alpine": "$ALPINE",
  "kernel_image": "$KERNEL",
  "kernel_release": "$HOST_RELEASE",
  "kernel_sha256": "$(sha256sum "$KERNEL" | cut -d' ' -f1)",
  "disk_mb": $SIZE_MB,
  "modules": $WITH_MODULES,
  "user": "hunter",
  "uid": $USER_ID
}
EOF

say "base image: $OUT/base.qcow2  (kernel $HOST_RELEASE)"

# Parity is the premise, so boot it once and read uname -r back rather than
# trusting that the filename matches the modules dir.
if [ "$VERIFY" = 1 ]; then
    if [ -r /dev/kvm ]; then
        say "verifying boot"
        "$(cd "$(dirname "$0")" && pwd)/vmctl.py" doctor --base-dir "$OUT" --boot-check ||
            die "boot verification failed"
    else
        say "skipping verification: /dev/kvm not readable"
    fi
fi

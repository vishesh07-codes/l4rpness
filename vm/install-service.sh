#!/bin/sh
# Install the control plane as a systemd user service, so it does not have to be
# a foreground process someone remembers to start.
#
# Deliberately a *user* service: vmctl must run unprivileged, and the socket it
# creates is the only thing the container is allowed to touch.
#
#   vm/install-service.sh            install, enable, start
#   vm/install-service.sh status     what it is doing
#   vm/install-service.sh uninstall  stop and remove

set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
UNIT="l4rpness-vm.service"
DEST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$UNIT"

case "${1:-install}" in
status)
    systemctl --user status "$UNIT" --no-pager || true
    exit 0
    ;;
uninstall)
    # Guests outlive a service restart on purpose, so take them down explicitly
    # rather than orphaning a QEMU per campaign.
    "$REPO/.venv/bin/python" "$HERE/vmctl.py" stop-all 2>/dev/null || true
    systemctl --user disable --now "$UNIT" 2>/dev/null || true
    rm -f "$DEST"
    systemctl --user daemon-reload
    echo "removed $DEST"
    exit 0
    ;;
install) ;;
*)
    echo "usage: $0 [install|status|uninstall]" >&2
    exit 2
    ;;
esac

command -v systemctl >/dev/null 2>&1 || { echo "systemd not available" >&2; exit 1; }
[ -x "$REPO/.venv/bin/python" ] || { echo "no venv at $REPO/.venv — create it first" >&2; exit 1; }
[ -f "$REPO/vm/base/base.qcow2" ] || echo "warning: no base image yet; run vm/build-base.sh"

mkdir -p "$(dirname "$DEST")"
sed "s|__REPO__|$REPO|g" "$HERE/$UNIT" > "$DEST"
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT"

sleep 1
echo
systemctl --user is-active "$UNIT" >/dev/null 2>&1 &&
    echo "control plane: active" ||
    { echo "control plane: not running — check 'journalctl --user -u $UNIT'"; exit 1; }

"$REPO/.venv/bin/python" "$HERE/vmctl.py" doctor || true

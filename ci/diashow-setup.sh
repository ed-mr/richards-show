#!/bin/sh
# Sets up the rclone remote "nc" for diashow-sync on hydra (issue #25):
# asks for the Nextcloud app password of the read-only user and stores it
# obscured in ~/.config/rclone/rclone.conf. Run interactively:
#   make diashow-setup        (or: ~/fb-slideshow/ci/diashow-setup.sh)
set -eu
URL=${DIASHOW_URL:-https://cloud.orthos.selfhost.eu/remote.php/dav/files/diashow/}
USER_NAME=${DIASHOW_USER:-diashow}

command -v rclone >/dev/null || { echo "rclone missing: sudo apt install rclone" >&2; exit 1; }
if [ -t 0 ]; then
    printf 'App password for Nextcloud user %s: ' "$USER_NAME"
    stty -echo; trap 'stty echo' EXIT
    read -r pw; stty echo; trap - EXIT; echo
else
    read -r pw  # piped in
fi
[ -n "$pw" ] || { echo "no password given, nothing changed" >&2; exit 1; }
obscured=$(printf '%s' "$pw" | rclone obscure -)
conf=$(rclone config file | tail -n 1)
mkdir -p "$(dirname "$conf")"
rclone config delete nc 2>/dev/null || true
umask 077
{
    echo "[nc]"
    echo "type = webdav"
    echo "url = $URL"
    echo "vendor = nextcloud"
    echo "user = $USER_NAME"
    echo "pass = $obscured"
    echo
} >>"$conf"
chmod 600 "$conf"
echo "remote nc: saved in $conf"
if rclone lsd nc: --contimeout 20s --timeout 1m; then
    echo "remote nc: ok"
    systemctl --user enable --now diashow-sync.timer 2>/dev/null &&
        echo "diashow-sync.timer enabled" || true
else
    echo "remote nc: login failed (wrong password?) - run again" >&2
    exit 1
fi

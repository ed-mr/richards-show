#!/bin/sh
# Sets up the rclone remote "nc" for diashow-sync on hydra (issue #25):
# asks for the Nextcloud app password and stores it obscured in
# ~/.config/rclone/rclone.conf. Run interactively:
#   make diashow-setup        (or: ~/fb-slideshow/ci/diashow-setup.sh)
#
# The Nextcloud address and user are not kept in the repo. They are taken from
# DIASHOW_URL / DIASHOW_USER, else from the existing remote "nc" (so a new app
# password keeps them), else asked for once.
set -eu
RCLONE=${RCLONE:-rclone}
SYSTEMCTL=${SYSTEMCTL:-systemctl}

command -v "$RCLONE" >/dev/null || { echo "rclone missing: sudo apt install rclone" >&2; exit 1; }

ask() {  # ask PROMPT DEFAULT -> answer on stdout (prompt on the terminal)
    printf '%s' "$1${2:+ [$2]}: " >&2
    read -r answer || answer=
    printf '%s\n' "${answer:-$2}"
}

old=$("$RCLONE" config show nc 2>/dev/null || true)
URL=${DIASHOW_URL:-$(printf '%s\n' "$old" | sed -n 's/^url = //p')}
USER_NAME=${DIASHOW_USER:-$(printf '%s\n' "$old" | sed -n 's/^user = //p')}
if [ -z "$USER_NAME" ]; then
    [ -t 0 ] || { echo "no Nextcloud user: set DIASHOW_USER" >&2; exit 1; }
    USER_NAME=$(ask "Nextcloud user" diashow)
fi
if [ -z "$URL" ]; then
    [ -t 0 ] || { echo "no Nextcloud address: set DIASHOW_URL" >&2; exit 1; }
    host=$(ask "Nextcloud address, e.g. cloud.example.org" "")
    host=${host#https://}; host=${host#http://}; host=${host%%/*}
    [ -n "$host" ] || { echo "no address given, nothing changed" >&2; exit 1; }
    URL="https://$host/remote.php/dav/files/$USER_NAME/"
fi
echo "Nextcloud: $URL (user $USER_NAME)"

if [ -t 0 ]; then
    printf 'App password for Nextcloud user %s: ' "$USER_NAME"
    stty -echo; trap 'stty echo' EXIT
    read -r pw; stty echo; trap - EXIT; echo
else
    read -r pw || true  # piped in
fi
[ -n "$pw" ] || { echo "no password given, nothing changed" >&2; exit 1; }
obscured=$(printf '%s' "$pw" | "$RCLONE" obscure -)
conf=$("$RCLONE" config file | tail -n 1)
mkdir -p "$(dirname "$conf")"
"$RCLONE" config delete nc 2>/dev/null || true
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
if "$RCLONE" lsd nc: --contimeout 20s --timeout 1m; then
    echo "remote nc: ok"
    "$SYSTEMCTL" --user enable --now diashow-sync.timer 2>/dev/null &&
        echo "diashow-sync.timer enabled" || true
else
    echo "remote nc: login failed (wrong password?) - run again" >&2
    exit 1
fi

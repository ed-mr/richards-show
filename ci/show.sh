#!/bin/sh
# Controls the slideshow on the Pi via its systemd user service. Runs on
# hydra (deployed next to fb-slideshow.py), usually invoked over ssh by the
# Makefile.
#
#   show.sh install                 install/update and enable the service
#   show.sh start|stop|restart|pause|resume|status
#   show.sh sequence <file>         check <file>, then make it the sequence
#                                   (/mnt/stick/config.yaml)
#   show.sh config "<options>"      set options for fb-slideshow.py, on top
#                                   of the service's DEFAULT_ARGS (empty =
#                                   defaults only), restarts if running
#
# Logs go to the journal, which is volatile (RAM) on hydra.
set -eu

DIR=$(cd "$(dirname "$0")/.." && pwd)
UNIT=fb-slideshow.service
UNIT_DIR=$HOME/.config/systemd/user
CONF_DIR=$HOME/.config/fb-slideshow
CONF=$CONF_DIR/env
SEQUENCE=/mnt/stick/config.yaml

export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
sc() { systemctl --user "$@"; }

active() { sc is-active --quiet "$UNIT"; }

default_args() {
    # DEFAULT_ARGS from the service file (installed copy, else the deployed one)
    f=$UNIT_DIR/$UNIT; [ -f "$f" ] || f=$DIR/ci/$UNIT
    sed -n 's/^Environment="DEFAULT_ARGS=\(.*\)"$/\1/p' "$f"
}

log_lines() {
    # messages of the current run only
    id=$(sc show -p InvocationID --value "$UNIT")
    [ -n "$id" ] || return 0
    journalctl --no-pager -q -o cat "_SYSTEMD_INVOCATION_ID=$id" 2>/dev/null ||
        journalctl --no-pager -q -o cat --user-unit "$UNIT" -n 200 2>/dev/null || true
}

paused() {
    # fb-slideshow.py logs PAUSED / RESUMED on every SIGUSR1
    [ "$(log_lines | grep -E '^(PAUSED|RESUMED)$' | tail -n 1)" = PAUSED ]
}

install_unit() {
    mkdir -p "$UNIT_DIR"
    [ "$DIR" = "$HOME/fb-slideshow" ] ||
        echo "warning: deployed to $DIR, but the service runs ~/fb-slideshow" >&2
    # plain copy; only written when changed (keeps SD-card writes down)
    if ! cmp -s "$DIR/ci/$UNIT" "$UNIT_DIR/$UNIT"; then
        cp "$DIR/ci/$UNIT" "$UNIT_DIR/$UNIT"
        sc daemon-reload
        echo "service file updated"
    fi
    sc is-enabled --quiet "$UNIT" 2>/dev/null || sc enable --quiet "$UNIT"
    # a show started by hand (outside systemd) would block /dev/fb1
    for pid in $(pgrep -f 'python3? .*fb-slideshow\.py' || true); do
        if [ "$pid" != "$(sc show -p MainPID --value "$UNIT")" ]; then
            echo "stopping show outside systemd (pid $pid)"
            kill -TERM "$pid"
        fi
    done
    [ "$(loginctl show-user "$(id -un)" -p Linger --value)" = yes ] ||
        echo "warning: linger is off, the show will not start at boot" \
             "(fix: loginctl enable-linger)" >&2
    echo "service installed and enabled"
}

configure() {
    # check first: a bad option would make the service fail on every restart
    # (checked together with the defaults, as the service will start it)
    # shellcheck disable=SC2086  # options are meant to be split
    if ! out=$(PYTHONDONTWRITEBYTECODE=1 python3 "$DIR/sequence.py" \
            $(default_args) $1 --check 2>&1); then
        echo "invalid options, nothing changed:" >&2
        printf '%s\n' "$out" | grep -E 'error:' >&2 || printf '%s\n' "$out" >&2
        return 1
    fi
    mkdir -p "$CONF_DIR"
    new="SHOW_ARGS=$1"
    if [ "$(cat "$CONF" 2>/dev/null)" != "$new" ]; then
        printf '%s\n' "$new" >"$CONF"
    fi
    echo "defaults: $(default_args)"
    echo "options:  ${1:-(none, defaults only)}"
    if active; then sc restart "$UNIT"; echo "restarted"; report_sequence; fi
}

sequence_config() {  # $1 = uploaded file; checked, then made the config
    if ! out=$(PYTHONDONTWRITEBYTECODE=1 python3 "$DIR/sequence.py" \
            --config "$1" $(default_args) $(sed -n 's/^SHOW_ARGS=//p' "$CONF" 2>/dev/null) \
            --check 2>&1); then
        echo "invalid sequence, nothing changed:" >&2
        printf '%s\n' "$out" >&2
        rm -f "$1"
        return 1
    fi
    if cmp -s "$1" "$SEQUENCE"; then
        rm -f "$1"
        echo "sequence unchanged"
    else
        mv "$1" "$SEQUENCE"
        echo "sequence saved to $SEQUENCE (the show picks it up)"
    fi
    printf '%s\n' "$out" | head -n 1 | sed 's/^[^ ]* ok:/sequence ok:/'
}

report_sequence() {
    # after a (re)start: what the show made of the config file; the line is
    # logged ~2 s after the start (python + numpy load slowly on the Pi)
    for _ in $(seq 1 40); do
        line=$(log_lines | grep -m 1 -E '(^no |^/).*\.ya?ml' || true)
        if [ -n "$line" ]; then
            case "$line" in
                *"NOT USED"*) echo "sequence: $line" >&2; return 1 ;;
                *) echo "sequence: $line" ;;
            esac
            return 0
        fi
        active || { echo "the show did not start (make status)" >&2; return 1; }
        sleep 0.5
    done
    echo "sequence: not reported yet (make status)" >&2
}

signal_pause() {  # $1 = wanted state: pause | resume
    if ! active; then
        echo "not running" >&2
        return 1
    fi
    if { [ "$1" = pause ] && paused; } || { [ "$1" = resume ] && ! paused; }; then
        echo "already ${1}d"
        return 0
    fi
    sc kill --signal=USR1 --kill-whom=main "$UNIT"
    # the PAUSED/RESUMED line can take a moment to reach the journal
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
        if { [ "$1" = pause ] && paused; } || { [ "$1" = resume ] && ! paused; }; then
            echo "${1}d"
            return 0
        fi
        sleep 0.25
    done
    echo "signal sent, but state not confirmed in the log (check: make status)" >&2
    return 1
}

status() {
    if ! active; then
        echo "not running ($(sc is-active "$UNIT" || true))"
    elif paused; then
        echo "paused (pid $(sc show -p MainPID --value "$UNIT"))"
    else
        echo "running (pid $(sc show -p MainPID --value "$UNIT"))"
    fi
    echo "enabled at boot: $(sc is-enabled "$UNIT" 2>/dev/null || echo no)"
    echo "defaults: $(default_args)"
    echo "options:  $(sed -n 's/^SHOW_ARGS=//p' "$CONF" 2>/dev/null)"
    echo "--- log (current run)"
    log_lines | tail -n 5
}

case "${1:-}" in
    install) install_unit ;;
    start) sc start "$UNIT" && echo "started" && report_sequence ;;
    stop) sc stop "$UNIT" && echo "stopped" ;;
    restart) sc restart "$UNIT" && echo "restarted" && report_sequence ;;
    pause) signal_pause pause ;;
    resume) signal_pause resume ;;
    status) status ;;
    config) configure "${2:-}" ;;
    sequence) sequence_config "$2" ;;
    *) echo "usage: $0 install|start|stop|restart|pause|resume|status|config [options]|sequence <file>" >&2
       exit 2 ;;
esac

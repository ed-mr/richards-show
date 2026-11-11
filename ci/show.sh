#!/bin/sh
# Controls the slideshow on the Pi. Runs on hydra (deployed next to
# fb-slideshow.py), usually invoked over ssh by the Makefile.
#
#   show.sh start|stop|restart|pause|resume|status
#
# SHOW_ARGS (env) is passed to fb-slideshow.py on start, e.g. "--shuffle".
# Log and pid file live in /tmp (tmpfs), so nothing is written to the SD card.
set -eu

DIR=$(cd "$(dirname "$0")/.." && pwd)
LOG=/tmp/fb-slideshow.log
PIDFILE=/tmp/fb-slideshow.pid
SHOW_ARGS=${SHOW_ARGS:-}

running_pid() {
    pgrep -f 'python3? .*fb-slideshow\.py' | head -n 1
}

paused() {
    # fb-slideshow.py logs PAUSED / RESUMED on every SIGUSR1
    [ -f "$LOG" ] && [ "$(grep -E '^(PAUSED|RESUMED)$' "$LOG" | tail -n 1)" = PAUSED ]
}

start() {
    pid=$(running_pid); if [ -n "$pid" ]; then
        echo "already running (pid $pid)"
        return 0
    fi
    # shellcheck disable=SC2086  # SHOW_ARGS is meant to be split
    PYTHONDONTWRITEBYTECODE=1 setsid nohup python3 "$DIR/fb-slideshow.py" $SHOW_ARGS \
        >"$LOG" 2>&1 </dev/null &
    echo $! >"$PIDFILE"
    sleep 2
    pid=$(running_pid); if [ -n "$pid" ]; then
        echo "started (pid $pid)"
    else
        echo "failed to start, log:" >&2
        cat "$LOG" >&2
        return 1
    fi
}

stop() {
    pid=$(running_pid) || true
    if [ -z "$pid" ]; then
        echo "not running"
        return 0
    fi
    kill -TERM "$pid"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        [ -n "$(running_pid)" ] || { rm -f "$PIDFILE"; echo "stopped"; return 0; }
        sleep 0.5
    done
    echo "still running after SIGTERM (pid $pid)" >&2
    return 1
}

signal_pause() {  # $1 = wanted state: pause | resume
    pid=$(running_pid) || true
    if [ -z "$pid" ]; then
        echo "not running" >&2
        return 1
    fi
    if { [ "$1" = pause ] && paused; } || { [ "$1" = resume ] && ! paused; }; then
        echo "already ${1}d"
        return 0
    fi
    kill -USR1 "$pid"
    sleep 0.5
    if paused; then echo "paused"; else echo "running"; fi
}

status() {
    pid=$(running_pid) || true
    if [ -z "$pid" ]; then
        echo "not running"
    elif paused; then
        echo "paused (pid $pid)"
    else
        echo "running (pid $pid)"
    fi
    if [ -f "$LOG" ]; then
        echo "--- $LOG (last lines)"
        tail -n 5 "$LOG"
    fi
}

case "${1:-}" in
    start) start ;;
    stop) stop ;;
    restart) stop && start ;;
    pause) signal_pause pause ;;
    resume) signal_pause resume ;;
    status) status ;;
    *) echo "usage: $0 start|stop|restart|pause|resume|status" >&2; exit 2 ;;
esac

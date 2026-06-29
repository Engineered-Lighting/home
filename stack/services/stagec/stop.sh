#!/usr/bin/env bash
# Stop the Stage C perception service.
PIDFILE="$HOME/stagec/stagec.pid"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    PID="$(cat "$PIDFILE")"
    kill "$PID"
    for _ in $(seq 1 25); do
        kill -0 "$PID" 2>/dev/null || break
        sleep 0.2
    done
    if kill -0 "$PID" 2>/dev/null; then
        kill -9 "$PID"
        echo "stagec force-killed (pid $PID)"
    else
        echo "stagec stopped (pid $PID)"
    fi
    rm -f "$PIDFILE"
else
    echo "stagec not running"
    rm -f "$PIDFILE"
fi

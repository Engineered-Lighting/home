#!/usr/bin/env bash
# Start the Stage C perception service (nohup + pidfile + log).
set -u
DIR="$HOME/stagec"
PIDFILE="$DIR/stagec.pid"
LOG="$DIR/stagec.log"
PY="$HOME/autocalib/venv/bin/python"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "stagec already running (pid $(cat "$PIDFILE"))"
    exit 0
fi

cd "$DIR"
nohup "$PY" -u stagec.py >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"
sleep 1
if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "stagec started (pid $(cat "$PIDFILE")), log: $LOG"
else
    echo "stagec FAILED to start — see $LOG"
    exit 1
fi

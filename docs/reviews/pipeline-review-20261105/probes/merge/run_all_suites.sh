#!/bin/bash
# Run every in-scope tool's unittest suite in one checkout (e.g. the merge-rehearsal tree).
# Usage: run_all_suites.sh ROOT [PYTHON] [LOGDIR]
# PYTHON defaults to python3; pass a venv python with homeassistant installed to run den_lighting's
# HA-dependent test modules too. Prints one line per suite: path, rc, seconds, unittest summary.
ROOT=$1; PY=${2:-python3}; LOG=${3:-$(mktemp -d)}
mkdir -p "$LOG"; cd "$ROOT" || exit 2
for p in tools/den-take tools/den-label tools/den-n2 tools/den-core tools/correction-capture tools/den-register tools/den-observe tools/den-track tools/den-bundle tools/den-light tools/den-photometry tools/den-verify ha-config/custom_components/den_lighting/tests; do
  [ -d "$p" ] || { echo "$p MISSING"; continue; }
  n=$(echo $p | tr / _); s=$(date +%s)
  timeout 1200 $PY -B -m unittest discover -s "$p" -p 'test*.py' > "$LOG/$n.log" 2>&1; rc=$?
  echo "$p rc=$rc $(( $(date +%s)-s ))s $(grep -E '^(Ran|OK|FAILED)' "$LOG/$n.log" | tr '\n' ' ')"
done
echo "logs in $LOG"

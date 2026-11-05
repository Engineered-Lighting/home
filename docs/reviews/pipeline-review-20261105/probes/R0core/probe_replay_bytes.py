"""probe_replay_bytes: replay determinism of den-core on synthetic data (statistic, not a finding).

Runs tools/den-core/run.py --synthetic evening --touch --game --policies need incumbent decide
three times into fresh temp folders (two different PYTHONHASHSEED values), compares the log.jsonl
bytes, then runs check.py and validate.py on the first log. Synthetic only, offline, temp dirs only.

    python3 probe_replay_bytes.py /path/to/den-core-checkout
"""
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-core"
tmp = Path(tempfile.mkdtemp(prefix="probe-replay-"))
logs = []
for n, seed in enumerate(("0", "12345", "random")):
    out = tmp / ("run%d" % n)
    env = dict(os.environ, PYTHONHASHSEED=seed)
    r = subprocess.run([sys.executable, "-B", str(tool / "run.py"), "--synthetic", "evening", "--touch", "--game",
                        "--policies", "need", "incumbent", "decide", "--out", str(out)],
                       cwd=str(root), env=env, capture_output=True, text=True)
    if r.returncode:
        print("FAIL: run %d exited %d: %s" % (n, r.returncode, r.stderr[-400:]))
        sys.exit(1)
    data = (out / "log.jsonl").read_bytes()
    logs.append(data)
    print("RESULT: run %d (PYTHONHASHSEED=%s) %d bytes sha256 %s" % (n, seed, len(data),
                                                                  hashlib.sha256(data).hexdigest()[:16]))
print(("PASS" if len(set(logs)) == 1 else "FAIL") + ": the three logs are %sbyte-identical"
      % ("" if len(set(logs)) == 1 else "NOT "))
for script in ("check.py", "validate.py"):
    r = subprocess.run([sys.executable, "-B", str(tool / script), str(tmp / "run0" / "log.jsonl")],
                       cwd=str(root), capture_output=True, text=True)
    print("RESULT: %s exit %d:\n  %s" % (script, r.returncode, r.stdout.strip().replace("\n", "\n  ")))

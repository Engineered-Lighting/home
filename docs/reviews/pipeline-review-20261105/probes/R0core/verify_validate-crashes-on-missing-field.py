"""verify_validate-crashes-on-missing-field: does validate.py print a traceback instead of its per-check lines
when a decide decision lacks the 'skipped' field?

Writes a synthetic evening log (run.py --synthetic evening --touch --policies need incumbent decide) in a temp
dir, then three variants: unchanged; 'skipped' renamed to 'skip' in every decide decision (an older or other
writer); 'skipped' removed from every decide decision. Runs validate.py on each and prints the exit code, how
many per-check lines (replay/need parity/only the look/holds/quiet/overall) reached stdout, and stderr's last line.
Synthetic data only, temp dirs only, no host.

    python3 -B verify_validate-crashes-on-missing-field.py /path/to/den-core-checkout
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-core"
tmp = Path(tempfile.mkdtemp(prefix="verify-validate-"))
r = subprocess.run([sys.executable, "-B", str(tool / "run.py"), "--synthetic", "evening", "--touch", "--policies",
                    "need", "incumbent", "decide", "--out", str(tmp / "ev")], cwd=str(tmp), capture_output=True, text=True)
assert r.returncode == 0, r.stderr
lines = (tmp / "ev" / "log.jsonl").read_text("ascii").split("\n")[:-1]


def canon(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def variant(name, edit):
    out = [lines[0]]
    n = 0
    for text in lines[1:]:
        e = json.loads(text)
        if edit and e["kind"] == "decision" and e["source"] == "decide":
            edit(e["data"]); n += 1
            text = canon(e)
        out.append(text)
    path = tmp / (name + ".jsonl")
    path.write_text("\n".join(out) + "\n", "ascii")
    v = subprocess.run([sys.executable, "-B", str(tool / "validate.py"), str(path)], cwd=str(tmp),
                       capture_output=True, text=True)
    checks = [x for x in v.stdout.splitlines()
              if x.split(":")[0] in ("replay", "need parity", "only the look", "holds", "quiet", "overall")]
    print("RESULT: %-8s edited %d decide decision(s): exit %d; %d per-check/overall line(s) on stdout; stderr: %s"
          % (name, n, v.returncode, len(checks), (v.stderr.strip().splitlines() or ["(none)"])[-1]))
    for x in checks:
        print("    " + x[:150])
    return v, checks


variant("baseline", None)
v1, c1 = variant("renamed", lambda d: d.__setitem__("skip", d.pop("skipped")))
v2, c2 = variant("removed", lambda d: d.pop("skipped"))
crashed = all(v.returncode == 1 and not c and "Traceback" in v.stderr and "KeyError" in v.stderr for v, c in ((v1, c1), (v2, c2)))
print("FAIL: validate prints only a traceback (exit 1), no per-check lines" if crashed else "PASS")

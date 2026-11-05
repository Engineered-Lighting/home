"""verify_check-header-compare-untested_repro: is check.py's line-1 (header) comparison pinned by any test, and
which header edits does it actually catch?

1. Copies tools/den-core and what its tests need from the checkout into a temp folder (empty .git marker).
2. Writes a synthetic evening log there (run.py --synthetic evening --touch, --out in a temp dir).
3. With the unmutated check.py, checks header-only edits (each re-canonicalised):
     sources[0].params changed, sources replaced by another file name + sha256,
     policies[0].version changed, actuation changed, an extra header key added.
4. Applies the mutant M1 (loop starts at line 2, i.e. the header is never compared), repeats 3,
   then runs test_spine.CheckTest with -f and the whole den-core suite.
Synthetic data only, temp dirs only, no host; the checkout itself is never modified.

    python3 -B verify_check-header-compare-untested_repro.py /path/to/den-core-checkout
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
PARTS = ["tools/den-core", "tools/la-latency", "stack/services/perception-lab",
         "ha-config/custom_components/den_lighting",
         "stack/services/lighting-brain-bridges"]
tmp = Path(tempfile.mkdtemp(prefix="verify-hdr-"))
work = tmp / "co"
for part in PARTS:
    if (root / part).exists():
        shutil.copytree(root / part, work / part)
(work / ".git").mkdir()
tool = work / "tools" / "den-core"
out = tmp / "out"
r = subprocess.run([sys.executable, "-B", str(tool / "run.py"), "--synthetic", "evening", "--touch", "--out", str(out)],
                   cwd=str(tmp), capture_output=True, text=True)
assert r.returncode == 0, r.stderr
lines = (out / "log.jsonl").read_text("ascii").split("\n")[:-1]
header = json.loads(lines[0])
print("RESULT: header keys:", sorted(header), "sources:", header["sources"])


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def edits():
    h = json.loads(lines[0]); h["sources"][0]["params"]["seed"] = 1; yield "sources[0].params.seed changed", h
    h = json.loads(lines[0]); h["sources"] = [{"name": "other.jsonl", "sha256": "0" * 64}]; yield "sources replaced", h
    h = json.loads(lines[0]); h["policies"][0]["version"] = "den_lighting@9.9.9"; yield "policies[0].version changed", h
    h = json.loads(lines[0]); h["actuation"] = "replay"; yield "actuation synthetic->replay", h
    h = json.loads(lines[0]); h["extra"] = 1; yield "extra header key", h


def check_all(label):
    results = {}
    for n, (name, h) in enumerate(edits()):
        path = tmp / ("v-%s-%d.jsonl" % (label, n))
        path.write_text("\n".join([canon(h)] + lines[1:]) + "\n", "ascii")
        r = subprocess.run([sys.executable, "-B", str(tool / "check.py"), str(path)], cwd=str(tmp),
                           capture_output=True, text=True)
        results[name] = r.stdout.splitlines()[0] if r.stdout else r.stderr.strip()[-120:]
        print("RESULT: [%s] %-30s -> exit %d: %s" % (label, name, r.returncode, results[name]))
    return results


before = check_all("original")
src = tool / "check.py"
text = src.read_text("utf-8")
old = "for index in range(max(len(want), len(got))):"
assert text.count(old) == 1
src.write_text(text.replace(old, "for index in range(1, max(len(want), len(got))):"), "utf-8")
after = check_all("M1")
r = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", "test_spine.CheckTest"], cwd=str(tool),
                   capture_output=True, text=True)
print("RESULT: M1 test_spine.CheckTest exit %d: %s" % (r.returncode, r.stderr.strip().splitlines()[-1]))
r2 = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tools/den-core", "-p", "test_*.py"],
                    cwd=str(work), capture_output=True, text=True)
print("RESULT: M1 full den-core suite exit %d: %s | %s" % (r2.returncode, r2.stderr.strip().splitlines()[-3],
                                                         r2.stderr.strip().splitlines()[-1]))
caught_only_by_header = [k for k in before if before[k].startswith("FAIL") and after[k].startswith("OK")]
print("RESULT: header-only edits caught by the original but passed by M1:", caught_only_by_header)
print("RESULT: header-only edits passed even by the original:", [k for k in before if before[k].startswith("OK")])
print("FAIL: M1 survives the suite" if r.returncode == 0 and r2.returncode == 0 and caught_only_by_header else "PASS")
shutil.rmtree(tmp, ignore_errors=True)

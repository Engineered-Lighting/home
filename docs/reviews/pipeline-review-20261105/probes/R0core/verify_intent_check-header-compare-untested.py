"""verify_check-header-compare-untested: what does check.py's header (line 1) comparison actually protect,
and does any den-core test pin it?

On a den-core checkout, offline, synthetic data, temp dirs only (the checkout is never modified):
  A. Unmutated code. A synthetic incumbent evening log is written, then two header-only edits are made
     (re-canonicalised): (1) the incumbent's 'version' label, (2) the 'sha256' of a header 'sources' entry.
     check.check() is run on each. Expected if the header compare matters: (1) 'line 1 differs'.
     For (2) the finding claims the header compare protects the sources list.
  B. Mutant M1 (the comparison loop starts at index 1, skipping the header), in a copy: the same edit (1)
     is checked again, then test_spine.CheckTest and the whole den-core suite are run on the copy.

    python3 verify_check-header-compare-untested.py /path/to/den-core-checkout [MUTATION_PARENT_DIR]
"""
import json
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
base = sys.argv[2] if len(sys.argv) > 2 else None
PARTS = ["tools/den-core", "tools/la-latency", "stack/services/perception-lab",
         "ha-config/custom_components/den_lighting"] + [
    str(p.relative_to(root)) for p in root.glob("stack/services/lighting-brain-bridges/*-ha/tests/fixtures")]

SCRIPT = r'''
import json, sys
sys.dont_write_bytecode = True
tool = sys.argv[1]; out = sys.argv[2]
sys.path.insert(0, tool)
import events, runner, incumbent, synthetic, check
from pathlib import Path
items, _, _ = synthetic.evening(unclear_rate=0, invalid_rate=0, fail_rate=0, touch=True)
header, log = runner.run(runner.tagged(items, "synthetic"), [incumbent.make_policy()], synthetic=True,
                         sources=[{"name": "synthetic:evening", "params": {}, "sha256": "a" * 64}])
out = Path(out)
events.write(out / "log.jsonl", header, log)
lines = (out / "log.jsonl").read_text("ascii").split("\n")[:-1]
def variant(name, edit):
    h = json.loads(lines[0]); edit(h)
    p = out / (name + ".jsonl")
    p.write_text("\n".join([events.dumps(h)] + lines[1:]) + "\n", "ascii")
    return check.check(p)
print("base:", check.check(out / "log.jsonl")["reason"])
r = variant("version", lambda h: h["policies"][0].__setitem__("version", "den_lighting@9.9.9"))
print("version label edit:", r["ok"], r["reason"])
r = variant("sources", lambda h: h["sources"][0].__setitem__("sha256", "b" * 64))
print("sources sha256 edit:", r["ok"], r["reason"])
'''


def run_script(tool, label):
    out = Path(tempfile.mkdtemp(prefix="verify-hdr-out-"))
    r = subprocess.run([sys.executable, "-B", "-c", SCRIPT, str(tool), str(out)], capture_output=True, text=True,
                       cwd=str(out))
    print("RESULT %s:" % label)
    print("\n".join("  " + x for x in (r.stdout + r.stderr).strip().splitlines()))
    shutil.rmtree(out, ignore_errors=True)


run_script(root / "tools" / "den-core", "A unmutated")

tmp = Path(tempfile.mkdtemp(prefix="verify-hdr-mut-", dir=base))
for part in PARTS:
    if (root / part).exists():
        shutil.copytree(root / part, tmp / part)
(tmp / ".git").mkdir()
path = tmp / "tools" / "den-core" / "check.py"
text = path.read_text("utf-8")
old, new = "for index in range(max(len(want), len(got))):", "for index in range(1, max(len(want), len(got))):"
assert text.count(old) == 1
path.write_text(text.replace(old, new), "utf-8")
run_script(tmp / "tools" / "den-core", "B mutant M1")
for target, args in (("CheckTest", ["-m", "unittest", "-f", "test_spine.CheckTest"]),
                     ("full suite", ["-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"])):
    r = subprocess.run([sys.executable, "-B"] + args, cwd=str(tmp / "tools" / "den-core"), capture_output=True,
                       text=True, timeout=900)
    print("RESULT B mutant M1 %s: exit %d, %s" % (target, r.returncode, (r.stderr.strip().splitlines() or ["?"])[-1]))
shutil.rmtree(tmp, ignore_errors=True)

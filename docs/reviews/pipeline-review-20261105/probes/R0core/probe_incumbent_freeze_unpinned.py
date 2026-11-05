"""probe_incumbent_freeze_unpinned: den-core adopts whatever den_lighting policy.py sits in the checkout as
"the frozen incumbent", under the same version label.

The ladder freezes the incumbent as read statically: manifest 1.0.4, VACANCY_SECONDS 120, gaming 38/255 at
warmest, its hold semantics. incumbent.make_policy() records the sha256 and constants of whatever policy.py it
finds above tools/den-core, but pins none of them. This probe copies the checkout's den-core and den_lighting
into a temp folder, changes VACANCY_SECONDS 120 -> 90 (manifest untouched), runs the synthetic evening, and
shows that run.py, check.py and validate.py all accept it and label it den_lighting@1.0.4. Temp copy only.

    python3 probe_incumbent_freeze_unpinned.py /path/to/den-core-checkout
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tmp = Path(tempfile.mkdtemp(prefix="probe-freeze-"))
for part in [str(q.relative_to(root)) for q in (root / "tools" / "den-core", *root.glob("tools/*-latency"),
                                                 root / "stack" / "services" / "perception-lab",
                                                 root / "ha-config" / "custom_components" / "den_lighting")]:
    shutil.copytree(root / part, tmp / "repo" / part)
(tmp / "repo" / ".git").mkdir()
policy = tmp / "repo" / "ha-config/custom_components/den_lighting/policy.py"
text = policy.read_text("utf-8")
assert text.count("VACANCY_SECONDS = 120\n") == 1
policy.write_text(text.replace("VACANCY_SECONDS = 120\n", "VACANCY_SECONDS = 90\n"), "utf-8")
out = tmp / "ev"
tool = tmp / "repo" / "tools" / "den-core"
r = subprocess.run([sys.executable, "-B", str(tool / "run.py"), "--synthetic", "evening", "--policies", "incumbent",
                    "--out", str(out)], cwd=str(tmp / "repo"), capture_output=True, text=True)
print("RESULT: run.py exit %d" % r.returncode)
header = json.loads((out / "log.jsonl").read_text("ascii").splitlines()[0])
inc = next(p for p in header["policies"] if p["id"] == "incumbent")
print("RESULT: logged incumbent %s, config %s" % (inc["version"], inc["config"]))
c = subprocess.run([sys.executable, "-B", str(tool / "check.py"), str(out / "log.jsonl")], capture_output=True, text=True)
v = subprocess.run([sys.executable, "-B", str(tool / "validate.py"), str(out / "log.jsonl")], capture_output=True, text=True)
print("RESULT: check.py exit %d: %s" % (c.returncode, c.stdout.strip()))
print("RESULT: validate.py exit %d: %s" % (v.returncode, v.stdout.strip().splitlines()[-2]))
if r.returncode == 0 and inc["config"]["vacancy_seconds"] == 90 and inc["version"] == "den_lighting@1.0.4" \
        and c.returncode == 0:
    print("FAIL: an edited incumbent (VACANCY_SECONDS 90) runs as den_lighting@1.0.4 and passes check and validate; "
          "nothing pins the frozen values")
else:
    print("PASS: the edited incumbent was refused")

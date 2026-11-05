#!/usr/bin/env python3
"""verify_model-open-slot-refusal-untested: model.run_session's refusal while a METHOD-V1 Stage 1 slot is
open works today, and no test exercises it.

What it checks (temp folders only, synthetic, no network):
 1. model.run_session(<a complete session folder>, <meter_norm>, model.load_stage1()) raises Refused that
    names the open slots, and writes nothing into the folder.
 2. With the refusal line removed (applied to a temp copy of tools/den-light), the same call no longer
    refuses at that point (it goes on to read the session's files).
 3. Statically: every call to model.run_session / model.main in the test files, with the method passed.
The full-suite mutant run is separate (see the verdict's evidence).

How to run:
    python3 -B verify_model-open-slot-refusal-untested.py <root of the claude/rung-8-stage1 checkout>
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
DEN = ROOT / "tools" / "den-light"

CALL = r"""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import model
m = model.load_stage1()
print("RESULT open slots in the committed block:", len(m.open))
with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp)
    (folder / "session.json").write_text(json.dumps({"outcome": "complete", "id": "s1", "stage0": None}))
    try:
        model.run_session(folder, {"parameters": {}, "parameters_sha256": "0" * 64}, m)
        print("RESULT no exception")
    except model.Refused as exc:
        print("RESULT Refused:", str(exc)[:110])
    except Exception as exc:
        print("RESULT got past the refusal point:", type(exc).__name__, str(exc)[:90])
    print("RESULT files after:", sorted(p.name for p in folder.iterdir()))
"""


def run_in(den_dir):
    return subprocess.run([sys.executable, "-B", "-c", CALL, str(den_dir)], capture_output=True, text=True,
                          cwd=tempfile.gettempdir(), timeout=120).stdout.strip()


def main():
    print("== committed code")
    out = run_in(DEN)
    print(out)
    refused_today = "Refused:" in out and "open" in out.lower()
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "repo"
        shutil.copytree(ROOT / "tools", copy / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "docs", copy / "docs")
        path = copy / "tools" / "den-light" / "model.py"
        text = path.read_text(encoding="utf-8")
        old = '    if scored:\n        method1.refuse_if_open("A scored light model")\n'
        assert text.count(old) == 1
        path.write_text(text.replace(old, ""), encoding="utf-8")
        print("== refusal line removed (temp copy)")
        out2 = run_in(copy / "tools" / "den-light")
        print(out2)
    print("== calls in the tests")
    for name in ("test_stage1.py", "test_den_light.py"):
        for n, line in enumerate((DEN / name).read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"model\.(run_session|main)\(", line):
                print(f"RESULT {name}:{n}: {line.strip()}")
    gone = "got past the refusal point" in out2 or "no exception" in out2
    print("RESULT refusal works today:", refused_today, "| removing it changes behaviour:", gone)


if __name__ == "__main__":
    main()

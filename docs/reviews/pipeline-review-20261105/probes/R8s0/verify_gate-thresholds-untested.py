#!/usr/bin/env python3
"""verify_gate-thresholds-untested.py: independent re-check of finding gate-thresholds-untested.

What it checks (rung 8 Stage 0, tools/den-light): three mutants of the Stage 0 gate logic, each
applied alone to a fresh temp copy of tools/den-light (plus the fixture the suite reads). For each,
the WHOLE suite (unittest discover, failfast) runs; a mutant "survives" only if every test passes.

  M1 meter.py: residual limit R doubled in the residual comparison
  M2 meter.py: region tolerance P doubled in the region comparison
  M3 sweep.py: --recheck skips its repeat check (always scores)

How to run:
  python3 -B verify_gate-thresholds-untested.py <root of the claude/rung-8-stage0 checkout>

Synthetic data and fakes on 127.0.0.1 only (the suite's own); temp dirs only; nothing in the
checkout is modified. Prints one line per mutant and a RESULT line.
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MUTANTS = [
    ("meter.py", "M1 residual limit R doubled",
     'residual_pass = all(value <= method["residual_max_codes"] for value in residual.values())',
     'residual_pass = all(value <= 2 * method["residual_max_codes"] for value in residual.values())'),
    ("meter.py", "M2 region tolerance P doubled",
     'deviation <= tolerance + 1e-12', 'deviation <= 2 * tolerance + 1e-12'),
    ("sweep.py", "M3 --recheck skips the repeat check",
     '    if agrees:\n        record.update(verdict_fields', '    if True:\n        record.update(verdict_fields'),
]


def main():
    root = Path(sys.argv[1]).resolve()
    only = set(sys.argv[2:])
    rows = []
    for file, name, old, new in MUTANTS:
        if only and name.split()[0] not in only:
            continue
        with tempfile.TemporaryDirectory(prefix="r8s0-vmut-") as tmp:
            tool = Path(tmp) / "tools" / "den-light"
            shutil.copytree(root / "tools" / "den-light", tool)
            (Path(tmp) / "tools" / "fixtures").mkdir()
            shutil.copy(root / "tools/fixtures/installed-oak-capture-20261016.py", Path(tmp) / "tools/fixtures/")
            target = tool / file
            text = target.read_text(encoding="utf-8")
            if text.count(old) != 1:
                rows.append((name, "not-run", f"pattern found {text.count(old)} times"))
                print(f"not-run  {name}: pattern found {text.count(old)} times", flush=True)
                continue
            target.write_text(text.replace(old, new), encoding="utf-8")
            start = time.time()
            proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", str(tool)],
                                  cwd=tool, capture_output=True, text=True, timeout=1200)
            lines = proc.stderr.strip().splitlines()
            ran = [l for l in lines if l.startswith("Ran ")]
            failed = [l for l in lines if l.startswith(("FAIL:", "ERROR:"))]
            result = "caught" if proc.returncode != 0 else "survived"
            detail = (failed[0] if failed else "") + " | " + (ran[0] if ran else "") + " | " + (lines[-1] if lines else "")
            rows.append((name, result, detail))
            print(f"{result:9} {name} ({time.time() - start:.0f}s) :: {detail}", flush=True)
    survived = [r for r in rows if r[1] == "survived"]
    print(f"RESULT: {len(survived)} of {len(rows)} mutants survived the full suite: "
          + ", ".join(r[0] for r in survived))
    return 0


if __name__ == "__main__":
    sys.exit(main())

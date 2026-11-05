#!/usr/bin/env python3
"""Verify finding fixture-coverage-refusal-untested (den-label, tools/den-label/n1.py freeze_labels).

What it checks
  1. Baseline: on a copy of tools/den-label + tools/den-take, a session registered and
     labelled AFTER the fixture freeze makes `freeze_labels` refuse with
     "does not cover these sessions and checks" (the check works when reached).
  2. Mutant: the same scenario with the check replaced by `if False:` freezes labels-v1
     against a fixture-v1 that lacks the new session (the mutant is not equivalent).
  3. Optional (--suite): runs the WHOLE den-label unittest suite (failfast) on the mutant
     copy and prints whether any test catches it (caught) or none does (survived).

Synthetic takes only (built by the repo's own test helpers), temp folders only, no network.
The checkout is never modified; copies go under the work folder.

How to run
  python3 -B verify_fixture-coverage-refusal-untested.py <den-label checkout> [work folder] [--suite]
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

OLD = 'if {s["take"]: s["check_sha256"] for s in fixture["sessions"]} != checks:'
NEW = "if False:"

SCENARIO = r'''
import json, unittest
from unittest import mock
import test_den_label as T
from test_den_label import n1, BASE_UNIX, DAY, staged_take


class Probe(T.Base):
    def test_session_added_after_fixture_freeze(self):
        ws = T.FreezeTests.build(self, names=(("a1", "hard-a"), ("b1", "hard-b")))
        with mock.patch.object(n1, "now", return_value=BASE_UNIX + 10 * DAY):
            fixture_line, _ = ws.freeze_fixture()
        print("INFO", fixture_line)
        staged_take(self.takes, "a2", "hard-a", BASE_UNIX + 11 * DAY)  # recorded after the fixture freeze
        added, notes = ws.scan()
        print("INFO registered after fixture freeze:", [(e["session"], e["split"]) for e in added], notes)
        with mock.patch.object(n1, "now", return_value=BASE_UNIX + 12 * DAY):
            self.boxes_done(ws, "a2")
            self.labels_done(ws, "a2")
        with mock.patch.object(n1, "now", return_value=BASE_UNIX + 13 * DAY):
            try:
                line, record, _ = ws.freeze_labels()
            except n1.Refused as exc:
                print("RESULT refused:", exc)
                return
        fixture = json.loads((ws.root / "fixture-v1" / "fixture.json").read_bytes())
        labels = json.loads((ws.root / "labels-v1" / "labels.json").read_bytes())
        print("RESULT frozen:", line)
        print("INFO fixture-v1 sessions:", [s["take"] for s in fixture["sessions"]])
        print("INFO labels-v1 sessions:", [(s["take"], s["split"]) for s in labels["sessions"]])
        print("INFO labels.json top-level keys:", sorted(labels))
        print("INFO printed record:", json.dumps(record["gold_set"]))


if __name__ == "__main__":
    unittest.main()
'''


def copy_tools(root, work, name):
    folder = Path(tempfile.mkdtemp(prefix=name + "-", dir=work))
    for sub in ("den-label", "den-take"):
        shutil.copytree(root / "tools" / sub, folder / "tools" / sub,
                        ignore=shutil.ignore_patterns("__pycache__"))
    return folder / "tools" / "den-label"


def mutate(tool_dir):
    path = tool_dir / "n1.py"
    text = path.read_text(encoding="utf-8")
    count = text.count(OLD)
    if count != 1:
        raise SystemExit(f"FAIL pattern occurs {count} times in n1.py; cannot apply the mutant")
    path.write_text(text.replace(OLD, NEW), encoding="utf-8")


def run_scenario(tool_dir):
    (tool_dir / "test_verify_probe.py").write_text(SCENARIO, encoding="utf-8")
    proc = subprocess.run([sys.executable, "-B", "test_verify_probe.py", "-v"], cwd=tool_dir,
                          capture_output=True, text=True, timeout=1200)
    out = [l for l in proc.stdout.splitlines() if l.startswith(("RESULT", "INFO"))]
    return proc.returncode, out, proc.stderr.strip().splitlines()[-1:] if proc.stderr.strip() else []


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    suite = "--suite" in sys.argv
    root = Path(args[0]).resolve()
    work = Path(args[1]) if len(args) > 1 else Path(tempfile.mkdtemp(prefix="verify-fixture-coverage-"))
    work.mkdir(parents=True, exist_ok=True)

    base = copy_tools(root, work, "baseline")
    code, out, tail = run_scenario(base)
    print("== baseline ==")
    print("\n".join(out), tail)
    base_refused = any(l.startswith("RESULT refused:") and "does not cover these sessions" in l for l in out)
    print(("PASS" if base_refused else "FAIL") + " baseline refuses a labels freeze against a stale fixture")

    mut = copy_tools(root, work, "mutant")
    mutate(mut)
    code, out, tail = run_scenario(mut)
    print("== mutant (coverage check -> if False) ==")
    print("\n".join(out), tail)
    mut_frozen = any(l.startswith("RESULT frozen:") for l in out)
    print(("PASS" if mut_frozen else "FAIL") + " mutant freezes labels against the stale fixture (not equivalent)")

    if suite:
        (mut / "test_verify_probe.py").unlink()
        start = time.time()
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "."], cwd=mut,
                              capture_output=True, text=True, timeout=7000)
        lines = proc.stderr.strip().splitlines()
        ran = next((l for l in lines if l.startswith("Ran ")), "")
        first_fail = next((l for l in lines if l.startswith(("FAIL:", "ERROR:"))), "")
        verdict = "caught" if proc.returncode != 0 else "survived"
        print(f"RESULT full suite on mutant: {verdict} ({ran}; {lines[-1] if lines else ''}; "
              f"{time.time() - start:.0f} s) {first_fail}")
    shutil.rmtree(base.parent.parent, ignore_errors=True)
    if not suite:
        shutil.rmtree(mut.parent.parent, ignore_errors=True)
    return 0 if (base_refused and mut_frozen) else 1


if __name__ == "__main__":
    sys.exit(main())

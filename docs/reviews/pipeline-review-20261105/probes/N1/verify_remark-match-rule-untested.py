#!/usr/bin/env python3
"""verify_remark-match-rule-untested: is den-label's re-mark matching rule ("two intervals match if their
overlap is at least half of each one's length", PROTOCOL-V1.md; n1.py match_intervals) pinned by its tests?

REPRODUCE lens, verifier for finding remark-match-rule-untested. Independent of the reporter's runner.

For the unmutated code and each mutant it copies <checkout>/tools/den-label and tools/den-take into a fresh
temp dir (never writes the checkout), applies one exact text replacement (must match exactly once), then:
  Part A  runs n1.agreement() in a subprocess on synthetic boundary pairs and prints "matched" per case,
          so you can see whether the mutant changes what the tool reports;
  Part B  runs the narrow test RemarkTests.test_agreement_on_hand_made_cases with failfast and prints
          caught/survived. CONTROL (no change) must pass; POSITIVE (threshold 0.15) must be caught, which
          shows the runner really tests the mutated copy.
  --full  also runs every test that reaches agreement() (found by grep: RemarkTests,
          RemarkBlindTests.test_agreement_comes_from_the_locked_first_pass,
          FreezeTests.test_freeze_verify_versions_and_refusals, DryRunTests) for M1 and M2.
Synthetic data only; nothing leaves 127.0.0.1.

Run:
    python3 verify_remark-match-rule-untested.py <den-label checkout> [work dir] [--full]
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

args = [a for a in sys.argv[1:] if not a.startswith("--")]
FULL = "--full" in sys.argv
ROOT = Path(args[0]).resolve()
WORK = Path(args[1]).resolve() if len(args) > 1 else Path(tempfile.mkdtemp(prefix="verify-remark-"))
WORK.mkdir(parents=True, exist_ok=True)

A_LINE = '            if (overlap >= 0.5 * (a["offset_t_s"] - a["onset_t_s"])\n'
B_LINE = '                    and overlap >= 0.5 * (b["offset_t_s"] - b["onset_t_s"]) and overlap > 0):'
MUTANTS = [
    ("CONTROL", "no change", None),
    ("M1", "threshold 0.5 -> 0.3 on both lengths",
     (A_LINE + B_LINE, A_LINE.replace("0.5", "0.3") + B_LINE.replace("0.5", "0.3"))),
    ("M2", "second interval's half-length condition dropped",
     (B_LINE, '                    and overlap > 0):')),
    ("M2b", "first interval's half-length condition dropped",
     (A_LINE + B_LINE, '            if (overlap > 0\n' + B_LINE)),
    ("POSITIVE", "threshold 0.5 -> 0.15 on both lengths (the 37.5%/20% pair now matches)",
     (A_LINE + B_LINE, A_LINE.replace("0.5", "0.15") + B_LINE.replace("0.5", "0.15"))),
]

SNIPPET = r'''
import json, sys
sys.path.insert(0, ".")
import n1
def iv(on, off, band="fine", case="sorting_mail"):
    return {"onset_t_s": on, "offset_t_s": off, "onset_kind": "touch", "offset_kind": "release",
            "band": band, "case": case, "sure": {"onset": True, "offset": True, "band": True}}
cases = [
    ("0-10 vs 5-15 (50% of each)", [iv(0, 10)], [iv(5, 15)]),
    ("0-10 vs 6-16 (40% of each)", [iv(0, 10)], [iv(6, 16)]),
    ("0-10 vs 6.5-16.5 (35% of each)", [iv(0, 10)], [iv(6.5, 16.5)]),
    ("0-4 vs 1-13 (75% of first, 25% of second)", [iv(0, 4)], [iv(1, 13)]),
    ("1-13 vs 0-4 (25% of first, 75% of second)", [iv(1, 13)], [iv(0, 4)]),
    ("100-104 vs 102.5-110 (the test's non-match: 37.5%/20%)", [iv(100, 104)], [iv(102.5, 110)]),
]
out = [(name, n1.agreement(a, b)["intervals"]["matched"]) for name, a, b in cases]
rep = n1.agreement([iv(0, 10, "fine")], [iv(6, 16, "casual")])
out.append(("band_agreement: fine 0-10 vs casual 6-16", rep["band_agreement"]))
print(json.dumps(out))
'''

AGREEMENT_TESTS = ["test_den_label.RemarkTests",
                   "test_den_label.RemarkBlindTests.test_agreement_comes_from_the_locked_first_pass",
                   "test_den_label.FreezeTests.test_freeze_verify_versions_and_refusals",
                   "test_den_label.DryRunTests"]


def make_copy(mid, repl):
    work = Path(tempfile.mkdtemp(prefix=f"remark-{mid}-", dir=WORK))
    for sub in ("den-label", "den-take"):
        shutil.copytree(ROOT / "tools" / sub, work / "tools" / sub, ignore=shutil.ignore_patterns("__pycache__"))
    target = work / "tools" / "den-label" / "n1.py"
    if repl:
        text = target.read_text(encoding="utf-8")
        old, new = repl
        if text.count(old) != 1:
            raise SystemExit(f"FAIL {mid}: pattern found {text.count(old)} times")
        target.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
    return work / "tools" / "den-label"


def run_tests(cwd, tests):
    started = time.time()
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f"] + tests, cwd=cwd,
                          capture_output=True, text=True, timeout=3600)
    lines = proc.stderr.strip().splitlines()
    ran = next((l for l in lines if l.startswith("Ran ")), "?")
    return ("caught" if proc.returncode else "survived"), f"{ran}; {lines[-1] if lines else ''} ({time.time() - started:.0f} s)"


def main():
    copies, semantics = {}, {}
    for mid, what, repl in MUTANTS:
        copies[mid] = make_copy(mid, repl)
        proc = subprocess.run([sys.executable, "-B", "-c", SNIPPET], cwd=copies[mid], capture_output=True, text=True)
        if proc.returncode:
            raise SystemExit(f"FAIL {mid}: semantic snippet crashed: {proc.stderr[-500:]}")
        semantics[mid] = json.loads(proc.stdout)
    print("PART A  matched count per synthetic pair (original = CONTROL)")
    names = [n for n, _ in semantics["CONTROL"]]
    print(f"  {'case':58} " + " ".join(f"{m:>8}" for m, _, _ in MUTANTS))
    for k, name in enumerate(names):
        print(f"  {name:58} " + " ".join(f"{str(semantics[m][k][1]):>8}" for m, _, _ in MUTANTS))
    for mid in ("M1", "M2", "M2b"):
        changed = [semantics["CONTROL"][k][0] for k in range(len(names)) if semantics[mid][k][1] != semantics["CONTROL"][k][1]]
        print(f"RESULT {mid} changes the report on {len(changed)} case(s): {changed}")
    print("\nPART B  narrow test RemarkTests.test_agreement_on_hand_made_cases (failfast)", flush=True)
    narrow = {}
    for mid, what, _ in MUTANTS:
        narrow[mid] = run_tests(copies[mid], ["test_den_label.RemarkTests.test_agreement_on_hand_made_cases"])
        print(f"  {mid:9} {narrow[mid][0]:9} {what} :: {narrow[mid][1]}", flush=True)
    ok_runner = narrow["CONTROL"][0] == "survived" and narrow["POSITIVE"][0] == "caught"
    print(f"{'PASS' if ok_runner else 'FAIL'} runner sanity: control passes, positive control caught")
    if FULL:
        print("\nFULL  every test that reaches agreement(): " + " ".join(t.split('.', 1)[1] for t in AGREEMENT_TESTS),
              flush=True)
        for mid in ("M1", "M2"):
            res = run_tests(copies[mid], AGREEMENT_TESTS)
            print(f"  {mid:9} {res[0]:9} :: {res[1]}", flush=True)
    for mid in ("M1", "M2", "M2b"):
        print(f"RESULT {mid} narrow test: {narrow[mid][0]}")
    shutil.rmtree(WORK, ignore_errors=True) if len(args) < 2 else None


if __name__ == "__main__":
    main()

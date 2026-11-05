#!/usr/bin/env python3
"""verify_label-boundary-mutants-survive: are den-label's two boundary rules in n1.check_labels pinned by tests?
  - the pause rule: same-case intervals in one segment whose gap is pause_s (6 s) OR LESS must be joined
    (PROTOCOL-V1.md item 4; n1.py `item["onset_t_s"] - prev["offset_t_s"] <= pause_s`);
  - quiet/uncertain spans never overlap an interval or each other; frame ranges are inclusive, so a span
    whose end frame is an interval's onset frame overlaps it (n1.py `if a <= d and c <= b`).

REPRODUCE lens, verifier for finding label-boundary-mutants-survive. Independent of the reporter's runner.

For the unmutated code and each mutant it copies <checkout>/tools/den-label and tools/den-take into a fresh
temp dir (never writes the checkout), applies one exact text replacement (must match exactly once), then:
  Part A  calls n1.check_labels in a subprocess on synthetic frames (one segment, frames 0..1000, t = n/10 s,
          so 56.0 - 50.0 is exactly 6.0) and prints accepted/refused per case;
  Part B  runs the narrow LabelTests the reporter named, failfast, and prints caught/survived. CONTROL must
          pass; POS5 (pause limit 5 s) and POS6 (overlap check removed) must be caught, which shows the
          runner really tests the mutated copy.
  --full  also runs the whole test_den_label module once with M5 and M6 applied together.
Synthetic data only; nothing leaves 127.0.0.1.

Run:
    python3 verify_label-boundary-mutants-survive.py <den-label checkout> [work dir] [--full]
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
WORK = Path(args[1]).resolve() if len(args) > 1 else Path(tempfile.mkdtemp(prefix="verify-boundary-"))
WORK.mkdir(parents=True, exist_ok=True)

PAUSE = '                and item["onset_t_s"] - prev["offset_t_s"] <= protocol.settings["pause_s"]):'
OVER = '            if a <= d and c <= b:'
M5 = (PAUSE, PAUSE.replace("<= protocol", "< protocol"))
M6 = (OVER, '            if a < d and c < b:')
MUTANTS = [
    ("CONTROL", "no change", [], ["test_den_label.LabelTests.test_the_pause_rule_never_applies_across_a_cut",
                                  "test_den_label.LabelTests.test_every_protocol_check"]),
    ("M5", "pause rule '<= pause_s' -> '< pause_s'", [M5],
     ["test_den_label.LabelTests.test_the_pause_rule_never_applies_across_a_cut",
      "test_den_label.LabelTests.test_every_protocol_check"]),
    ("M6", "span overlap test strict (shared end frame allowed)", [M6],
     ["test_den_label.LabelTests.test_every_protocol_check"]),
    ("POS5", "positive control: pause limit pause_s - 1 (5 s)", [(PAUSE, PAUSE.replace('["pause_s"]):', '["pause_s"] - 1):'))],
     ["test_den_label.LabelTests.test_the_pause_rule_never_applies_across_a_cut",
      "test_den_label.LabelTests.test_every_protocol_check"]),
    ("POS6", "positive control: span overlap check removed", [(OVER, '            if False:')],
     ["test_den_label.LabelTests.test_every_protocol_check"]),
]

SNIPPET = r'''
import json, sys
sys.path.insert(0, ".")
import n1
P = n1.load_protocol()
t_of = {n: n / 10 for n in range(0, 1001)}
def iv(a, b, case="sorting_mail"):
    return {"onset_n": a, "onset_kind": "touch", "offset_n": b, "offset_kind": "release", "band": "fine",
            "case": case, "sure": {"onset": True, "offset": True, "band": True}, "by": "owner"}
def sp(a, b, kind="quiet"):
    d = {"kind": kind, "start_n": a, "end_n": b, "by": "owner"}
    if kind == "quiet":
        d["case"] = "idle_seated"
    return d
cases = [
    ("same case, gap exactly 6.0 s (t 50.0 -> 56.0)", [iv(400, 500), iv(560, 700)], []),
    ("same case, gap 5.9 s", [iv(400, 500), iv(559, 700)], []),
    ("same case, gap 6.1 s", [iv(400, 500), iv(561, 700)], []),
    ("quiet 300-400 ends on interval 400-500's onset frame", [iv(400, 500)], [sp(300, 400)]),
    ("uncertain 500-550 starts on interval 400-500's offset frame", [iv(400, 500)], [sp(500, 550, "uncertain")]),
    ("quiet 300-400 and uncertain 400-450 share frame 400", [], [sp(300, 400), sp(400, 450, "uncertain")]),
    ("quiet 300-399 ends one frame before onset 400", [iv(400, 500)], [sp(300, 399)]),
]
out = []
for name, ivs, sps in cases:
    try:
        n1.check_labels(ivs, sps, P, [(0, 1000)], t_of)
        out.append((name, "accepted"))
    except n1.Refused as e:
        out.append((name, "refused"))
print(json.dumps({"pause_s": P.settings["pause_s"], "cases": out}))
'''


def make_copy(mid, repls):
    work = Path(tempfile.mkdtemp(prefix=f"boundary-{mid}-", dir=WORK))
    for sub in ("den-label", "den-take"):
        shutil.copytree(ROOT / "tools" / sub, work / "tools" / sub, ignore=shutil.ignore_patterns("__pycache__"))
    target = work / "tools" / "den-label" / "n1.py"
    text = target.read_text(encoding="utf-8")
    for old, new in repls:
        if text.count(old) != 1:
            raise SystemExit(f"FAIL {mid}: pattern found {text.count(old)} times")
        text = text.replace(old, new)
    target.write_text(text, encoding="utf-8", newline="\n")
    return work / "tools" / "den-label"


def run_tests(cwd, tests):
    started = time.time()
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f"] + tests, cwd=cwd,
                          capture_output=True, text=True, timeout=5400)
    lines = proc.stderr.strip().splitlines()
    ran = next((l for l in lines if l.startswith("Ran ")), "?")
    return ("caught" if proc.returncode else "survived"), f"{ran}; {lines[-1] if lines else ''} ({time.time() - started:.0f} s)"


def main():
    copies, sem = {}, {}
    for mid, what, repls, _ in MUTANTS:
        copies[mid] = make_copy(mid, repls)
        proc = subprocess.run([sys.executable, "-B", "-c", SNIPPET], cwd=copies[mid], capture_output=True, text=True)
        if proc.returncode:
            raise SystemExit(f"FAIL {mid}: semantic snippet crashed: {proc.stderr[-500:]}")
        sem[mid] = json.loads(proc.stdout)
    print(f"PART A  n1.check_labels on synthetic frames (pause_s = {sem['CONTROL']['pause_s']})")
    print(f"  {'case':62} " + " ".join(f"{m:>9}" for m, _, _, _ in MUTANTS))
    for k, (name, _) in enumerate(sem["CONTROL"]["cases"]):
        print(f"  {name:62} " + " ".join(f"{sem[m]['cases'][k][1]:>9}" for m, _, _, _ in MUTANTS))
    for mid in ("M5", "M6"):
        changed = [sem["CONTROL"]["cases"][k][0] for k in range(len(sem["CONTROL"]["cases"]))
                   if sem[mid]["cases"][k][1] != sem["CONTROL"]["cases"][k][1]]
        print(f"RESULT {mid} changes check_labels on {len(changed)} case(s): {changed}")
    print("\nPART B  narrow tests (failfast)", flush=True)
    narrow = {}
    for mid, what, _, tests in MUTANTS:
        narrow[mid] = run_tests(copies[mid], tests)
        print(f"  {mid:8} {narrow[mid][0]:9} {what} :: {narrow[mid][1]} tests={' '.join(t.split('.', 1)[1] for t in tests)}",
              flush=True)
    ok = narrow["CONTROL"][0] == "survived" and narrow["POS5"][0] == "caught" and narrow["POS6"][0] == "caught"
    print(f"{'PASS' if ok else 'FAIL'} runner sanity: control passes, both positive controls caught", flush=True)
    if FULL:
        both = make_copy("M5+M6", [M5, M6])
        res = run_tests(both, ["test_den_label"])
        print(f"\nFULL  whole test_den_label module with M5+M6 together: {res[0]} :: {res[1]}", flush=True)
    for mid in ("M5", "M6"):
        print(f"RESULT {mid} narrow tests: {narrow[mid][0]}")


if __name__ == "__main__":
    main()

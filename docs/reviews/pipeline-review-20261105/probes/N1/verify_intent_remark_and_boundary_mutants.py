#!/usr/bin/env python3
"""verify_intent_remark_and_boundary_mutants: verifier (INTENT AND SPEC lens) for findings
remark-match-rule-untested and label-boundary-mutants-survive on den-label.

For the original n1.py and each mutant (applied to a temp copy of tools/den-label + tools/den-take):
  1. SEMANTICS: calls n1.match_intervals / n1.check_labels directly on hand-built synthetic inputs at the
     boundary each mutant moves, so a "survived" result is shown to be a real behaviour change, not an
     equivalent mutant.
       M1 re-mark match threshold 0.5 -> 0.3 of each length   (first 0-10 vs remark 6-16: 40% of each)
       M2 re-mark match drops the remark interval's half-length condition (first 10-14 vs remark 10-30)
       M5 pause rule '<= pause_s' -> '< pause_s'               (same-case gap of exactly 6.0 s, t_s = n/4)
       M6 quiet/uncertain overlap test made strict             (quiet span ends on an interval's onset frame)
  2. NARROW: runs the unittest names the finding cites, failfast, and prints caught/survived.
  3. FULL (only with --full): runs the WHOLE den-label suite (unittest discover) once on M1+M5+M6 applied
     together and once on M2, and prints caught/survived.
Synthetic data only, temp dirs only, nothing leaves 127.0.0.1 (the branch's own loopback guard is in place).

Run:
    python3 verify_intent_remark_and_boundary_mutants.py <den-label checkout> <mut work dir> [--full] [--skip-narrow]
    (--full-set=A runs only the M1+M5+M6 full-suite run, --full-set=B only the M2 one, so they can run in parallel)
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve()
FULL = "--full" in sys.argv[3:]
SKIP_NARROW = "--skip-narrow" in sys.argv[3:]

M1 = ('            if (overlap >= 0.5 * (a["offset_t_s"] - a["onset_t_s"])\n'
      '                    and overlap >= 0.5 * (b["offset_t_s"] - b["onset_t_s"]) and overlap > 0):',
      '            if (overlap >= 0.3 * (a["offset_t_s"] - a["onset_t_s"])\n'
      '                    and overlap >= 0.3 * (b["offset_t_s"] - b["onset_t_s"]) and overlap > 0):')
M2 = ('                    and overlap >= 0.5 * (b["offset_t_s"] - b["onset_t_s"]) and overlap > 0):',
      '                    and overlap > 0):')
M5 = ('                and item["onset_t_s"] - prev["offset_t_s"] <= protocol.settings["pause_s"]):',
      '                and item["onset_t_s"] - prev["offset_t_s"] < protocol.settings["pause_s"]):')
M6 = ('            if a <= d and c <= b:', '            if a < d and c < b:')

NARROW = {
    "M1": ["test_den_label.RemarkTests.test_agreement_on_hand_made_cases"],
    "M2": ["test_den_label.RemarkTests.test_agreement_on_hand_made_cases"],
    "M5": ["test_den_label.LabelTests.test_the_pause_rule_never_applies_across_a_cut",
           "test_den_label.LabelTests.test_every_protocol_check"],
    "M6": ["test_den_label.LabelTests.test_every_protocol_check"],
}
MUTANTS = {"original": [], "M1": [M1], "M2": [M2], "M5": [M5], "M6": [M6]}

SCENARIO = r'''
import sys
sys.path.insert(0, sys.argv[1])
import n1
P = n1.load_protocol()
def m(on, off):
    return {"onset_t_s": on, "offset_t_s": off}
print("M1-case matches", len(n1.match_intervals([m(0, 10)], [m(6, 16)])))
print("M2-case matches", len(n1.match_intervals([m(10, 14)], [m(10, 30)])))
t_of = {n: n / 4 for n in range(1, 401)}
segs = [(1, 400)]
def iv(a, b, case="sorting_mail"):
    return {"onset_n": a, "onset_kind": "touch", "offset_n": b, "offset_kind": "release", "band": "fine",
            "case": case, "sure": {"onset": True, "offset": True, "band": True}, "by": "owner"}
def outcome(intervals, spans):
    try:
        n1.check_labels(intervals, spans, P, segs, t_of)
        return "accepted"
    except n1.Refused as exc:
        return "refused (" + str(exc)[:60] + ")"
# offset frame 80 (t 20.0), next onset frame 104 (t 26.0): a same-case gap of exactly pause_s = 6 s
print("M5-case gap", t_of[104] - t_of[80], outcome([iv(40, 80), iv(104, 140)], []))
# quiet span 20..40 ends on frame 40, the interval's onset frame
print("M6-case shared-frame", outcome([iv(40, 80)], [{"kind": "quiet", "case": "idle_seated", "start_n": 20,
                                                       "end_n": 40, "by": "owner"}]))
print("M6-case adjacent-frame", outcome([iv(40, 80)], [{"kind": "quiet", "case": "idle_seated", "start_n": 20,
                                                         "end_n": 39, "by": "owner"}]))
'''


def copy_with(repls, tag):
    work = Path(tempfile.mkdtemp(prefix=f"vi-{tag}-", dir=WORK))
    for sub in ("den-label", "den-take"):
        shutil.copytree(ROOT / "tools" / sub, work / "tools" / sub, ignore=shutil.ignore_patterns("__pycache__"))
    target = work / "tools" / "den-label" / "n1.py"
    text = target.read_text(encoding="utf-8")
    for old, new in repls:
        if text.count(old) != 1:
            raise SystemExit(f"FAIL mutant {tag}: pattern found {text.count(old)} times")
        text = text.replace(old, new)
    target.write_text(text, encoding="utf-8", newline="\n")
    return work


def semantics():
    out = {}
    for name, repls in MUTANTS.items():
        work = copy_with(repls, name)
        script = work / "scenario.py"
        script.write_text(SCENARIO, encoding="utf-8")
        proc = subprocess.run([sys.executable, "-B", str(script), str(work / "tools" / "den-label")],
                              capture_output=True, text=True, timeout=300)
        out[name] = proc.stdout.strip().splitlines() + ([proc.stderr.strip()[-400:]] if proc.returncode else [])
        for line in out[name]:
            print(f"RESULT semantics {name}: {line}", flush=True)
        shutil.rmtree(work, ignore_errors=True)
    o = out["original"]
    checks = [
        ("original: 40%/40% overlap is no match", "M1-case matches 0" in o[0]),
        ("M1 changes it to a match", "M1-case matches 1" in out["M1"][0]),
        ("original: 100%/20% overlap is no match", "M2-case matches 0" in o[1]),
        ("M2 changes it to a match", "M2-case matches 1" in out["M2"][1]),
        ("original refuses a same-case gap of exactly 6.0 s", "refused" in o[2]),
        ("M5 accepts it", "accepted" in out["M5"][2]),
        ("original refuses a quiet span ending on the interval's onset frame", "refused" in o[3]),
        ("M6 accepts it", "accepted" in out["M6"][3]),
        ("original accepts a quiet span ending one frame before the onset", "accepted" in o[4]),
    ]
    for what, ok in checks:
        print(("PASS " if ok else "FAIL ") + what, flush=True)


def run_tests(repls, tag, tests):
    work = copy_with(repls, tag)
    started = time.time()
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest"] + tests,
                          cwd=work / "tools" / "den-label", capture_output=True, text=True, timeout=3600)
    lines = proc.stderr.strip().splitlines()
    ran = next((l for l in lines if l.startswith("Ran ")), "")
    result = "caught" if proc.returncode != 0 else "survived"
    if proc.returncode != 0:
        (WORK / f"vi-{tag}.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    print(f"RESULT {tag:10} {result:9} {ran}; {lines[-1] if lines else ''} ({time.time() - started:.0f} s) "
          f"tests={' '.join(tests)}", flush=True)
    return result


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    semantics()
    if not SKIP_NARROW:
        for name in ("M1", "M2", "M5", "M6"):
            run_tests(MUTANTS[name], f"narrow-{name}", ["-f"] + NARROW[name])
    sets = [a.split("=", 1)[1] for a in sys.argv[3:] if a.startswith("--full-set=")]
    if FULL or "A" in sets:
        run_tests([M1, M5, M6], "full-M1M5M6", ["discover", "-s", ".", "-p", "test*.py"])
    if FULL or "B" in sets:
        run_tests([M2], "full-M2", ["discover", "-s", ".", "-p", "test*.py"])


if __name__ == "__main__":
    main()

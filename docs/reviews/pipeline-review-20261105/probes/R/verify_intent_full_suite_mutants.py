"""verify_intent_full_suite_mutants: re-run three test-coverage mutants against the WHOLE den-take suite.

Checks the claims in findings elapsed-bound-boundary-untested (M1) and b2-health-session-untested
(M4, M10): does each mutant survive `python3 -B -m unittest discover -f` over all of
tools/den-take/test_den_take.py, not only a narrow subset?

  M1  check.py  A_elapsed_bound '<=' becomes '<'
  M4  check.py  B2 drops the /health-before-and-after session comparison
  M10 take.py   a poller that stops by itself after the end slate no longer marks the take incomplete

How to run
  python3 -B verify_intent_full_suite_mutants.py <branch checkout root> <work dir> [--narrow] [M1 M4 M10]
--narrow runs, per mutant, only the test classes and tests that could catch it (listed in NARROW)
instead of the whole suite (the whole suite takes 40+ min per mutant on a loaded 4-CPU host).
The work dir must be a scratch folder (e.g. <scratchpad>/mut/R-intent); each mutant gets a fresh copy
of tools/den-take there. All mutants run concurrently. The suite uses synthetic data and a fake camera
on 127.0.0.1 (ephemeral port) only. Prints one RESULT line per mutant: SURVIVED (exit 0) or CAUGHT.
"""
import re, shutil, subprocess, sys, time
from pathlib import Path

ROOT, WORK = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
NARROW_MODE = "--narrow" in sys.argv[3:]
WANT = set(a for a in sys.argv[3:] if a != "--narrow") or {"M1", "M4", "M10"}
T = "test_den_take."
NARROW = {
    # every test that runs check.py on a take built with a stepped end slate, plus the slate-rule unit tests
    "M1": [T + "FaultTests.test_clean_take_passes", T + "FaultTests.test_offset_between_slates",
           T + "FaultTests.test_offset_near_five_seconds_wraps", T + "FaultTests.test_one_second_error_at_end_slate",
           T + "SlateRuleTests"],
    # the only B2 test, the clean take, and every class that runs check.py on take.py's own output
    "M4": [T + "FaultTests.test_session_change", T + "FaultTests.test_clean_take_passes", T + "EndToEndTests",
           T + "TakeScriptTests", T + "PollerTests"],
    # every class that runs take.run (its finally block) or the poller
    "M10": [T + "TakeScriptTests", T + "EndToEndTests", T + "PollerTests"],
}
SRC = ROOT / "tools" / "den-take"

MUTANTS = {
    "M1": ("check.py", 'round(elapsed, 9) <= m["elapsed_bound_s"]', 'round(elapsed, 9) < m["elapsed_bound_s"]'),
    "M4": ("check.py", 'and health_sessions == [next(iter(sessions))] * 2', ''),
    "M10": ("take.py", '        if take["stop_reason"] is None and poller.stop_reason:  # stopped by itself near the end\n'
                       '            take["stop_reason"] = poller.stop_reason\n', ''),
}

procs = {}
for mid in sorted(WANT):
    fname, old, new = MUTANTS[mid]
    dst = WORK / mid / "den-take"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(SRC, dst)
    path = dst / fname
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        print(f"RESULT {mid} NOT-RUN old text occurs {text.count(old)} times", flush=True)
        continue
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
    log = open(WORK / f"{mid}.log", "w")
    procs[mid] = (subprocess.Popen([sys.executable, "-B", "-m", "unittest", "-f", *NARROW[mid]] if NARROW_MODE else
                                  [sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", ".", "-p", "test*.py"],
                                   cwd=dst, stdout=log, stderr=subprocess.STDOUT, text=True), time.time(), log)
    print(f"started {mid} in {dst}", flush=True)

for mid, (proc, t0, log) in procs.items():
    code = proc.wait()
    log.close()
    out = (WORK / f"{mid}.log").read_text()
    ran = re.findall(r"Ran (\d+) tests? in ([\d.]+)s", out)
    failed = re.findall(r"^(?:FAIL|ERROR): (\S+ \([^)]+\))", out, re.M)
    verdict = "SURVIVED" if code == 0 else "CAUGHT"
    print(f"RESULT {mid} {verdict} exit={code} ran={ran[-1] if ran else '?'} wall={time.time() - t0:.0f}s "
          f"by={'; '.join(failed) or '-'}", flush=True)

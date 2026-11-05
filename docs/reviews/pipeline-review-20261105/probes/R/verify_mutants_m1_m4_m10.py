"""verify_mutants_m1_m4_m10: do the elapsed-bound '<' mutant (M1), the B2 /health mutant (M4) and the
take.py poller-stop mutant (M10) survive the FULL den-take test module, and does any test ever reach the
input that would tell each mutant apart from the original?

What it does
  * Copies tools/den-take from the branch checkout (first CLI arg) to a temp dir (or to the dir given
    as the second CLI arg), applies all three mutants at once, and adds side-effect-free logging of each
    mutant's kill condition (the only inputs on which the mutant behaves differently):
      M1  check.py  round(elapsed, 9) <= bound  ->  <      kill condition: round(elapsed, 9) == bound
      M4  check.py  drop 'and health_sessions == [s] * 2'  kill condition: one bundle session, frames,
                                                            and health sessions != [s, s]
      M10 take.py   drop 'if stop_reason is None and poller.stop_reason: stop_reason = poller...'
                                                            kill condition: stop_reason None and
                                                            poller.stop_reason truthy
  * Runs `python3 -B -m unittest test_den_take` there (the whole module, no failfast) and prints the
    test totals, any failing tests, and how often each kill condition was reached.
  If the module passes and a kill condition count is 0, that mutant survives by construction.

How to run
  python3 -B verify_mutants_m1_m4_m10.py <branch checkout root> [work dir]
Synthetic data only (the suite's own fakes on 127.0.0.1), temp dirs only.
"""
import os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix="verify-mut-"))
dst = work / "den-take"
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(ROOT / "tools" / "den-take", dst)
log = work / "kill.log"
log.write_text("", encoding="utf-8")

LOGFN = ("\ndef _vlog(tag):\n    try:\n        import os as _o\n        p = _o.environ.get('VERIFY_LOG')\n"
         "        if p:\n            with open(p, 'a', encoding='utf-8') as fh:\n                fh.write(tag + '\\n')\n"
         "    except Exception:\n        pass\n")

def sub(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, (path.name, old[:60], text.count(old))
    path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")

chk, tk = dst / "check.py", dst / "take.py"
sub(chk, "\n# ---- rules ----", LOGFN + "\n# ---- rules ----")
# M4 + its kill condition
sub(chk, '    rules["B2_session"] = {"pass": bool(frames) and len(sessions) == 1\n'
         '                           and health_sessions == [next(iter(sessions))] * 2,\n',
         '    if frames and len(sessions) == 1 and health_sessions != [next(iter(sessions))] * 2:\n'
         '        _vlog("M4_KILL_CONDITION")\n'
         '    rules["B2_session"] = {"pass": bool(frames) and len(sessions) == 1\n'
         '                           ,\n')
# M1 + its kill condition
sub(chk, '    rules["A_elapsed_bound"] = {"pass": elapsed is not None and round(elapsed, 9) <= m["elapsed_bound_s"],\n',
         '    if elapsed is not None and round(elapsed, 9) == m["elapsed_bound_s"]:\n'
         '        _vlog("M1_KILL_CONDITION")\n'
         '    if elapsed is not None:\n'
         '        _vlog("M1_SEEN %r" % round(elapsed, 9))\n'
         '    rules["A_elapsed_bound"] = {"pass": elapsed is not None and round(elapsed, 9) < m["elapsed_bound_s"],\n')
# M10 + its kill condition
sub(tk, '        if take["stop_reason"] is None and poller.stop_reason:  # stopped by itself near the end\n'
        '            take["stop_reason"] = poller.stop_reason\n',
        '        if take["stop_reason"] is None and poller.stop_reason:  # M10 kill condition\n'
        '            check._vlog("M10_KILL_CONDITION")\n')
t0 = time.time()
env = dict(os.environ, VERIFY_LOG=str(log), PYTHONDONTWRITEBYTECODE="1")
proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "test_den_take"], cwd=dst, env=env,
                      capture_output=True, text=True, timeout=3000)
took = time.time() - t0
lines = log.read_text(encoding="utf-8").splitlines()
seen = sorted({l.split(" ", 1)[1] for l in lines if l.startswith("M1_SEEN")}, key=float)
print(f"RESULT branch {ROOT.name}: unittest exit {proc.returncode} in {took:.0f}s; "
      + "; ".join(re.findall(r"^(Ran \d+ tests? in [\d.]+s|OK.*|FAILED.*)$", proc.stderr, re.M)))
for name in re.findall(r"^(?:FAIL|ERROR): (.+)$", proc.stderr, re.M):
    print(f"RESULT failing test: {name}")
for tag in ("M1_KILL_CONDITION", "M4_KILL_CONDITION", "M10_KILL_CONDITION"):
    print(f"RESULT {tag} reached {sum(1 for l in lines if l == tag)} times")
print(f"RESULT distinct rounded elapsed bounds computed by the suite ({len(seen)}): "
      f"{', '.join(seen[:12])}{' ...' if len(seen) > 12 else ''}; max {seen[-1] if seen else None}")
if proc.returncode == 0:
    print("RESULT all three mutants applied together: the full module still passes (each SURVIVED)")
else:
    print("RESULT the full module fails with the mutants applied (see failing tests above)")
    print(proc.stderr[-3000:])

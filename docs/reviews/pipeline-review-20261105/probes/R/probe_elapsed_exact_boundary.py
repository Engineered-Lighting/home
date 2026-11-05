"""probe_elapsed_exact_boundary: a real take geometry whose elapsed bound is exactly elapsed_bound_s.

What it checks
  * c and the window widths sit on the 1 ms fit grid, so the elapsed statistic takes values on a
    0.5 ms lattice and can equal CHECK-V1's elapsed_bound_s (0.20) exactly. This builds such a take
    with R's make_take (start offset 3.2137 s, end-slate offset 0.137 s later) and checks that
    check.py says PASS at exactly 0.200 (the method says "<=").
  * It then shows the same take under a "<" comparison (the M1 mutant) would FAIL, so a test at this
    point is what distinguishes the two; prints whether any test in test_den_take.py builds a
    bound of exactly the limit (it greps for a step that lands on it).

How to run
  python3 -B probe_elapsed_exact_boundary.py <branch checkout root>
Synthetic data only, temp dirs only.
"""
import shutil, sys, tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import check  # noqa: E402
import test_den_take as T  # noqa: E402

M = check.load_method()
tmp = Path(tempfile.mkdtemp(prefix="probe-exact-")).resolve()
try:
    for d in (0.1365, 0.137, 0.1375):
        folder = T.make_take(tmp, c=3.2137, step_both=("begin", 15, d))
        code, report, _ = T.run_check(folder)
        rule = report["rules"]["A_elapsed_bound"]
        b = rule["elapsed_bound_s"]
        print(f"RESULT D={d}: bound={b!r} rounded={round(b, 9)} verdict={report['verdict']} "
              f"failures={report['failures']} '<' would say {'PASS' if round(b, 9) < M['elapsed_bound_s'] else 'FAIL'}")
        if abs(round(b, 9) - M["elapsed_bound_s"]) < 1e-12:
            ok = report["verdict"] == "PASS"
            print(("PASS" if ok else "FAIL") + f": exactly-at-limit bound {b!r} gives {report['verdict']} "
                  "(method: <= elapsed_bound_s)")
    src = (ROOT / "tools" / "den-take" / "test_den_take.py").read_text(encoding="utf-8")
    print("RESULT tests that step the end slate:", sorted(set(
        line.strip() for line in src.splitlines() if "step_both=" in line and "verdict(" in line)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    if T._CLEAN:
        shutil.rmtree(T._CLEAN[0].parent, ignore_errors=True)

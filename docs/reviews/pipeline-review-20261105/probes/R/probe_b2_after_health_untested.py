#!/usr/bin/env python3
"""B2's after-take /health session: what check.py does, and that the suite never pins it.

    python3 -B probe_b2_after_health_untested.py <checkout root>     # e.g. .../wt/rung-r

Builds one clean synthetic take with the checkout's own test helper (make_take,
synthetic bundles only, in a temp dir), then makes three copies that differ ONLY
in take.json's health_after: a new camera session, no body (the final /health was
unreadable), and the second-Ctrl+C record. Runs check.py on each (the real code,
then the code with B2's after-check dropped, applied in memory to a copy of
check_rules' source). Prints PASS/FAIL lines and a RESULT line. With the real code
every variant must FAIL B2; with the mutant they PASS, which no test in
test_den_take.py notices (see mutate_den_take.py --only=M03).
"""
import io
import json
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import check  # noqa: E402
import take  # noqa: E402
import test_den_take as t  # noqa: E402  (helpers only; no test runs)

ok = True
tmp = Path(tempfile.mkdtemp(prefix="probe-b2-"))
try:
    base = t.make_take(tmp)
    record = json.loads((base / "take.json").read_text(encoding="utf-8"))
    variants = {
        "after_new_session": {"status": 200, "body": {"healthy": True, "session": "sess-restarted"}},
        "after_unreadable": {"status": None, "body": None, "error": "ConnectionRefusedError"},
        "after_interrupted": {"status": None, "body": None, "error": "interrupted"},
    }
    source = Path(check.__file__).read_text(encoding="utf-8")
    old = "and health_sessions == [next(iter(sessions))] * 2,"
    assert source.count(old) == 1
    mutant = types.ModuleType("check_mutant")
    mutant.__file__ = check.__file__
    exec(compile(source.replace(old, "and health_sessions[0] == next(iter(sessions)),"), check.__file__, "exec"),
         mutant.__dict__)
    for name, after in variants.items():
        for label, module in (("real", check), ("mutant", mutant)):
            folder = tmp / f"{name}-{label}"
            shutil.copytree(base, folder)
            changed = dict(record, health_after=after)
            take.write_json(folder / "take.json", changed)
            code, report, _ = module.run_check(folder, ask=lambda: "none", show=lambda p: None,
                                               out=io.StringIO(), console=t.Console())
            expect_fail = label == "real"
            good = (report["verdict"] == "FAIL" and "B2_session" in report["failures"]) if expect_fail \
                else report["verdict"] == "PASS"
            ok &= good
            print(("PASS " if good else "FAIL ") + f"{name} with {label} check.py: verdict {report['verdict']}, "
                  f"failures {report['failures']}, retained {report['retained']}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("RESULT", "CONFIRMED: real code fails B2 on a changed or missing after-take /health; the mutant passes"
      if ok else "UNEXPECTED")

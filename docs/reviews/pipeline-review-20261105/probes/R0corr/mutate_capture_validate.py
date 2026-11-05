"""R0corr mutation runner (lens validate) for tools/correction-capture/capture.py.

What it checks: applies each targeted mutant of the gate, verdict, statistics and counting logic, one at a time,
to a temporary copy of the branch's tools/correction-capture + tools/den-core + den_lighting, runs the tool's own
unittest suite with failfast, and prints a caught/survived table. A mutant "survives" when the suite still passes.

How to run (stdlib only, synthetic data only, never touches the checkout it is given):
    python3 -B mutate_capture_validate.py /path/to/branch/checkout

The copy gets an empty .git folder at its root so the suite's "never write inside a checkout" test still sees a
checkout, exactly as in the real worktree.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CAP = "tools/correction-capture/capture.py"

# (id, file, old, new, count) - count is how many occurrences must be replaced (all of them).
MUTANTS = [
    ("M01-insufficient-boundary", CAP, 'if occupied_h < constants["min_occupied_h"]:',
     'if occupied_h <= constants["min_occupied_h"]:', 2),
    ("M02-beyond-ge", CAP, "every_high_hours > budget", "every_high_hours >= budget", 1),
    ("M03-within-lt", CAP, "people_low_hours <= budget", "people_low_hours < budget", 1),
    ("M04-verdict-people-upper-end", CAP, "garwood(people, level)[0]", "garwood(people, level)[1]", 1),
    ("M05-verdict-every-lower-end", CAP, "garwood(every, level)[1]", "garwood(every, level)[0]", 1),
    ("M06-phi-drop-exposure-offset", CAP, 'mu = s["occupied_h"] * c / h', "mu = c / h", 1),
    ("M07-phi-one-param-not-per-stratum", CAP, "_r(pearson / (len(used) - len(strata)))",
     "_r(pearson / (len(used) - 1))", 1),
    ("M08-phi-df-is-n", CAP, "_r(pearson / (len(used) - len(strata)))", "_r(pearson / len(used))", 1),
    ("M09-per-session-counts-every-hold", CAP, '"corrections": sessions.get((block, stratum), (0, 0))[0]}',
     '"corrections": sessions.get((block, stratum), (0, 0))[1]}', 1),
    ("M10-restart-ignores-unavailable-row", CAP,
     'restart = prev is not None and (broken or s["revision"] < prev["revision"])',
     'restart = prev is not None and (s["revision"] < prev["revision"])', 1),
    ("M11-restart-on-equal-revision", CAP, 's["revision"] < prev["revision"]', 's["revision"] <= prev["revision"]', 1),
    ("M12-ack-window-6s", CAP, "ACK_BEFORE_S, ACK_AFTER_S = 2.0, 8.0", "ACK_BEFORE_S, ACK_AFTER_S = 2.0, 6.0", 1),
    ("M13-own-fade-ignores-flip", CAP,
     'return any(not (row["flip"] and row["on"] != target["on"]) for _, target in near)', "return bool(near)", 1),
    ("M14-settle-strict", CAP, 'c["t"] - actions[-1][-1]["t"] <= SETTLE_S',
     'c["t"] - actions[-1][-1]["t"] < SETTLE_S', 1),
    ("M15-automation-counts-as-people", CAP, 'PEOPLE = ("app", "switch_or_dimmer")',
     'PEOPLE = ("app", "switch_or_dimmer", "automation")', 1),
    ("M16-rule-pin-not-checked", CAP, 'if rule.get("id") != RULE_ID or digest != RULE_SHA256:',
     'if rule.get("id") != RULE_ID:', 1),
    ("M17-outside-exposure-not-checked", CAP, 'if not _in_charge(first["status"]):', "if False:", 1),
    ("M18-weeks-one-arm", CAP, "weeks = 2 * hours * span_days", "weeks = hours * span_days", 1),
    ("M19-garwood-one-sided", CAP, "a = (1 - level) / 2", "a = (1 - level)", 1),
    ("M20-phi-keeps-zero-strata", CAP,
     'used = [s for s in per_session if s["occupied_h"] > 0 and totals[s["mode_stratum"]][1] > 0]',
     'used = [s for s in per_session if s["occupied_h"] > 0]', 1),
    ("M21-in-charge-ignores-occupancy-unknown", CAP, 'return s["automatic"] and s["occupancy"] == "on"',
     'return s["automatic"] and s["occupancy"] != "off"', 1),
    ("M22-window-3s", CAP, "WINDOW_S = 5.0", "WINDOW_S = 3.0", 1),
]


def run_suite(root):
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "tools/correction-capture",
                           "-p", "test_*.py"], cwd=root, capture_output=True, text=True, timeout=300)
    tail = (proc.stderr.strip().splitlines() or [""])[-1]
    failed = [line for line in proc.stderr.splitlines() if line.startswith(("FAIL:", "ERROR:"))]
    return proc.returncode, tail, failed[:1]


def main():
    src = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="mut-cc-validate-") as tmp:
        base = Path(tmp) / "base"
        for rel in ("tools/correction-capture", "tools/den-core", "ha-config/custom_components/den_lighting"):
            shutil.copytree(src / rel, base / rel, ignore=shutil.ignore_patterns("__pycache__"))
        (base / ".git").mkdir()
        code, tail, _ = run_suite(base)
        print("BASELINE suite: rc=%d %s" % (code, tail))
        if code != 0:
            print("FAIL: baseline suite does not pass; mutants not run")
            return 1
        rows = []
        for mid, rel, old, new, count in MUTANTS:
            work = Path(tmp) / mid
            shutil.copytree(base, work)
            path = work / rel
            text = path.read_text("utf-8")
            found = text.count(old)
            if found != count:
                rows.append((mid, "not-run", "pattern found %d times, expected %d" % (found, count)))
                continue
            path.write_text(text.replace(old, new), "utf-8")
            code, tail, failed = run_suite(work)
            rows.append((mid, "caught" if code != 0 else "survived", (failed[0] if failed else tail)))
            shutil.rmtree(work, ignore_errors=True)
        width = max(len(r[0]) for r in rows)
        print("%-*s  %-8s  %s" % (width, "mutant", "result", "first failing test / suite tail"))
        for mid, result, note in rows:
            print("%-*s  %-8s  %s" % (width, mid, result, note))
        survived = [r[0] for r in rows if r[1] == "survived"]
        print("RESULT: %d mutants, %d caught, %d survived, %d not run" % (
            len(rows), sum(r[1] == "caught" for r in rows), len(survived), sum(r[1] == "not-run" for r in rows)))
        print("SURVIVED: %s" % (", ".join(survived) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

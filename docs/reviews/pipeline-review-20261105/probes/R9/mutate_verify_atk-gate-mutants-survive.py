"""mutate_verify_atk-gate-mutants-survive: independent re-run of three gate-logic mutants of tools/den-core/score.py
(finding atk-gate-mutants-survive, verifier lens: intent and spec).

Each mutant is applied (exact text, must match once) to a fresh temp copy of tools/den-core plus the folders its
tests need (with an empty .git at the copy root), then the narrowest test classes that should catch it run with
failfast: test_rung9.ScoreTest and test_rung9.RecordTest (only test_rung9 imports score.py). The unmutated copy runs
first. Prints PASS/FAIL baseline and a caught/survived line per mutant.

How to run (synthetic data only; nothing contacts a host; temp dirs only):
  python3 -B mutate_verify_atk-gate-mutants-survive.py /path/to/den-core-live-checkout
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

NEEDED = ("tools/den-core", "tools/la-latency", "tools/correction-capture", "stack/services/perception-lab",
          "ha-config/custom_components/den_lighting")
TESTS = ["test_rung9.ScoreTest", "test_rung9.RecordTest"]
MUTANTS = [
    ("V-M6", "replay check result ignored in the verdict",
     '"replay": {"ok": result["ok"], "text": result["reason"]},', '"replay": {"ok": True, "text": result["reason"]},'),
    ("V-M1", "timing check ignores decision_ms_max",
     '"timing": {"ok": bool(ms) and max(ms) < fixed["decision_ms_max"],', '"timing": {"ok": True,'),
    ("V-M3", "anticipation needs no rise",
     'hits["anticipation"].append(any(e >= (1 + rise) * start if start > 0 else e > 0 for _, e, _ in up))',
     'hits["anticipation"].append(bool(up))'),
]


def copy(root, dest):
    for rel in NEEDED:
        src = root / rel
        if src.exists():
            shutil.copytree(src, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    (dest / ".git").mkdir()


def run(dest):
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f"] + TESTS, cwd=dest / "tools" / "den-core",
                          capture_output=True, text=True, timeout=1500)
    tail = [line for line in proc.stderr.splitlines() if line.startswith(("FAIL:", "ERROR:", "OK", "FAILED", "Ran "))]
    return proc.returncode, " | ".join(tail[-3:])


def main():
    root = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="r9-verify-mut-") as tmp:
        base = Path(tmp) / "base"
        copy(root, base)
        code, text = run(base)
        print("%s baseline exit %d: %s" % ("PASS" if code == 0 else "FAIL", code, text), flush=True)
        for mid, what, old, new in MUTANTS:
            dest = Path(tmp) / mid
            copy(root, dest)
            path = dest / "tools" / "den-core" / "score.py"
            src = path.read_text("utf-8")
            if src.count(old) != 1:
                print("RESULT %s not applied (matches %d)" % (mid, src.count(old)), flush=True)
                continue
            path.write_text(src.replace(old, new), "utf-8")
            code, text = run(dest)
            print("RESULT %s %s: %s [%s]" % (mid, "caught" if code else "survived", what, text), flush=True)
            shutil.rmtree(dest, ignore_errors=True)


if __name__ == "__main__":
    main()

"""Mutation runner (attack lens) for den-verify's gate logic.

For each mutant: copy tools/den-verify and docs/architecture/live-v1 from the
checkout into a temp dir, apply one exact-text replacement (it must match once),
run the suite failfast (`python3 -B -m unittest discover -s tools/den-verify
-p "test_*.py" -f`), and record caught (suite failed) or survived (suite passed).
Prints a table and a SURVIVED line per surviving mutant.

Run
  python3 mutate_atk_den_verify.py <den-verify branch checkout root>
Synthetic data only; the checkout is never modified; temp dirs only.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()

MUTANTS = [
    ("vacuous-pass", "score.py", '"verdict": ("insufficient" if not n else', '"verdict": ("pass" if not n else'),
    ("held-symmetric-exclusion", "score.py",
     'row["excluded_held"] = row["status"] == "outside" and row["held_share"] >= m["held_share_min"]',
     'row["excluded_held"] = row["status"] in ("within", "outside") and row["held_share"] >= m["held_share_min"]'),
    ("tolerance-strict", "score.py", "return abs(m - p) <= tol * abs(p) + Fraction(extra)",
     "return abs(m - p) < tol * abs(p) + Fraction(extra)"),
    ("gate-strict", "score.py", "Fraction(hits, n) >= gate", "Fraction(hits, n) > gate"),
    ("drift-ignores-occupancy", "score.py", 'if (inc["status"] == "outside" and not occ', 'if (inc["status"] == "outside"'),
    ("v_min-ignored", "score.py", 'if 1 - float(e.get("clipped_frac") or 0) < m["v_min"]:', "if False:"),
    ("no-settle-margin", "settle.py", 'end = sent + fade + float(m["settle_margin_s"])', "end = sent + fade"),
    ("held-explained-normally", "score.py", 'if row["excluded_held"]:\n            return {"cause": "held"',
     'if False:\n            return {"cause": "held"'),
    ("mid-transition-admitted", "score.py", "admitted = not any(s.mid_transition for s in d.steps)", "admitted = True"),
    ("open-slots-scored", "method.py", "return dict(self.values) if not self.open else None",
     "return dict(self.values)"),
    ("no-person-change-window", "score.py",
     'self.windows.add_change(ts, (c.get("action") or {}).get("settle_s"),',
     '(lambda *a: None)(ts, (c.get("action") or {}).get("settle_s"),'),
]


def copy_tree(dst):
    shutil.copytree(ROOT / "tools" / "den-verify", dst / "tools" / "den-verify",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "docs" / "architecture" / "live-v1", dst / "docs" / "architecture" / "live-v1")


def run_suite(cwd):
    p = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tools/den-verify",
                        "-p", "test_*.py", "-f"], cwd=cwd, capture_output=True, text=True, timeout=600)
    tail = (p.stderr.strip().splitlines() or [""])
    failed = next((line for line in tail if line.startswith(("FAIL:", "ERROR:"))), "")
    return p.returncode, failed


with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp) / "base"
    copy_tree(base)
    code, _ = run_suite(base)
    print(f"baseline suite exit {code}")
    rows = []
    for name, rel, old, new in MUTANTS:
        work = Path(tmp) / name
        shutil.copytree(base, work)
        f = work / "tools" / "den-verify" / rel
        text = f.read_text(encoding="utf-8")
        n = text.count(old)
        if n != 1:
            rows.append((name, rel, "not-run", f"pattern matched {n} times"))
            continue
        f.write_text(text.replace(old, new), encoding="utf-8")
        code, failed = run_suite(work)
        rows.append((name, rel, "caught" if code else "survived", failed))
    print(f"{'mutant':28} {'file':10} {'result':9} caught by")
    for name, rel, result, by in rows:
        print(f"{name:28} {rel:10} {result:9} {by}")
    for name, rel, result, _ in rows:
        if result == "survived":
            print(f"SURVIVED: {name} ({rel})")
    print(f"RESULT {sum(r[2] == 'caught' for r in rows)} caught, {sum(r[2] == 'survived' for r in rows)} survived, "
          f"{sum(r[2] == 'not-run' for r in rows)} not run")

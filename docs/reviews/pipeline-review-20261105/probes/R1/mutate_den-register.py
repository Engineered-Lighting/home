#!/usr/bin/env python3
"""Mutation runner for tools/den-register (rung 1) gate logic.

Copies tools/den-register, tools/den-take and docs/architecture/live-v1 from the given
checkout into a fresh folder per mutant (under the mutation work dir, never inside the
checkout), applies one source edit, runs the den-register unittest suite with failfast,
and prints a caught/survived table.

Run:  python3 mutate_den-register.py <rung-1 checkout root> [<mutation work dir>]
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
base = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(tempfile.gettempdir())
base.mkdir(parents=True, exist_ok=True)

MUTANTS = [
    ("register.py", "if residual > gate:", "if residual > 2 * gate:", "fit-residual gate doubled"),
    ("register.py", 'if p["error_m"] > gate:', 'if p["error_m"] > gate + 0.002:', "check gate +2 mm"),
    ("register.py", 'if spread < m["min_spread_m"]:', 'if spread < m["min_spread_m"] / 100:', "collinearity floor /100"),
    ("register.py", "if translation[2] <= 0:", "if translation[2] <= -5:", "camera-below-floor at -5 m"),
    ("register.py", 'result = rigid.fit([p["camera_xyz_m"] for p in fit_pairs], [p["room_xyz_m"] for p in fit_pairs])',
     'result = rigid.fit([p["camera_xyz_m"] for p in fit_pairs + check_pairs], '
     '[p["room_xyz_m"] for p in fit_pairs + check_pairs])', "checks leak into the fit"),
    ("register.py", 'if len(check_pairs) < m["min_checks"]:', 'if len(check_pairs) < m["min_checks"] - 1:',
     "one check accepted"),
    ("register.py", 'if gone and take.get("ended_unix", 0) <= gone.get("take_ended_unix", 0):',
     'if gone and take.get("ended_unix", 0) < gone.get("take_ended_unix", 0) - 3600:',
     "takes up to 1 h before a failed mount check accepted"),
    ("scene.py", "changed > changed_fraction * compared", "changed > 0.95 * compared",
     "mount 'changed' needs 95% of cells"),
    ("scene.py", "max(tolerance_mm, tolerance_fraction * reference)", "max(4 * tolerance_mm, tolerance_fraction * reference)",
     "mount tolerance x4"),
    ("records.py", "if len(bare) != 1:", "if False:", "ambiguous transform_id resolves"),
    ("records.py", 'invalidated = next((c["invalidated"] for c in found if c.get("invalidated")), None)',
     'invalidated = found[-1].get("invalidated")', "invalidation read from the last revision only"),
    ("serve.py", 'and origin in (None, f"http://127.0.0.1:{port}")', "", "Origin check removed"),
    ("method.py", "return dict(self.values) if not self.open else None", "return dict(self.values)",
     "open slots no longer refuse"),
]


def copy_tree(dest):
    for rel in ("tools/den-register", "tools/den-take", "docs/architecture/live-v1"):
        shutil.copytree(root / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    (dest / ".git").mkdir()  # an empty marker: one test expects the tool to sit inside a checkout


def run_suite(dest):
    out = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(dest / "tools/den-register"),
                          "-f"], capture_output=True, text=True, timeout=600, cwd=dest)
    tail = out.stderr.strip().splitlines()
    first_fail = next((l for l in tail if l.startswith(("FAIL:", "ERROR:"))), "")
    return out.returncode, first_fail, tail[-1] if tail else ""


rows = []
with tempfile.TemporaryDirectory(dir=base) as t:
    clean = Path(t) / "clean"
    copy_tree(clean)
    code, first, last = run_suite(clean)
    print(f"baseline: exit {code}, {last}")
    for i, (rel, old, new, label) in enumerate(MUTANTS, 1):
        dest = Path(t) / f"m{i}"
        copy_tree(dest)
        path = dest / "tools/den-register" / rel
        src = path.read_text(encoding="utf-8")
        if src.count(old) != 1:
            rows.append((i, rel, label, "not-run", f"pattern found {src.count(old)} times"))
            continue
        path.write_text(src.replace(old, new), encoding="utf-8")
        code, first, last = run_suite(dest)
        rows.append((i, rel, label, "caught" if code else "survived", first or last))
        shutil.rmtree(dest)
print(f"{'#':>2} {'file':12} {'mutant':48} {'result':9} by")
for i, rel, label, result, by in rows:
    print(f"{i:>2} {rel:12} {label:48} {result:9} {by}")
print(f"RESULT {sum(r[3] == 'caught' for r in rows)} caught, {sum(r[3] == 'survived' for r in rows)} survived, "
      f"{sum(r[3] == 'not-run' for r in rows)} not run")

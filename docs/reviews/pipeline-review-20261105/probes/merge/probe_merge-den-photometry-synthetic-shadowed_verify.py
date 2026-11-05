#!/usr/bin/env python3
"""Verifier probe for finding merge-den-photometry-synthetic-shadowed.

Checks, in a copy made under a temp dir (the given checkouts are never written to):
  1. after `import card`, which file `import synthetic` resolves to for den-photometry's tests
     (den-photometry's own synthetic.py, or tools/den-light/synthetic.py);
  2. the den-photometry unittest suite result (unless --no-suite);
  3. that `photometry.py --self-check` (no camera, no data, no network) still passes, i.e. the
     runtime path is not affected.

Usage:
  python3 probe_merge-den-photometry-synthetic-shadowed_verify.py <tree-root> [<stage1-root>] [--no-suite]

<tree-root>   a checkout holding tools/den-photometry and tools/den-light (a merged tree, or the
              claude/rung-7-photometry checkout alone).
<stage1-root> optional: a claude/rung-8-stage1 checkout. If given, its tools/den-light/*.py files are
              laid over the copy of <tree-root> (simulating the merge without git).

Synthetic data only; nothing binds anything but 127.0.0.1 and no host is contacted.
Prints RESULT lines and a final PASS (own module, suite OK) or FAIL (shadowed / suite broken).
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

args = [a for a in sys.argv[1:] if not a.startswith("--")]
no_suite = "--no-suite" in sys.argv
root = Path(args[0]).resolve()
stage1 = Path(args[1]).resolve() if len(args) > 1 else None

with tempfile.TemporaryDirectory(prefix="probe-shadow-") as tmp:
    work = Path(tmp) / "tree"
    for rel in ("tools/den-photometry", "tools/den-light", "tools/fixtures", "docs/architecture/live-v1"):
        src = root / rel
        if src.exists():
            shutil.copytree(src, work / rel, ignore=shutil.ignore_patterns("__pycache__"))
    if stage1 is not None:
        for f in (stage1 / "tools/den-light").glob("*.py"):
            shutil.copy2(f, work / "tools/den-light" / f.name)
        fx = stage1 / "tools/fixtures"
        if fx.exists():
            shutil.copytree(fx, work / "tools/fixtures", dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
    has_light_syn = (work / "tools/den-light/synthetic.py").exists()
    print("RESULT tools/den-light/synthetic.py present:", has_light_syn)

    code = ("import sys; sys.path.insert(0, 'tools/den-photometry'); import card, synthetic; "
            "print(synthetic.__file__); print(hasattr(synthetic, 'linear_parameters')); "
            "print(sys.path[0])")
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=work, capture_output=True, text=True)
    lines = out.stdout.splitlines() + ["?", "?", "?"]
    syn_file, has_lp, path0 = lines[:3]
    shadowed = "den-light" in syn_file
    print("RESULT sys.path[0] after import card:", path0.replace(str(work), "<tree>"))
    print("RESULT import synthetic resolves to:", syn_file.replace(str(work), "<tree>"))
    print("RESULT it has den-photometry's linear_parameters:", has_lp)

    suite_ok = None
    if not no_suite:
        suite = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tools/den-photometry",
                                "-p", "test*.py"], cwd=work, capture_output=True, text=True, timeout=1200)
        tail = [l for l in suite.stderr.splitlines() if l.startswith(("Ran ", "OK", "FAILED"))]
        errs = [l for l in suite.stderr.splitlines() if l.startswith(("AttributeError", "TypeError"))]
        kinds = sorted(set(errs))
        print("RESULT den-photometry suite:", " | ".join(tail), "rc=", suite.returncode)
        for k in kinds:
            print("RESULT   error kind x%d: %s" % (errs.count(k), k))
        suite_ok = suite.returncode == 0

    sc = subprocess.run([sys.executable, "-B", "tools/den-photometry/photometry.py", "--self-check"], cwd=work,
                        capture_output=True, text=True, timeout=300)
    first = (sc.stdout.splitlines() or ["?"])[0]
    print("RESULT --self-check rc=%d: %s" % (sc.returncode, first[:60]))

    if shadowed or suite_ok is False:
        print("FAIL den-photometry's tests import den-light's synthetic.py (shadowed); runtime self-check rc=%d"
              % sc.returncode)
    else:
        print("PASS den-photometry imports its own synthetic.py" + ("" if no_suite else " and its suite passes"))

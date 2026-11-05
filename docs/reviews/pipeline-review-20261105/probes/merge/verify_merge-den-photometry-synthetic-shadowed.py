#!/usr/bin/env python3
"""verify_merge-den-photometry-synthetic-shadowed: independent re-check of the merge seam where
den-photometry's tests pick up den-light's synthetic.py.

Usage:
    python3 verify_merge-den-photometry-synthetic-shadowed.py <tree-root> [--control]

<tree-root> is a checkout holding claude/rung-7-photometry (tools/den-photometry) and, for the
merged case, claude/rung-8-stage1 (which adds tools/den-light/synthetic.py). Synthetic data only;
the suites' fakes bind 127.0.0.1; nothing here contacts a camera, the Jetson, the GPU host or
Home Assistant. Python is run with -B so the tree gets no __pycache__.

Steps:
 1. Emulates `unittest discover -s tools/den-photometry` import order (start dir put at sys.path[0],
    then `import card`, then `import synthetic`) and prints which synthetic.py resolves.
 2. Runs the README's documented test command `python -B -m unittest discover -s tools/den-photometry -v`
    from the tree root, and the alternate form `python -B -m unittest` from inside tools/den-photometry,
    printing each summary and the distinct exception lines.
 3. Runs `python -B tools/den-photometry/photometry.py --self-check` (no camera, no data) and prints rc.
 4. With --control: copies tools/den-photometry, tools/den-light and docs/architecture/live-v1 to a temp
    dir (marked as a checkout with a .git file, since one refusal test expects that), deletes ONLY
    tools/den-light/synthetic.py there, and re-runs the documented suite (minutes).
Prints RESULT lines and a final PASS (own module, suite not broken) or FAIL (shadowed) line.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable


def run(cmd, cwd, timeout=1500):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def summary(stderr):
    tail = [l for l in stderr.splitlines() if l.startswith(("Ran ", "OK", "FAILED"))]
    excs = {}
    for l in stderr.splitlines():
        m = re.match(r"^(\w+(Error|Exception)): (.*)$", l)
        if m:
            excs[l] = excs.get(l, 0) + 1
    return " ".join(tail), excs


def resolve(root):
    code = ("import sys; sys.path.insert(0, %r); import card, synthetic; "
            "print(synthetic.__file__); print(hasattr(synthetic, 'linear_parameters')); print(sys.path[0])"
            % str(root / "tools" / "den-photometry"))
    rc, out, err = run([PY, "-B", "-c", code], cwd=root)
    lines = (out.split("\n") + ["?", "?", "?"])[:3]
    return lines, err


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).resolve()
    control = "--control" in sys.argv[2:]
    dl_syn = root / "tools" / "den-light" / "synthetic.py"
    dp_syn = root / "tools" / "den-photometry" / "synthetic.py"
    print("RESULT tree:", root)
    print("RESULT tools/den-photometry/synthetic.py exists:", dp_syn.exists())
    print("RESULT tools/den-light/synthetic.py exists:", dl_syn.exists())

    (path, has, sp0), err = resolve(root)
    print("RESULT after `import card`, sys.path[0] =", sp0)
    print("RESULT `import synthetic` resolves to:", path)
    print("RESULT it has den-photometry's linear_parameters:", has)
    if err.strip():
        print("RESULT import stderr:", err.strip().splitlines()[-1])
    shadowed = "den-light" in path

    rc, out, err = run([PY, "-B", "-m", "unittest", "discover", "-s", "tools/den-photometry", "-v"], cwd=root)
    tail, excs = summary(err)
    print("RESULT documented suite command rc=%d: %s" % (rc, tail))
    for line, n in sorted(excs.items(), key=lambda kv: -kv[1]):
        print("RESULT   %3dx %s" % (n, line[:160]))
    suite_broken = rc != 0

    rc2, out2, err2 = run([PY, "-B", "-m", "unittest"], cwd=root / "tools" / "den-photometry")
    tail2, excs2 = summary(err2)
    print("RESULT alternate (cd tools/den-photometry; python -m unittest) rc=%d: %s" % (rc2, tail2))

    rc3, out3, err3 = run([PY, "-B", "tools/den-photometry/photometry.py", "--self-check"], cwd=root, timeout=300)
    print("RESULT --self-check rc=%d: %s" % (rc3, (out3.strip().splitlines() or err3.strip().splitlines() or ["?"])[0][:160]))

    if control:
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            for rel in ("tools/den-photometry", "tools/den-light", "docs/architecture/live-v1"):
                shutil.copytree(root / rel, t / rel, ignore=shutil.ignore_patterns("__pycache__"))
            # den-photometry's refusal test expects its own folder to sit inside a git checkout
            # (sweep.inside_checkout looks for a .git entry), so mark the temp copy as one.
            (t / ".git").write_text("gitdir: control-copy\n")
            removed = t / "tools" / "den-light" / "synthetic.py"
            if removed.exists():
                removed.unlink()
            (cpath, chas, _), _ = resolve(t)
            print("RESULT control (only den-light/synthetic.py removed): synthetic resolves to", cpath)
            rc4, _, err4 = run([PY, "-B", "-m", "unittest", "discover", "-s", "tools/den-photometry"], cwd=t)
            tail4, excs4 = summary(err4)
            print("RESULT control suite rc=%d: %s" % (rc4, tail4))
            for line, n in sorted(excs4.items(), key=lambda kv: -kv[1]):
                print("RESULT   control %3dx %s" % (n, line[:160]))

    if shadowed or suite_broken:
        print("FAIL (shadowed: den-photometry's tests import %s; documented suite rc=%d)" % (path, rc))
        return 1
    print("PASS (own module; documented suite rc=0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

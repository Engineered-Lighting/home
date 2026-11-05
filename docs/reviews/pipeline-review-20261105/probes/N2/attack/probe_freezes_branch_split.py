"""Probe: can the owner run score.py, as den-n2's README says, once N1 has frozen on its own branch?

den-label's README: the owner works in a claude/den-label worktree; an agent appends the freeze lines to
FREEZES.md there and pushes. den-n2's README: make a separate claude/den-n2 worktree and run
`python -B tools\\den-n2\\score.py --split development --den-core ...` there. score.py always reads
tools/den-label/FREEZES.md (and PROTOCOL-V1.md) from its OWN checkout and has no option to read another one.

This probe builds frozen N1 folders (synthetic, temp folder) with a matching FREEZES text in a separate
"den-label worktree" file, then runs the checkout's score.py through its CLI, as the owner would, and once
more with a --freezes option.

Run:  python3 -B probe_freezes_branch_split.py <root of the den-n2 checkout>
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402


def main():
    ok = True
    with tempfile.TemporaryDirectory(prefix="n2-probe-freezes-") as tmp:
        tmp = Path(tmp)
        world = T.World(tmp, T.FOUR_DAYS[:2])  # world.freezes stands for the den-label worktree's FREEZES.md
        core = tmp / "den-core"
        core.mkdir()
        (core / "check.py").write_text("def check(path):\n    return {'ok': True, 'reason': 'fake'}\n",
                                       encoding="utf-8")
        script = ROOT / "tools" / "den-n2" / "score.py"
        print("RESULT checkout FREEZES.md lines:",
              [l for l in (ROOT / "tools/den-label/FREEZES.md").read_text().splitlines() if l.startswith("den-n1 ")])
        cmd = [sys.executable, "-B", str(script), "--split", "development", "--n1", str(world.n1),
               "--n2", str(world.n2), "--den-core", str(core)]
        p = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True)
        print(f"RESULT README command: exit={p.returncode} out={p.stdout.strip()[:200]!r}")
        if p.returncode == 2 and "no labels freeze line" in p.stdout:
            print("FAIL the README's command refuses although N1's folders are frozen and logged in the den-label "
                  "worktree's FREEZES.md")
            ok = False
        p = subprocess.run(cmd + ["--freezes", str(world.freezes)], cwd=tmp, capture_output=True, text=True)
        print(f"RESULT with --freezes: exit={p.returncode} err={p.stderr.strip().splitlines()[-1][:160]!r}")
        if p.returncode != 0:
            print("FAIL score.py offers no option to read the FREEZES.md that holds N1's lines")
            ok = False
    print("RESULT overall:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()

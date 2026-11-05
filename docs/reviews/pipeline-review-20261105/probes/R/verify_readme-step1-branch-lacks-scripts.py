#!/usr/bin/env python3
"""verify readme-step1-branch-lacks-scripts: does the branch README Step 1 names accept Step 8's --script?

    python3 -B verify_readme-step1-branch-lacks-scripts.py <rung-r checkout> <den-take-script checkout>

Reads den-take-script's README (Step 1's worktree branch, Step 8's command), then parses
Step 8's arguments with each checkout's own take.py argument parser ONLY: take.main is
called with --script hard-a; on rung-r argparse must exit 2 before run() (no camera,
no token, no folder). On den-take-script only the parser is inspected (run() is patched
out so nothing is read or written). Prints RESULT.
"""
import contextlib
import importlib
import io
import re
import sys
from pathlib import Path
from unittest import mock

rung_r, script_branch = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
readme = (script_branch / "tools" / "den-take" / "README.md").read_text(encoding="utf-8")
step1 = re.search(r"## Step 1.*?(?=\n## )", readme, re.S).group(0)
step8 = re.search(r"## Step 8.*?(?=\n## |\nExit codes)", readme, re.S).group(0)
branch = re.search(r"worktree add \S+ (\S+)", step1).group(1)
cmd = re.search(r"python -B tools\\den-take\\take\.py (--script \S+)", step8).group(1)
print(f"README Step 1 worktree branch: {branch}; Step 8 command uses: {cmd}")

def parse(root):
    sys.path.insert(0, str(root / "tools" / "den-take"))
    for name in ("take", "check"):
        sys.modules.pop(name, None)
    take = importlib.import_module("take")
    sys.path.pop(0)
    err = io.StringIO()
    with mock.patch.object(take, "run", lambda args: ("would run", args.script)) as _, \
            contextlib.redirect_stderr(err):
        try:
            out = take.main(["--script", "hard-a", "--out", "/nonexistent/den-takes/x", "--camera",
                             "http://127.0.0.1:9", "--token-file", "/nonexistent/token.txt"])
            return f"parsed, run() would be called: {out}"
        except SystemExit as exc:
            return f"SystemExit {exc.code}: {err.getvalue().strip().splitlines()[-1]}"

r = parse(rung_r)
s = parse(script_branch)
print(f"{branch} take.py: {r}")
print(f"claude/den-take-script take.py: {s}")
ok = branch == "claude/rung-r" and r.startswith("SystemExit 2") and "invalid choice: 'hard-a'" in r \
    and s.startswith("parsed")
print("RESULT", "REPRODUCED: Step 1's branch rejects Step 8's --script hard-a at argparse (exit 2, nothing "
      "recorded); the branch carrying the README accepts it" if ok else "NOT REPRODUCED")

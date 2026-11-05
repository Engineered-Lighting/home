#!/usr/bin/env python3
"""Verifier probe (intent/spec lens) for finding stale-den-take-copy.

What it checks (read-only on the checkout; any git writes happen in a throwaway clone in a temp dir):
  1. The checkout's tools/den-take differs from rung-r's pin 8b1c945c only in CHECK-V1.md, take.py
     and test_den_take.py (check.py, which n1.py imports, is byte-identical).
  2. The checkout's tools/den-take equals the merge base with rung-r (den-n2 never edited it).
  3. Nothing in tools/den-n2 or tools/den-label calls take.run / take.main / check.read_take /
     check.main or reads CHECK-V1.md, i.e. N2's own path never records or checks a take.
  4. A three-way merge of den-n2 HEAD with rung-r (git merge-tree, in a temp clone) keeps rung-r's
     take.py and CHECK-V1.md.

Run:
    python3 -I verify_intent_stale-den-take-copy.py <den-n2 checkout root> [rung-r commit]
No network, no camera; the pinned checkout is never modified.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
rung_r = sys.argv[2] if len(sys.argv) > 2 else "8b1c945ce28b8b8237d5def3de737867ea6d4cac"


def git(*args, cwd=root):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


head = git("rev-parse", "HEAD").strip()
changed = git("diff", "--name-only", rung_r, head, "--", "tools/den-take").split()
print("RESULT den-take files differing from rung-r pin:", changed)
print("PASS" if "tools/den-take/check.py" not in changed else "FAIL", "check.py identical to rung-r's")
base = git("merge-base", head, rung_r).strip()
same_as_base = git("diff", "--name-only", base, head, "--", "tools/den-take").split() == []
print("RESULT merge base:", base[:12], "| den-take unchanged since base on this branch:", same_as_base)

calls = re.compile(r"\b(?:den_take|take)\.(?:run|main)\(|\bcheck\.(?:read_take|main|run)\(|CHECK-V1")
hits = []
for folder in ("tools/den-n2", "tools/den-label"):
    for path in sorted((root / folder).glob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if calls.search(line):
                hits.append(f"{path.relative_to(root)}:{i}: {line.strip()[:100]}")
print("RESULT N2/N1 code lines that record or check a take, or read CHECK-V1:", hits or "none")
uses = sorted(set(re.findall(r"\b(?:check|den_take)\.[a-z_]+", (root / "tools/den-label/n1.py").read_text())
                  + re.findall(r"\bn1\.den_take\.[a-z_]+", (root / "tools/den-n2/score.py").read_text())))
print("RESULT den-take names N1/N2 code uses:", uses)

with tempfile.TemporaryDirectory(prefix="n2-merge-") as tmp:
    clone = Path(tmp) / "repo"
    subprocess.run(["git", "clone", "-q", "--no-checkout", "--shared", str(root), str(clone)], check=True)
    tree = git("merge-tree", "--write-tree", head, rung_r, cwd=clone).split()[0]
    for f in ("tools/den-take/take.py", "tools/den-take/CHECK-V1.md"):
        merged = git("rev-parse", f"{tree}:{f}", cwd=clone).strip()
        theirs = git("rev-parse", f"{rung_r}:{f}", cwd=clone).strip()
        print("PASS" if merged == theirs else "FAIL", f"merge keeps rung-r's {f}")

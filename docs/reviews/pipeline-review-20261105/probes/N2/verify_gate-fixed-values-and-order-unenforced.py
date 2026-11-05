#!/usr/bin/env python3
"""Verifier probe for finding gate-fixed-values-and-order-unenforced, in the documented flow's shape:
the gate lives in a git checkout (not a loose file), as tools/den-n2/GATE-V1.md does for the owner.

What it checks (synthetic N1 world from the branch's own test helpers, temp dirs only, fake check.py,
no network, no real data):
  1. A gate repo (temp git repo, separate from the N2 folder) holds GATE-V1.md with every slot filled and
     the canonical fixed values, COMMITTED AFTER the held-out ask lines' asked_unix (GIT_COMMITTER_DATE set
     5 days after them). Does --split held_out refuse because the gate's commit time does not precede the
     first held-out ask line (plan section 7.3)? Prints commit_unix vs the latest asked_unix.
  2. The same gate with sample.units/sessions/days edited to 1 in the WORKING COPY only (dirty). Does the
     held-out run refuse a dirty gate or a changed fixed value, or does the verdict flip?
  3. What the RESULTS.md line carries (commit or dirty, or only the sha256).

Run:  python3 -B verify_gate-fixed-values-and-order-unenforced.py <root of the claude/den-n2 checkout>
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402
score = T.score

tmp = Path(tempfile.mkdtemp(prefix="n2-verify-gate-"))
try:
    world = T.World(tmp / "world", T.FOUR_DAYS[:4])
    repo = tmp / "repo"
    repo.mkdir()
    git = ["git", "-c", "user.email=v@example.invalid", "-c", "user.name=v", "-C", str(repo)]
    subprocess.run(git + ["init", "-q"], check=True)
    gate = repo / "GATE-V1.md"
    shutil.copy(world.gate, gate)
    asked = [json.loads(x)["asked_unix"] for p in (world.n2 / "asks").glob("*.jsonl")
             for x in p.read_text().splitlines() if x.strip()]
    late = int(max(asked)) + 5 * 86400
    env = dict(os.environ, GIT_COMMITTER_DATE=f"{late} +0000", GIT_AUTHOR_DATE=f"{late} +0000")
    subprocess.run(git + ["add", "GATE-V1.md"], check=True, env=env)
    subprocess.run(git + ["commit", "-qm", "gate filled"], check=True, env=env)

    try:
        r1, p1 = world.run("held_out", gate_path=gate)
        g = r1["gate"]
        print(f"RESULT 1 committed after the asks: verdict={r1['verdict']} commit={str(g['commit'])[:12]} "
              f"commit_unix={g['commit_unix']} latest asked_unix={max(asked)} dirty={g['dirty']}")
        print(("FAIL" if g["commit_unix"] > max(asked) else "PASS") +
              " 1: a held-out run scores with a gate committed after its ask lines (order not checked)")
    except score.Refused as exc:
        print(f"PASS 1: refused: {exc}")

    text = gate.read_text(encoding="utf-8")
    for key in ("sample.units = 30", "sample.sessions = 3", "sample.days = 2"):
        assert key in text, key
    edited = (text.replace("sample.units = 30", "sample.units = 1").replace("sample.sessions = 3", "sample.sessions = 1")
              .replace("sample.days = 2", "sample.days = 1"))
    gate.write_text(edited, encoding="utf-8")
    try:
        r2, p2 = world.run("held_out", gate_path=gate)
        g = r2["gate"]
        print(f"RESULT 2 dirty gate, fixed sample.* = 1: verdict={r2['verdict']} commit={str(g['commit'])[:12]} "
              f"dirty={g['dirty']}")
        flipped = r1["verdict"] == "insufficient" and r2["verdict"] == "feasible"
        print(("FAIL" if flipped else "PASS") + " 2: a dirty gate with an edited fixed value flips insufficient "
              "to feasible without a refusal")
        line = score.results_line(r2, p2)
        print("RESULT 3 RESULTS.md line: " + line)
        print(("FAIL" if "commit" not in line and "dirty" not in line else "PASS") +
              " 3: the RESULTS.md line carries neither the gate's commit nor its dirty flag")
    except score.Refused as exc:
        print(f"PASS 2: refused: {exc}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

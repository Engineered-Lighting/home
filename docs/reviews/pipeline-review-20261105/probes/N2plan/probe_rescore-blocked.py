"""probe_rescore-blocked: N2 plan's "insufficient -> more sessions -> score once more, GATE-V1
unchanged" path cannot run: adding sessions forces a new N1 fixture version with a new hash, and
N2 refuses any stand-in hash that differs from GATE-V1's.

What it checks
  1. PLAN.md: the fixture is a stand-in (section 3), GATE-V1 holds "stand-in versions and hashes"
     (section 8), rule 1 refuses a stand-in hash that differs (4.7), the ask step's refusal is
     pinned to labels-v1 (section 6), and the insufficient path keeps GATE-V1 unchanged (7.5).
  2. With N1's own tool (den-label's n1.py and its synthetic-take helpers, copied to a temp dir):
     freeze fixture-v1 + labels-v1 on 4 sessions, register 2 more sessions, label them, and show
     (a) freeze_labels refuses until the fixture is frozen again, (b) fixture-v2's sha256 differs
     from fixture-v1's although the old sessions' box files are byte-identical.

Run
  python3 probe_rescore-blocked.py <root of claude/plan-n2> [<root of claude/den-label checkout>]
  (the second root defaults to the sibling folder 'den-label'; nothing is written inside it)
Synthetic takes only, in a temp dir; the only socket guard is den-label's own loopback guard.
"""
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def plan_checks(root):
    plan = re.sub(r"\s+", " ", (Path(root) / "docs/plans/n2-need-test/PLAN.md").read_text(encoding="utf-8"))
    for phrase in ("| Boxes and id | `fixture-v1`",
                   "stand-in versions and hashes, S_dev, D_dev",
                   "A stand-in's hash differs from GATE-V1's",
                   "any session until the labels-v1 freeze line is in `tools/den-label/FREEZES.md`",
                   "the enlarged held-out set is scored once more, GATE-V1 unchanged"):
        print(("PASS" if phrase in plan else "FAIL") + f": PLAN.md says {phrase!r}")


def main(root, label_root):
    plan_checks(root)
    work = Path(tempfile.mkdtemp(prefix="n2plan-rescore-"))
    try:
        for sub in ("tools/den-label", "tools/den-take"):
            shutil.copytree(Path(label_root) / sub, work / sub, ignore=shutil.ignore_patterns("__pycache__"))
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(work / "tools/den-label"))
        import test_den_label as T  # noqa: E402  (the copy)
        import n1  # noqa: E402
        import label  # noqa: E402
        T.setUpModule()
        t = T.FreezeTests("test_canonical_bytes_give_fixed_hashes")
        t.setUp()
        try:
            ws = t.build()
            with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 10 * T.DAY):
                fixture_line, _ = ws.freeze_fixture()
                labels_line, record, _ = ws.freeze_labels()
            v1 = json.loads((ws.root / "fixture-v1" / "fixture.json").read_bytes())
            print("RESULT v1:", fixture_line.split(" protocol=")[0], "|", labels_line.split(" protocol=")[0])
            # 'insufficient': record two more sessions under N1's rule, a new labels version
            for i, (name, script) in enumerate((("a3", "hard-a"), ("b3", "hard-b"))):
                T.staged_take(t.takes, name, script, T.BASE_UNIX + (2 + i) * T.DAY)
            ws = n1.Workspace(t.takes, t.takes / "n1")
            ws.scan()
            with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 12 * T.DAY):
                for name in ("a3", "b3"):
                    t.boxes_done(ws, name)
                    t.labels_done(ws, name)
            with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 13 * T.DAY):
                try:
                    ws.freeze_labels(labels_line + "\n")
                    print("RESULT labels-v2 froze without a new fixture")
                    refused = False
                except n1.Refused as exc:
                    print(f"RESULT labels-v2 freeze refused: {exc}")
                    refused = True
                line2, _ = ws.freeze_fixture(fixture_line + "\n")
            v2 = json.loads((ws.root / "fixture-v2" / "fixture.json").read_bytes())
            h1 = n1.file_sha256(ws.root / "fixture-v1" / "fixture.json")
            h2 = n1.file_sha256(ws.root / "fixture-v2" / "fixture.json")
            old = {s["take"]: s["boxes"]["sha256"] for s in v1["sessions"]}
            new = {s["take"]: s["boxes"]["sha256"] for s in v2["sessions"]}
            same_old = all(new.get(k) == v for k, v in old.items())
            print(f"RESULT fixture-v1 sha256 {h1[:16]}..., fixture-v2 sha256 {h2[:16]}...; "
                  f"old sessions' box files identical in v2: {same_old}; v2 sessions {sorted(new)}")
            if refused and h1 != h2:
                print("FAIL: the enlarged held-out set needs fixture-v2, whose hash differs from the fixture-v1 "
                      "hash GATE-V1 pins, so rule 1 refuses the rescoring that section 7 step 5 requires "
                      "with GATE-V1 unchanged")
            else:
                print("PASS: the enlarged set can be scored under the same fixture hash")
        finally:
            t.doCleanups()
            T.tearDownModule()
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    lab = sys.argv[2] if len(sys.argv) > 2 else str(Path(root).resolve().parent / "den-label")
    main(root, lab)

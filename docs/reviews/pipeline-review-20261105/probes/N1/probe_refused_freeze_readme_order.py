#!/usr/bin/env python3
"""probe_refused_freeze_readme_order: after a newer check, does the README's own step order
(`freeze fixture`, then `freeze labels`) print the clip changes, unlike `freeze labels` run first?

Verifier probe for finding refused-freeze-silently-clips-drafts (INTENT lens). Same synthetic set-up
as probe_freeze_sync_unprinted.py: two staged takes a1 (hard-a) and b1 (hard-b) boxed, labelled end
to end, b1 re-marked, fixture-v1 frozen; then a newer check on a1 (a second person at 01:03, cutting
an interval). Case A runs `freeze fixture` next (README Step 6 order: a changed check needs a new
fixture anyway); case B, from the same snapshot, runs `freeze labels` next (the finding's order).
Prints whether each run printed the clip lines, its exit code, and whether a1's draft was rewritten.
Synthetic takes from the branch's own test helpers; temp dirs only; nothing leaves 127.0.0.1.
Run:
    python3 probe_refused_freeze_readme_order.py /path/to/den-label-checkout
"""
import io
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-label"))
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import test_den_label as T  # noqa: E402
import n1  # noqa: E402
import label  # noqa: E402


class Builder(T.Base):
    def runTest(self):
        pass


def unlock(tree):
    for p in Path(tree).rglob("*"):
        try:
            os.chmod(p, stat.S_IRWXU)
        except OSError:
            pass


def main():
    T.setUpModule()
    tmp = Path(tempfile.mkdtemp(prefix="n1-probe-order-")).resolve()
    try:
        b = Builder()
        b.tmp, b.takes = tmp, tmp / "takes"
        b.takes.mkdir()
        b.n1root = b.takes / "n1"
        names = (("a1", "hard-a"), ("b1", "hard-b"))
        for i, (name, script) in enumerate(names):
            T.staged_take(b.takes, name, script, T.BASE_UNIX + i * T.DAY)
        T.commissioning_take(b.takes)
        ws = n1.Workspace(b.takes, b.n1root)
        ws.scan()
        b.surfaces_done(ws, "c1")
        with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 5 * T.DAY):
            for name, _ in names:
                b.boxes_done(ws, name)
                b.labels_done(ws, name)
        with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 9 * T.DAY):
            p = label.Pass(ws, "remark", out=io.StringIO())
            p.save({"intervals": [b.interval(ws, "b1", 40.5, 50.2)], "spans": [], "end_to_end": True})
        argv = ["--takes", str(b.takes), "--n1", str(b.n1root)]
        with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 10 * T.DAY):
            label.main(["freeze", "fixture"] + argv, out=io.StringIO())
        T.recheck(b.takes / "a1", "01:03")  # newer check: second person at 01:03
        snap = tmp / "snap"
        unlock(b.takes)
        shutil.copytree(b.takes, snap / "takes")
        for case, takes, cmd in (("A freeze fixture first (README order)", b.takes, ["freeze", "fixture"]),
                                 ("B freeze labels first (finding's order)", snap / "takes", ["freeze", "labels"])):
            a = ["--takes", str(takes), "--n1", str(takes / "n1")]
            wsx = n1.Workspace(takes, takes / "n1")
            before = json.loads(wsx.draft_path("labels", "a1").read_bytes())
            out = io.StringIO()
            with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 11 * T.DAY):
                code = label.main(cmd + a, out=out)
            after = json.loads(wsx.draft_path("labels", "a1").read_bytes())
            text = out.getvalue()
            clip_lines = [l for l in text.splitlines() if l.startswith("a1 labels:")]
            print(f"RESULT case {case}: exit {code}; draft rewritten "
                  f"{before['intervals'] != after['intervals'] or before['check'] != after['check']}; "
                  f"clip lines printed {len(clip_lines)}")
            for l in clip_lines:
                print("   ", l)
            if not clip_lines:
                print("    output:", text.strip()[:200])
    finally:
        T.tearDownModule()
        unlock(tmp)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

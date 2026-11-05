#!/usr/bin/env python3
"""probe_freeze_sync_unprinted: does `label.py freeze labels` print the clip changes it applies?

README "A second person R's check missed": once a newer check is accepted, "the next label.py
command clips your drafts to the new cuts, prints each change, and clears the end-to-end mark".
freeze_fixture/freeze_labels start with sync_all(), which writes the clipped drafts, but label.main
prints `changes` only after the freeze returns. If the freeze then refuses (exit 2, "refused,
nothing written"), the clipped/dropped intervals are written to disk and never printed, and a
second command has nothing left to print.

Also prints the "EvalRecord fields N1 fills" JSON of a successful freeze and compares its layout
with schemas.json's EvalRecord.gold_set {n_intervals, refreshed, label_version, hash, ..., n1}.

Synthetic takes from the branch's own test helpers; temp dirs only; nothing leaves 127.0.0.1.
Run:
    python3 probe_freeze_sync_unprinted.py /path/to/den-label-checkout [/path/to/live-records-checkout]
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
SPEC = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
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
    tmp = Path(tempfile.mkdtemp(prefix="n1-probe-sync-")).resolve()
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
        # a successful pair of freezes first, to read the EvalRecord fields
        snapshot = tmp / "snap"
        shutil.copytree(b.takes, snapshot / "takes")
        with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 10 * T.DAY):
            out = io.StringIO()
            code1 = label.main(["freeze", "fixture"] + argv, out=out)
            out2 = io.StringIO()
            code2 = label.main(["freeze", "labels"] + argv, out=out2)
        text = out2.getvalue().splitlines()
        rec = json.loads(text[text.index("EvalRecord fields N1 fills:") + 1])
        print(f"RESULT freeze fixture exit {code1}, freeze labels exit {code2}")
        print("RESULT printed EvalRecord fields:", json.dumps(rec, sort_keys=True))
        top = sorted(rec)
        inside = sorted(rec["gold_set"])
        print(f"RESULT top-level keys {top}; gold_set keys {inside}")
        if SPEC:
            schema = json.loads((SPEC / "schemas.json").read_text(encoding="utf-8"))
            ev = next(r for r in schema["records"] if r["name"] == "EvalRecord")
            gs = next(f for f in ev["fields"] if f["name"] == "gold_set")
            print("RESULT schemas.json EvalRecord.gold_set type:", gs["type"])
            want_inside = {"n_intervals", "refreshed", "label_version", "hash", "n1"}
            misplaced = sorted(k for k in want_inside if k not in rec["gold_set"] and k in rec)
            print(("FAIL " if misplaced else "PASS ") + f"fields schemas.json puts inside gold_set but the tool prints at top level: {misplaced}")
        # now the refusal case, from the snapshot taken before the freezes
        takes = snapshot / "takes"
        argv = ["--takes", str(takes), "--n1", str(takes / "n1")]
        ws2 = n1.Workspace(takes, takes / "n1")
        with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 10 * T.DAY):
            label.main(["freeze", "fixture"] + argv, out=io.StringIO())
        before = json.loads(ws2.draft_path("labels", "a1").read_bytes())
        T.recheck(takes / "a1", "01:03")  # a newer check: a second person at 01:03, cut over the 60-66 s interval
        with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 11 * T.DAY):
            out = io.StringIO()
            code = label.main(["freeze", "labels"] + argv, out=out)
        after = json.loads(ws2.draft_path("labels", "a1").read_bytes())
        printed = out.getvalue()
        print(f"RESULT freeze labels after the newer check: exit {code}; output: {printed.strip()!r}")
        changed = before["intervals"] != after["intervals"] or before["check"] != after["check"]
        print(f"RESULT a1 labels draft on disk changed by that refused command: {changed} "
              f"(intervals {len(before['intervals'])} -> {len(after['intervals'])}, check {before['check']} -> {after['check']}, "
              f"end_to_end {bool(before['actor']['end_to_end'])} -> {bool(after['actor']['end_to_end'])})")
        mentions = "interval" in printed and "carried" in printed
        print(("FAIL " if changed and not mentions else "PASS ") +
              "a refused freeze that clipped drafts printed the clip changes")
        out = io.StringIO()
        label.main(["status"] + argv, out=out)
        again = [l for l in out.getvalue().splitlines() if "interval" in l and "frames" in l]
        print(("FAIL " if changed and not again else "PASS ") + f"the next command (status) prints them: {again}")
    finally:
        unlock(tmp)
        shutil.rmtree(tmp, ignore_errors=True)
        T.tearDownModule()


if __name__ == "__main__":
    main()

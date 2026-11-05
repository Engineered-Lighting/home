#!/usr/bin/env python3
"""probe_split_rule: N1's whole-session split, recomputed independently, plus late registration.

PROTOCOL-V1 "Split": within each staged script name, sessions alternate development, held-out,
... in recording order (take.json's started_unix); the first of each script is development; a
session's split never changes. "Re-mark": the re-marked session is the second development session
in recording order.

Checks:
  1. n1.register on shuffled synthetic sessions vs an independent per-script alternation over
     started_unix: same split for every session, and no session in both parts.
  2. Late registration (a take checked after later takes were registered): the split follows
     registration order, not recording order.
  3. With real Workspace scans on synthetic takes: a development take registered late, with an
     earlier started_unix, moves the re-mark target after the re-mark of another session has
     started; that session's first label pass stays locked.
Synthetic takes from the branch's own test helpers; temp dirs only; nothing leaves 127.0.0.1.
Run:
    python3 probe_split_rule.py /path/to/den-label-checkout
"""
import io
import os
import random
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


def independent_split(sessions):
    by_script = {}
    for take_id, script, started in sorted(sessions, key=lambda s: (s[2], s[0])):
        by_script.setdefault(script, []).append(take_id)
    out = {}
    for script, ids in by_script.items():
        for k, take_id in enumerate(ids):
            out[take_id] = "development" if k % 2 == 0 else "held_out"
    return out


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
    tmp = Path(tempfile.mkdtemp(prefix="n1-probe-split-")).resolve()
    rnd = random.Random(7)
    bad = 0
    for trial in range(200):
        sessions = [(f"s{i:02d}", rnd.choice(["hard-a", "hard-b", "hard-c"]), float(rnd.randint(0, 10_000)) + i * 1e-3)
                    for i in range(rnd.randint(1, 12))]
        path = tmp / f"split-{trial}.json"
        shuffled = sessions[:]
        rnd.shuffle(shuffled)
        added = n1.register(path, shuffled)  # one scan sees all of them
        got = {e["session"]: e["split"] for e in added}
        both = [s for s in got if list(e["session"] for e in n1.load_split(path)).count(s) != 1]
        if got != independent_split(sessions) or both:
            bad += 1
    print(("PASS " if bad == 0 else "FAIL ") + f"one-scan registration matches an independent per-script alternation "
          f"in started_unix order, every session in exactly one part ({bad} bad of 200)")

    path = tmp / "late.json"
    n1.register(path, [("b2", "hard-b", 300.0)])            # b1 (recorded at 100) not checked yet
    n1.register(path, [("b1", "hard-b", 100.0)])            # checked later, registered later
    got = {e["session"]: e["split"] for e in n1.load_split(path)}
    want = independent_split([("b1", "hard-b", 100.0), ("b2", "hard-b", 300.0)])
    print(("FAIL " if got != want else "PASS ") + f"late registration: tool {got} vs recording-order rule {want}")

    T.setUpModule()
    try:
        b = Builder()
        b.tmp, b.takes = tmp / "ws", tmp / "ws" / "takes"
        b.takes.mkdir(parents=True)
        b.n1root = b.takes / "n1"
        T.staged_take(b.takes, "a1", "hard-a", T.BASE_UNIX)
        T.staged_take(b.takes, "b1", "hard-b", T.BASE_UNIX + 3600, typed=None)  # no check file yet
        T.staged_take(b.takes, "a2", "hard-a", T.BASE_UNIX + T.DAY)
        T.staged_take(b.takes, "a3", "hard-a", T.BASE_UNIX + 2 * T.DAY)
        ws = n1.Workspace(b.takes, b.n1root)
        added, notes = ws.scan()
        print("RESULT first scan:", [(e["session"], e["split"]) for e in added], notes)
        first_target = ws.remark_take()
        print("RESULT re-mark target after the first scan:", first_target)
        with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 3 * T.DAY):
            b.boxes_done(ws, first_target)
            b.labels_done(ws, first_target)
        with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 7 * T.DAY):
            label.Pass(ws, "remark", out=io.StringIO())  # the owner starts re-marking it
        T.recheck(b.takes / "b1", "none")
        added, _ = ws.scan()
        print("RESULT second scan:", [(e["session"], e["split"]) for e in added])
        second_target = ws.remark_take()
        print(("FAIL " if second_target != first_target else "PASS ") +
              f"re-mark target stays put once its re-mark started: {first_target} -> {second_target}")
        try:
            label.Pass(ws, "labels", first_target, out=io.StringIO())
            locked = False
        except n1.Refused as exc:
            locked = "locked" in str(exc)
        print(f"RESULT {first_target}'s first label pass still locked by an orphaned re-mark: {locked}")
        status = "\n".join(ws.status())
        print("RESULT status re-mark line:", [l for l in status.splitlines() if l.startswith("Re-mark")])
    finally:
        T.tearDownModule()
        unlock(tmp)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

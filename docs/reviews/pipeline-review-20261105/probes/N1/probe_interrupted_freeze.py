#!/usr/bin/env python3
"""probe_interrupted_freeze: what an interrupted `freeze fixture` (disk full) leaves behind.

n1.write_frozen makes the folder, then writes each file with open(..., "xb"), the manifest last
(fixture.json is the last key). The plan notes C: is 97% full. This probe makes the 3rd file
write raise OSError(ENOSPC) during `label.py freeze fixture`, then runs, as the owner would next:
`label.py verify`, `label.py freeze fixture` again, and `label.py freeze labels`, and reports
whether each fails cleanly (a Refused message / exit code) or with an uncaught traceback, and
whether the half-written fixture-v1 is left as the "latest fixture" that freeze labels reads.

Synthetic takes from the branch's own test helpers; temp dirs only; nothing leaves 127.0.0.1.
Run:
    python3 probe_interrupted_freeze.py /path/to/den-label-checkout
"""
import builtins
import errno
import io
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


def attempt(name, argv):
    out = io.StringIO()
    try:
        code = label.main(argv, out=out)
        print(f"RESULT {name}: exit {code}; output {out.getvalue().strip()[:300]!r}")
        return code, None
    except Exception as exc:  # noqa: BLE001  what the owner would see as a traceback
        print(f"RESULT {name}: UNCAUGHT {type(exc).__name__}: {exc}")
        return None, exc


def main():
    T.setUpModule()
    tmp = Path(tempfile.mkdtemp(prefix="n1-probe-int-")).resolve()
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
        real_open, count = builtins.open, {"n": 0}

        def full_disk(path, mode="r", *a, **k):
            if "x" in mode:
                count["n"] += 1
                if count["n"] == 3:
                    raise OSError(errno.ENOSPC, "No space left on device", str(path))
            return real_open(path, mode, *a, **k)
        with mock.patch.object(builtins, "open", full_disk):
            attempt("freeze fixture with the disk filling on the 3rd file", ["freeze", "fixture"] + argv)
        folder = b.n1root / "fixture-v1"
        left = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()) if folder.exists() else None
        print(f"RESULT left behind: fixture-v1 exists={folder.exists()} files={left}")
        attempt("verify", ["verify"] + argv)
        attempt("freeze fixture again", ["freeze", "fixture"] + argv)
        code, exc = attempt("freeze labels", ["freeze", "labels"] + argv)
        print(f"RESULT versions on disk: fixture {n1.versions(b.n1root, 'fixture')}")
        clean = exc is None
        print(("PASS " if clean else "FAIL ") + "after an interrupted fixture freeze, verify and freeze labels fail cleanly or work")
    finally:
        T.tearDownModule()
        unlock(tmp)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

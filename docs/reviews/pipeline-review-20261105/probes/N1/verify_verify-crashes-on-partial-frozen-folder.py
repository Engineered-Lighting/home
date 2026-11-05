#!/usr/bin/env python3
"""verify_verify-crashes-on-partial-frozen-folder: after an interrupted `freeze fixture` (disk full on the
3rd file), does `label.py verify` crash, does `freeze labels` run straight away crash, and does the
documented re-freeze (`freeze fixture` again -> fixture-v2) recover verify or only the freeze?

Also: does the half-written folder fix the pad (versions() counts it as a frozen fixture)?

Synthetic takes from the branch's own test helpers; temp dirs only (under the given scratch dir);
nothing leaves 127.0.0.1. Prints RESULT lines and PASS/FAIL.
Run:
    python3 verify_verify-crashes-on-partial-frozen-folder.py /path/to/den-label-checkout [/scratch/dir]
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
SCRATCH = sys.argv[2] if len(sys.argv) > 2 else None
sys.path.insert(0, str(ROOT / "tools" / "den-label"))
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import test_den_label as T  # noqa: E402
import n1  # noqa: E402
import label  # noqa: E402


class Builder(T.Base):
    def runTest(self):
        pass


def attempt(name, argv):
    out = io.StringIO()
    try:
        code = label.main(argv, out=out)
        text = out.getvalue().strip().replace("\n", " | ")
        print(f"RESULT {name}: exit {code}; output {text[:220]!r}")
        return code, None
    except Exception as exc:  # noqa: BLE001  the owner would see a traceback, exit 1
        print(f"RESULT {name}: UNCAUGHT {type(exc).__name__}: {str(exc)[-80:]}")
        return None, exc


def main():
    T.setUpModule()
    tmp = Path(tempfile.mkdtemp(prefix="n1-verify-partial-", dir=SCRATCH)).resolve()
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
        with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 10 * T.DAY), \
                mock.patch.object(builtins, "open", full_disk):
            attempt("freeze fixture, disk full on the 3rd file", ["freeze", "fixture"] + argv)
        folder = b.n1root / "fixture-v1"
        left = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
        print(f"RESULT left behind: fixture-v1 files={left}; versions(fixture)={n1.versions(b.n1root, 'fixture')}")
        _, e1 = attempt("verify", ["verify"] + argv)
        with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 10 * T.DAY):
            _, e2 = attempt("freeze labels straight away", ["freeze", "labels"] + argv)
        code_pad, _ = attempt("boxes --pad 30 (raise the pad)", ["boxes", "--pad", "30"] + argv)
        with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 10 * T.DAY):
            c3, _ = attempt("freeze fixture again (documented re-freeze)", ["freeze", "fixture"] + argv)
            c4, _ = attempt("freeze labels after the re-freeze", ["freeze", "labels"] + argv)
        _, e5 = attempt("verify after the re-freeze", ["verify"] + argv)
        print(("FAIL " if e1 else "PASS ") + "verify reports a frozen folder with no manifest instead of crashing")
        print(("FAIL " if e2 else "PASS ") + "freeze labels straight away refuses cleanly instead of crashing")
        print(("PASS " if c3 == 0 and c4 == 0 else "FAIL ") + "re-running freeze fixture recovers the freezes")
        print(("FAIL " if e5 else "PASS ") + "verify works again after the documented re-freeze")
        print(("FAIL " if code_pad == 2 else "PASS ") + "a half-written fixture folder does not fix the pad")
    finally:
        T.tearDownModule()
        for p in tmp.rglob("*"):
            try:
                os.chmod(p, stat.S_IRWXU)
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

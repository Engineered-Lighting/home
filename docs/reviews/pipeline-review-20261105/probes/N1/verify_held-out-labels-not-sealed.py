#!/usr/bin/env python3
"""Verify finding held-out-labels-not-sealed (den-label freeze_labels; consumer den-n2 score.load_n1).

What it checks
  1. On the den-label checkout: freeze_labels writes development AND held-out session labels
     into one labels-vN/sessions/ folder under one labels.json hash (the finding's premise).
  2. On the den-n2 checkout (N1's only consumer): a development run's loader,
     score.load_n1(n1root, "development", ...), is traced (every Path.read_bytes and open under
     the N1 folder is logged) to see whether it opens any held-out session label file, i.e.
     whether a development run "cannot be kept from" held-out labels.

Synthetic takes from den-label's own test helpers, temp folders only, no network, checkouts untouched.

How to run
  python3 -B verify_held-out-labels-not-sealed.py <den-label checkout> <den-n2 checkout>
"""
import builtins
import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock


def main():
    root = Path(sys.argv[1]).resolve()
    n2root = Path(sys.argv[2]).resolve()
    sys.path[:0] = [str(root / "tools/den-label"), str(root / "tools/den-take")]
    import n1
    import test_den_label as T
    for name in ("n1.py",):
        same = (root / "tools/den-label" / name).read_bytes() == (n2root / "tools/den-label" / name).read_bytes()
        print(f"INFO den-n2's tools/den-label/{name} identical to den-label's: {same}")

    class P(T.Base):
        def runTest(self):
            pass

    t = P()
    t.setUp()
    try:
        ws = T.FreezeTests.build(t)  # a1, b1 development; a2, b2 held_out
        with mock.patch.object(n1, "now", return_value=T.BASE_UNIX + 10 * T.DAY):
            fixture_line, _ = ws.freeze_fixture()
            labels_line, _, _ = ws.freeze_labels()
        manifest = json.loads((ws.root / "labels-v1" / "labels.json").read_bytes())
        files = sorted(p.name for p in (ws.root / "labels-v1" / "sessions").iterdir())
        print("RESULT premise: labels-v1/sessions holds", files, "splits",
              {s["take"]: s["split"] for s in manifest["sessions"]}, "under one labels.json hash")

        spec = importlib.util.spec_from_file_location("den_n2_score", n2root / "tools/den-n2/score.py")
        score = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(score)
        seen = []
        real_read_bytes, real_open = Path.read_bytes, builtins.open

        def traced_read_bytes(self):
            seen.append(str(self))
            return real_read_bytes(self)

        def traced_open(file, *a, **k):
            seen.append(str(file))
            return real_open(file, *a, **k)

        freezes = f"{fixture_line}\n{labels_line}\n"
        with mock.patch.object(Path, "read_bytes", traced_read_bytes), \
                mock.patch.object(builtins, "open", traced_open):
            out = score.load_n1(ws.root, "development", freezes)
        sessions = out[-1]
        opened = sorted({Path(p).relative_to(ws.root).as_posix() for p in seen
                         if str(p).startswith(str(ws.root)) and "/sessions/" in str(p).replace("\\", "/")})
        print("RESULT development load_n1 returned sessions:", [getattr(s, "take", s) for s in sessions])
        print("RESULT development load_n1 opened session label files:", opened)
        held = [p for p in opened if any(p.endswith(f"/{take}.json") for take in ("a2", "b2"))]
        if held:
            print("FAIL a development run opened held-out label files:", held)
            return 1
        print("PASS a development run never opens a held-out label file (the guard exists in N2's loader)")
        return 0
    finally:
        t.cleanup()


if __name__ == "__main__":
    sys.exit(main())

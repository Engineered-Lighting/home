#!/usr/bin/env python3
"""--recheck's open-slot refusal is not protected by the suite: the test that
claims to check it corrupts a frame first, so the hash check refuses instead.

    python3 probe_recheck_open_refusal_masked.py <branch-checkout-root>

<root> is a checkout of claude/rung-8-stage0. Copies tools/den-light (and the
camera fixture the tests read) to a temp dir, runs one synthetic sweep against
the suite's own FakeCamera on 127.0.0.1, then:

1. corrupted frame + open METHOD, original code: Refused (message names the
   open numbers);
2. corrupted frame + open METHOD, mutant M13 (open-slot check removed from
   recheck()): still Refused, now by the frame hash -- the suite's assertRaises
   (test_recheck_refuses_a_changed_frame_a_void_or_open_numbers) cannot tell;
3. intact frames + open METHOD, mutant M13: recheck writes recheck-1.json with
   a scored outcome although METHOD-V1 still has open numbers.
No host but 127.0.0.1 is contacted.
"""
import importlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sys.exit(__doc__)
OLD = ('    if method.open:\n        raise Refused("METHOD-V1 still has numbers the owner has not confirmed: " + '
       '", ".join(method.open))\n    folder = Path(folder)')
NEW = '    folder = Path(folder)'


def fresh(tmp, mutant):
    base = Path(tmp) / ("mutant" if mutant else "original")
    shutil.copytree(ROOT / "tools/den-light", base / "tools/den-light")
    (base / "tools/fixtures").mkdir(parents=True)
    shutil.copy2(ROOT / "tools/fixtures/installed-oak-capture-20261016.py", base / "tools/fixtures")
    if mutant:
        p = base / "tools/den-light/sweep.py"
        src = p.read_text()
        assert src.count(OLD) == 1
        p.write_text(src.replace(OLD, NEW))
    for name in ("meter", "sweep", "test_den_light"):
        sys.modules.pop(name, None)
    sys.path.insert(0, str(base / "tools/den-light"))
    try:
        return importlib.import_module("test_den_light")
    finally:
        sys.path.pop(0)


def scenario(t, corrupt):
    fake = t.FakeCamera()
    try:
        method = t.sweep_method()
        with t.stage0_folder() as (stage0, token):
            camera = t.sweep.Camera(fake.url, t.TOKEN)
            _, folder = t.sweep.start_run(stage0, camera, token, method)
            response = t.sweep.Sweep(camera, method, folder, say=t.quiet).run()
            if corrupt:
                frame = sorted((folder / "frames").iterdir())[3]
                frame.write_bytes(b"\0" + frame.read_bytes()[1:])
            opened = t.meter.Method(method.values, ("pixel_step",))
            try:
                record, path = t.sweep.recheck(folder, opened)
                return response["outcome"], f"WROTE {path.name}: outcome {record['outcome']}"
            except t.sweep.Refused as exc:
                return response["outcome"], f"Refused: {exc}"
    finally:
        fake.close()


def main():
    with tempfile.TemporaryDirectory() as tmp:
        t = fresh(tmp, mutant=False)
        print("RESULT original, corrupted frame, open METHOD:", scenario(t, True))
        t = fresh(tmp, mutant=True)
        saved, text = scenario(t, True)
        print("RESULT mutant M13, corrupted frame, open METHOD:", (saved, text))
        masked = text.startswith("Refused") and "does not hash" in text
        saved2, text2 = scenario(t, False)
        print("RESULT mutant M13, intact frames, open METHOD:", (saved2, text2))
        print(("FAIL (defect shown)" if masked and text2.startswith("WROTE") else "PASS")
              + ": the suite's open-number assertion is satisfied by the hash refusal; without the check a "
              "recheck scores a sweep while METHOD-V1 has open numbers")


if __name__ == "__main__":
    main()

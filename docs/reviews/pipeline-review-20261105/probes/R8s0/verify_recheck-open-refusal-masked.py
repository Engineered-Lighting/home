#!/usr/bin/env python3
"""verify_recheck-open-refusal-masked.py: is --recheck's open-number refusal (sweep.recheck's
`if method.open: raise Refused`) really tested, or does the suite's test only pass because the frame
it corrupted first makes recheck refuse on the hash anyway?

How to run:
  python3 -B verify_recheck-open-refusal-masked.py <root of the claude/rung-8-stage0 checkout>

Synthetic data, temp dirs, the suite's own FakeCamera bound to 127.0.0.1 only. Steps:
  1. Makes one saved sweep folder with the checkout's code (sweep_method(), no open numbers).
  2. Loads a mutant copy of sweep.py with the two open-refusal lines of recheck() removed.
  3. Intact folder, METHOD with an open number ('pixel_step'):
       original recheck -> ?   mutant recheck -> ?
  4. The suite's sequence (test_recheck_refuses_a_changed_frame_a_void_or_open_numbers): frame 4
     corrupted first and never restored, then the open-number call -> what does the mutant raise?
Whole-suite survival of the mutant: verify_mutate_den_light.py (OPEN).
"""
import contextlib
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else sys.exit(__doc__)
TOOL = ROOT / "tools/den-light"
sys.path.insert(0, str(TOOL))
import test_den_light as t  # noqa: E402
meter, sweep = t.meter, t.sweep

OLD = ('    """Recompute a saved sweep\'s repeat check and verdict; writes recheck-<n>.json beside response.json."""\n'
       '    if method.open:\n'
       '        raise Refused("METHOD-V1 still has numbers the owner has not confirmed: " + ", ".join(method.open))\n')
NEW = '    """Recompute a saved sweep\'s repeat check and verdict; writes recheck-<n>.json beside response.json."""\n'


def load_mutant(tmp):
    copy = Path(tmp) / "den-light"
    shutil.copytree(TOOL, copy)
    src = (copy / "sweep.py").read_text(encoding="utf-8")
    assert src.count(OLD) == 1
    (copy / "sweep_mut.py").write_text(src.replace(OLD, NEW), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("sweep_mut", copy / "sweep_mut.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def attempt(fn, folder, method):
    try:
        record, path = fn(folder, method)
        return f"WROTE {path.name}: outcome {record['outcome']}"
    except Exception as exc:  # Refused from either module
        return f"{type(exc).__name__}: {exc}"


def main():
    fake = t.FakeCamera()
    try:
        method = t.sweep_method()
        open_method = meter.Method(method.values, ("pixel_step",))
        with tempfile.TemporaryDirectory() as tmp, t.stage0_folder() as (stage0, token):
            mutant = load_mutant(tmp)
            camera = sweep.Camera(fake.url, t.TOKEN)
            _, folder = sweep.start_run(stage0, camera, token, method)
            response = sweep.Sweep(camera, method, folder, say=t.quiet).run()
            print(f"RESULT saved sweep: outcome {response['outcome']}, files {sorted(p.name for p in folder.iterdir())}")
            orig = attempt(sweep.recheck, folder, open_method)
            mut = attempt(mutant.recheck, folder, open_method)
            print(f"RESULT intact folder, open number: original -> {orig}")
            print(f"RESULT intact folder, open number: mutant   -> {mut}")
            for p in folder.glob("recheck-*.json"):
                p.unlink()
            frame = sorted((folder / "frames").iterdir())[3]
            frame.write_bytes(b"\0" + frame.read_bytes()[1:])
            masked = attempt(mutant.recheck, folder, open_method)
            print(f"RESULT suite's sequence (frame 4 corrupted, then open number): mutant -> {masked}")
        shown = (orig.startswith("Refused: METHOD-V1 still has numbers") and mut.startswith("WROTE")
                 and masked.startswith("Refused:") and "does not hash" in masked)
        print(("FAIL (defect shown)" if shown else "PASS")
              + ": the open-number refusal is masked in the suite by the earlier hash refusal")
    finally:
        fake.close()


if __name__ == "__main__":
    main()

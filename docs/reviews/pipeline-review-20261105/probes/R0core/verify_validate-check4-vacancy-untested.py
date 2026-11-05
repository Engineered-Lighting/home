"""verify_validate-check4-vacancy-untested (skeptical verifier, INTENT lens).

Checks whether the reported surviving mutant V_holdvac of tools/den-core/validate.py is a real (non-equivalent)
weakening of check 4 ("holds"), which validate.py's docstring says leaves out only "occupancy off for less than
vacancy_s", and whether a decide bug of the kind the finding names would still be caught elsewhere in the suite.

Mutant (applied in memory only, to the source text; nothing is written to the checkout):
    or occupancy == "off" and (vacant_since is None or t - vacant_since < vacancy_s)):   ->   or occupancy == "off"):

Case (the shape of test_spine.DiffTest.test_a_hold_during_a_vacancy): four bulbs seen at 0 s, occupancy on at 0 s,
off at 100 s, and a person's correction on light.den_rear_left at 150 s (a touch during the vacancy wait). den_lighting
keeps that hold until max(100, 150) + 120 = 270 s, while decide's vacancy OFF falls at 100 + 120 = 220 s.
  unedited: real check 4 should say OK (the hold is checked from 220 s); the mutant says n/a.
  edited:   every decide decision from 220 s requests OFF for the held bulb (a decide that ignores a touch made
            during vacancy). Real check 4 should FAIL; the mutant should not.
Part 2 (optional, needs a writable scratch dir as the 2nd arg): copies tools/den-core and den_lighting there, plants
that decide bug in decide.py (held bulbs OFF under vacant_off) and runs test_spine.DiffTest and test_decide, to see
whether the suite catches the decide bug itself.

How to run (synthetic data only; temp copies only; no network):
    python3 -B verify_validate-check4-vacancy-untested.py /path/to/den-core-checkout [/path/to/scratch-dir]
"""
import copy
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
SCRATCH = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
HERE = ROOT / "tools" / "den-core"
sys.path.insert(0, str(HERE))

import events  # noqa: E402
import runner  # noqa: E402
import validate  # noqa: E402

OLD = 'or occupancy == "off" and (vacant_since is None or t - vacant_since < vacancy_s)):'
NEW = 'or occupancy == "off"):'
LIGHTS = events.LIGHTS
RL = LIGHTS[2]
SCENE = {"on": True, "brightness": 100, "kelvin": 3000}
HELD = {"on": True, "brightness": 80, "kelvin": 2202}
OFF = {"on": False, "brightness": None, "kelvin": None}
DECIDE_OLD = '    requested.update({b: _scene(h["pinned"]) for b, h in held.items()})'
DECIDE_NEW = '    requested.update({b: (OFF if rule == "vacant_off" else _scene(h["pinned"])) for b, h in held.items()})'


def mutant():
    src = (HERE / "validate.py").read_text("utf-8")
    assert src.count(OLD) == 1, "mutation pattern not found exactly once"
    spec = importlib.util.spec_from_loader("validate_mutant", loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(HERE / "validate.py")
    exec(compile(src.replace(OLD, NEW), "validate_mutant.py", "exec"), module.__dict__)
    return module


def part1():
    inputs = [(0.0, "light_observed", {"entity_id": e, **SCENE, "context": "unknown"}, "synthetic") for e in LIGHTS]
    inputs += [(0.0, "occupancy", {"value": "on", "via": "t"}, "synthetic"),
               (100.0, "occupancy", {"value": "off", "via": "t"}, "synthetic"),
               (150.0, "correction", {"correction_id": "c1", "entity_id": RL, "after": HELD, "source": "t"},
                "synthetic")]
    header, log = runner.run(inputs, runner.load_policies(["incumbent", "decide"]))
    holds = [(m["t"], [h["entity_id"] for h in m["data"]["holds"]]) for m in log if m["kind"] == "mode"]
    print("RESULT: incumbent modes (t, held): %s" % holds)
    print("RESULT: decide decisions (t, rear_left request): %s" % [
        (d["t"], d["data"]["requested_scene"].get(RL)) for d in log if d["kind"] == "decision" and d["source"] == "decide"])
    mut = mutant()
    print("RESULT: unedited: real check 4 %s | mutant %s" % (validate.holds_respected(header, log),
                                                              mut.holds_respected(header, log)))
    edited = copy.deepcopy(log)
    for d in edited:
        if d["kind"] == "decision" and d["source"] == "decide" and d["t"] >= 220.0:
            d["data"]["requested_scene"][RL] = dict(OFF)
    real, fake = validate.holds_respected(header, edited), mut.holds_respected(header, edited)
    print("RESULT: edited (held bulb OFF from 220 s): real check 4 %s" % (real,))
    print("RESULT: edited (held bulb OFF from 220 s): mutant check 4 %s" % (fake,))
    if real[0] == "FAIL" and fake[0] != "FAIL":
        print("FAIL: the mutant does not fail a decide that turns a held bulb off during vacancy; the real check does")
    else:
        print("PASS: the mutant and the real check agree here")


def part2():
    copy_root = SCRATCH / "check4-decide-bug"
    shutil.rmtree(copy_root, ignore_errors=True)
    for rel in ("tools/den-core", "ha-config/custom_components/den_lighting", "tools/la-latency",
                "stack/services/perception-lab"):
        shutil.copytree(ROOT / rel, copy_root / rel, ignore=shutil.ignore_patterns("__pycache__"))
    (copy_root / ".git").write_text("gitdir: /nonexistent\n")
    path = copy_root / "tools/den-core/decide.py"
    text = path.read_text("utf-8")
    assert text.count(DECIDE_OLD) == 1
    path.write_text(text.replace(DECIDE_OLD, DECIDE_NEW), "utf-8")
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "test_spine.DiffTest", "test_decide"],
                          cwd=str(copy_root / "tools/den-core"), capture_output=True, text=True, timeout=600,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    lines = proc.stderr.strip().splitlines()
    failed = [line for line in lines if line.startswith(("FAIL:", "ERROR:"))]
    print("RESULT: decide bug (held bulbs OFF under vacant_off) vs test_spine.DiffTest + test_decide: exit=%d; %s; %s" % (
        proc.returncode, lines[-1] if lines else "", failed[:4]))
    shutil.rmtree(copy_root, ignore_errors=True)


part1()
if SCRATCH is not None:
    part2()

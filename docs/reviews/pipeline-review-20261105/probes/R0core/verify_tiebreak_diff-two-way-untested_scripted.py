"""verify_tiebreak_diff-two-way-untested_scripted (skeptical verifier, TIEBREAK lens).

Checks whether the den-core suite's OWN DiffTest harness (test_spine.scripted, a stand-in decide that requests any
scene) can express the case the one-way mutant of run._both stops flagging: decide requests ON with no level while
den_lighting wants a level. Runs that three-input case against the committed _both and the one-way variant, in
memory, without editing the checkout.

How to run (synthetic data only; in memory; no network):
    python3 -B verify_tiebreak_diff-two-way-untested_scripted.py /path/to/den-core-checkout
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-core"))

import events  # noqa: E402
import incumbent  # noqa: E402
import run  # noqa: E402
import runner  # noqa: E402
import test_spine as ts  # noqa: E402

REAL = run._both
LEVEL_LESS = {"on": True, "brightness": None, "kelvin": None}


def spans_with(fn):
    inputs = [ts.seen(0.0, ts.LIGHTS[0], ts.SCENE), ts.occupied(0.0, "on")]
    script = [(0, {ts.LIGHTS[0]: LEVEL_LESS})]  # decide: ON, no level; den_lighting wants SCENE (a level)
    header, log = runner.run(inputs, [incumbent.make_policy(), ts.scripted(script)])
    run._both = fn
    try:
        return ts.spans(run.diff(header, log))
    finally:
        run._both = REAL


def main():
    print("SCENE (what den_lighting wants) = %s" % ts.SCENE)
    committed = spans_with(REAL)
    one_way = spans_with(lambda a, b: events.scene_matches(a, b))
    print("RESULT committed run.diff spans: %s" % committed)
    print("RESULT one-way   run.diff spans: %s" % one_way)
    if committed and not one_way:
        print("PASS (mutant killable with the suite's own scripted harness): the committed rule flags the bulb, "
              "the one-way rule does not")
    else:
        print("FAIL (no difference)")


if __name__ == "__main__":
    main()

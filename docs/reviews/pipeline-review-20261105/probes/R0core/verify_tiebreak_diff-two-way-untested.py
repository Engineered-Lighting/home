"""verify_tiebreak_diff-two-way-untested (skeptical verifier, TIEBREAK lens).

Finding: run._both ('scenes have to match each way') can be cut to one way, events.scene_matches(decide, den),
and the den-core suite stays green.

What this probe checks, in four parts:
  A. Unit level: the committed _both and the one-way variant on (null-level ON, level ON) in both orders.
  B. Reachability with decide-v0: runs run.diff over synthetic logs that the earlier probes did not build, all with
     the bulbs first seen ON with NO level (as HA's recorder gives them), so den_lighting's normal is a null-level
     ON: plant on and plant off, with and without a touch correction, with and without a game, a null-level touch
     correction, several seeds. It counts the comparisons where decide's latest request is a null-level ON and
     den_lighting wants a level (the only case the one-way rule stops flagging), and compares the spans of the
     committed and the one-way rule.
  C. A log in which decide's 'base' request for one bulb is a null-level ON (one edit, as a later decide could
     write): run.diff spans and validate check 3 under the committed and the one-way rule.
  D. The mutant itself: the whole den-core suite (failfast) on a copy with _both cut to one way.

How to run (synthetic data only; temp folders only; no network):
    python3 -B verify_tiebreak_diff-two-way-untested.py /path/to/den-core-checkout [WORK_PARENT_DIR]
WORK_PARENT_DIR (optional) is where the temp copy for part D is made; the checkout itself is never written.
"""
import copy
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
WORK_PARENT = sys.argv[2] if len(sys.argv) > 2 else None
sys.path.insert(0, str(ROOT / "tools" / "den-core"))

import events  # noqa: E402
import run  # noqa: E402
import runner  # noqa: E402
import synthetic  # noqa: E402
import validate  # noqa: E402
from synthetic import T0  # noqa: E402

POLICIES = ["need", "incumbent", "decide"]
REAL_BOTH = run._both
NULL_ON = {"on": True, "brightness": None, "kelvin": None}
LEVEL_ON = {"on": True, "brightness": 128, "kelvin": 2700}


def one_way(a, b):
    return events.scene_matches(a, b)


def part_a():
    print("A: committed _both(null-level ON, level ON) = %s, _both(level ON, null-level ON) = %s"
          % (REAL_BOTH(NULL_ON, LEVEL_ON), REAL_BOTH(LEVEL_ON, NULL_ON)))
    print("A: one-way    (null-level ON, level ON) = %s, one-way(level ON, null-level ON) = %s"
          % (one_way(NULL_ON, LEVEL_ON), one_way(LEVEL_ON, NULL_ON)))


def null_level_inputs(seed, touch, game, null_touch):
    items, _, _ = synthetic.evening(seed=seed, touch=touch, game=game)
    out = []
    for t, kind, data in items:
        if kind == "light_observed":
            data = {**data, "brightness": None, "kelvin": None, "context": "own"}
        if kind == "correction" and null_touch:
            data = {**data, "after": dict(NULL_ON)}
        out.append((t, kind, data))
    return out


def part_b():
    seen = {"calls": 0, "reverse": 0}

    def counting(a, b):
        seen["calls"] += 1
        if events.scene_matches(a, b) and not events.scene_matches(b, a):
            seen["reverse"] += 1
        return REAL_BOTH(a, b)

    total_rev, total_span_diff, logs = 0, 0, 0
    for seed in (20261103, 7, 11):
        for plant in (True, False):
            for touch, null_touch in ((False, False), (True, False), (True, True)):
                for game in (False, True):
                    items = null_level_inputs(seed, touch, game, null_touch)
                    header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(POLICIES),
                                             synthetic=True, plant=plant)
                    seen["calls"], seen["reverse"] = 0, 0
                    run._both = counting
                    try:
                        spans_real = run.diff(header, log)
                    finally:
                        run._both = REAL_BOTH
                    run._both = one_way
                    try:
                        spans_one = run.diff(header, log)
                    finally:
                        run._both = REAL_BOTH
                    null_req = sum(1 for e in log if e["kind"] == "decision" and e["source"] == "decide"
                                   for s in e["data"]["requested_scene"].values()
                                   if s["on"] and s["brightness"] is None)
                    logs += 1
                    total_rev += seen["reverse"]
                    total_span_diff += spans_real != spans_one
                    print("B: seed=%-8s plant=%-5s touch=%-5s null_touch=%-5s game=%-5s compares=%4d "
                          "decide-null-level-ON bulb requests=%2d decide-null/den-level compares=%d spans equal=%s"
                          % (seed, plant, touch, null_touch, game, seen["calls"], null_req, seen["reverse"],
                             spans_real == spans_one))
    print("RESULT B: %d logs; decide-null/den-level comparisons=%d; logs where one-way spans differ=%d"
          % (logs, total_rev, total_span_diff))
    return total_rev, total_span_diff


def part_c():
    items, _, _ = synthetic.evening(touch=True)
    header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(POLICIES), synthetic=True)
    edited = copy.deepcopy(log)
    target = None
    for e in edited:
        if e["kind"] == "decision" and e["source"] == "decide" and e["data"]["why"].startswith("base"):
            bulb = "light.den_rear_left"
            if bulb in e["data"]["requested_scene"] and e["data"]["requested_scene"][bulb]["on"]:
                e["data"]["requested_scene"][bulb] = dict(NULL_ON)
                target = (e["t"] - T0, bulb)
                break
    if target is None:
        print("RESULT C: no base decision to edit; skipped")
        return None
    print("C: edited decide's base decision at %+.1f s: %s requests a null-level ON" % target)
    out = {}
    for name, fn in (("committed", REAL_BOTH), ("one-way", one_way)):
        run._both = fn
        try:
            spans = [(round(s["start"] - T0, 1), round(s["end"] - T0, 1)) for s in run.diff(header, edited)
                     if s["entity_id"] == target[1]]
            check3 = validate.only_the_look(header, edited)
        finally:
            run._both = REAL_BOTH
        out[name] = (spans, check3)
        print("RESULT C %-9s rear-left diff spans=%s check3=%s" % (name, spans, str(check3)[:170]))
    return out


def part_d():
    parent = WORK_PARENT or None
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="rd_both_", dir=parent)
    try:
        work = Path(tmp) / "copy"
        shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"),
                        symlinks=True)
        (work / ".git").write_text("gitdir: /nonexistent\n")
        target = work / "tools" / "den-core" / "run.py"
        text = target.read_text("utf-8")
        old = "    return events.scene_matches(a, b) and events.scene_matches(b, a)"
        assert text.count(old) == 1, "pattern not found once"
        results = {}
        for name, body in (("baseline", text), ("RD_both", text.replace(old, "    return events.scene_matches(a, b)"))):
            target.write_text(body, "utf-8")
            p = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "tools/den-core",
                                "-p", "test_*.py"], cwd=str(work), capture_output=True, text=True, timeout=900,
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            lines = p.stderr.strip().splitlines()
            ran = next((ln for ln in lines if ln.startswith("Ran ")), "")
            first = next((ln for ln in lines if ln.startswith(("FAIL:", "ERROR:"))), "")
            results[name] = (p.returncode, ran, lines[-1] if lines else "", first)
            print("RESULT D %-8s exit=%d %s | %s %s" % (name, p.returncode, ran, results[name][2], first))
        return results
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    part_a()
    rev, span_diff = part_b()
    c = part_c()
    d = part_d()
    mutant_survives = d["baseline"][0] == 0 and d["RD_both"][0] == 0
    distinguishable = bool(c) and c["committed"][0] != c["one-way"][0]
    print("RESULT: mutant survives the suite=%s; distinguishable on an edited log=%s; reachable with decide-v0 in "
          "part B=%s" % (mutant_survives, distinguishable, rev > 0 or span_diff > 0))
    if mutant_survives and distinguishable:
        print("PASS (finding reproduced as a test gap): RD_both survives and is not equivalent at run.diff's input")
    else:
        print("FAIL (finding not reproduced)")


if __name__ == "__main__":
    main()

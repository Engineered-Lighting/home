"""Tests for validate.py. Offline, standard library; logs are written only to temp folders.

    python -B -m unittest discover -s tools/den-core -p "test_validate.py"

Evenings are built as test_spine.py builds them, through need, incumbent and decide. Tests that need
den_lighting's policy.py skip, with that reason, only when it is not found above this folder.
"""
import contextlib
import copy
import io
import json
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import events  # noqa: E402
import incumbent  # noqa: E402
import runner  # noqa: E402
import synthetic  # noqa: E402
import validate  # noqa: E402
from synthetic import T0  # noqa: E402

HAVE_POLICY = incumbent.find_policy() is not None
NO_POLICY = "den_lighting policy.py is not found above tools/den-core"
POLICIES = ["need", "incumbent", "decide"]
RL = "light.den_rear_left"
_EVENINGS = {}


def evening(clean=True, policies=POLICIES, **options):
    """(header, log, truth) for one synthetic evening, built once and handed out as copies."""
    key = (clean, tuple(policies), tuple(sorted(options.items())))
    if key not in _EVENINGS:
        rates = {"unclear_rate": 0, "invalid_rate": 0, "fail_rate": 0} if clean else {}
        items, truth, _ = synthetic.evening(**rates, **options)
        header, log = runner.run(runner.tagged(items, "synthetic"), runner.load_policies(policies), synthetic=True,
                                 sources=[{"name": "synthetic:evening", "params": options}])
        _EVENINGS[key] = header, log, truth
    return copy.deepcopy(_EVENINGS[key])


def statuses(lines):
    """{check name: 'OK' | 'FAIL' | 'n/a'} from validate's check lines."""
    out = {}
    for line in lines:
        name, _, rest = line.partition(": ")
        if name in validate.NAMES:
            out[name] = rest.split(" ", 1)[0].rstrip(":")
    return out


def of(log, kind, source):
    return [e for e in log if e["kind"] == kind and e["source"] == source]


def insert(log, *chain):
    """log with chain placed after every event at or before the chain's first t."""
    n = max((i for i, e in enumerate(log) if e["t"] <= chain[0]["t"]), default=-1) + 1
    return log[:n] + list(chain) + log[n:]


def decide_copy(log, t, **scenes):
    """A copy of decide's latest decision at or before t, moved to t, with some bulbs' requested scenes replaced."""
    event = copy.deepcopy([d for d in of(log, "decision", "decide") if d["t"] <= t][-1])
    event["t"] = event["data"]["ts"] = t
    event["data"]["requested_scene"].update(scenes)
    return event


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, header, log, name="log.jsonl"):
        events.write(self.tmp / name, header, log)
        return self.tmp / name


@unittest.skipUnless(HAVE_POLICY, NO_POLICY)
class Evenings(Temp):
    EXPECTED = {
        "plain": ({}, "n/a"),
        "touch": ({"touch": True}, "OK"),
        "game": ({"game": True}, "n/a"),
        "touch and game": ({"touch": True, "game": True}, "OK"),
    }

    def test_every_check_passes(self):
        for name, (options, holds) in self.EXPECTED.items():
            with self.subTest(evening=name):
                header, log, _ = evening(**options)
                lines, passed = validate.report(self.write(header, log, name.replace(" ", "-") + ".jsonl"))
                self.assertTrue(passed, "\n".join(lines))
                self.assertEqual(statuses(lines), {"replay": "OK", "need parity": "OK", "only the look": "OK",
                                                   "holds": holds, "quiet": "OK"})
                self.assertIn("all of them while need is reading", lines[2])
                self.assertTrue(lines[5].startswith("overall: OK: no check failed"))
                self.assertEqual(lines[-1], "synthetic: plumbing only, never a gate")

    def test_the_look_intervals_and_the_game_are_seen(self):
        header, log, _ = evening(touch=True, game=True)
        self.assertRegex(validate.only_the_look(header, log)[1], r"^[1-9]\d* interval\(s\)")
        self.assertEqual(validate.need_parity(header, log, validate.load_den_lighting())[1][:4], "2 on")
        self.assertNotIn(" 0.0 s of game", validate.quiet(header, log)[1])
        self.assertEqual(validate.holds_respected(header, log), ("OK", "1 hold(s) pinned and skipped by decide "
                                                                       "whenever it was deciding"))


@unittest.skipUnless(HAVE_POLICY, NO_POLICY)
class Mutations(Temp):
    def test_one_edited_line_fails_the_replay(self):
        header, log, _ = evening(touch=True)
        path = self.write(header, log)
        lines = path.read_text("ascii").split("\n")
        n = next(i for i, line in enumerate(lines) if '"why":"reading_look: chose' in line)
        lines[n] = lines[n].replace("reading_look: chose", "reading_look: chosE")
        path.write_text("\n".join(lines), "ascii", newline="\n")
        status, text = validate.replay(path, log[0]["t"])
        self.assertEqual(status, "FAIL")
        self.assertRegex(text, r"^line %d differs: the logged decision from decide at 2026-11-04T" % (n + 1))
        self.assertFalse(validate.report(path)[1])

    def test_a_decide_difference_outside_reading_fails_check_3(self):
        header, log, _ = evening()
        self.assertEqual(validate.only_the_look(header, log)[0], "OK")
        odd = {"on": True, "brightness": 120, "kelvin": 3000}
        status, text = validate.only_the_look(header, insert(log, decide_copy(log, T0 + 100, **{RL: odd})))
        self.assertEqual(status, "FAIL")
        self.assertIn("light.den_rear_left differs from den_lighting from 2026-11-04T04:01:40.0Z", text)
        self.assertIn("not the look", text)

    def test_a_look_decision_with_another_scene_on_the_bulb_fails_check_3(self):
        header, log, _ = evening()
        first = of(log, "decision", "decide")
        n = next(i for i, d in enumerate(first) if validate.chosen(d["data"]).startswith("look:"))
        first[n]["data"]["requested_scene"][RL] = {"on": True, "brightness": 200, "kelvin": 2700}
        status, text = validate.only_the_look(header, log)
        self.assertEqual(status, "FAIL")
        self.assertIn("chose look:reading@0 but requests 200@2700K for the bulb", text)

    def test_a_moved_held_bulb_fails_check_4(self):
        header, log, _ = evening(touch=True)
        held = [d for d in of(log, "decision", "decide") if RL in {s["channel"] for s in d["data"]["skipped"]}]
        self.assertTrue(held)
        for d in held:
            d["data"]["requested_scene"][RL]["brightness"] = 120
        status, text = validate.holds_respected(header, log)
        self.assertEqual(status, "FAIL")
        self.assertIn("light.den_rear_left held at 80@2202K since 2026-11-04T04:12:00.0Z", text)
        self.assertIn("requests 120@2202K", text)

    def test_a_held_bulb_missing_from_skipped_fails_check_4(self):
        header, log, _ = evening(touch=True)
        for d in of(log, "decision", "decide"):
            d["data"]["skipped"] = [s for s in d["data"]["skipped"] if s["channel"] != RL]
        status, text = validate.holds_respected(header, log)
        self.assertEqual(status, "FAIL")
        self.assertIn("does not list it in skipped", text)

    def test_a_shifted_engaged_moment_fails_check_2(self):
        header, log, _ = evening()
        den = validate.load_den_lighting()
        self.assertEqual(validate.need_parity(header, log, den)[0], "OK")
        engaged = next(e for e in of(log, "person_state", "need")
                       if e["data"]["focused_engagement"]["state"] == "engaged")
        on = engaged["t"]
        engaged["t"] += 4.0
        status, text = validate.need_parity(header, log, den)
        self.assertEqual(status, "FAIL")
        self.assertEqual(text, "move 1: den_lighting on at %s, need into engaged at %s" % (
            validate.at(on, log[0]["t"]), validate.at(on + 4.0, log[0]["t"])))

    def test_a_decide_decision_in_a_game_or_unknown_occupancy_fails_check_5(self):
        header, log, _ = evening(game=True)
        gaming = [m["t"] for m in of(log, "mode", "incumbent") if m["data"]["gaming"]]
        status, text = validate.quiet(header, insert(log, decide_copy(log, gaming[0] + 30)))
        self.assertEqual(status, "FAIL")
        self.assertIn("during a game (mode.gaming since %s)" % validate.at(gaming[0], log[0]["t"]), text)
        early = decide_copy(log, of(log, "decision", "decide")[0]["t"])
        early["t"] = log[0]["t"]
        status, text = validate.quiet(header, [early] + log)
        self.assertEqual(status, "FAIL")
        self.assertIn("with occupancy unknown since", text)

    def test_a_decision_planned_before_decide_saw_unknown_occupancy_is_allowed(self):
        header, log, _ = evening()
        t = T0 + 101.0  # between ticks, in the first rest block
        root = {**log[0], "t": t, "kind": "occupancy", "source": "synthetic", "data": {"value": "unknown", "via": "x"}}
        tick = {**log[0], "t": t, "kind": "tick", "source": "runner", "data": {}}
        back = {**root, "t": t + 0.5, "data": {"value": "on", "via": "x"}}
        self.assertEqual(validate.quiet(header, insert(log, root, decide_copy(log, t), back)), ("OK", (
            "no decide decision in 0.0 s of game or 0.5 s of unknown occupancy")))
        status, text = validate.quiet(header, insert(log, root, tick, decide_copy(log, t), back))
        self.assertEqual(status, "FAIL")
        self.assertIn("with occupancy unknown since %s" % validate.at(t, log[0]["t"]), text)


@unittest.skipUnless(HAVE_POLICY, NO_POLICY)
class RealDataShape(Temp):
    """Facts only, as copied Jetson fact logs replay (run.py --facts), with and without --assume-occupied."""

    def facts_log(self, assume_occupied):
        items, _, _ = synthetic.evening()
        facts = [item for item in items if item[1] == "facts"]
        return runner.run(runner.tagged(facts, "fact_log"), runner.load_policies(POLICIES),
                          assume_occupied=assume_occupied)

    def test_assumed_occupancy(self):
        lines, passed = validate.report(self.write(*self.facts_log(True)))
        self.assertTrue(passed, "\n".join(lines))
        self.assertEqual(statuses(lines), {"replay": "OK", "need parity": "OK", "only the look": "OK",
                                           "holds": "n/a", "quiet": "OK"})
        # den_lighting never saw the bulbs, so decide's base requests nothing and its look stays requested.
        self.assertRegex(lines[2], r"; up to \d+\.\d s of one \(light\.\w+ from .*\) while need is not reading\)$")
        self.assertNotIn("synthetic", "\n".join(lines))

    def test_no_occupancy_keeps_decide_quiet(self):
        lines, passed = validate.report(self.write(*self.facts_log(False)))
        self.assertTrue(passed, "\n".join(lines))
        self.assertEqual(lines[2], "only the look: n/a: decide made no decision in this log")
        self.assertRegex(lines[4], r"^quiet: OK \(no decide decision in 0\.0 s of game or [1-9]\d*\.\d s of unknown")


@unittest.skipUnless(HAVE_POLICY, NO_POLICY)
class Cli(Temp):
    def test_run_py_style_evening_with_truth_offline(self):
        header, log, truth = evening(clean=False, touch=True, game=True)
        path = self.write(header, log)
        (self.tmp / "truth.json").write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n", "ascii")
        out = io.StringIO()
        forbidden = mock.Mock(side_effect=AssertionError("network used"))
        with mock.patch.object(socket, "socket", forbidden), contextlib.redirect_stdout(out):
            code = validate.main([str(path), "--truth", str(self.tmp / "truth.json")])
        lines = out.getvalue().splitlines()
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(set(statuses(lines).values()), {"OK"})
        self.assertEqual(lines[6], "truth: " + validate.TRUTH_LABEL)
        self.assertRegex(lines[7], r"^  b2 \(cue .*\): engaged \d+\.\d s after the cue; "
                                   r"none \d+\.\d s after the next cue$")
        self.assertRegex(lines[8], r"^  b4 \(cue ")
        self.assertEqual(lines[9:], ["synthetic: plumbing only, never a gate"])
        text = out.getvalue()
        self.assertEqual(text.count("lighting-quality gate"), text.count("not a lighting-quality gate"))

    def test_a_failure_exits_1(self):
        header, log, _ = evening()
        path = self.write(header, log)
        path.write_bytes(path.read_bytes().replace(b'"why":"base: chose', b'"why":"base: chosE', 1))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(validate.main([str(path)]), 1)
        self.assertIn("replay: FAIL: line ", out.getvalue())
        self.assertIn("overall: FAIL: 1 of 5 checks failed", out.getvalue())

    def test_a_log_without_need_or_decide(self):
        header, log, _ = evening(policies=["incumbent"])
        lines, passed = validate.report(self.write(header, log))
        self.assertTrue(passed)
        self.assertEqual(statuses(lines), {"replay": "OK", "need parity": "n/a", "only the look": "n/a",
                                           "holds": "n/a", "quiet": "n/a"})


class Unreadable(Temp):
    def test_unreadable_log_fails(self):
        path = self.tmp / "log.jsonl"
        path.write_text("not a log\n", "ascii")
        lines, passed = validate.report(path, self.tmp / "missing.json")
        self.assertFalse(passed)
        self.assertEqual(statuses(lines), {"replay": "FAIL", "need parity": "n/a", "only the look": "n/a",
                                           "holds": "n/a", "quiet": "n/a"})
        self.assertIn("  n/a: unreadable log", lines)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(validate.main([str(self.tmp / "absent.jsonl")]), 1)


if __name__ == "__main__":
    unittest.main()

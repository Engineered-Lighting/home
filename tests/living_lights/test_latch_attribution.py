"""Who wrote the asleep latch.

The definition of done for story S says that while the estimator is live every
``input_boolean.living_lights_asleep`` transition is written by the mirror
automation, by hand, or by the ungated hard backstop, and that none is written
on presence reconnect, at 09:00, or by the tick. That is a statement about
attribution, so the attribution has to be evidence rather than inference: the
helper each automation writes in the same action block as the flip.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


nj = _load("night_postmortem_join", REPO / "tools" / "night-postmortem-join.py")

T0 = dt.datetime(2026, 9, 18, 1, 0, tzinfo=dt.timezone.utc)


def at(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


def rows(*pairs) -> list[dict]:
    return [{"t": at(s), "state": state} for s, state in pairs]


class WriterFamilyTests(unittest.TestCase):
    def test_the_family_is_the_part_before_the_colon(self):
        self.assertEqual(nj.writer_family("legacy_off:presence"), "legacy_off")
        self.assertEqual(nj.writer_family("mirror:likely_asleep"), "mirror")
        self.assertEqual(nj.writer_family("hard_backstop"), "hard_backstop")
        self.assertEqual(nj.writer_family("MANUAL"), "manual")

    def test_an_empty_or_unknown_helper_names_nobody(self):
        for value in ("", "   ", None, "unknown", "unavailable"):
            with self.subTest(value=value):
                self.assertIsNone(nj.writer_family(value))


class LatchTransitionTests(unittest.TestCase):
    def test_each_flip_takes_the_writer_written_just_after_it(self):
        latch = rows((0, "off"), (100, "on"), (500, "off"))
        writer = rows((101, "legacy_on"), (502, "legacy_off:occupancy"))
        found = nj.latch_transitions(latch, writer, [])
        self.assertEqual([t["to"] for t in found], ["on", "off"])
        self.assertEqual([t["writer"] for t in found], ["legacy_on", "legacy_off:occupancy"])
        self.assertEqual([t["writer_family"] for t in found], ["legacy_on", "legacy_off"])

    def test_a_writer_written_before_the_flip_does_not_claim_it(self):
        """The previous transition's writer must not be read as this one's.

        A window that looks backwards would make the attribution lie, which is
        worse than saying nothing.
        """
        latch = rows((0, "off"), (100, "on"))
        writer = rows((90, "hard_backstop"))
        found = nj.latch_transitions(latch, writer, [])
        self.assertEqual(found[0]["writer"], "unattributed")

    def test_a_writer_written_long_after_the_flip_does_not_claim_it(self):
        latch = rows((0, "off"), (100, "on"))
        writer = rows((100 + nj.WRITER_WINDOW_S + 1, "legacy_on"))
        found = nj.latch_transitions(latch, writer, [])
        self.assertEqual(found[0]["writer_family"], "unattributed")

    def test_a_repeat_of_the_same_state_is_not_a_transition(self):
        latch = rows((0, "off"), (100, "off"), (200, "on"), (300, "on"))
        self.assertEqual(len(nj.latch_transitions(latch, [], [])), 1)

    def test_an_unavailable_stretch_does_not_invent_a_transition(self):
        """Coming back from unavailable to the state it already held is not a
        flip, and must not be counted as one."""
        latch = rows((0, "on"), (100, "unavailable"), (200, "on"))
        self.assertEqual(nj.latch_transitions(latch, [], []), [])

    def test_the_restarts_it_skipped_are_counted_not_hidden(self):
        summary = nj.latch_summary([], availability_gaps=3)
        self.assertEqual(summary["availability_gaps"], 3)

    def test_each_transition_records_whether_the_estimator_was_live(self):
        latch = rows((0, "off"), (100, "on"), (500, "off"))
        live = rows((-10, "off"), (200, "on"))
        found = nj.latch_transitions(latch, [], live)
        self.assertEqual([t["estimator_live"] for t in found], [False, True])


class LatchSummaryTests(unittest.TestCase):
    def test_a_legacy_writer_while_live_is_the_violation_the_plan_names(self):
        transitions = [
            {"at": "t1", "to": "on", "writer": "mirror:likely_asleep",
             "writer_family": "mirror", "estimator_live": True},
            {"at": "t2", "to": "off", "writer": "legacy_off:presence",
             "writer_family": "legacy_off", "estimator_live": True},
            {"at": "t3", "to": "off", "writer": "legacy_off:midday",
             "writer_family": "legacy_off", "estimator_live": False},
        ]
        summary = nj.latch_summary(transitions)
        self.assertEqual(summary["transitions"], 3)
        self.assertEqual(summary["latches"], 1)
        self.assertEqual(summary["clears"], 2)
        self.assertEqual(summary["while_estimator_live"], 2)
        self.assertEqual(summary["forbidden_while_live"], 1,
                         "the legacy clear that fired while the estimator was live")
        self.assertEqual(summary["forbidden_detail"][0]["writer"], "legacy_off:presence")
        self.assertEqual(summary["written_by_the_publisher"], 1)

    def test_the_three_allowed_writers_are_not_violations(self):
        transitions = [
            {"at": "t", "to": "on", "writer": f"{family}:x" if family == "mirror" else family,
             "writer_family": family, "estimator_live": True}
            for family in nj.ALLOWED_WHILE_LIVE
        ]
        summary = nj.latch_summary(transitions)
        self.assertEqual(summary["while_estimator_live"], 3)
        self.assertEqual(summary["forbidden_while_live"], 0)

    def test_clears_are_broken_down_by_the_trigger_that_wrote_them(self):
        transitions = [
            {"at": "t", "to": "off", "writer": "legacy_off:presence",
             "writer_family": "legacy_off", "estimator_live": False},
            {"at": "u", "to": "off", "writer": "legacy_off:presence",
             "writer_family": "legacy_off", "estimator_live": False},
            {"at": "v", "to": "off", "writer": "legacy_off:arrival",
             "writer_family": "legacy_off", "estimator_live": False},
        ]
        summary = nj.latch_summary(transitions)
        self.assertEqual(summary["clears_by_writer"],
                         {"legacy_off:presence": 2, "legacy_off:arrival": 1})


if __name__ == "__main__":
    unittest.main()

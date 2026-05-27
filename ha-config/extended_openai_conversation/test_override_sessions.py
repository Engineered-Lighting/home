"""Offline tests for _override_sessions.collapse_into_sessions + the M22
intent classifier. Pure stdlib, no HA imports — runnable on the dev
workstation. Mirrors the unit-test pattern used by the other
extended_openai_conversation test_*.py files.

Run:  python -m unittest -v ha-config/extended_openai_conversation/test_override_sessions.py
"""
from __future__ import annotations

import importlib.util
import os
import unittest


# Load the module without going through the HA package import path so
# this test file works standalone on the dev box (HA package would try
# to pull in homeassistant.* which isn't on the local venv).
_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "_override_sessions",
    os.path.join(_HERE, "_override_sessions.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
collapse_into_sessions = _mod.collapse_into_sessions
_classify_session_intent = _mod._classify_session_intent
_split_user_path_and_tail = _mod._split_user_path_and_tail


def _ev(light: str, ts: str, actual_pct: int, **kw) -> dict:
    """Build a synthetic override_event."""
    base = {
        "kind": "override_event",
        "context_id": f"ovr-{ts.replace(':','-').replace('.','-')}-{actual_pct}",
        "ts": ts,
        "light_entity": light,
        "zone": light.replace("light.", ""),
        "actual_pct": actual_pct,
        "predicted_pct": 20,
        "delta_pct": actual_pct - 20,
        "light_state": "off" if actual_pct == 0 else "on",
    }
    base.update(kw)
    return base


class TestSplitUserPathAndTail(unittest.TestCase):
    """The lower-level path splitter — independent of the classifier."""

    def test_empty(self):
        self.assertEqual(_split_user_path_and_tail([]), ([], []))

    def test_all_off(self):
        # Whole path is off → user_path is the whole thing, no tail
        self.assertEqual(_split_user_path_and_tail([0, 0, 0]), ([0, 0, 0], []))

    def test_no_tail(self):
        self.assertEqual(_split_user_path_and_tail([60]), ([60], []))
        self.assertEqual(_split_user_path_and_tail([35, 58]), ([35, 58], []))

    def test_split_simple(self):
        self.assertEqual(
            _split_user_path_and_tail([60, 0, 0]),
            ([60], [0, 0]),
        )

    def test_split_burst(self):
        path = [35, 58, 58, 58, 58, 58, 58, 58, 58, 58, 0, 0]
        self.assertEqual(
            _split_user_path_and_tail(path),
            ([35, 58, 58, 58, 58, 58, 58, 58, 58, 58], [0, 0]),
        )

    def test_split_user_path_ends_with_intermediate_zero(self):
        # Path: [50, 0, 60, 0, 0]. last_nonzero is the 60 at idx=2,
        # so user_path is [50, 0, 60], tail is [0, 0].
        self.assertEqual(
            _split_user_path_and_tail([50, 0, 60, 0, 0]),
            ([50, 0, 60], [0, 0]),
        )

    def test_none_values_treated_as_off(self):
        self.assertEqual(
            _split_user_path_and_tail([60, None, None]),
            ([60], [None, None]),
        )


class TestClassifyIntent(unittest.TestCase):
    """The intent classifier on its own (offline, no events list)."""

    # ── Required cases from M22 spec ──────────────────────────────────

    def test_pure_slider_burst_ending_at_60(self):
        """Required case (a): pure burst ending at 60 → learning=60,
        automation_tail_detected=false."""
        path = [35, 50, 55, 60, 60]
        r = _classify_session_intent(path, final_actual_pct=60)
        self.assertEqual(r["learning_value_pct"], 60)
        self.assertFalse(r["automation_tail_detected"])
        self.assertEqual(r["session_final_actual_pct"], 60)
        self.assertEqual(r["session_user_landed_pct"], 60)
        self.assertEqual(r["session_tail_path"], [])
        self.assertEqual(r["learning_value_source"], "final_actual")
        self.assertEqual(r["intent_confidence"], "high")

    def test_burst_ending_58_then_automation_off(self):
        """Required case (b): burst ending 58 + automation [0,0] tail.
        final=0, landed=58, learning=58, tail=true."""
        path = [35, 58, 58, 58, 58, 58, 58, 58, 58, 58, 0, 0]
        r = _classify_session_intent(path, final_actual_pct=0)
        self.assertEqual(r["session_final_actual_pct"], 0)
        self.assertEqual(r["session_user_landed_pct"], 58)
        self.assertEqual(r["learning_value_pct"], 58)
        self.assertTrue(r["automation_tail_detected"])
        self.assertEqual(r["session_tail_path"], [0, 0])
        self.assertEqual(r["learning_value_source"], "user_landed")
        # 9 stable 58s before the tail → high confidence
        self.assertEqual(r["intent_confidence"], "high")

    def test_intentional_off_only_session(self):
        """Required case (c): off-only session → learning=0, tail=false."""
        path = [0, 0]
        r = _classify_session_intent(path, final_actual_pct=0)
        self.assertEqual(r["learning_value_pct"], 0)
        self.assertFalse(r["automation_tail_detected"])
        self.assertEqual(r["session_final_actual_pct"], 0)
        self.assertEqual(r["session_user_landed_pct"], 0)
        self.assertEqual(r["learning_value_source"], "off_session")
        self.assertEqual(r["intent_confidence"], "high")

    # ── Edge cases ─────────────────────────────────────────────────────

    def test_single_event_off(self):
        r = _classify_session_intent([0], final_actual_pct=0)
        self.assertEqual(r["learning_value_pct"], 0)
        self.assertEqual(r["learning_value_source"], "off_session")
        self.assertFalse(r["automation_tail_detected"])

    def test_single_event_nonzero(self):
        r = _classify_session_intent([60], final_actual_pct=60)
        self.assertEqual(r["learning_value_pct"], 60)
        self.assertEqual(r["learning_value_source"], "final_actual")
        self.assertFalse(r["automation_tail_detected"])
        self.assertEqual(r["intent_confidence"], "high")

    def test_low_confidence_single_user_event_then_tail(self):
        """Path [60, 0, 0]: user had only 1 event before the tail.
        Could be a glitch — confidence is low."""
        r = _classify_session_intent([60, 0, 0], final_actual_pct=0)
        self.assertEqual(r["learning_value_pct"], 60)
        self.assertTrue(r["automation_tail_detected"])
        self.assertEqual(r["intent_confidence"], "low")

    def test_medium_confidence_scrubbing_then_tail(self):
        """Path [50, 60, 0, 0]: user_path = [50, 60]. Two values that
        DIFFER → user was still scrubbing when automation cut in.
        Confidence: medium. user_landed = the last value = 60."""
        r = _classify_session_intent([50, 60, 0, 0], final_actual_pct=0)
        self.assertEqual(r["session_user_landed_pct"], 60)
        self.assertEqual(r["learning_value_pct"], 60)
        self.assertTrue(r["automation_tail_detected"])
        self.assertEqual(r["intent_confidence"], "medium")

    def test_high_confidence_stable_pair_then_tail(self):
        """Path [60, 60, 0]: two stable 60s before tail → high."""
        r = _classify_session_intent([60, 60, 0], final_actual_pct=0)
        self.assertEqual(r["session_user_landed_pct"], 60)
        self.assertEqual(r["intent_confidence"], "high")
        self.assertTrue(r["automation_tail_detected"])

    def test_empty_path(self):
        r = _classify_session_intent([], final_actual_pct=0)
        self.assertEqual(r["learning_value_pct"], 0)
        self.assertEqual(r["learning_value_source"], "off_session")
        self.assertFalse(r["automation_tail_detected"])

    def test_final_actual_pct_none(self):
        """Defensive: None final shouldn't crash. When final_actual_pct
        is missing and the path is non-empty, fall back to the last
        path value for both final and learning (no tail = high
        confidence the path is the truth)."""
        r = _classify_session_intent([60, 60], final_actual_pct=None)
        self.assertEqual(r["session_final_actual_pct"], 60)
        self.assertEqual(r["session_user_landed_pct"], 60)
        self.assertEqual(r["learning_value_pct"], 60)
        self.assertEqual(r["learning_value_source"], "final_actual")
        self.assertFalse(r["automation_tail_detected"])


class TestCollapseEndToEnd(unittest.TestCase):
    """collapse_into_sessions + classifier together — mirroring how the
    REST endpoint sees it."""

    def test_burst_with_automation_tail_ends_at_58(self):
        # Reconstruct the M20-live scenario: 10 burst events landing at
        # 58, plus 2 automation turn-off events ([0, 0]). Burst events
        # span 0.9s; automation events arrive ~3s after the LAST burst
        # event — all comfortably inside session_window_s=10.
        ts0_sec = "2026-05-26T21:52:05"
        events = []
        burst_values = [35, 58, 58, 58, 58, 58, 58, 58, 58, 58]
        # i=0..9 with i*100ms steps → microseconds 100000..900100
        for i, pct in enumerate(burst_values):
            us = i * 100000 + 100000  # 100000, 200000, ..., 1000000-1
            if us >= 1000000:
                us = 999999
            events.append(_ev(
                "light.office",
                f"{ts0_sec}.{us:06d}-07:00",
                pct,
            ))
        # Automation turn-off, ~3 s after the last burst event
        events.append(_ev(
            "light.office",
            "2026-05-26T21:52:08.000000-07:00",
            0,
        ))
        events.append(_ev(
            "light.office",
            "2026-05-26T21:52:08.200000-07:00",
            0,
        ))
        sessions = collapse_into_sessions(events, session_window_s=10.0)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["session_event_count"], 12)
        self.assertEqual(
            s["session_observed_path"],
            [35, 58, 58, 58, 58, 58, 58, 58, 58, 58, 0, 0],
        )
        # M20 legacy view: final value wins → actual_pct=0
        self.assertEqual(s["actual_pct"], 0)
        # M22 new view: user-landed is 58, learning value is 58
        self.assertEqual(s["session_final_actual_pct"], 0)
        self.assertEqual(s["session_user_landed_pct"], 58)
        self.assertEqual(s["learning_value_pct"], 58)
        self.assertTrue(s["automation_tail_detected"])
        self.assertEqual(s["session_tail_path"], [0, 0])
        self.assertEqual(s["learning_value_source"], "user_landed")
        self.assertEqual(s["intent_confidence"], "high")

    def test_pure_burst_no_tail(self):
        ts0 = "2026-05-26T22:00:00"
        events = [
            _ev("light.office", f"{ts0}.000100-07:00", 35),
            _ev("light.office", f"{ts0}.200100-07:00", 50),
            _ev("light.office", f"{ts0}.500100-07:00", 60),
        ]
        sessions = collapse_into_sessions(events, session_window_s=10.0)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["session_observed_path"], [35, 50, 60])
        self.assertEqual(s["learning_value_pct"], 60)
        self.assertEqual(s["learning_value_source"], "final_actual")
        self.assertFalse(s["automation_tail_detected"])

    def test_intentional_off_session(self):
        ts0 = "2026-05-26T22:05:00"
        events = [
            _ev("light.office", f"{ts0}.000100-07:00", 0),
            _ev("light.office", f"{ts0}.500100-07:00", 0),
        ]
        sessions = collapse_into_sessions(events, session_window_s=10.0)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["learning_value_pct"], 0)
        self.assertEqual(s["learning_value_source"], "off_session")
        self.assertFalse(s["automation_tail_detected"])

    def test_two_independent_sessions_on_different_lights(self):
        ts0 = "2026-05-26T22:10:00"
        events = [
            _ev("light.office", f"{ts0}.000100-07:00", 40),
            _ev("light.office", f"{ts0}.500100-07:00", 60),
            _ev("light.sink", f"{ts0}.700100-07:00", 30),
            _ev("light.sink", f"{ts0}.800100-07:00", 0),
            _ev("light.sink", f"{ts0}.900100-07:00", 0),
        ]
        sessions = collapse_into_sessions(events, session_window_s=10.0)
        self.assertEqual(len(sessions), 2)
        office, sink = sorted(sessions, key=lambda s: s["light_entity"])
        self.assertEqual(office["light_entity"], "light.office")
        self.assertEqual(office["learning_value_pct"], 60)
        self.assertFalse(office["automation_tail_detected"])
        self.assertEqual(sink["light_entity"], "light.sink")
        self.assertEqual(sink["learning_value_pct"], 30)
        self.assertTrue(sink["automation_tail_detected"])
        self.assertEqual(sink["intent_confidence"], "low")  # only 1 user event


if __name__ == "__main__":
    unittest.main(verbosity=2)

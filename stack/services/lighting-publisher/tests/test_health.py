"""Health: mirror fresh and observer fresh and MQTT up, else heartbeat stops and beliefs go unknown."""
import datetime as dt
import unittest

from lighting_publisher import stories
from lighting_publisher.health import UNKNOWN, Health, evaluate

T0 = dt.datetime(2026, 9, 17, 22, 0, tzinfo=dt.timezone.utc)


def s(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


class EvaluateTest(unittest.TestCase):
    def test_all_fresh_is_ok(self):
        st = evaluate(s(10), mirror_at=s(0), observer_at=s(0), mqtt_up=True)
        self.assertTrue(st.ok)
        self.assertTrue(st.heartbeat_allowed)
        self.assertEqual(st.belief_state("ON"), "ON")
        self.assertEqual(st.reasons, ())

    def test_never_seen_is_stale(self):
        st = evaluate(s(0), None, None, True)
        self.assertFalse(st.ok)
        self.assertEqual(st.reasons, ("mirror_never_seen", "observer_never_seen"))

    def test_thresholds_at_the_constants(self):
        self.assertTrue(evaluate(s(stories.MIRROR_FRESH_S - 1), s(0), s(stories.MIRROR_FRESH_S - 5), True).ok)
        st = evaluate(s(stories.MIRROR_FRESH_S), s(0), s(stories.MIRROR_FRESH_S - 5), True)
        self.assertEqual(st.reasons, ("mirror_stale",))
        st = evaluate(s(stories.OBSERVER_FRESH_S), s(0), s(0), True)
        self.assertEqual(st.reasons, ("observer_stale",))
        self.assertFalse(st.heartbeat_allowed)
        self.assertEqual(st.belief_state("likely_asleep"), UNKNOWN)

    def test_mqtt_down(self):
        st = evaluate(s(1), s(0), s(0), False)
        self.assertEqual(st.reasons, ("mqtt_down",))
        self.assertEqual(st.as_dict()["mqtt_up"], False)


class HealthStateTest(unittest.TestCase):
    def test_journal_records_changes_of_ok_only(self):
        h = Health()
        self.assertFalse(h.status(s(0)).ok)
        h.note_mirror(s(1))
        h.note_observer(s(1))
        h.set_mqtt(True)
        self.assertTrue(h.status(s(2)).ok)
        self.assertTrue(h.status(s(3)).ok)
        st = h.status(s(1 + stories.OBSERVER_FRESH_S))
        self.assertFalse(st.ok)
        self.assertEqual(st.reasons, ("observer_stale",))
        self.assertEqual([e["ok"] for e in h.journal], [False, True, False])
        h.note_observer(s(20))
        self.assertTrue(h.status(s(21)).ok)
        self.assertEqual(h.status(s(21)).observer_age_s, 1.0)


if __name__ == "__main__":
    unittest.main()

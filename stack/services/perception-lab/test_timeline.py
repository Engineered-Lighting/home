import unittest
import test_paired_runtime as fixtures
from controller import Controller


class TimelineTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def test_late_bookmarks_and_actual_switches_survive_noisy_event_history(self):
        sid=self.session["id"]
        with self.store.transaction() as db:
            for _ in range(1100): self.store._event(db,sid,"perception.offered",{})
        self.store.append_event(sid,"bookmark",{"at_ms":6000,"label":"missed reach","actor":"private"})
        self.store.append_event(sid,"experiment.activated",{"effective_at":105,"generation":2,"experiment_id":"pose","profiles":{"internal":"not timeline data"}})
        self.store.append_event(sid,"pipeline.switch",{"outcome":{"state":"applied"}})
        self.store.append_event(sid,"pipeline.switch",{"outcome":{"state":"blocked"}})
        detail=Controller(self.store).detail(sid,6000)
        self.assertEqual(len(detail["events"]),1000)
        timeline=detail["timeline_events"]
        self.assertEqual([(e["kind"],e["t_s"]) for e in timeline],[("bookmark",6),("switch",5)])
        self.assertNotIn("actor",timeline[0]["payload"])
        self.assertNotIn("profiles",timeline[1]["payload"])

    def test_legacy_failed_switch_is_not_shown_as_a_change(self):
        sid=self.session["id"]
        self.store.clock=lambda:107
        self.store.append_event(sid,"pipeline.switch",{"outcome":{"state":"blocked"}})
        self.store.append_event(sid,"pipeline.switch",{"outcome":{"state":"applied"}})
        timeline=self.store.timeline_events(sid)
        self.assertEqual(len(timeline),1)
        self.assertEqual(timeline[0]["t_s"],7)


if __name__=='__main__': unittest.main()

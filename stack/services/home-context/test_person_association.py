import unittest
from person_association import AnonymousAssociator


class TrackingTests(unittest.TestCase):
    def tracker(self):
        return AnonymousAssociator({"site_id": "synthetic", "room_id": "room", "session_id": "session", "domain_epoch": "domain"}, "camera:synthetic", "capture:1")

    def row(self, ref, box):
        return {"instance_ref": ref, "pose_instance_ref": ref, "box": box}

    def frame(self, tracker, t, rows, root=None, capture="capture:1"):
        return tracker.observe(observed_at=t, observation_root=root or f"frame:{t}", detections=rows, clock_status="commissioned", capture_epoch=capture)

    def test_distinct_concurrent_tracks_and_frame_dedupe(self):
        tr = self.tracker()
        rows = [self.row("a", [.1, .1, .3, .8]), self.row("b", [.6, .1, .8, .8])]
        a = self.frame(tr, 1, rows)
        b = self.frame(tr, 2, [self.row("new-a", [.11, .1, .31, .8]), self.row("new-b", [.61, .1, .81, .8])])
        self.assertEqual([x["person_ref"] for x in a["tracks"]], [x["person_ref"] for x in b["tracks"]])
        self.assertEqual(self.frame(tr, 3, rows, root="frame:2"), b)

    def test_crossing_abstains_and_resets_epochs(self):
        tr = self.tracker()
        old = self.frame(tr, 1, [self.row("a", [.1, .1, .4, .8]), self.row("b", [.6, .1, .9, .8])])
        conflict = self.frame(tr, 2, [self.row("c", [.3, .1, .6, .8]), self.row("d", [.35, .1, .65, .8])])
        self.assertEqual(conflict["status"], "unresolved")
        new = self.frame(tr, 3, [self.row("a", [.1, .1, .4, .8])])
        self.assertNotEqual(new["tracks"][0]["person_ref"]["track_epoch"], old["tracks"][0]["person_ref"]["track_epoch"])

    def test_gap_and_capture_restart_never_inherit_pid(self):
        tr = self.tracker()
        rows = [self.row("a", [.1, .1, .4, .8])]
        first = self.frame(tr, 1, rows)["tracks"][0]["person_ref"]
        later = self.frame(tr, 5, rows)["tracks"][0]["person_ref"]
        restart = self.frame(tr, 6, rows, capture="capture:2")["tracks"][0]["person_ref"]
        self.assertNotEqual(first["track_epoch"], later["track_epoch"])
        self.assertNotEqual(later["track_epoch"], restart["track_epoch"])

    def test_unverified_clock_and_pose_missing_abstain(self):
        tr = self.tracker()
        self.assertEqual(tr.observe(observed_at=1, observation_root="frame", detections=[], clock_status="unverified", capture_epoch="capture:1")["status"], "unobservable")
        result = self.frame(tr, 1, [{"instance_ref": "a", "pose_instance_ref": "b", "box": [.1, .1, .4, .8]}])
        self.assertEqual(result["status"], "unresolved")

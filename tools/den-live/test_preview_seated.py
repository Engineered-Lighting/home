"""The owner's posture on LA (2026-11-14): their mid-hip sits on the seated band's top edge, 0.75 m. Through the preview,
the seated rule's hysteresis (seated.held_seated) seats them, keeps the reading light on through hips at the band's
edge, and still ends the light when they stand up, within a frame of the evidence."""

import unittest

import test_preview as base
import test_preview_manual as manual_tests


def pose(hip, head):
    """base.joints' seated reader, with the hips and the face at these heights."""
    out = base.joints(True)
    for name in ("left_hip", "right_hip"):
        out[name] = [(out[name][0][0], out[name][0][1], hip), "supported"]
    out["nose"] = [(out["nose"][0][0], out["nose"][0][1], head), "supported"]
    return out


class BandEdgeTest(unittest.TestCase):
    hip, head = 0.78, 1.20
    _helpers = manual_tests.PreviewManualTest
    setUpClass = classmethod(_helpers.setUpClass.__func__)
    setUp, make_preview, fixtures, lit, frames, light = (_helpers.setUp, _helpers.make_preview, _helpers.fixtures,
                                                         _helpers.lit, _helpers.frames, _helpers.light)
    read, frame, seat = base.PreviewTest.read, base.PreviewTest.frame, base.PreviewTest.seat

    def track(self, *, seated=True, epoch=1, standing=(1.0, 1.65), **_ignored):
        self.seq += 1
        joints = pose(self.hip, self.head) if seated else pose(*standing)
        reader = {"track_id": "t1", "state": "confirmed", "position_basis": "stereo", "association_status": "confident",
                  "association_revision": 2, "current_obs_ids": ("pose",), "supported_pose": joints,
                  "seated": self.rule.update("t1", 2, self.now, joints)}
        self.preview.offer_snapshot({"service_run_id": "run", "camera_epoch": epoch, "seq": self.seq,
                                     "frame": {**base.BINDING, "capture_mono": self.now}, "status": "fresh",
                                     "presence": "present", "eligibility_revision": 1,
                                     "eligible_track_id": "t1" if seated else None, "tracks": [reader]})

    def test_the_light_stays_on_through_hips_at_the_bands_edge(self):
        name = self.light()   # seated with the mid-hip over 0.75 m: the copy alone never sat them
        seen = []
        for i in range(24):   # 6 s at the edge (inside the yes's validity), 0.70 and 0.85 in turn, as measured
            self.hip = 0.70 if i % 2 else 0.85
            self.now += .25
            self.track()
            seen.append(self.fixtures()[name]["on"])
        self.assertTrue(all(seen), "no quick exit, no flicker")
        self.assertTrue(self.preview.track["seated"])

    def test_standing_up_is_as_quick_as_ever(self):
        """A body that has stood up (head and hips both up) ends seated on its first frame, and the light goes."""
        name = self.light()
        self.now += .25
        self.track(seated=False)
        self.assertFalse(self.preview.track["seated"])
        self.assertFalse(self.fixtures()[name]["on"])

    def test_one_signal_alone_takes_two_frames(self):
        """Evidence from one signal (a high head over hips still at the band's edge) ends seated only on the second
        frame in a row of it: within a frame of the evidence, and never on a stray one."""
        name = self.light()
        self.now += .25
        self.track(seated=False, standing=(0.85, 1.55))
        self.assertTrue(self.preview.track["seated"])
        self.assertTrue(self.fixtures()[name]["on"])
        self.now += .25
        self.track(seated=False, standing=(0.85, 1.55))
        self.assertFalse(self.preview.track["seated"])
        self.assertFalse(self.fixtures()[name]["on"])


if __name__ == "__main__":
    unittest.main()

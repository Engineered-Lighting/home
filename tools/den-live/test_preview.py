"""Coordinator replay with the real pinned policy and no HA client."""

import http.client
import json
import math
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import ask
import coordinator_live
from contracts import FrameRef, InferenceReply, PlaneRef, TrackEvidence, TrackingSnapshot
import preview
import run_local
import run_service
import seated as seated_rule
import sofa_check
import stream


HERE = Path(__file__).resolve().parent
GOLDEN = json.loads((HERE / "fixtures" / "preview-f3.json").read_text(encoding="utf-8"))
BINDING = GOLDEN["snapshot"]["binding"]
FIXTURES = {"F3": {"aim_origin_m": [1.8, .2, 2.5], "horizontal_world_reference": [0, 1, 0]}}


def joints(seated):
    height = 0 if seated else .5
    xyz = {"left_shoulder": [1, 1, .95], "right_shoulder": [1.4, 1, .95], "left_hip": [1, 1, .45],
           "right_hip": [1.4, 1, .45], "nose": [1.2, 1.1, 1.18], "left_wrist": [1.1, 1.3, .65],
           "right_wrist": [1.3, 1.3, .65]}
    return {key: [(x, y, z + height), "supported"] for key, (x, y, z) in xyz.items()}


class PreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tree.cleanup)
        cls.core, _learn = coordinator_live.load_core(sofa_check.archive(Path(cls.tree.name), "coordinator"))

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = tmp.name
        self.now = 100.0
        self.seq = 0
        self.encoded = []
        self.rule, self.rule_epoch = seated_rule.Published(), 1
        self.preview = self.make_preview()
        self.preview.configure({"fade_in_s": 0, "pre_aim": False})

    def make_preview(self, **overrides):
        return preview.Preview(self.core, binding=BINDING, fixtures=FIXTURES, state=self.path,
                               clock=lambda: self.now, **{"threaded": False, "append": self.encoded.append, **overrides})

    def track(self, *, seated=True, epoch=1, more=False):
        self.seq += 1
        if epoch != self.rule_epoch:   # a new epoch is a new tracker, and with it a new seated
            self.rule, self.rule_epoch = seated_rule.Published(), epoch
        pose = joints(seated)
        track = {"track_id": "t1", "state": "confirmed", "position_basis": "stereo", "association_status": "confident",
                 "association_revision": 2, "current_obs_ids": ("pose",), "supported_pose": pose,
                 "seated": self.rule.update("t1", 2, self.now, pose)}   # perception's one answer
        frame = {**BINDING, "capture_mono": self.now}
        self.preview.offer_snapshot({"service_run_id": "run", "camera_epoch": epoch, "seq": self.seq, "frame": frame,
                                     "status": "fresh", "presence": "present", "eligibility_revision": 1,
                                     "eligible_track_id": "t1" if seated and not more else None,
                                     "tracks": [track, track] if more else [track]})

    def read(self, *, at=None, start=None, kind="reading", frames=None, safe=True, **facts):
        at = self.now if at is None else at
        start = at if start is None else start
        if frames is None:
            frames = (self.frame(at),) if start == at else (self.frame(start), self.frame(at))
        self.preview.offer_inference(InferenceReply(
            str(at), "synthetic", "run", 1, "t1", 2, 1, frames,
            {"status": "valid", "facts": {"people_visible": "one", "activity_hypothesis": kind, **facts}},
            {"safe": safe, "status": "confident", "window_start_mono": start, "window_end_mono": at},
            {}, {}, at, self.now, at + 10, {}))

    def frame(self, capture):
        seq = round(capture * 100)
        return FrameRef("run", 1, seq, "synthetic", "rgb", PlaneRef(seq, capture, capture, 640, 360),
                        None, None, None, BINDING["transform_id"], BINDING["scan_revision"], capture, capture, capture)

    def seat(self):
        for _ in range(5):
            self.now += .5
            self.track()

    def light(self):
        self.seat()
        self.read()
        self.now += .5
        self.track()
        self.read()
        for _ in range(12):
            self.now += .25
            self.track()
        self.assertTrue(self.row()["on"])

    def row(self):
        return self.preview.latest["fixtures"]["F3"]

    def test_fresh_pose_cannot_extend_reply_and_renewal_uses_move(self):
        self.light()
        move = self.row()["move_sequence"]
        keep = next(row for row in reversed(self.encoded) if row["action"] == "keep_lit")
        self.assertEqual(keep["move_token"], move)
        self.assertEqual(keep["decision_id"], self.row()["move_decision_id"])
        for _ in range(20):
            self.now += .5
            self.track()
        self.assertFalse(self.row()["on"])
        self.assertIsNone(self.row()["keep_lit_token"])

    def test_stand_and_reseat_reject_old_window(self):
        self.light()
        old = self.now
        self.now += .25
        self.track(seated=False)
        self.assertFalse(self.row()["on"])
        self.seat()
        self.read(start=old)
        self.assertFalse(self.preview.episode.need.engaged)
        self.now += .5
        self.track()
        self.read()
        self.assertFalse(self.preview.episode.need.engaged)
        self.now += .5
        self.track()
        self.read()
        self.assertTrue(self.preview.episode.need.engaged)

    def test_second_person_and_new_epoch_clear(self):
        self.light()
        self.now += .25
        self.track(more=True)
        self.assertFalse(self.row()["on"])
        self.now += .25
        self.track(epoch=2)
        self.read()
        self.assertIsNone(self.preview.episode)

    def test_route_over_180_selects_runner_up(self):
        f3 = FIXTURES["F3"]
        # Set F3's forward reference so the room target solves to -165 degrees.
        room_direction = math.atan2(1.38 - .2, 1.2 - 1.8)
        heading = room_direction + math.radians(165)
        fixtures = {"F3": {**f3, "horizontal_world_reference": [math.cos(heading), math.sin(heading), 0],
                           "initial_cdeg": (17000, 0)}, "F4": f3}
        self.preview = preview.Preview(self.core, binding=BINDING, fixtures=fixtures, state=self.path,
                                       clock=lambda: self.now, threaded=False, append=self.encoded.append)
        self.seat()
        chosen, _, reasons = self.preview._candidate(self.preview.track)
        self.assertEqual(chosen, "F4")
        self.assertEqual(reasons["F3"], "route_over_180")

    def test_conflicting_and_unclear_do_not_qualify(self):
        self.seat()
        for i, kind in enumerate(("reading", "unclear", "reading", "reading")):
            self.now += .5
            self.track()
            self.read(kind=kind, **({"book_state": "closed"} if i == 3 else {}))
        self.assertFalse(self.preview.episode.need.engaged)

    def test_reply_checks_its_attached_stills_and_association(self):
        self.seat()
        frame = self.frame(self.now)
        for frames, safe in (((), True), ((replace(frame, camera_epoch=2),), True),
                             ((replace(frame, scan_revision="another_scan"),), True),
                             ((self.frame(self.preview.episode.started - .1), frame), True), ((frame,), False)):
            with self.subTest(frames=frames, safe=safe):
                self.read(frames=frames, safe=safe)
                self.assertIsNone(self.preview.episode.last_observed)
        self.read(frames=(frame,))
        self.assertEqual(self.preview.episode.need.playing, 1)

    def test_the_ask_and_the_preview_read_one_seated_since(self):
        """Contract 2's seated, published once by perception: on the same frames the ask's reply and the preview's
        episode carry the snapshot's own since_mono, so stills from the hold are inside the preview's episode."""
        from test_ask import FakeDetector, FakeOccupancy, completion, facts
        asker = ask.Asker(detector=FakeDetector(), occupancy=FakeOccupancy(), state=self.path, site_id="site",
                          camera_id="den-oak", post=lambda body: completion(json.dumps(facts())),
                          clock=lambda: self.now, wall=lambda: self.now, start=False)
        self.addCleanup(asker.close)
        observe = run_local.evidence_observer(asker, self.preview)
        replies, offer = [], self.preview.offer_inference
        self.preview.offer_inference = lambda reply: (replies.append(reply), offer(reply))
        rule, published, captures = seated_rule.Published(), [], []
        for frame_id in range(1, 25):
            self.now += .25
            captured = self.now - .1
            captures.append(captured)
            frame = FrameRef("run", 1, frame_id, "synthetic", "rgb", PlaneRef(frame_id, captured, captured, 640, 360),
                             None, None, None, BINDING["transform_id"], BINDING["scan_revision"], captured, captured,
                             captured)
            pose = {name: (tuple(xyz), status) for name, (xyz, status) in joints(True).items()}
            track = TrackEvidence("t1", "confirmed", (1.2, 1.0), .1, "stereo", ("pose",), "confident", 2, 90.0, pose,
                                  seated=rule.update("t1", 2, captured, pose))
            published.append(track.seated)
            observe({"tracks": [{"track_id": "t1", "state": "confirmed", "xy_m": [1.2, 1.0]}], "health": {}},
                    TrackingSnapshot("run", 1, frame_id, frame, "fresh", None, "present", (track,)))
            while not asker.jobs.empty():
                job, args, kwargs = asker.jobs.get_nowait()
                job(*args, **kwargs)
        since = {state.since_mono for state in published if state.state}
        self.assertEqual(len(since), 1, "one seated episode")
        self.assertTrue(replies, "the ask answered while seated")
        self.assertEqual({reply.gate["seated_since_mono"] for reply in replies}, since)
        self.assertEqual({self.preview.episode.started}, since)
        self.assertEqual(list(self.preview.episode.requests), [reply.request_id for reply in replies],
                         "the preview took every reply: its stills lie inside the episode")
        said_seated = captures[next(i for i, state in enumerate(published) if state.state)]
        self.assertLess(replies[0].frames[0].capture_mono, said_seated, "the first stills include the hold")

    def test_visible_contradictions_break_the_reading_streak(self):
        self.seat()
        for cue in ("book_closed", "phone_screen_toward_face", "hands_on_keyboard"):
            self.now += .5
            self.track()
            self.read()
            self.now += .5
            self.track()
            self.read(visible_basis=[cue])
            self.assertFalse(self.preview.episode.need.engaged)

    def test_multiple_people_in_the_stills_revokes_current_support(self):
        self.light()
        self.read(people_visible="multiple")
        self.assertFalse(self.row()["on"])
        self.assertIsNone(self.row()["keep_lit_token"])

    def test_dim_travel_records_the_requested_level_before_dark_fallback(self):
        self.light()
        self.preview.configure({"move_dim_level": .35})
        row = self.preview.rows["F3"]
        target = (row["current"][0] + 100, row["current"][1])
        self.preview._move("F3", row, target, .7, "dim", self.now, self.preview.track, "task")
        record = self.encoded[-1]
        self.assertEqual(record["requested"]["dim_level"], .35)
        self.assertEqual(record["effective"]["travel"], "dark")
        self.assertIsNone(record["effective"]["dim_level"])

    def test_phone_patience_does_not_restart(self):
        self.light()
        self.preview.configure({"phone_patience_s": 2})
        for index in range(5):
            self.now += .5
            self.track()
            self.read(kind="phone_use")
            self.assertEqual(self.row()["on"], index < 4)

    def test_stop_counter_survives_restart_and_resumes_in_order(self):
        self.light()
        _, first = self.preview.stop({})
        _, second = self.preview.stop({})
        self.assertFalse(self.row()["on"])
        self.assertEqual(self.preview.resume(first)[0], 409)
        self.preview = self.make_preview()
        self.assertTrue(self.preview.latest["stopped"])
        self.assertEqual(self.preview.resume(second)[0], 200)
        self.assertEqual(self.encoded[-1]["action"], "resume")
        self.assertFalse(self.row()["on"])

    def test_blocked_trace_cannot_delay_stop_or_light_after_stop(self):
        entered, unblock = threading.Event(), threading.Event()

        def append(row):
            entered.set()
            unblock.wait(2)
            self.encoded.append(row)

        self.preview = self.make_preview(threaded=True, append=append).start()
        self.addCleanup(self.preview.close)
        self.addCleanup(unblock.set)
        self.seat()
        self.read()
        self.now += .5
        self.track()
        self.read()
        self.assertTrue(entered.wait(1))
        self.assertEqual(self.preview.stop({})[0], 200)
        self.assertTrue(self.preview.latest["stopped"])
        self.assertFalse(self.row()["on"])
        unblock.set()

    def test_phase_one_starter_uses_geometry_export_without_ha(self):
        settings, why = run_service.coordinator_settings({"coordinator": {
            "preview": True, "transform_id": BINDING["transform_id"], "geometry": {
                "schema": "lighting-brain-geometry-1", "fixtures": [{"fixture_id": "F3", **FIXTURES["F3"]}],
                "binding": {"content_revision": BINDING["scan_revision"]},
                "equipment_ref": {"revision": BINDING["equipment_revision"], "digest": BINDING["equipment_digest"]},
                "zones": {"sofa": {"seat_m": [1.2, 1.38, .65]}},
            },
        }}, None)
        self.assertIsNone(why)
        self.assertEqual(settings["binding"], BINDING)
        core_tree = next(Path(self.tree.name).glob("coordinator-*"))
        service, close = run_service.coordinator_starter(settings, None, state=self.path, tree=core_tree)()
        self.addCleanup(close)
        self.assertIsNone(service.hue)
        self.assertNotIn(("POST", "/coordinator/desk"), service.routes())
        self.assertEqual(service.latest["fixtures"]["F3"]["level"], 0)

    def test_stale_hue_and_real_service_mappings_do_not_open_a_write_client(self):
        fixtures = {"F3": {**FIXTURES["F3"], "real": True, "services": {"aim_move": "c12.move"}}}
        fixed = {"ha_url": "synthetic", "token": "synthetic"}
        settings, why = run_service.coordinator_settings({"coordinator": {
            "preview": True, "hue_overlay": True, "fixtures": fixtures, "binding": BINDING,
        }}, fixed)
        self.assertIsNone(why)
        tree = next(Path(self.tree.name).glob("coordinator-*"))
        with patch.object(run_service.ha_link, "HaClient", side_effect=AssertionError("HA write client opened")) as client:
            self.assertIsNone(run_service.coordinator_starter({"mode": "virtual"}, fixed, state=self.path, tree=tree)())
            service, close = run_service.coordinator_starter(settings, fixed, state=self.path, tree=tree)()
            self.addCleanup(close)
            self.assertIsNone(service.hue)
            self.assertEqual(service.stop({})[0], 200)
            self.assertNotIn(("POST", "/coordinator/virtual/desk"), service.routes())
            client.assert_not_called()

    def test_reconnect_receives_current_stop_snapshot(self):
        server = stream.PeopleStream(port=0).start()
        self.addCleanup(server.close)
        self.preview.connect(server.publish_spot)
        self.preview.stop({})
        for seq in range(20):
            server.publish({"seq": seq, "tracks": []})
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=2)
        self.addCleanup(connection.close)
        try:
            connection.request("GET", "/api/den-live/v1/homes/den/stream")
        except PermissionError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 10013:
                self.skipTest("the Windows sandbox blocks loopback connections")
            raise
        with connection.getresponse() as response:
            self.assertEqual(response.status, 200)
            events = {}
            name = None
            while "spot_snapshot" not in events:
                line = response.readline()
                self.assertTrue(line)
                if line.startswith(b"event: "):
                    name = line[7:].strip().decode()
                if line.startswith(b"data: "):
                    events[name] = json.loads(line[6:])
            snapshot = events["spot_snapshot"]
            self.assertEqual(events["spot_clock"]["epoch"], snapshot["epoch"])
            self.assertTrue(snapshot["stopped"])
            self.assertFalse(snapshot["fixtures"]["F3"]["on"])


if __name__ == "__main__":
    unittest.main()

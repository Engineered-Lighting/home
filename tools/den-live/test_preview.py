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


def joints(seated, turned=False):
    height = 0 if seated else .5
    xyz = {"left_shoulder": [1, 1, .95], "right_shoulder": [1.4, 1, .95], "left_hip": [1, 1, .45],
           "right_hip": [1.4, 1, .45], "nose": [1.2, .9 if turned else 1.1, 1.18], "left_wrist": [1.1, 1.3, .65],
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

    def track(self, *, seated=True, epoch=1, more=False, turned=False, posed=True, association="confident", revision=2,
              age=0.0, eligible=True, eligibility=1):
        self.seq += 1
        if epoch != self.rule_epoch:   # a new epoch is a new tracker, and with it a new seated
            self.rule, self.rule_epoch = seated_rule.Published(), epoch
        pose = joints(seated, turned) if posed else {}
        track = {"track_id": "t1", "state": "confirmed", "position_basis": "stereo", "association_status": association,
                 "association_revision": revision, "current_obs_ids": ("pose",), "supported_pose": pose,
                 "seated": self.rule.update("t1", revision, self.now, pose)}   # perception's one answer
        frame = {**BINDING, "capture_mono": self.now - age}
        self.preview.offer_snapshot({"service_run_id": "run", "camera_epoch": epoch, "seq": self.seq, "frame": frame,
                                     "status": "fresh", "presence": "present", "eligibility_revision": eligibility,
                                     "eligible_track_id": "t1" if seated and not more and eligible else None,
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
        self.assertFalse(self.preview.episode.need.engaged, "stills from before this seated episode are refused")
        self.now += .5
        self.track()
        self.read()
        self.assertTrue(self.preview.episode.need.engaged, "one confident yes inside the episode")

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

    def test_one_confident_yes_lights(self):
        """Spine decision 4: a single valid, bound yes lights, with no second answer. With pre-aim on (its default), the
        fixture is already on the task point, so the reply itself lights it."""
        self.preview.configure({"pre_aim": True})
        self.seat()
        for _ in range(30):   # the dark pre-aim move settles
            self.now += .25
            self.track()
        self.assertFalse(self.row()["on"])
        self.read()
        self.assertTrue(self.preview.episode.need.engaged)
        self.assertTrue(self.row()["on"], "lit by the reply itself")
        self.assertEqual(len(self.preview.episode.requests), 1)

    def test_unclear_and_contradictions_alone_never_light(self):
        self.seat()
        for answer in ({"kind": "unclear"}, {"book_state": "closed"}, {"visible_basis": ["phone_screen_toward_face"]},
                       {"visible_basis": ["hands_on_keyboard"]}, {"people_visible": "multiple"}):
            with self.subTest(answer=answer):
                self.now += .5
                self.track()
                self.read(**answer)
                self.assertFalse(self.preview.episode.need.engaged)
                self.assertFalse(self.row()["on"])

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

    def test_a_second_answer_confirms_it_or_ends_it(self):
        """After the one yes that lit it, a yes confirms it; a contradiction starts the patience and ends it there."""
        self.preview.configure({"phone_patience_s": 2})
        for cue in ("book_closed", "phone_screen_toward_face", "hands_on_keyboard"):
            with self.subTest(cue=cue):
                self.light()
                self.read()
                self.assertTrue(self.row()["on"], "confirmed")
                self.assertIsNone(self.preview.episode.need.idle_since)
                self.now += .5
                self.track()
                self.read(visible_basis=[cue])
                self.assertIsNotNone(self.preview.episode.need.idle_since, "the patience starts")
                for _ in range(3):
                    self.now += .5
                    self.track()
                self.assertTrue(self.row()["on"], "still inside the patience")
                self.now += .75
                self.track()
                self.assertFalse(self.row()["on"], "ended once the patience ran out")
                self.now += .25
                self.track(seated=False)   # stand up, so the next cue starts a new episode

    def test_multiple_people_in_the_stills_revokes_current_support(self):
        self.light()
        self.read(people_visible="multiple")
        self.assertFalse(self.row()["on"])
        self.assertIsNone(self.row()["keep_lit_token"])

    def test_the_next_frame_keeps_a_negative_and_older_stills_are_refused(self):
        """The review's false light: a valid multiple reply moves the episode's start on to it. The next tracking frame,
        whose published since hasn't moved, keeps that episode, so replies whose stills began before the negative are
        refused and two of them light nothing. The control: stills after the negative are taken."""
        self.light()
        episode, negative = self.preview.episode, self.now
        self.read(people_visible="multiple")
        self.assertEqual(episode.started, negative)
        self.assertFalse(self.row()["on"])
        for _ in range(2):
            self.now += .5
            self.track()
            self.assertIs(self.preview.episode, episode, "the next frame keeps the episode")
            self.read(start=negative - .25)
            self.assertNotIn(str(self.now), episode.requests, "stills from before the negative are refused")
        self.assertEqual(episode.last_observed, negative)
        self.assertFalse(self.row()["on"])
        self.now += .5
        self.track()
        self.read(start=negative + .25)
        self.assertEqual(episode.last_observed, self.now, "the control: stills after the negative are taken")

    def test_one_bad_snapshot_darkens_but_keeps_the_episode(self):
        """Contract 2's consumer rule: a stale snapshot or an unsafe frame darkens, and the episode, its onset, the need
        state and its requests survive it; the next good frame lights again with no new answer."""
        for bad in ({"age": preview.POSE_AGE_S + .5}, {"association": "ambiguous"}, {"posed": False}, {"more": True}):
            with self.subTest(bad=bad):
                self.light()
                episode, requests = self.preview.episode, list(self.preview.episode.requests)
                onset = episode.need.onset_ts
                self.now += .25
                self.track(**bad)
                self.assertFalse(self.row()["on"], "a bad frame darkens")
                self.assertIs(self.preview.episode, episode)
                self.assertEqual((list(episode.requests), episode.need.onset_ts), (requests, onset))
                self.assertTrue(episode.need.engaged)
                self.now += .25
                self.track()
                self.assertTrue(self.row()["on"], "the next good frame lights again, with no new answer")
                self.now += .25
                self.track(seated=False)

    def test_a_reply_in_flight_over_a_bad_frame_still_counts(self):
        """On a bad frame the ask publishes no eligible track with the same eligibility revision. The person stays
        eligible: the reply already in flight is taken, and the next good frame lights. A new revision drops it."""
        self.seat()
        asked = self.now
        self.now += .25
        self.track(association="ambiguous", eligible=False)
        self.assertEqual(self.preview.eligible_track, "t1")
        self.read(at=asked)
        self.assertEqual(len(self.preview.episode.requests), 1, "the reply in flight is taken")
        for _ in range(12):   # good frames again, while the first (dark) move settles
            self.now += .25
            self.track()
        self.assertTrue(self.row()["on"])
        episode = self.preview.episode
        self.now += .25
        self.track(eligible=False, eligibility=2)
        self.assertIsNone(self.preview.eligible_track, "a new eligibility revision ends it")
        self.assertIsNot(self.preview.episode, episode, "and its episode")
        self.assertFalse(self.row()["on"])

    def test_a_stale_tick_darkens_but_keeps_the_episode(self):
        self.light()
        episode = self.preview.episode
        self.now += preview.POSE_AGE_S + .1
        self.preview.tick()
        self.assertFalse(self.row()["on"])
        self.assertIs(self.preview.episode, episode)
        self.now += .1
        self.track()
        self.assertTrue(self.row()["on"])

    def test_a_new_association_revision_drops_the_episode(self):
        self.light()
        episode = self.preview.episode
        self.now += .25
        self.track(revision=3)
        self.assertFalse(self.row()["on"])
        self.assertIsNot(self.preview.episode, episode, "a new person binding starts over")
        self.read()   # bound to the old revision
        self.assertFalse(self.row()["on"])

    def test_an_answer_after_standing_never_lights(self):
        """The published seated holds through poseless frames, so an ask can go out as the person stands. Its yes is
        taken into the episode but lights nothing without a current, posed, seated frame."""
        self.seat()
        asked = self.now   # the ask's stills end on the last posed frame
        self.now += .25
        self.track(posed=False)
        self.read(at=asked)
        self.assertTrue(self.preview.episode.need.engaged, "the reply was taken")
        self.assertFalse(self.row()["on"], "but no posed frame: no light")
        self.now += .25
        self.track(seated=False)
        self.assertIsNone(self.preview.episode, "the posed frame shows standing: the episode ends")
        self.assertFalse(self.row()["on"])

    def test_facing_jitter_under_a_second_keeps_the_lit_fixture(self):
        """Contract 10, for 04: a facing refusal of the lit fixture holds it for KEEP_HOLD_S before releasing it."""
        self.light()
        for _ in range(3):   # 0.75 s turned the other way
            self.now += .25
            self.track(turned=True)
            self.assertTrue(self.row()["on"], "jitter under a second doesn't flicker it")
        self.now += .25
        self.track()
        self.assertTrue(self.row()["on"])
        for _ in range(5):   # 1.25 s turned
            self.now += .25
            self.track(turned=True)
        self.assertFalse(self.row()["on"], "a refusal that lasts past the hold releases it")
        self.assertEqual(self.row()["reason"], "in_front_of_reader")

    def test_the_subject_names_the_person_and_the_reply(self):
        self.seat()
        subject = {"track_id": "t1", "camera_epoch": 1, "association_revision": 2, "request_id": None}
        self.assertEqual(self.preview.latest["subject"], subject)
        self.read()
        lit = {**subject, "request_id": str(self.now)}
        self.assertEqual(self.preview.latest["subject"], lit)
        self.assertEqual(self.preview.latest_decision["subject"], lit, "the Decision carries the same fields")
        move = next(row for row in reversed(self.encoded) if row["action"] == "aim_move")
        self.assertEqual(move["decision_id"], self.preview.latest_decision["decision_id"])

    def test_controls_write_what_memory_holds_and_on_off_stop_never_conflict(self):
        """Contract 6's fixes: the file matches memory after every control, control_revision persists, and a Stop
        arriving inside an On leaves both answered 200 (the old spurious 409)."""
        def saved():
            return json.loads((Path(self.path) / "coordinator" / "preview-control.json").read_text(encoding="utf-8"))

        for action in ("off", "on", "stop"):
            status, _ = self.preview._control(action)
            self.assertEqual(status, 200)
            self.assertEqual(saved(), self.preview._persisted())
        counter = self.preview.stop_counter
        self.assertEqual(self.preview.resume({"stop_counter": counter})[0], 200)
        self.assertEqual(saved(), self.preview._persisted())
        self.assertFalse(saved()["stopped"])
        self.preview.switch({"on": False})
        fired = []

        def publish(snapshot):
            if snapshot["on"] and not snapshot["stopped"] and not fired:
                fired.append(True)
                self.assertEqual(self.preview.stop({})[0], 200)
        self.preview.connect(publish)
        self.assertEqual(self.preview.switch({"on": True})[0], 200, "a Stop arriving inside an On is no conflict")
        self.assertTrue(fired)
        self.assertEqual(saved(), {**self.preview._persisted(), "on": True, "stopped": True})
        revision = self.preview.control_revision
        self.preview = self.make_preview()
        self.assertEqual(self.preview.control_revision, revision, "control_revision survives a restart")

    def test_a_stop_during_resume_wins(self):
        self.light()
        _, stopped = self.preview.stop({})
        fired = []

        def during(row):
            self.encoded.append(row)
            if row["action"] == "resume" and not fired:
                fired.append(True)
                self.preview.stop({})   # a Stop while Resume waits for its trace row
        self.preview = self.make_preview(append=during)
        self.assertTrue(self.preview.stopped)
        status, body = self.preview.resume({"stop_counter": stopped["stop_counter"]})
        self.assertEqual(status, 409)
        self.assertTrue(self.preview.stopped)
        saved = json.loads((Path(self.path) / "coordinator" / "preview-control.json").read_text(encoding="utf-8"))
        self.assertTrue(saved["stopped"])
        self.assertEqual(saved["stop_counter"], stopped["stop_counter"] + 1)

    def test_a_failed_resume_write_stays_stopped(self):
        self.light()
        _, stopped = self.preview.stop({})
        with patch.object(self.preview, "_save", side_effect=OSError("disk")):
            self.assertEqual(self.preview.resume({"stop_counter": stopped["stop_counter"]})[0], 503)
        self.assertTrue(self.preview.stopped, "memory never runs ahead of the file on Resume")

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

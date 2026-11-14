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
import c12_fc13
import coordinator_live
import direction
from contracts import PERCEPTION_S, FrameRef, InferenceReply, PlaneRef, TrackEvidence, TrackingSnapshot
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
INSTANT = {"fade_in_s": 0, "fade_out_s": 0, "leave_fade_s": 0, "crossfade_s": 0, "pre_aim": False}


def joints(seated, turned=False, shift=0.0, page_turn=False):
    """shift: the reader moved along the sofa (x, metres); page_turn: the right hand lifted 15 cm."""
    height = 0 if seated else .5
    xyz = {"left_shoulder": [1, 1, .95], "right_shoulder": [1.4, 1, .95], "left_hip": [1, 1, .45],
           "right_hip": [1.4, 1, .45], "nose": [1.2, .9 if turned else 1.1, 1.18], "left_wrist": [1.1, 1.3, .65],
           "right_wrist": [1.3, 1.3, .8 if page_turn else .65]}
    return {key: [(x + shift, y, z + height), "supported"] for key, (x, y, z) in xyz.items()}


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
        # The rules' tests play every change at once: the direction's fades and crossfades have tests of their own
        # (test_direction.py, and the direction tests below).
        self.preview.configure(INSTANT)

    def make_preview(self, **overrides):
        return preview.Preview(self.core, binding=BINDING, fixtures=FIXTURES, state=self.path,
                               clock=lambda: self.now, **{"threaded": False, "append": self.encoded.append, **overrides})

    def track(self, *, seated=True, epoch=1, more=False, turned=False, posed=True, association="confident", revision=2,
              age=0.0, eligible=True, eligibility=1, head=None, frame_extra=None, shift=0.0, page_turn=False):
        self.seq += 1
        if epoch != self.rule_epoch:   # a new epoch is a new tracker, and with it a new seated
            self.rule, self.rule_epoch = seated_rule.Published(), epoch
        pose = joints(seated, turned, shift, page_turn) if posed else {}
        track = {"track_id": "t1", "state": "confirmed", "position_basis": "stereo", "association_status": association,
                 "association_revision": revision, "current_obs_ids": ("pose",), "supported_pose": pose,
                 "seated": self.rule.update("t1", revision, self.now, pose)}   # perception's one answer
        if head is not None:
            track["head_point_m"] = {"xyz": head, "n": 3}
        frame = {**BINDING, "capture_mono": self.now - age, **(frame_extra or {})}
        self.preview.offer_snapshot({"service_run_id": "run", "camera_epoch": epoch, "seq": self.seq, "frame": frame,
                                     "status": "fresh", "presence": "present", "eligibility_revision": eligibility,
                                     "eligible_track_id": "t1" if seated and not more and eligible else None,
                                     "tracks": [track, track] if more else [track]})

    def stale(self):
        """Perception's snapshot in a camera gap: no frame, status stale, same epoch."""
        self.seq += 1
        self.preview.offer_snapshot({"service_run_id": "run", "camera_epoch": self.rule_epoch, "seq": self.seq,
                                     "frame": None, "status": "stale", "presence": "unknown", "eligibility_revision": 1,
                                     "eligible_track_id": None, "tracks": []})

    def gap(self, seconds, step=.1):
        """A camera gap: the clock runs, stale snapshots every 0.5 s, the preview's own ticks between. Returns the lit
        state seen at each step."""
        seen, end, last_stale = [], self.now + seconds, self.now
        while self.now + step <= end + 1e-9:
            self.now = round(self.now + step, 6)
            if self.now - last_stale >= .5 - 1e-9:
                self.stale()
                last_stale = self.now
            else:
                self.preview.tick()
            seen.append(self.row()["on"])
        return seen

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

    def test_the_head_is_perceptions_head_point_and_the_pose_ages_from_capture_mono_alone(self):
        """Contract 2 (§10, for 04): the preview reads head_point_m, perception's one head, in place of the
        coarse_head_room_m nothing made (its own median of the face joints only without one); and the pose's age runs
        from the frame's capture_mono alone: capture_earliest_mono is no longer read."""
        self.track(head=(1.25, 1.05, 1.3))
        self.assertEqual(self.preview.track["head"], (1.25, 1.05, 1.3))
        self.now += .5
        self.track()
        self.assertEqual(tuple(self.preview.track["head"]), (1.2, 1.1, 1.18), "without one, the face's own median")
        self.now += .5
        self.track(head=(1.4, 1.05, 1.3), frame_extra={"capture_earliest_mono": self.now - 10})
        self.assertEqual((self.preview.captured, self.preview.track["head"]), (self.now, (1.4, 1.05, 1.3)),
                         "an old capture_earliest_mono no longer makes a current frame late")
        taken = self.now
        self.now += .5
        self.track(head=(1.5, 1.05, 1.3), age=PERCEPTION_S + .01)
        self.assertEqual((self.preview.captured, self.preview.track["head"]), (taken, (1.4, 1.05, 1.3)),
                         "a pose past PERCEPTION_S from its capture_mono isn't taken")

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
        for bad in ({"association": "ambiguous"}, {"posed": False}, {"more": True}):
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

    def test_a_gap_holds_a_lit_spot_up_to_three_seconds(self):
        """The gap hold: through a camera gap a lit spot stays lit, with keep-lit going on, until its newest fresh frame
        is GAP_HOLD_S old; then it darkens, keeping the episode, and the next fresh frame lights it again. The
        snapshot's validity never passes the hold."""
        self.light()
        episode, last = self.preview.episode, self.now
        keeps = sum(row["action"] == "keep_lit" for row in self.encoded)
        lit = self.gap(preview.GAP_HOLD_S - .1)
        self.assertTrue(all(lit), "held through the gap")
        self.assertLessEqual(self.preview.latest["valid_until_mono"], last + preview.GAP_HOLD_S)
        self.assertGreater(sum(row["action"] == "keep_lit" for row in self.encoded), keeps, "keep-lit goes on")
        self.assertTrue(self.preview.latest_decision["proposal"]["gap_hold"])
        self.assertEqual(self.gap(.1), [False], "a gap over 3.0 s darkens")
        self.assertIs(self.preview.episode, episode)
        self.now += .1
        self.track()
        self.assertTrue(self.row()["on"], "the next fresh frame lights it again")

    def test_a_late_frame_is_a_gap_too(self):
        self.light()
        self.now += .25
        self.track(age=preview.POSE_AGE_S + .5)
        self.assertTrue(self.row()["on"], "a frame older than the pose expiry teaches nothing and darkens nothing")

    def test_the_harvests_gaps_never_blink(self):
        """The first LA harvest: 25 gaps of 1.5-2.4 s in 5 minutes, replayed into a seated, lit episode at the camera's
        2.2 frames a second, with an answer every 2 s while frames flow. Not one blink, and no dark move."""
        self.light()
        gaps = [round(1.5 + .9 * ((7 * i) % 25) / 24, 3) for i in range(25)]
        self.assertEqual((min(gaps), max(gaps), len(gaps)), (1.5, 2.4, 25))
        start, moves, seen = self.now, len(self.encoded), []
        for gap in gaps:
            flowing, asked = 12 - gap, self.now
            end = self.now + flowing
            while self.now + .45 <= end:
                self.now = round(self.now + .45, 6)
                self.track()
                seen.append(self.row()["on"])
                if self.now - asked >= 2:
                    self.read()
                    asked = self.now
            seen.extend(self.gap(gap))
        self.assertGreaterEqual(self.now - start, 290)
        blinks = sum(1 for before, after in zip(seen, seen[1:]) if before and not after)
        self.assertEqual(blinks, 0)
        self.assertTrue(all(seen))
        dark = [row for row in self.encoded[moves:] if row["action"] == "aim_move" and not row["requested"]["level"]]
        self.assertEqual(dark, [], "no dark move went out")

    def test_standing_in_a_gap_darkens_on_the_next_fresh_frame(self):
        self.light()
        self.assertTrue(all(self.gap(2.0)))
        self.now += .1
        self.track(seated=False)
        self.assertFalse(self.row()["on"], "standing, seen on a fresh frame, darkens at once")
        self.assertIsNone(self.preview.episode)

    def test_lighting_still_needs_a_frame_within_the_pose_expiry(self):
        """The hold keeps a lit spot; it never lights one. An answer that lands 2 s into a gap waits for a fresh frame."""
        self.preview.configure({"pre_aim": True})
        self.seat()
        for _ in range(30):   # the dark pre-aim move settles
            self.now += .25
            self.track()
        asked = self.now
        self.gap(2.0)
        self.read(at=asked)
        self.assertTrue(self.preview.episode.need.engaged, "the answer is taken")
        self.assertFalse(self.row()["on"], "but nothing lights from a 2 s old pose")
        self.now += .1
        self.track()
        self.assertTrue(self.row()["on"], "the next fresh frame lights it")

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
        """Contract 10, for 04: a facing refusal of the lit fixture holds it for KEEP_HOLD_S before releasing it. The
        direction's seated facing is the mean of the last 3 s (direction.FACING_WINDOW_S), so a turn becomes a
        refusal only once most of that window has turned: a real turn still releases it, a little later."""
        self.light()
        for _ in range(3):   # 0.75 s turned the other way
            self.now += .25
            self.track(turned=True)
            self.assertTrue(self.row()["on"], "jitter under a second doesn't flicker it")
        self.now += .25
        self.track()
        self.assertTrue(self.row()["on"])
        seen = []
        for _ in range(12):   # 3 s turned
            self.now += .25
            self.track(turned=True)
            seen.append(self.row()["on"])
        self.assertTrue(all(seen[:5]), "1.25 s turned: the averaged facing hasn't turned yet")
        self.assertFalse(seen[-1], "a refusal that lasts past the hold releases it")
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


class DirectionTest(PreviewTest):
    """The light direction at its own defaults (the lighting-direction note): a rise is a level train of stationary
    MOVEs on the smooth curve; a lit move is one MOVE flown on the board's S-curve; the room's glow dims under the task
    light; an exit is a fade that's dark by the light's last published validity; Stop and expiry stay instant. Only these
    tests run here: the rules' tests run in PreviewTest, with every change instant."""

    def setUp(self):
        super().setUp()
        self.preview.configure({**{key: direction.SETTINGS[key] for key in INSTANT if key in direction.SETTINGS},
                                "pre_aim": True})
        self.snapshots = []
        self.preview.connect(lambda snapshot: self.snapshots.append(json.loads(json.dumps(snapshot))))

    def frames(self, seconds, step=.25, **track):
        for _ in range(round(seconds / step)):
            self.now = round(self.now + step, 6)
            self.track(**track)

    def lit_by_direction(self):
        """Seated, the dark pre-aim settled on the page, one yes, and the rise finished."""
        self.seat()
        self.frames(6)
        self.assertFalse(self.row()["on"])
        self.read()
        self.frames(self.preview.settings["fade_in_s"] + .5)
        self.assertTrue(self.row()["on"])
        self.assertIsNone(self.row()["fade"])

    def moves(self, since=0):
        return [row for row in self.encoded[since:] if row["action"] == "aim_move"]

    def test_a_yes_rises_on_the_curve_as_a_level_train(self):
        self.seat()
        self.frames(6)
        since = len(self.encoded)
        self.read()
        levels = [self.row()["level"]]
        self.assertEqual(self.row()["fade"]["purpose"], "fade_in")
        for _ in range(10):
            self.now = round(self.now + .25, 6)
            self.track()
            levels.append(self.row()["level"])
        goal = self.preview.rows["F3"]["goal"]
        self.assertTrue(0 < levels[0] < .15 * goal, "it blooms: visible with the yes itself, dim")
        self.assertLess(levels[1], .4 * goal, "and well short of full a quarter-second on")
        self.assertTrue(all(b >= a for a, b in zip(levels, levels[1:])), "never dips on the way up")
        self.assertEqual(levels[-1], direction.level_code(goal) / 254)
        steps = self.moves(since)
        self.assertGreater(len(steps), 4)
        self.assertTrue(all(row["requested"]["start_cdeg"] == row["requested"]["target_cdeg"]
                            and row["requested"]["travel"] == "none" for row in steps), "stationary MOVEs only")
        codes = [row["effective"]["spot_level"] for row in steps]
        self.assertEqual(codes, sorted(set(codes)), "one MOVE per new level code")
        self.assertEqual(round(self.row()["level"] * 254), codes[-1], "the light is what the trace committed")
        self.assertEqual(self.row()["keep_lit_token"], steps[-1]["sequence"])

    def test_standing_leaves_quickly_and_never_past_its_validity(self):
        self.lit_by_direction()
        promised = self.preview.latest["valid_until_mono"]
        since = len(self.encoded)
        self.now += .25
        self.track(seated=False)
        stood, levels = self.now, [self.row()["level"]]
        self.assertEqual(self.row()["fade"]["purpose"], "fade_out")
        while self.row()["on"]:
            self.now = round(self.now + .05, 6)
            self.preview.tick()
            levels.append(self.row()["level"])
        self.assertLessEqual(self.now - stood, self.preview.settings["leave_fade_s"] + .05 + 1e-9, "dark within 1 s")
        self.assertLessEqual(self.now, promised + 1e-9, "never lit past what a snapshot promised")
        self.assertTrue(all(b <= a for a, b in zip(levels, levels[1:])))
        self.assertEqual(self.moves(since)[-1]["effective"]["spot_level"], 0)

    def test_a_planned_end_is_dark_at_its_deadline(self):
        self.preview.configure({"phone_patience_s": 3})
        self.lit_by_direction()
        self.read(kind="phone_use")
        deadline = self.preview.task_until()
        fade = self.preview.settings["fade_out_s"]
        full = direction.level_code(self.preview.rows["F3"]["goal"]) / 254
        while self.now < deadline - fade - .05:
            self.now = round(self.now + .05, 6)
            self.preview.tick()
            self.assertEqual(self.row()["level"], full, "full until the exit has to start")
        while self.now < deadline:
            self.now = round(self.now + .05, 6)
            self.preview.tick()
        self.assertFalse(self.row()["on"], "dark at the patience's end, not after it")

    def test_stop_and_expiry_stay_instant(self):
        self.lit_by_direction()
        self.preview.stop({})
        self.assertFalse(self.row()["on"], "Stop: dark at once, no fade")
        self.assertIsNone(self.row()["fade"])
        self.preview.resume({"stop_counter": self.preview.stop_counter})
        self.lit_by_direction()
        self.gap(preview.GAP_HOLD_S - .1)
        self.assertTrue(self.row()["on"])
        self.assertEqual(self.gap(.1), [False], "the gap hold's end: dark at once")

    def test_a_page_turn_never_moves_a_lit_spot_and_a_shift_is_one_lit_glide(self):
        self.lit_by_direction()
        since = len(self.encoded)
        self.frames(.75, page_turn=True)
        self.frames(1.5)
        self.assertEqual([row for row in self.moves(since) if row["requested"]["start_cdeg"]
                          != row["requested"]["target_cdeg"]], [], "a page turn moves nothing")
        level = self.row()["level"]
        glide = None
        for _ in range(16):
            self.now = round(self.now + .25, 6)
            self.track(shift=-.3)
            glide = glide or self.row()["glide"]
            self.assertTrue(self.row()["on"], "lit all the way")
        glides = [row for row in self.moves(since) if row["requested"]["start_cdeg"] != row["requested"]["target_cdeg"]]
        self.assertEqual(len(glides), 1, "one movement, one command")
        self.assertEqual((glides[0]["requested"]["travel"], glides[0]["effective"]["lit_during_move"]), ("lit", True))
        self.assertEqual(glides[0]["effective"]["spot_level"], round(level * 254), "it glides at the level it had")
        start, target = glides[0]["requested"]["start_cdeg"], glides[0]["requested"]["target_cdeg"]
        expected = self.core.scurve.move_time(tuple((b - a) / 100 for a, b in zip(start, target)),
                                              self.core.scurve.FICTIONAL_PROFILE["axes"])
        self.assertAlmostEqual(glide["duration_s"], expected, places=5, msg="Home draws the board's own S-curve")
        self.assertEqual(glide["to_deg"], [-target[0] / 100, -target[1] / 100])

    def test_the_room_dims_under_the_task_light_and_comes_back(self):
        self.assertEqual(self.preview.latest["ambient"]["scale"], 1.0)
        self.lit_by_direction()
        settings = self.preview.settings
        self.frames(settings["ambient_delay_s"] + settings["ambient_dim_s"])
        ambient = self.preview.latest["ambient"]
        self.assertEqual((ambient["scale"], ambient["cct_k"]), (settings["ambient_task_scale"], 2700))
        self.now += .25
        self.track(seated=False)
        self.frames(settings["ambient_restore_s"] + .25, seated=False)
        self.assertEqual(self.preview.latest["ambient"]["scale"], 1.0)
        self.preview.switch({"on": False})
        self.assertIsNone(self.preview.latest["ambient"], "Off: the coordinator directs no room light")

    def test_a_stale_or_unsafe_frame_still_darkens_at_once(self):
        """Contract 2's consumer rule is the rules', unchanged: only the normal exits fade."""
        for bad in ({"posed": False}, {"more": True}, {"association": "ambiguous"}):
            with self.subTest(bad=bad):
                self.lit_by_direction()
                goal = self.row()["level"]
                self.now += .25
                self.track(**bad)
                self.assertFalse(self.row()["on"], "dark at once, no fade")
                self.assertIsNone(self.row()["fade"])
                self.frames(self.preview.settings["fade_in_s"] + .5)
                self.assertEqual(self.row()["level"], goal, "the next good frames rise again")
                self.now += .25
                self.track(seated=False)
                self.frames(1, seated=False)

    def test_the_level_train_is_capped_at_ten_a_second(self):
        self.seat()
        self.frames(6)
        since = len(self.encoded)
        self.read()
        for _ in range(150):   # ticks far faster than the cap, for 3 s
            self.now = round(self.now + .02, 6)
            self.preview.tick()
        steps = [row["at"] for row in self.moves(since)]
        self.assertGreater(len(steps), 10)
        self.assertGreaterEqual(min(b - a for a, b in zip(steps, steps[1:])), direction.LEVEL_STEP_S - 1e-9)
        self.assertLessEqual(len(steps), self.preview.settings["fade_in_s"] / direction.LEVEL_STEP_S + 2)

    def test_a_real_fixture_steps_at_two_a_second_with_no_keep_lit_between(self):
        """A profile that isn't fictional has a radio path: its train steps each RADIO_STEP_S, the steps renew the
        lease themselves, so it never sends more than 2 commands a second."""
        real = {"F3": {**FIXTURES["F3"], "profile": {**self.core.scurve.FICTIONAL_PROFILE, "fictional": False,
                                                       "label": "measured-test"}}}
        self.preview = preview.Preview(self.core, binding=BINDING, fixtures=real, state=self.path,
                                       clock=lambda: self.now, threaded=False, append=self.encoded.append)
        self.preview.configure({"pre_aim": True})
        self.seat()
        self.frames(6)
        since = len(self.encoded)
        self.read()
        for _ in range(250):
            self.now = round(self.now + .02, 6)
            self.preview.tick()
        sent = [row for row in self.encoded[since:] if row["action"] in ("aim_move", "keep_lit")]
        times = [row["at"] for row in sent]
        self.assertTrue(all(b - a >= direction.RADIO_STEP_S - 1e-9 for a, b in zip(times, times[1:])),
                        "at most 2 commands a second, keep-lit included")
        rise = [row for row in sent if row["action"] == "aim_move"]
        self.assertLessEqual(len(rise), self.preview.settings["fade_in_s"] / direction.RADIO_STEP_S + 1)
        self.assertEqual(self.row()["level"], direction.level_code(self.preview.rows["F3"]["goal"]) / 254)
        self.now += .25
        self.track(seated=False)
        self.assertFalse(self.row()["on"], "a 0.3 s exit is too short for a 0.5 s train: one MOVE, dark")

    def test_an_exit_during_a_glide_is_one_dark_move_to_where_it_is_going(self):
        """P3 holds a MOVE that arrives during a move until the landing, so an exit mid-glide never sends a train: one
        level-0 MOVE to the glide's own target, never back to a point it has passed."""
        self.lit_by_direction()
        for _ in range(12):
            self.now = round(self.now + .25, 6)
            self.track(shift=-.3)
            if self.row()["glide"]:
                break
        glide = self.preview.rows["F3"]["glide"]
        self.assertIsNotNone(glide)
        since = len(self.encoded)
        self.now += .05
        self.track(seated=False, shift=-.3)
        self.assertFalse(self.row()["on"])
        moves = self.moves(since)
        self.assertEqual(len(moves), 1)
        self.assertEqual((tuple(moves[0]["requested"]["target_cdeg"]), moves[0]["effective"]["spot_level"]),
                         (glide.target, 0))

    def test_a_hand_off_is_a_crossfade_from_the_standby(self):
        fixtures = {"F3": FIXTURES["F3"], "F4": {"aim_origin_m": [.6, .2, 2.5], "horizontal_world_reference": [0, 1, 0]}}
        self.preview = preview.Preview(self.core, binding=BINDING, fixtures=fixtures, state=self.path,
                                       clock=lambda: self.now, threaded=False, append=self.encoded.append)
        self.preview.configure({"pre_aim": True})
        self.preview.connect(lambda snapshot: self.snapshots.append(json.loads(json.dumps(snapshot))))
        self.lit_by_direction()
        rows = self.preview.latest["fixtures"]
        lit, spare = ("F3", "F4") if rows["F3"]["on"] else ("F4", "F3")
        self.frames(1.5)
        self.assertEqual(self.preview.rows[spare]["reason"], "standby", "the runner-up waits on the page, dark")
        self.assertFalse(self.preview.latest["fixtures"][spare]["on"])
        evaluate = preview.Preview._eye

        def refuse(owner, name, row, person, kept):
            if name == lit:
                return self.core.eye.aim.Candidate(name, False, "beam_through_head")
            return evaluate(owner, name, row, person, kept)
        since = len(self.snapshots)
        with patch.object(preview.Preview, "_eye", refuse):
            self.frames(self.preview.settings["crossfade_s"] + 1.5)
        seen = self.snapshots[since:]
        self.assertTrue(seen[-1]["fixtures"][spare]["on"] and not seen[-1]["fixtures"][lit]["on"])
        self.assertTrue(all(any(row["on"] for row in snapshot["fixtures"].values()) for snapshot in seen),
                        "never dark between them")
        self.assertTrue(any(snapshot["fixtures"][lit]["on"] and snapshot["fixtures"][spare]["on"] for snapshot in seen),
                        "they overlap: a crossfade")

    def test_every_snapshot_is_one_home_accepts(self):
        """Home's validRow: lit means a level above 0 and the keep-lit token equal to the move's sequence; dark means
        level 0 and no token; a glide only while moving; the validity never before the sample."""
        self.lit_by_direction()
        self.frames(4, shift=-.3)
        self.now += .25
        self.track(seated=False, shift=-.3)
        self.frames(1, seated=False, shift=-.3)
        self.assertGreater(len(self.snapshots), 50)
        for snapshot in self.snapshots:
            self.assertGreaterEqual(snapshot["valid_until_mono"], snapshot["sampled_mono"])
            for row in snapshot["fixtures"].values():
                if row["on"]:
                    self.assertGreater(row["level"], 0)
                    self.assertEqual(row["keep_lit_token"], row["move_sequence"])
                else:
                    self.assertEqual((row["level"], row["keep_lit_token"]), (0, None))
                self.assertEqual(row["glide"] is not None, row["motion_phase"] == "moving")


    # ---- a board with the timed MOVE (firmware 62ea043): one MOVE per cue ----------------------------------------------

    def timed_board(self, join=True, pre_aim=True):
        """F3 as a board that advertises the timed MOVE (c12.get_profile's capabilities), through the pinned codec."""
        if getattr(c12_fc13, "TIMED", None) is None:
            self.skipTest("the pinned codec predates the timed MOVE (firmware 62ea043)")
        caps = {"timed_move": True, "fade_curves": True, "join_flight": join, "at_rest": True}
        board = {"F3": {**FIXTURES["F3"], "profile": {**self.core.scurve.FICTIONAL_PROFILE, "fictional": False,
                                                        "label": "timed-test", "capabilities": caps}}}
        self.preview = preview.Preview(self.core, binding=BINDING, fixtures=board, state=self.path,
                                       clock=lambda: self.now, threaded=False, append=self.encoded.append)
        self.preview.configure({"pre_aim": pre_aim})
        self.preview.connect(lambda snapshot: self.snapshots.append(json.loads(json.dumps(snapshot))))

    def test_a_timed_board_rises_in_one_move_on_its_own_curve(self):
        self.timed_board()
        self.seat()
        self.frames(6)
        since = len(self.encoded)
        self.read()
        rise = self.moves(since)
        self.assertEqual(len(rise), 1, "one MOVE carries the whole rise")
        self.assertEqual(rise[0]["requested"]["timed"], {"min_duration_ms": 0, "fade_ms": 2000, "fade_curve": "ease_out"})
        self.assertEqual((rise[0]["effective"]["timed"], rise[0]["effective"]["fade_ms"], rise[0]["effective"]["fade_curve"]),
                         (True, 2000, "ease_out"), "the pinned codec encodes it")
        payload = bytes.fromhex(rise[0]["payload_hex"])
        self.assertEqual(payload[2] & 0x70, 0x10 | (1 << 5), "flag bit 4 and curve 1, ease-out")
        self.assertEqual(int.from_bytes(payload[12:14], "little"), 2000, "fade_ms in bytes 12-13")
        goal = self.preview.rows["F3"]["goal"]
        self.now = round(self.now + 2 / 3, 6)
        self.preview.tick()
        self.assertAlmostEqual(self.row()["level"], goal / 2, delta=.02, msg="half way at a third of its time")
        self.assertEqual(self.row()["keep_lit_token"], rise[0]["sequence"], "the timed MOVE holds the lease")
        self.frames(3)
        self.assertEqual(self.row()["level"], goal)
        self.assertEqual(len(self.moves(since)), 1, "and nothing more is sent for it")

    def test_a_dark_move_then_a_bloom_is_one_timed_move(self):
        self.timed_board(pre_aim=False)
        self.seat()
        since = len(self.encoded)
        self.read()
        moves = self.moves(since)
        self.assertEqual(len(moves), 1)
        self.assertEqual((moves[0]["requested"]["travel"], moves[0]["requested"]["timed"]["fade_curve"]), ("dark", "ease_out"))
        glide = self.preview.rows["F3"]["glide"]
        self.assertIsNotNone(glide)
        self.assertFalse(self.row()["on"], "dark on the way")
        while self.preview.rows["F3"]["glide"] is not None:
            self.assertEqual(self.row()["level"], 0.0)
            self.now = round(self.now + .1, 6)
            self.preview.tick()
        self.now = round(self.now + .5, 6)
        self.preview.tick()
        self.assertTrue(self.row()["on"], "it blooms from dark when it lands")
        self.assertEqual(len(self.moves(since)), 1)

    def test_a_timed_glide_takes_the_gentle_floor_and_an_exit_joins_the_flight(self):
        self.timed_board()
        self.seat()
        self.frames(6)
        self.read()
        self.frames(2.5)
        glide = None
        for _ in range(16):
            self.now = round(self.now + .25, 6)
            self.track(shift=-.3)
            glide = glide or self.row()["glide"]
            if glide:
                break
        self.assertIsNotNone(glide)
        move = next(row for row in reversed(self.moves()) if row["requested"]["start_cdeg"] != row["requested"]["target_cdeg"])
        self.assertEqual(move["requested"]["timed"]["min_duration_ms"], 1000)
        self.assertGreaterEqual(glide["duration_s"], 1.0, "the same S-curve, slower: Home draws the stretched one")
        target = self.preview.rows["F3"]["glide"].target
        since = len(self.encoded)
        self.now = round(self.now + .1, 6)
        self.track(seated=False, shift=-.3)
        exit_moves = self.moves(since)
        self.assertEqual(len(exit_moves), 1)
        self.assertEqual((tuple(exit_moves[0]["requested"]["target_cdeg"]), exit_moves[0]["effective"]["spot_level"]),
                         (target, 0), "one MOVE to the target being flown: it joins the flight")
        self.assertEqual(exit_moves[0]["requested"]["timed"]["fade_ms"], 300)
        if exit_moves[0]["effective"]["lit_during_move"]:
            self.assertTrue(self.row()["on"], "lit while it flies on, it fades rather than cuts")
        else:
            # The pinned codec sets lit_during_move only above level 0, so the board would go dark at once; the light
            # follows what was encoded. (The re-pinned codec should set it for a timed fade to 0.)
            self.assertFalse(self.row()["on"])
        self.now = round(self.now + .35, 6)
        self.preview.tick()
        self.assertFalse(self.row()["on"], "dark within the leave fade")

for _name in [name for name in vars(PreviewTest) if name.startswith("test_")]:
    setattr(DirectionTest, _name, None)   # the rules' tests run once, in PreviewTest



if __name__ == "__main__":
    unittest.main()

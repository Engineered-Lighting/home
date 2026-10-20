"""Scene continuity through the real public journal and persistent Node worker.

All records and approved examples are fictional. No camera/model or hardware.
"""
import copy
import tempfile
import unittest
from pathlib import Path

from journal import Journal, PREFIX
from live_test_helpers import LiveHttp, NOW, PROFILE, SCOPE, event, fact, geometry, publication_ref
from live_worker_test_helpers import WORKER_ID, WORKER_TOKEN, WorkerProbe, worker_receipt_resolver
from live_scene_test_helpers import scene_worker_fixture, HUMAN_TOKEN, HUMAN_ACTOR


class LiveSceneIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="synthetic-live-scene-", dir=Path(__file__).parent)
        self.addCleanup(self.work.cleanup)
        self.now = NOW + .2
        self.service, self.grants, self.config = scene_worker_fixture()
        self.journal = Journal(Path(self.work.name) / "synthetic.sqlite", grants=self.grants, profile=PROFILE,
                               clock=lambda: self.now, live_config=self.service,
                               live_receipt_resolver=worker_receipt_resolver)
        self.addCleanup(self.journal.close)
        self.http = LiveHttp(self.journal)
        self.addCleanup(self.http.close)
        self.config["service_url"] = f"http://127.0.0.1:{self.http.server.server_port}"
        self.probe = WorkerProbe()
        self.addCleanup(self.probe.close)
        self.heads = {}

    def publish(self, seq, *, track=None, kind=None, value=None):
        self.now = NOW + seq - 1 + .2
        payload = fact("synthetic-scene-fact-" + str(seq), seq=seq)
        if track:
            payload["person_ref"]["track_epoch"] = track
            payload["head"]["key"]["person_ref"] = copy.deepcopy(payload["person_ref"])
        if kind:
            payload.update(kind=kind, value=value)
            payload["head"]["key"]["predicate"] = kind
        self.publish_payload(payload)
        return payload

    def publish_payload(self, payload):
        key = str(payload["head"]["key"])
        if key in self.heads:
            payload["head"]["supersedes"] = publication_ref(self.heads[key])
        status, receipt = self.http.request("POST", PREFIX + "/events", event(payload))
        self.assertEqual(status, 200, receipt)
        self.heads[key] = receipt
        return receipt

    def tick(self):
        result = self.probe.command("tick", t=self.now)
        self.assertEqual(len(result["decisions"]), 1, result)
        return result["decisions"][0]

    def establish(self):
        for seq in range(1, 7):
            payload = self.publish(seq)
            if seq == 1:
                self.probe.command("start", config=self.config, t=self.now)
            decision = self.tick()
        self.assertEqual(decision["scene"]["status"], "selected", decision)
        return payload, decision

    def intent(self, person, *, kind="bind_profile", ident="synthetic-profile-binding"):
        from journal import digest
        catalog = self.config["scene_policy"]["catalog"]
        profile = catalog["profiles"][-1]
        example = next(e for e in catalog["examples"] if e["profile_id"] == profile["id"])
        return {"schema": "home-live-intent-event-1", "version": 1, "id": ident, **SCOPE,
                "manifest_ref": self.config["manifest_ref"], "actor_ref": HUMAN_ACTOR,
                "origin": "human_declaration", "truth_role": "runtime_intent", "kind": kind,
                "target": copy.deepcopy(person), "set_at": self.now, "available_at": self.now,
                "valid_until": self.now + 120, "reply_to": None,
                "value": None if kind == "unbind_profile" else {
                    "profile_ref": {"id": profile["id"], "digest": digest(profile)},
                    "example_ref": {"id": example["id"], "digest": digest(example)}}}

    def submit(self, body):
        status, receipt = self.http.request("POST", PREFIX + "/intent-events", body, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, receipt)
        self.assertEqual(receipt["status"], "accepted")
        return receipt

    def lookup(self, ident):
        status, result = self.http.request("GET", PREFIX + "/intent-events/" + ident
                                           + "?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"],
                                           token=HUMAN_TOKEN)
        self.assertEqual(status, 200, result)
        return result

    def test_bind_acceptance_is_distinct_from_atomic_application_and_motion_keeps_scene(self):
        payload, initial = self.establish()
        self.assertEqual(initial["allocation"]["status"], "unavailable")
        self.assertEqual(initial["execution"]["status"], "unavailable")
        body = self.intent(payload["person_ref"])
        original = self.submit(body)
        self.assertIsNone(self.lookup(body["id"]).get("application_result"))
        self.publish(7)
        bound = self.tick()
        applied = self.lookup(body["id"])["application_result"]
        self.assertEqual(applied["status"], "applied", applied)
        self.assertEqual(applied["decision_ref"], bound["id"])
        self.assertNotEqual(bound["scene"]["example_ref"], initial["scene"]["example_ref"])
        self.assertEqual(bound["scene"]["episode_id"], initial["scene"]["episode_id"])
        self.publish(8)
        moved = self.tick()
        for field in ("scene_id", "episode_id", "example_ref", "profile_ref", "nominal", "selected_at"):
            self.assertEqual(moved["scene"][field], bound["scene"][field], field)
        self.assertEqual(self.submit(body), original)
        self.assertEqual(self.lookup(body["id"])["application_result"], applied)
        self.assertFalse(moved["actuated"])
        self.assertEqual(moved["display_lease"]["status"], "withdrawn")

    def test_explicit_unbinding_returns_to_approved_anonymous_baseline(self):
        payload, initial = self.establish()
        self.submit(self.intent(payload["person_ref"]))
        self.publish(7)
        bound = self.tick()
        unbind = self.intent(payload["person_ref"], kind="unbind_profile", ident="synthetic-profile-unbind")
        self.submit(unbind)
        self.publish(8)
        anonymous = self.tick()
        self.assertEqual(self.lookup(unbind["id"])["application_result"]["status"], "applied")
        self.assertEqual(anonymous["scene"]["example_ref"], initial["scene"]["example_ref"])
        self.assertNotEqual(anonymous["scene"]["scene_id"], bound["scene"]["scene_id"])
        self.assertEqual(anonymous["scene"]["episode_id"], bound["scene"]["episode_id"])

    def test_future_track_binding_is_rejected_and_cannot_create_engagement(self):
        payload = self.publish(1)
        self.probe.command("start", config=self.config, t=self.now)
        target = copy.deepcopy(payload["person_ref"])
        target["track_epoch"] = "never-observed-track"
        body = self.intent(target)
        self.submit(body)
        decision = self.tick()
        self.assertEqual(self.lookup(body["id"])["application_result"]["status"], "rejected")
        self.assertEqual(decision["scene"]["status"], "unavailable")

    def test_fresh_cheap_presence_requests_separate_background_without_task_episode(self):
        self.publish(1, kind="person_presence", value="present")
        self.probe.command("start", config=self.config, t=self.now)
        decision = self.tick()
        self.assertEqual(decision["background"]["status"], "requested", decision)
        self.assertEqual(decision["background"]["kind"], "illuminate_presence")
        self.assertEqual(decision["scene"]["status"], "unavailable")
        self.assertEqual(decision["goal"]["status"], "unavailable")
        self.assertEqual(decision["allocation"]["status"], "unavailable")
        self.now += 11
        stale = self.tick()
        self.assertEqual(stale["background"]["status"], "unavailable")
        self.assertEqual(stale["scene"]["status"], "unavailable")

    def test_registered_geometry_moves_and_refinement_cannot_reselect_the_scene(self):
        _, first = self.establish()
        self.publish(7)
        near = geometry("synthetic-scene-geometry-7", seq=7)
        self.publish_payload(near)
        mapped = self.tick()
        self.assertEqual(len(mapped["goal"]["geometry_refs"]), 1)
        self.publish(8)
        moved = geometry("synthetic-scene-geometry-8", seq=8)
        moved["parts"]["work"]["shape"]["center_m"][0] += .5
        self.publish_payload(moved)
        refined = fact("synthetic-scene-refinement-8", seq=8)
        refined.update(kind="activity_refinement", value="reading")
        refined["head"]["key"]["predicate"] = "activity_refinement"
        self.publish_payload(refined)
        after = self.tick()
        self.assertNotEqual(after["goal"]["geometry_refs"], mapped["goal"]["geometry_refs"])
        for field in ("scene_id", "episode_id", "example_ref", "selected_at", "nominal"):
            self.assertEqual(first["scene"][field], after["scene"][field], field)
        self.assertEqual(after["allocation"]["status"], "unavailable")

    def test_restart_preserves_selected_profile_scene_and_nominal_without_redwell(self):
        payload, _ = self.establish()
        self.submit(self.intent(payload["person_ref"]))
        self.publish(7)
        selected = self.tick()
        self.probe.command("stop")
        self.publish(8)
        self.probe.command("start", config=self.config, t=self.now)
        restored = self.tick()
        for field in ("scene_id", "episode_id", "example_ref", "selected_at", "nominal"):
            self.assertEqual(restored["scene"][field], selected["scene"][field], field)

    def test_lost_commit_ack_does_not_apply_binding_twice_or_reselect(self):
        payload, _ = self.establish()
        body = self.intent(payload["person_ref"])
        self.submit(body)
        self.publish(7)
        self.probe.command("fault", mode="lost_ack")
        selected = self.tick()
        terminal = self.lookup(body["id"])["application_result"]
        self.publish(8)
        later = self.tick()
        self.assertEqual(self.lookup(body["id"])["application_result"], terminal)
        self.assertEqual(selected["scene"]["scene_id"], later["scene"]["scene_id"])

    def test_source_gap_commits_no_fresh_task_then_recovery_keeps_short_gap_scene(self):
        payload, _ = self.establish()
        self.submit(self.intent(payload["person_ref"]))
        self.publish(7)
        selected = self.tick()
        self.now += 10
        gap = self.tick()
        self.assertEqual(gap["goal"]["status"], "unavailable")
        self.publish(18)
        recovered = self.tick()
        self.assertEqual(recovered["scene"]["scene_id"], selected["scene"]["scene_id"])

    def test_reused_pid_in_new_epoch_does_not_inherit_personal_profile(self):
        payload, first = self.establish()
        self.submit(self.intent(payload["person_ref"]))
        self.publish(7)
        personal = self.tick()
        for seq in range(8, 14):
            self.publish(seq, track="synthetic-new-occupant")
            current = self.tick()
        self.assertEqual(current["scene"]["status"], "selected", current)
        self.assertEqual(current["scene"]["example_ref"], first["scene"]["example_ref"])
        self.assertNotEqual(current["scene"]["scene_id"], personal["scene"]["scene_id"])

    def test_geometry_only_current_person_can_bind_without_manufacturing_an_episode(self):
        payload = geometry("synthetic-geometry-only-person", seq=1)
        self.publish_payload(payload)
        body = self.intent(payload["person_ref"])
        self.submit(body)
        self.probe.command("start", config=self.config, t=self.now)
        decision = self.tick()
        self.assertEqual(self.lookup(body["id"])["application_result"]["status"], "applied")
        self.assertEqual(decision["scene"]["status"], "unavailable")
        self.assertEqual(decision["goal"]["status"], "unavailable")

    def test_accepted_intent_that_expires_before_consumption_is_durably_rejected(self):
        payload = self.publish(1)
        body = self.intent(payload["person_ref"])
        body["valid_until"] = self.now + 1
        original = self.submit(body)
        self.probe.command("start", config=self.config, t=self.now)
        self.now += 2
        decision = self.tick()
        result = self.lookup(body["id"])
        self.assertEqual(result["receipt"], original)
        self.assertEqual(result["application_result"]["status"], "rejected")
        self.assertEqual(result["application_result"]["reason"], "profile_intent_expired")
        self.assertEqual(decision["scene"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()

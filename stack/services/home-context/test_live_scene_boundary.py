"""Profile/scene authority tests through real HTTP and the supported Node worker."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from journal import Journal, PREFIX
from live_test_helpers import LiveHttp, NOW, PROFILE, SCOPE, barrier, event, fact, publication_ref, receipt_resolver
from live_worker_test_helpers import WORKER_ID, WORKER_TOKEN, WorkerProbe
from live_scene_test_helpers import HUMAN_TOKEN, intent, scene_worker_fixture


class LiveSceneBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="synthetic-scene-boundary-", dir=Path(__file__).parent)
        self.addCleanup(self.work.cleanup)
        self.now = NOW + .2
        self.service, self.grants, self.config = scene_worker_fixture()
        self.journal = Journal(Path(self.work.name) / "synthetic.sqlite", grants=self.grants, profile=PROFILE,
                               clock=lambda: self.now, live_config=self.service, live_receipt_resolver=receipt_resolver)
        self.addCleanup(self.journal.close)
        self.http = LiveHttp(self.journal)
        self.addCleanup(self.http.close)
        self.config["service_url"] = f"http://127.0.0.1:{self.http.server.server_port}"
        self.probe = WorkerProbe()
        self.addCleanup(self.probe.close)
        self.scope = "?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"]
        self.head = None

    def publish(self, seq):
        self.now = NOW + seq - 1 + .2
        payload = fact("synthetic-scene-boundary-fact-" + str(seq), seq=seq)
        if self.head:
            payload["head"]["supersedes"] = publication_ref(self.head)
        status, body = self.http.request("POST", PREFIX + "/events", event(payload))
        self.assertEqual(status, 200, body)
        self.head = body

    def establish(self):
        for seq in range(1, 7):
            self.publish(seq)
            if seq == 1:
                self.probe.command("start", config=self.config, t=self.now)
            result = self.probe.command("tick", t=self.now)
            self.assertTrue(result["decisions"], result)
        return result["decisions"][-1]

    def prepare(self):
        self.probe.command("fault", mode="before_commit")
        result = self.probe.command("tick", t=self.now)
        self.assertEqual(result["decisions"], [], result)
        return json.loads(result["commit_posts"][-1])

    def post_commit(self, commit):
        return self.http.request("POST", PREFIX + "/worker-commits", commit, token=WORKER_TOKEN)

    def test_unknown_actor_and_changed_payload_do_not_rewrite_accepted_intent(self):
        declaration = intent("synthetic-retry-bind", at=self.now)
        changed = copy.deepcopy(declaration)
        changed["actor_ref"] = "another-owner"
        status, body = self.http.request("POST", PREFIX + "/intent-events", changed, token=HUMAN_TOKEN)
        self.assertEqual(status, 403, body)
        status, original = self.http.request("POST", PREFIX + "/intent-events", declaration, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, original)
        changed = copy.deepcopy(declaration)
        changed["value"]["example_ref"]["digest"] = "0" * 64
        status, body = self.http.request("POST", PREFIX + "/intent-events", changed, token=HUMAN_TOKEN)
        self.assertEqual(status, 409, body)
        self.now += 1000
        status, retried = self.http.request("POST", PREFIX + "/intent-events", declaration, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, retried)
        self.assertEqual(retried, original)

    def test_private_checkpoint_and_input_cut_are_not_human_read_capabilities(self):
        for suffix in ("/worker-commits/" + WORKER_ID, "/live-input-cut"):
            status, body = self.http.request("GET", PREFIX + suffix + self.scope, token=HUMAN_TOKEN)
            self.assertEqual(status, 403, body)

    def test_public_catalog_contains_exact_approved_records_without_acquisition(self):
        status, catalog = self.http.request("GET", PREFIX + "/live-scene-catalog" + self.scope, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, catalog)
        self.assertEqual(catalog["catalog"], self.config["scene_policy"]["catalog"])
        self.assertEqual(catalog["manifest_ref"], self.config["manifest_ref"])

    def test_unqualified_target_cannot_be_claimed_applied_by_modified_worker_commit(self):
        self.publish(1)
        declaration = intent("synthetic-wrong-track", at=self.now)
        declaration["target"]["track_epoch"] = "unseen-track"
        status, body = self.http.request("POST", PREFIX + "/intent-events", declaration, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, body)
        self.probe.command("start", config=self.config, t=self.now)
        commit = self.prepare()
        commit["publications"][-1]["payload"]["intent_results"][0]["status"] = "applied"
        commit["checkpoint"]["state"]["intent_settlements"][0]["status"] = "applied"
        status, body = self.post_commit(commit)
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "invalid_application_result")
        route = PREFIX + "/intent-events/" + declaration["id"] + self.scope
        status, pending = self.http.request("GET", route, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, pending)
        self.assertNotIn("application_result", pending)

    def test_applied_bind_cannot_omit_the_binding_from_its_atomic_checkpoint(self):
        self.publish(1)
        declaration = intent("synthetic-no-effect-bind", at=self.now)
        status, body = self.http.request("POST", PREFIX + "/intent-events", declaration, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, body)
        self.probe.command("start", config=self.config, t=self.now)
        commit = self.prepare()
        commit["checkpoint"]["state"]["scene_and_corrections"]["state"]["bindings"] = []
        status, body = self.post_commit(commit)
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "invalid_application_result")

    def test_motion_cannot_reselect_scene_in_a_modified_atomic_commit(self):
        selected = self.establish()
        self.assertEqual(selected["scene"]["status"], "selected")
        self.publish(7)
        commit = self.prepare()
        decision = commit["publications"][-1]["payload"]
        decision["scene"]["scene_id"] = "unapproved-new-composition"
        decision["goal"]["scene_id"] = "unapproved-new-composition"
        commit["checkpoint"]["state"]["scene_and_corrections"]["state"]["selections"][0]["scene"]["scene_id"] = "unapproved-new-composition"
        status, body = self.post_commit(commit)
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "scene_continuity_conflict")

    def test_personal_binding_is_retained_through_short_source_expiry_without_new_goal(self):
        declaration = intent("synthetic-gap-bind", at=self.now)
        status, body = self.http.request("POST", PREFIX + "/intent-events", declaration, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, body)
        selected = self.establish()
        self.assertEqual(selected["scene"]["profile_ref"], declaration["value"]["profile_ref"])
        self.now = NOW + 15.2
        result = self.probe.command("tick", t=self.now)
        self.assertTrue(result["decisions"], result)
        held = result["decisions"][-1]
        self.assertEqual(held["scene"]["scene_id"], selected["scene"]["scene_id"])
        self.assertEqual(held["goal"]["status"], "unavailable")

    def personal_episode(self, event_id):
        declaration = intent(event_id, at=self.now)
        status, body = self.http.request("POST", PREFIX + "/intent-events", declaration, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, body)
        decision = self.establish()
        self.assertEqual(decision["scene"]["profile_ref"], declaration["value"]["profile_ref"])
        return decision

    def assert_binding_withdrawn(self):
        result = self.probe.command("tick", t=self.now)
        self.assertEqual(len(result["decisions"]), 1, {key: result[key] for key in ("statuses", "http_errors")})
        decision = result["decisions"][-1]
        self.assertEqual(decision["scene"]["status"], "unavailable", decision)
        self.assertEqual(decision["goal"]["status"], "unavailable", decision)
        status, checkpoint = self.http.request("GET", PREFIX + "/worker-commits/" + WORKER_ID + self.scope, token=WORKER_TOKEN)
        self.assertEqual(status, 200, checkpoint)
        self.assertEqual(checkpoint["checkpoint"]["state"]["scene_and_corrections"]["state"]["bindings"], [])

    def test_source_barrier_clears_personal_binding_on_the_next_committed_tick(self):
        self.personal_episode("synthetic-source-barrier-personal")
        self.now = NOW + 7
        control = barrier([self.head], event_id="synthetic-profile-source-barrier", at=self.now)
        status, body = self.http.request("POST", PREFIX + "/events", event(control))
        self.assertEqual(status, 200, body)
        self.assert_binding_withdrawn()

    def test_room_revocation_clears_personal_binding_on_the_next_committed_tick(self):
        self.personal_episode("synthetic-room-revocation-personal")
        self.now = NOW + 7
        payload = {"schema": "home-context-revocation-1", "version": 1, "id": "synthetic-profile-room-revocation", **SCOPE,
                   "source_id": "*", "revision": "*", "reason": "owner_revoked", "available_at": self.now}
        control = event(fact("revocation-envelope", seq=7))
        control.update(id=payload["id"], observed_at=self.now, available_at=self.now, valid_until=self.now + 1,
                       payload_type=payload["schema"], payload=payload)
        status, body = self.http.request("POST", PREFIX + "/revocations", control)
        self.assertEqual(status, 200, body)
        self.assert_binding_withdrawn()

    def test_uncertain_scene_cannot_refresh_its_original_gap_anchor(self):
        self.establish()
        self.now = NOW + 15.2
        held = self.probe.command("tick", t=self.now)
        self.assertEqual(len(held["decisions"]), 1)
        self.now = NOW + 16.2
        commit = self.prepare()
        selection = commit["checkpoint"]["state"]["scene_and_corrections"]["state"]["selections"][0]
        selection["gap_since"] = self.now
        selection["scene"]["valid_until"] = self.now + 3
        commit["publications"][-1]["payload"]["scene"]["valid_until"] = self.now + 3
        status, body = self.post_commit(commit)
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "invalid_scene")

    def test_personal_scene_cannot_outlive_its_explicit_binding(self):
        declaration = intent("synthetic-short-profile-binding", at=self.now)
        declaration["valid_until"] = NOW + 6.7
        status, body = self.http.request("POST", PREFIX + "/intent-events", declaration, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, body)
        self.establish()
        self.publish(7)
        commit = self.prepare()
        scene = commit["publications"][-1]["payload"]["scene"]
        scene["valid_until"] = self.now + 1
        commit["checkpoint"]["state"]["scene_and_corrections"]["state"]["selections"][0]["scene"]["valid_until"] = scene["valid_until"]
        status, body = self.post_commit(commit)
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "invalid_scene")

    def test_concurrent_engagement_cannot_be_changed_to_a_primary_scene_by_the_worker(self):
        self.establish()
        second = None
        for seq in range(7, 13):
            self.now = NOW + seq - 1 + .2
            payload = fact("synthetic-second-scene-person-" + str(seq), seq=seq)
            payload["person_ref"].update(pid=2, track_epoch="synthetic-second-track")
            payload["subject_ref"].update(subject_id="synthetic-second-work", subject_epoch="synthetic-second-subject")
            payload["head"]["key"].update(person_ref=copy.deepcopy(payload["person_ref"]), subject_ref=copy.deepcopy(payload["subject_ref"]))
            payload["head"]["supersedes"] = publication_ref(second) if second else None
            status, second = self.http.request("POST", PREFIX + "/events", event(payload))
            self.assertEqual(status, 200, second)
            result = self.probe.command("tick", t=self.now)
            self.assertEqual(len(result["decisions"]), 1)
        self.assertEqual(result["decisions"][0]["scene"]["reason"], "concurrent_scene_unresolved")
        self.now += 1
        commit = self.prepare()
        selection = commit["checkpoint"]["state"]["scene_and_corrections"]["state"]["selections"][0]
        commit["publications"][-1]["payload"]["scene"] = copy.deepcopy(selection["scene"])
        status, body = self.post_commit(commit)
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "concurrent_scene_unresolved")

    def test_human_bind_has_durable_acceptance_then_separate_committed_application(self):
        status, body = self.http.request("POST", PREFIX + "/events", event(fact()))
        self.assertEqual(status, 200, body)
        declaration = intent("synthetic-boundary-bind", at=self.now)
        status, accepted = self.http.request("POST", PREFIX + "/intent-events", declaration, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, accepted)
        route = PREFIX + "/intent-events/" + declaration["id"] + self.scope
        status, pending = self.http.request("GET", route, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, pending)
        self.assertEqual(pending["receipt"], accepted)
        self.assertNotIn("application_result", pending)
        self.probe.command("start", config=self.config, t=self.now)
        result = self.probe.command("tick", t=self.now)
        self.assertEqual(len(result["decisions"]), 1, result)
        status, applied = self.http.request("GET", route, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, applied)
        self.assertEqual(applied["receipt"], accepted)
        self.assertEqual(applied["application_result"]["status"], "applied")
        self.assertEqual(applied["application_result"]["decision_ref"], result["decisions"][0]["id"])


if __name__ == "__main__":
    unittest.main()

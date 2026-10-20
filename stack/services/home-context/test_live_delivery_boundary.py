"""Adversarial delivery claims through authenticated HTTP, never private DB edits."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from journal import Journal, PREFIX, digest
from live_delivery_test_helpers import delivery_worker_fixture, delivery_geometry
from live_test_helpers import LiveHttp, NOW, PROFILE, SCOPE, event, fact, publication_ref
from live_worker_test_helpers import WORKER_ID, WORKER_TOKEN, WorkerProbe, worker_receipt_resolver


class LiveDeliveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix="synthetic-delivery-boundary-", dir=Path(__file__).parent)
        self.addCleanup(work.cleanup)
        self.now, self.seq, self.started, self.heads = NOW + .2, 0, False, {}
        self.directory = Path(work.name)
        self.service, self.grants, self.config = delivery_worker_fixture()
        self.journal = Journal(self.directory / "synthetic.sqlite", grants=self.grants, profile=PROFILE,
                               clock=lambda: self.now, live_config=self.service,
                               live_receipt_resolver=worker_receipt_resolver)
        self.addCleanup(self.journal.close)
        self.http = LiveHttp(self.journal)
        self.addCleanup(self.http.close)
        self.config["service_url"] = f"http://127.0.0.1:{self.http.server.server_port}"
        self.probe = WorkerProbe()
        self.addCleanup(self.probe.close)
        self.scope = "?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"]

    def publish(self, payload):
        key = str(payload["head"]["key"])
        if key in self.heads:
            payload["head"]["supersedes"] = publication_ref(self.heads[key])
        status, result = self.http.request("POST", PREFIX + "/events", event(payload))
        self.assertEqual(status, 200, result)
        self.heads[key] = result

    def advance(self, mode="supplied", prepare=False):
        self.seq += 1
        self.now = NOW + self.seq - 1 + .2
        self.publish(fact("synthetic-boundary-fact-" + str(self.seq), seq=self.seq))
        self.publish(delivery_geometry(self.seq, mode))
        if not self.started:
            self.probe.command("start", config=self.config, t=self.now)
            self.started = True
        if prepare:
            self.probe.command("fault", mode="before_commit")
        result = self.probe.command("tick", t=self.now)
        if prepare:
            self.assertFalse(result["decisions"], result)
            return json.loads(result["commit_posts"][-1])
        self.assertEqual(len(result["decisions"]), 1, result)
        return result["decisions"][0]

    def establish(self):
        for _ in range(6):
            decision = self.advance()
        self.assertEqual(decision["execution"]["status"], "virtual", decision)
        return decision

    def assert_rejected(self, commit, code):
        status, result = self.http.request("POST", PREFIX + "/worker-commits", commit, token=WORKER_TOKEN)
        self.assertEqual(status, 409, result)
        self.assertEqual(result["error"]["code"], code, result)

    def prepared(self):
        self.establish()
        return self.advance(prepare=True)

    @staticmethod
    def decision(commit):
        return commit["publications"][-1]["payload"]

    def inject_prior_delivery(self, commit, prior, prior_state):
        """An adversarial worker reuses a once-valid claim against new evidence."""
        decision = self.decision(commit)
        for key in ("allocation", "execution", "display_lease"):
            decision[key] = copy.deepcopy(prior[key])
        t = decision["t"]
        decision["allocation"]["geometry_cut_digest"] = decision["input_cut"]["cut_digest"]
        lease = decision["display_lease"]
        lease.update(sample_at=t, geometry_refs=copy.deepcopy(decision["input_cut"]["geometry_refs"]),
                     protection_refs=copy.deepcopy(decision["input_cut"]["geometry_refs"]))
        execution = decision["execution"]
        execution.update(sample_at=t, interval_start=t, interval_end=t, continuity="continuous")
        execution["samples"] = [{"t": t, "readback": copy.deepcopy(execution["readback"])}]
        state = commit["checkpoint"]["state"]["virtual_execution"]["state"]
        state["plan"] = copy.deepcopy(prior_state["plan"])
        state["last_readback"] = copy.deepcopy(execution["readback"][0])
        state["last_samples"] = copy.deepcopy(execution["samples"])
        state["last_valid_until"] = lease["valid_until"]

    def prior_checkpoint(self):
        status, result = self.http.request("GET", PREFIX + "/worker-commits/" + WORKER_ID + self.scope, token=WORKER_TOKEN)
        self.assertEqual(status, 200, result)
        return result["checkpoint"]["state"]["virtual_execution"]["state"]

    def test_scene_nominal_cannot_be_replaced_by_the_allocator(self):
        commit = self.prepared()
        self.decision(commit)["allocation"]["assignments"][0]["nominal"]["brightness_fraction"] = .99
        self.assert_rejected(commit, "invalid_delivery_assignment")

    def test_uncommissioned_delivery_assumptions_fail_before_opening_a_ledger(self):
        variants = [
            ("missing_collision", lambda policy: policy["room"].pop("collision_ref")),
            ("incomplete_room", lambda policy: policy["room"].update(complete=False)),
            ("dark_residual", lambda policy: policy["fixture"]["optics"].update(full_dark_upper_fraction=.01)),
            ("reflected_mount", lambda policy: policy["fixture"].update(local_to_room=[-1, 0, 0, 0, 1, 0, 0, 0, 1])),
            ("unknown_field", lambda policy: policy.update(guess_clear=True)),
        ]
        for name, mutate in variants:
            with self.subTest(name=name):
                service = copy.deepcopy(self.service)
                mutate(service["manifest"]["worker"]["delivery_policy"])
                service["manifest_ref"]["digest"] = digest(service["manifest"])
                path = self.directory / (name + ".sqlite")
                with self.assertRaises(ValueError):
                    Journal(path, grants=self.grants, profile=PROFILE, clock=lambda: self.now,
                            live_config=service, live_receipt_resolver=worker_receipt_resolver)
                self.assertFalse(path.exists(), "invalid commissioning must not open a durable journal")

    def test_endpoint_proposal_cannot_be_published_as_achieved_state(self):
        commit = self.prepared()
        decision = self.decision(commit)
        sample = decision["execution"]["readback"][0]
        sample.update(pan_deg=decision["allocation"]["assignments"][0]["pan_deg"], brightness_fraction=.4, phase="lit")
        decision["execution"]["samples"][0]["readback"] = [copy.deepcopy(sample)]
        state = commit["checkpoint"]["state"]["virtual_execution"]["state"]
        state["last_readback"] = copy.deepcopy(sample)
        state["last_samples"] = copy.deepcopy(decision["execution"]["samples"])
        self.assert_rejected(commit, "invalid_execution_sample")

    def test_future_execution_sample_is_not_achieved_state(self):
        commit = self.prepared()
        self.decision(commit)["execution"]["samples"][0]["t"] += .1
        self.assert_rejected(commit, "invalid_execution_sample")

    def test_old_trajectory_cannot_restart_dwell_or_motion_each_tick(self):
        commit = self.prepared()
        commit["checkpoint"]["state"]["virtual_execution"]["state"]["plan"]["started_at"] = self.now
        self.assert_rejected(commit, "invalid_execution_plan")

    def test_independent_conservative_coverage_cannot_be_forged(self):
        commit = self.prepared()
        coverage = self.decision(commit)["allocation"]["candidates"][0]["coverage"]
        coverage["clear_fraction"], coverage["blocked_fraction"] = .9, .1
        self.assert_rejected(commit, "invalid_delivery_coverage")

    def test_display_lease_cannot_outlive_the_atomic_scene_and_support(self):
        commit = self.prepared()
        decision = self.decision(commit)
        decision["display_lease"]["valid_until"] = self.now + 100
        self.assert_rejected(commit, "invalid_display_lease")

    def test_lease_cannot_omit_protection_evidence(self):
        commit = self.prepared()
        self.decision(commit)["display_lease"]["protection_refs"] = []
        self.assert_rejected(commit, "invalid_delivery_reference")

    def test_proposal_cannot_rebind_registered_fixture_pose(self):
        commit = self.prepared()
        decision = self.decision(commit)
        decision["allocation"]["assignments"][0]["fixture_pose_ref"]["digest"] = "0" * 64
        self.assert_rejected(commit, "invalid_delivery_assignment")

    def test_head_in_beam_is_rejected_even_when_worker_reuses_previous_clear_claim(self):
        prior = self.establish()
        prior_state = self.prior_checkpoint()
        commit = self.advance("head_in_beam", prepare=True)
        self.assertEqual(self.decision(commit)["execution"]["status"], "unavailable")
        self.inject_prior_delivery(commit, prior, prior_state)
        self.assert_rejected(commit, "invalid_delivery_protection")

    def test_missing_protection_is_not_assumed_clear_by_a_forged_worker(self):
        prior = self.establish()
        prior_state = self.prior_checkpoint()
        commit = self.advance("missing_protection", prepare=True)
        self.inject_prior_delivery(commit, prior, prior_state)
        self.assert_rejected(commit, "invalid_delivery_geometry")

    def test_body_occlusion_cannot_be_overridden_by_a_previous_clear_central_ray(self):
        prior = self.establish()
        prior_state = self.prior_checkpoint()
        commit = self.advance("body_blocked", prepare=True)
        self.inject_prior_delivery(commit, prior, prior_state)
        self.assert_rejected(commit, "invalid_delivery_coverage")

    def test_known_person_ledger_cannot_disappear_from_a_worker_checkpoint(self):
        commit = self.prepared()
        commit["checkpoint"]["state"]["virtual_execution"]["state"]["known_persons"] = []
        self.assert_rejected(commit, "invalid_delivery_hazards")

    def test_expired_checkpoint_restart_cannot_choose_a_near_endpoint_initial_pose(self):
        prior = self.establish()
        self.seq += 5
        commit = self.advance(prepare=True)
        decision = self.decision(commit)
        state = commit["checkpoint"]["state"]["virtual_execution"]["state"]
        state["plan"]["started_at"] = self.now
        state["plan"]["from_pan_deg"] = state["plan"]["to_pan_deg"] - .01
        state["execution_starts"] += 1
        decision["execution"]["continuity"] = "discontinuous_dark_reset"
        self.assert_rejected(commit, "invalid_execution_plan")

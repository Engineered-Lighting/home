"""Actual Node worker against the real HTTP journal with fictional inputs only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from journal import Journal, Limits, PREFIX
from live_test_helpers import LiveHttp, NOW, PROFILE, SCOPE, TOKEN, barrier, event, fact, geometry, publication_ref, unavailable
from live_worker_test_helpers import (WORKER_ID, WORKER_TOKEN, WorkerProbe, second_source_fact,
                                     worker_fixture, worker_receipt_resolver)


class HomeLiveWorkerFixture(unittest.TestCase):
    journal_limits = Limits()
    second_source = False

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="synthetic-live-worker-", dir=Path(__file__).parent)
        self.addCleanup(self.work.cleanup)
        self.now = NOW + .2
        self.service, self.grants, self.config = worker_fixture(second_source=self.second_source)
        self.journal = Journal(Path(self.work.name) / "synthetic.sqlite", grants=self.grants, profile=PROFILE,
                               clock=lambda: self.now, live_config=self.service, live_receipt_resolver=worker_receipt_resolver,
                               limits=self.journal_limits)
        self.addCleanup(self.journal.close)
        self.http = LiveHttp(self.journal)
        self.addCleanup(self.http.close)
        self.config["service_url"] = f"http://127.0.0.1:{self.http.server.server_port}"
        self.probe = WorkerProbe()
        self.addCleanup(self.probe.close)
        self.last = None
        self.heads = {}

    def publish(self, seq):
        self.now = NOW + seq - 1 + .2
        payload = fact("synthetic-worker-fact-" + str(seq), seq=seq)
        return self.publish_payload(payload)

    def publish_payload(self, payload):
        key = json.dumps(payload["head"]["key"], sort_keys=True)
        if key in self.heads:
            payload["head"]["supersedes"] = publication_ref(self.heads[key])
        status, receipt = self.http.request("POST", PREFIX + "/events", event(payload))
        self.assertEqual(status, 200, receipt)
        self.last = receipt
        self.heads[key] = receipt
        return receipt

    def checkpoint(self):
        route = PREFIX + "/worker-commits/" + WORKER_ID + "?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"]
        status, body = self.http.request("GET", route, token=WORKER_TOKEN)
        self.assertEqual(status, 200, body)
        return body

    def current(self, kind):
        status, snapshot = self.http.request("GET", PREFIX + "/snapshot?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"], token=WORKER_TOKEN)
        self.assertEqual(status, 200, snapshot)
        return [p["payload"] for p in snapshot["items"] if p["payload_type"] == kind]

    def establish(self):
        for seq in range(1, 7):
            self.publish(seq)
            if seq == 1:
                self.probe.command("start", config=self.config, t=self.now)
            result = self.probe.command("tick", t=self.now)
            self.assertEqual(len(result["decisions"]), 1, result)
        value = self.current("home-live-engagement-state-1")[0]
        self.assertEqual(value["state"], "supported")
        return value


class LiveWorkerIntegrationTests(HomeLiveWorkerFixture):

    def test_progressing_interaction_commits_generic_engagement_and_checkpoint(self):
        self.publish(1)
        self.probe.command("start", config=self.config, t=self.now)
        result = self.probe.command("tick", t=self.now)
        self.assertTrue(result["decisions"], result)
        for seq in range(2, 7):
            self.publish(seq)
            result = self.probe.command("tick", t=self.now)
        saved = self.checkpoint()
        self.assertEqual(saved["checkpoint"]["state"]["semantic_seq"], 6)
        status, snapshot = self.http.request("GET", PREFIX + "/snapshot?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"], token=WORKER_TOKEN)
        self.assertEqual(status, 200, snapshot)
        engagements = [p["payload"] for p in snapshot["items"] if p["payload_type"] == "home-live-engagement-state-1"]
        self.assertEqual(len(engagements), 1, snapshot)
        self.assertEqual(engagements[0]["state"], "supported")
        self.assertEqual(engagements[0]["refinement"]["activity"], "unknown")
        decision = result["decisions"][-1]
        self.assertFalse(decision["actuated"])
        self.assertFalse(decision["acceptance_eligible"])
        self.assertEqual(decision["allocation"], {"status": "unavailable", "reason": "not_implemented"})
        self.assertEqual(decision["execution"], {"status": "unavailable", "reason": "not_implemented"})
        self.assertEqual(decision["display_lease"]["status"], "withdrawn")

    def test_repeated_old_observation_under_new_ids_does_not_earn_dwell(self):
        original = fact("original-static-frame", seq=1)
        self.publish_payload(copy.deepcopy(original))
        self.probe.command("start", config=self.config, t=self.now)
        for revision in range(6):
            self.now = NOW + revision + .2
            if revision:
                alias = copy.deepcopy(original)
                alias["id"] = "same-frame-new-id-" + str(revision)
                alias["head"]["position"]["revision"] = revision
                self.publish_payload(alias)
            result = self.probe.command("tick", t=self.now)
            self.assertEqual(len(result["decisions"]), 1, result)
        engagement = self.current("home-live-engagement-state-1")
        self.assertEqual(engagement[0]["state"], "candidate")
        self.assertIsNone(engagement[0]["episode_id"])
        self.assertEqual(engagement[0]["support"]["progression_through_at"], NOW)
        self.assertEqual(set(self.checkpoint()["checkpoint"]["state"]["root_ledger"]["times"].values()), {NOW})

    def test_proximity_open_material_activity_and_active_pc_never_establish_use(self):
        predicates = [("hand_material_relation", "near"), ("material_state", "open"),
                      ("activity_refinement", "reading"), ("pc_input_interaction", "active_unattributed")]
        for seq in range(1, 7):
            self.now = NOW + seq - 1 + .2
            for kind, value in predicates:
                payload = fact("confuser-" + kind + "-" + str(seq), seq=seq)
                payload.update(kind=kind, value=value, reason="qualified_per_observation_evidence")
                payload["head"]["key"]["predicate"] = kind
                self.publish_payload(payload)
            if seq == 1:
                self.probe.command("start", config=self.config, t=self.now)
            result = self.probe.command("tick", t=self.now)
            self.assertEqual(len(result["decisions"]), 1, result)
        engagement = self.current("home-live-engagement-state-1")[0]
        self.assertEqual(engagement["state"], "unknown")
        self.assertIsNone(engagement["episode_id"])
        self.assertIsNone(engagement["support"])
        self.assertEqual(engagement["refinement"]["activity"], "unknown")

    def test_failure_before_commit_exposes_nothing_and_retries_identical_bytes(self):
        self.publish(1)
        self.probe.command("start", config=self.config, t=self.now)
        self.probe.command("fault", mode="before_commit")
        failed = self.probe.command("tick", t=self.now)
        self.assertFalse(failed["decisions"], failed)
        self.assertTrue(any(s["status"] == "commit_unconfirmed" for s in failed["statuses"]), failed)
        saved = self.checkpoint()
        self.assertEqual(saved["context_seq"], 0)
        self.assertIsNone(saved["checkpoint"])
        self.assertEqual(self.current("home-live-engagement-state-1"), [])
        self.assertEqual(self.current("home-live-shadow-decision-1"), [])
        self.now += 1
        recovered = self.probe.command("tick", t=self.now)
        self.assertEqual(recovered["commit_posts"][0], recovered["commit_posts"][1])
        self.assertEqual([d["seq"] for d in recovered["decisions"]], [1, 2], recovered)
        self.assertEqual(self.checkpoint()["context_seq"], 2)

    def test_lost_ack_reconciles_real_durable_receipt_without_duplicate_output(self):
        self.publish(1)
        self.probe.command("start", config=self.config, t=self.now)
        self.probe.command("fault", mode="lost_ack")
        result = self.probe.command("tick", t=self.now)
        self.assertEqual([d["seq"] for d in result["decisions"]], [1], result)
        self.assertEqual(len(result["commit_posts"]), 1)
        committed = json.loads(result["commit_posts"][0])
        route = PREFIX + "/live-worker-commits/" + quote(committed["id"], safe="") + "?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"]
        status, original = self.http.request("GET", route, token=WORKER_TOKEN)
        self.assertEqual(status, 200, original)
        status, retry = self.http.request("POST", PREFIX + "/worker-commits", committed, token=WORKER_TOKEN)
        self.assertEqual(status, 200, retry)
        self.assertEqual(retry, original)
        self.assertEqual(self.checkpoint()["context_seq"], 1)
        self.assertEqual(len(self.current("home-live-shadow-decision-1")), 1)

    def test_process_crash_before_commit_recovers_only_durable_progress(self):
        self.publish(1)
        self.probe.command("start", config=self.config, t=self.now)
        self.probe.command("fault", mode="before_commit")
        pending = self.probe.command("tick", t=self.now)
        self.assertFalse(pending["decisions"], pending)
        self.assertEqual(self.checkpoint()["context_seq"], 0)
        self.probe.process.kill()
        self.probe.process.wait(5)
        self.probe.close()
        self.probe = WorkerProbe()
        self.addCleanup(self.probe.close)
        self.now += 1
        self.probe.command("start", config=self.config, t=self.now)
        result = self.probe.command("tick", t=self.now)
        self.assertEqual([d["seq"] for d in result["decisions"]], [1], result)
        self.assertEqual(self.checkpoint()["context_seq"], 1)
        abandoned = json.loads(pending["commit_posts"][0])
        durable = json.loads(result["commit_posts"][0])
        self.assertNotEqual(abandoned["id"], durable["id"])
        self.assertEqual(len(self.current("home-live-shadow-decision-1")), 1)

    def test_compatible_restart_keeps_episode_and_ticks_expiry_without_new_input(self):
        original = self.establish()
        ledger = self.checkpoint()["checkpoint"]["state"]["root_ledger"]
        self.probe.command("stop")
        self.now += .1
        self.probe.command("start", config=self.config, t=self.now)
        result = self.probe.command("tick", t=self.now)
        self.assertEqual([d["seq"] for d in result["decisions"]], [7], result)
        restarted = self.current("home-live-engagement-state-1")[0]
        self.assertEqual(restarted["state"], "supported")
        self.assertEqual(restarted["episode_id"], original["episode_id"])
        self.assertEqual(self.checkpoint()["checkpoint"]["state"]["root_ledger"], ledger)
        self.now = NOW + 15
        expired = self.probe.command("tick", t=self.now)
        self.assertEqual([d["seq"] for d in expired["decisions"]], [8], expired)
        suspended = self.current("home-live-engagement-state-1")[0]
        self.assertEqual(suspended["state"], "suspended")
        self.assertEqual(suspended["episode_id"], original["episode_id"])
        self.assertIsNone(suspended["support"])
        self.now += 25
        no_negative = self.probe.command("tick", t=self.now)
        self.assertEqual([d["seq"] for d in no_negative["decisions"]], [9], no_negative)
        self.assertEqual(self.current("home-live-engagement-state-1")[0]["state"], "suspended")

    def test_moving_geometry_preserves_subject_episode_but_track_epoch_does_not(self):
        for seq in range(1, 7):
            self.publish(seq)
            shape = geometry("moving-working-region-" + str(seq), seq=seq)
            shape["parts"]["work"]["shape"]["center_m"][0] += seq / 10
            self.publish_payload(shape)
            if seq == 1:
                self.probe.command("start", config=self.config, t=self.now)
            result = self.probe.command("tick", t=self.now)
            self.assertEqual(len(result["decisions"]), 1, result)
        before = self.current("home-live-engagement-state-1")[0]
        self.assertEqual(before["state"], "supported")
        episode = before["episode_id"]
        self.now += 1
        newer = fact("new-track-person", seq=7)
        newer["person_ref"]["track_epoch"] = "new-track-epoch"
        newer["subject_ref"]["subject_epoch"] = "new-subject-epoch"
        newer["head"]["key"]["person_ref"] = copy.deepcopy(newer["person_ref"])
        newer["head"]["key"]["subject_ref"] = copy.deepcopy(newer["subject_ref"])
        self.publish_payload(newer)
        result = self.probe.command("tick", t=self.now)
        self.assertEqual(len(result["decisions"]), 1, result)
        after = self.current("home-live-engagement-state-1")
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["state"], "candidate")
        self.assertIsNone(after[0]["episode_id"])
        self.assertNotEqual(after[0]["episode_id"], episode)
        self.assertEqual(after[0]["person_ref"]["track_epoch"], "new-track-epoch")

    def test_source_generation_change_cannot_keep_old_dwell_even_with_fresh_same_track(self):
        original = self.establish()
        self.now = NOW + 7
        control = barrier([self.last], at=self.now)
        status, receipt = self.http.request("POST", PREFIX + "/events", event(control))
        self.assertEqual(status, 200, receipt)
        self.assertEqual(self.current("home-live-engagement-state-1"), [])
        self.now = NOW + 8.2
        recovered = fact("fresh-after-source-gap", seq=9, generation=2)
        self.publish_payload(recovered)
        result = self.probe.command("tick", t=self.now)
        self.assertEqual(len(result["decisions"]), 1, result)
        current = self.current("home-live-engagement-state-1")[0]
        self.assertEqual(current["state"], "candidate")
        self.assertIsNone(current["episode_id"])
        self.assertNotEqual(current["episode_id"], original["episode_id"])
        self.assertEqual(current["support"]["progression_start_at"], NOW + 8)

    def test_unavailable_current_head_suspends_and_expiry_does_not_resurrect_old_use(self):
        original = self.establish()
        self.now += 1
        missing = unavailable(fact("current-material-occluded", seq=7), until=NOW + 8)
        self.publish_payload(missing)
        result = self.probe.command("tick", t=self.now)
        self.assertEqual(len(result["decisions"]), 1, result)
        current = self.current("home-live-engagement-state-1")[0]
        self.assertEqual(current["state"], "suspended")
        self.assertEqual(current["episode_id"], original["episode_id"])
        self.now = NOW + 8
        result = self.probe.command("tick", t=self.now)
        self.assertEqual(len(result["decisions"]), 1, result)
        self.assertEqual(self.current("home-live-engagement-state-1")[0]["state"], "suspended")
        self.assertIsNone(self.current("home-live-engagement-state-1")[0]["support"])

    def test_irrelevant_expiry_preserves_support_then_fresh_negative_conflicts_all_clauses(self):
        self.establish()
        self.publish(7)
        cue = fact("short-lived-visible-material", seq=7)
        cue.update(kind="material_state", value="visible_state_unknown", valid_until=NOW + 7,
                   reason="qualified_material_state")
        cue["head"]["key"]["predicate"] = cue["kind"]
        self.publish_payload(cue)
        result = self.probe.command("tick", t=self.now)
        self.assertEqual(len(result["decisions"]), 1, result)
        self.now = NOW + 7.2
        result = self.probe.command("tick", t=self.now)
        self.assertEqual(len(result["decisions"]), 1, result)
        supported = self.current("home-live-engagement-state-1")[0]
        self.assertEqual(supported["state"], "supported")
        self.assertGreater(supported["support"]["basis_valid_until"], cue["valid_until"])
        self.publish(9)
        holding = fact("independent-holding-predicate", seq=9)
        holding.update(kind="hand_material_relation", value="supported_holding", reason="qualified_hand_material_relation")
        holding["head"]["key"]["predicate"] = holding["kind"]
        self.publish_payload(holding)
        negative = fact("fresh-observed-use-ended", seq=9)
        negative.update(kind="negative_engagement", value="observed_use_ended", reason="fresh_covered_use_ending")
        negative["head"]["key"]["predicate"] = negative["kind"]
        self.publish_payload(negative)
        result = self.probe.command("tick", t=self.now)
        self.assertEqual(len(result["decisions"]), 1, result)
        conflicted = self.current("home-live-engagement-state-1")[0]
        self.assertEqual(conflicted["state"], "conflicted")
        self.assertIsNone(conflicted["support"])
        alternative_ids = {ref["id"] for alternative in conflicted["alternatives"] for ref in alternative["evidence_refs"]}
        self.assertTrue({"independent-holding-predicate", "synthetic-worker-fact-9"} <= alternative_ids)

    def test_hidden_source_barrier_between_cut_and_commit_rejects_and_withdraws_current_output(self):
        self.establish()
        self.now = NOW + 7
        control = barrier([self.last], event_id="barrier-after-worker-cut", at=self.now)
        self.probe.command("fault", mode="barrier_before_commit", event=event(control), token=TOKEN)
        result = self.probe.command("tick", t=self.now)
        self.assertFalse(result["decisions"], result)
        self.assertEqual(len(result["fault_receipts"]), 1)
        self.assertTrue(any(row["status"] == 409 and row["body"]["error"]["code"] == "revocation_conflict"
                            for row in result["http_errors"]), result)
        self.assertEqual(self.checkpoint()["context_seq"], 6)
        self.assertEqual(self.current("home-live-engagement-state-1"), [])
        self.assertEqual(self.current("home-live-shadow-decision-1"), [])

    def test_lost_ack_after_source_withdrawal_does_not_announce_accepted_history_as_current(self):
        self.establish()
        self.now = NOW + 7
        control = barrier([self.last], event_id="barrier-before-ack-reconciliation", at=self.now)
        self.probe.command("fault", mode="lost_ack", event=event(control), token=TOKEN)
        result = self.probe.command("tick", t=self.now)
        self.assertFalse(result["decisions"], result)
        self.assertTrue(any(s["status"] == "committed_output_withdrawn" for s in result["statuses"]), result)
        self.assertEqual(len(result["fault_receipts"]), 1)
        saved = self.checkpoint()
        self.assertEqual(saved["context_seq"], 7)
        self.assertEqual(saved["revocation_generation"], 1)
        self.assertEqual(saved["checkpoint"]["authority_generation"], 0)
        committed = json.loads(result["commit_posts"][-1])
        status, historical = self.http.request("POST", PREFIX + "/worker-commits", committed, token=WORKER_TOKEN)
        self.assertEqual(status, 200, historical)
        self.assertEqual(historical["status"], "accepted")
        self.assertEqual(self.current("home-live-engagement-state-1"), [])
        self.assertEqual(self.current("home-live-shadow-decision-1"), [])

    def test_expired_restart_discards_anonymous_bindings_but_preserves_original_root_ledger(self):
        self.establish()
        prior = self.checkpoint()["checkpoint"]
        self.probe.command("stop")
        self.now = NOW + 16
        started = self.probe.command("start", config=self.config, t=self.now)
        self.assertTrue(any(s["status"] == "cold_start" and s["reason"] == "checkpoint_support_expired"
                            for s in started["statuses"]), started)
        result = self.probe.command("tick", t=self.now)
        self.assertEqual([d["seq"] for d in result["decisions"]], [7], result)
        saved = self.checkpoint()["checkpoint"]
        self.assertEqual(saved["state"]["root_ledger"], prior["state"]["root_ledger"])
        self.assertEqual(saved["state"]["identity_bindings"], [])
        self.assertIsNone(result["decisions"][0]["engagement_ref"])
        self.assertEqual(self.current("home-live-engagement-state-1"), [])

    def test_production_scheduler_commits_expiry_after_clock_advance_without_manual_cycle(self):
        self.establish()
        self.probe.command("stop")
        self.now += .1
        started = self.probe.command("start", config=self.config, t=self.now, schedule=True)
        self.assertEqual([d["seq"] for d in started["decisions"]], [7], started)
        self.now = NOW + 15
        autonomous = self.probe.command("advance_clock", t=self.now)
        self.assertEqual([d["seq"] for d in autonomous["decisions"]], [8], autonomous)
        self.assertEqual(self.checkpoint()["context_seq"], 8)
        self.assertEqual(self.current("home-live-engagement-state-1")[0]["state"], "suspended")
        decision = autonomous["decisions"][0]
        self.assertEqual(decision["display_lease"]["status"], "withdrawn")
        self.assertFalse(decision["actuated"])
        self.assertFalse(decision["acceptance_eligible"])


class LiveWorkerRetentionIntegrationTests(HomeLiveWorkerFixture):
    journal_limits = Limits(max_publications=8)

    def test_retained_cursor_gap_recovers_current_heads_but_requires_fresh_dwell(self):
        self.establish()
        ledger = self.checkpoint()["checkpoint"]["state"]["root_ledger"]
        for seq in range(7, 14):
            self.publish(seq)
        result = self.probe.command("tick", t=self.now)
        self.assertTrue(any(s["status"] == "cold_start" and s["reason"] == "retained_cursor_gap"
                            for s in result["statuses"]), result)
        self.assertEqual([d["seq"] for d in result["decisions"]], [7], result)
        current = self.current("home-live-engagement-state-1")[0]
        self.assertEqual(current["state"], "candidate")
        self.assertIsNone(current["episode_id"])
        self.assertEqual(current["support"]["progression_start_at"], NOW + 12)
        saved = self.checkpoint()["checkpoint"]["state"]["root_ledger"]
        self.assertTrue(set(ledger["times"].items()) <= set(saved["times"].items()))
        for root, binding in ledger["bindings"].items():
            self.assertEqual(saved["bindings"][root], binding)


class LiveWorkerIndependentSourceIntegrationTests(HomeLiveWorkerFixture):
    second_source = True

    def test_one_source_gap_preserves_unrelated_supported_person_and_restarts_only_affected_dwell(self):
        for seq in range(1, 7):
            first_receipt = self.publish(seq)
            self.publish_payload(second_source_fact(seq))
            if seq == 1:
                self.probe.command("start", config=self.config, t=self.now)
            result = self.probe.command("tick", t=self.now)
            self.assertEqual(len(result["decisions"]), 1, result)
        before = {value["person_ref"]["pid"]: value for value in self.current("home-live-engagement-state-1")}
        self.assertEqual(set(before), {1, 2})
        self.assertTrue(all(value["state"] == "supported" for value in before.values()))
        self.now = NOW + 7
        control = barrier([first_receipt], event_id="only-first-camera-gap", at=self.now)
        status, receipt = self.http.request("POST", PREFIX + "/events", event(control))
        self.assertEqual(status, 200, receipt)
        self.now = NOW + 8.2
        recovered = fact("first-camera-fresh-after-gap", seq=9, generation=2)
        self.publish_payload(recovered)
        result = self.probe.command("tick", t=self.now)
        self.assertEqual(len(result["decisions"]), 1, result)
        current = {value["person_ref"]["pid"]: value for value in self.current("home-live-engagement-state-1")}
        self.assertEqual(current[1]["state"], "candidate")
        self.assertIsNone(current[1]["episode_id"])
        self.assertEqual(current[2]["state"], "supported")
        self.assertEqual(current[2]["episode_id"], before[2]["episode_id"])
        self.assertEqual(current[2]["support"]["evidence_refs"][0]["id"], "second-source-fact-6")


if __name__ == "__main__":
    unittest.main()

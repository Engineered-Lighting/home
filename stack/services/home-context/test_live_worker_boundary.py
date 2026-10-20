"""Adversarial public HTTP commits prepared by the actual supported Node worker."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from journal import Journal, PREFIX, digest
from live_test_helpers import LiveHttp, NOW, PROFILE, SCOPE, event, fact, receipt_resolver
from live_worker_test_helpers import WORKER_ID, WORKER_TOKEN, WorkerProbe, worker_fixture


class LiveWorkerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="synthetic-worker-boundary-", dir=Path(__file__).parent)
        self.addCleanup(self.work.cleanup)
        self.now = NOW + .2
        self.config, self.grants, self.worker = worker_fixture()
        self.journal = Journal(Path(self.work.name) / "synthetic.sqlite", grants=self.grants, profile=PROFILE,
                               clock=lambda: self.now, live_config=self.config, live_receipt_resolver=receipt_resolver)
        self.addCleanup(self.journal.close)
        self.http = LiveHttp(self.journal)
        self.addCleanup(self.http.close)
        self.worker["service_url"] = f"http://127.0.0.1:{self.http.server.server_port}"
        status, receipt = self.http.request("POST", PREFIX + "/events", event(fact()))
        self.assertEqual(status, 200, receipt)
        self.probe = WorkerProbe()
        self.addCleanup(self.probe.close)
        self.probe.command("start", config=self.worker, t=self.now)
        self.probe.command("fault", mode="before_commit")
        response = self.probe.command("tick", t=self.now)
        self.assertEqual(response["decisions"], [], response)
        self.prepared = json.loads(response["commit_posts"][-1])

    def post(self, commit):
        return self.http.request("POST", PREFIX + "/worker-commits", commit, token=WORKER_TOKEN)

    def checkpoint(self, token=WORKER_TOKEN):
        route = PREFIX + "/worker-commits/" + WORKER_ID + "?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"]
        return self.http.request("GET", route, token=token)

    def reject_without_output(self, changed, code, expected=409):
        status, response = self.post(changed)
        self.assertEqual(status, expected, response)
        self.assertEqual(response["error"]["code"], code, response)
        status, checkpoint = self.checkpoint()
        self.assertEqual(status, 200, checkpoint)
        self.assertEqual(checkpoint["context_seq"], 0)
        self.assertIsNone(checkpoint["checkpoint"])

    def test_delivered_receipt_cannot_change_producer_or_original_commit_time(self):
        for key, value in (("producer_id", "unrelated-producer"), ("committed_at", NOW), ("received_at", NOW)):
            with self.subTest(field=key):
                changed = copy.deepcopy(self.prepared)
                changed["input_receipts"][0][key] = value
                changed["checkpoint"]["state"]["engagement"]["input_receipts"][0][key] = value
                self.reject_without_output(changed, "invalid_input_receipt")

    def test_omitting_current_input_is_not_a_complete_causal_cut(self):
        changed = copy.deepcopy(self.prepared)
        changed["input_receipts"] = []
        changed["checkpoint"]["state"]["engagement"]["input_receipts"] = []
        decision = changed["publications"][-1]["payload"]
        decision["input_cut"]["input_receipts"] = []
        cut = {key: value for key, value in decision["input_cut"].items() if key != "cut_digest"}
        decision["input_cut"]["cut_digest"] = digest(cut)
        self.reject_without_output(changed, "invalid_input_receipt")

    def test_configuration_and_module_identity_cannot_be_rewritten_in_checkpoint(self):
        for target in ("config", "module"):
            with self.subTest(target=target):
                changed = copy.deepcopy(self.prepared)
                cp = changed["checkpoint"]
                if target == "config":
                    cp["config_digest"] = "0" * 64
                else:
                    cp["state"]["engagement"]["module_hashes"]["tools/home-live-context-worker.cjs"] = "0" * 64
                self.reject_without_output(changed, "checkpoint_conflict")

    def test_checkpoint_cannot_claim_an_uncommitted_source_generation(self):
        changed = copy.deepcopy(self.prepared)
        generations = changed["checkpoint"]["state"]["engagement"]["source_generations"]
        generations[next(iter(generations))] += 1
        self.reject_without_output(changed, "source_generation_conflict")

    def test_private_root_times_and_physical_bindings_remain_original(self):
        for kind in ("times", "bindings"):
            with self.subTest(kind=kind):
                changed = copy.deepcopy(self.prepared)
                state = changed["checkpoint"]["state"]
                root = next(iter(state["root_ledger"][kind]))
                replacement = NOW - 1 if kind == "times" else ["camera:unrelated"]
                state["root_ledger"][kind][root] = replacement
                field = "root_times" if kind == "times" else "root_bindings"
                state["engagement"]["reducer"]["state"][field][root] = copy.deepcopy(replacement)
                self.reject_without_output(changed, "root_time_conflict")

    def test_private_fact_cache_cannot_relabel_delivered_observation(self):
        changed = copy.deepcopy(self.prepared)
        cache = changed["checkpoint"]["state"]["engagement"]["reducer"]["state"]["cache"]
        next(iter(cache.values()))["value"] = False
        self.reject_without_output(changed, "invalid_reference")

    def test_checkpoint_cannot_claim_next_sequence_or_binding_from_another_worker(self):
        for key, value in (("expected_context_seq", 1), ("expected_input_cursor", 1)):
            with self.subTest(field=key):
                changed = copy.deepcopy(self.prepared)
                changed[key] = value
                self.reject_without_output(changed, "checkpoint_conflict")

    def test_atomic_commit_ack_retry_and_lookup_preserve_original_receipt(self):
        status, original = self.post(self.prepared)
        self.assertEqual(status, 200, original)
        self.assertEqual(original["status"], "accepted")
        self.assertEqual(original["commit_digest"], digest(self.prepared))
        self.now += 1
        status, retry = self.post(self.prepared)
        self.assertEqual(status, 200, retry)
        self.assertEqual(retry, original)
        route = PREFIX + "/live-worker-commits/" + self.prepared["id"] + "?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"]
        status, looked_up = self.http.request("GET", route, token=WORKER_TOKEN)
        self.assertEqual(status, 200, looked_up)
        self.assertEqual(looked_up, original)
        changed = copy.deepcopy(self.prepared)
        changed["checkpoint"]["last_committed_at"] += .1
        self.assertEqual(self.post(changed)[1]["error"]["code"], "identity_conflict")

    def test_producer_reader_cannot_obtain_private_worker_checkpoint_or_cut(self):
        status, response = self.checkpoint(token="synthetic-live-token")
        self.assertEqual(status, 403, response)
        status, response = self.http.request("GET", PREFIX + "/live-input-cut?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"], token="synthetic-live-token")
        self.assertEqual(status, 403, response)

    def test_same_domain_checkpoint_cannot_discard_an_older_original_root(self):
        status, first = self.post(self.prepared)
        self.assertEqual(status, 200, first)
        # Reconcile the already durable first commit before arranging a fault
        # in the next external HTTP commit, rather than mutating worker state.
        self.probe.command("tick", t=self.now)
        self.now += 1
        self.probe.command("fault", mode="before_commit")
        response = self.probe.command("tick", t=self.now)
        changed = json.loads(response["commit_posts"][-1])
        state = changed["checkpoint"]["state"]
        state["root_ledger"] = {"times": {}, "bindings": {}}
        state["engagement"]["reducer"]["state"]["root_times"] = {}
        state["engagement"]["reducer"]["state"]["root_bindings"] = {}
        status, rejected = self.post(changed)
        self.assertEqual(status, 409, rejected)
        self.assertEqual(rejected["error"]["code"], "root_time_conflict", rejected)
        status, saved = self.checkpoint()
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["context_seq"], 1)
        self.assertTrue(saved["checkpoint"]["state"]["root_ledger"]["times"])

    def test_private_checkpoint_family_cannot_be_granted_to_an_ordinary_reader(self):
        from schema_registry import schema_digest
        changed = copy.deepcopy(self.grants[0])
        changed["payload_types"].append("home-live-worker-checkpoint-1")
        changed["schema_pins"]["home-live-worker-checkpoint-1"] = schema_digest("home-live-worker-checkpoint-1", profile=PROFILE)
        path = Path(self.work.name) / "forbidden-private.sqlite"
        with self.assertRaises(ValueError):
            Journal(path, grants=[changed], profile=PROFILE, clock=lambda: self.now,
                    live_config=self.config, live_receipt_resolver=receipt_resolver)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()

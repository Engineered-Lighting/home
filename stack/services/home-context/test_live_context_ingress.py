"""Controlled commissioned synthetic inputs at the real producer/journal seam."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from journal import Journal, Limits, PREFIX
from live_test_helpers import (LiveHttp, NOW, PROFILE, SCOPE, MANIFEST, configuration, event, fact, geometry,
                               grant, independent_digest, publication_ref, receipt_resolver, unavailable, barrier, reference)


class LiveIngressTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="synthetic-live-ingress-", dir=Path(__file__).parent)
        self.addCleanup(self.work.cleanup)
        self.path = Path(self.work.name) / "synthetic.sqlite"
        self.now = NOW + .2
        self.config = configuration()
        self.grants = [grant()]
        self.resolver = receipt_resolver
        self.limits = Limits()
        self.journal = self.server = None
        self.open()

    def open(self):
        self.journal = Journal(self.path, grants=self.grants, profile=PROFILE, clock=lambda: self.now,
                               live_config=self.config, live_receipt_resolver=lambda store, key: self.resolver(store, key), limits=self.limits)
        self.server = LiveHttp(self.journal)

    def close(self):
        if self.server is not None:
            self.server.close()
            self.server = None
        if self.journal is not None:
            self.journal.close()
            self.journal = None

    def tearDown(self):
        self.close()

    def restart(self):
        self.close()
        self.open()

    def fresh_configuration(self, config, grants=None):
        self.close()
        self.path.unlink()
        self.config = config
        self.grants = [grant()] if grants is None else grants
        self.open()

    def post(self, payload):
        return self.server.request("POST", PREFIX + "/events", event(payload))

    def accept(self, payload):
        status, response = self.post(payload)
        self.assertEqual(status, 200, response)
        return response

    def reject(self, payload, code, status=409):
        actual, response = self.post(payload)
        self.assertEqual(actual, status, response)
        self.assertEqual(response["error"]["code"], code, response)

    def snapshot(self, token="synthetic-live-token"):
        status, response = self.server.request("GET", PREFIX + "/snapshot?site_id=synthetic-site&room_id=synthetic-room", token=token)
        self.assertEqual(status, 200, response)
        return response

    def test_fact_geometry_publication_receipts_and_snapshot(self):
        for expected_cursor, payload in enumerate([fact(), geometry()], start=1):
            saved = self.accept(payload)
            self.assertEqual(saved["cursor"], expected_cursor)
            self.assertEqual(saved["committed_at"], NOW + .2)
            self.assertEqual(saved["payload_digest"], independent_digest(payload))
        snapshot = self.snapshot()
        self.assertEqual(snapshot["cursor"], 2)
        self.assertEqual([item["id"] for item in snapshot["items"]], ["synthetic-fact-1", "synthetic-geometry-1"])
        self.assertEqual(snapshot["items"][0]["payload"]["value"], True)
        self.assertEqual(snapshot["items"][1]["payload"]["parts"]["work"]["shape"]["center_m"], [1.25, 2.5, .9])
        self.assertEqual(snapshot["items"][0]["payload"]["observed_at"], NOW)
        self.assertEqual(snapshot["items"][0]["payload"]["available_at"], NOW + .1)

    def test_exact_retry_changed_payload_and_restart_after_expiry(self):
        original = fact()
        saved = self.accept(original)
        self.assertEqual(self.accept(original), saved)
        changed = copy.deepcopy(original)
        changed["value"] = False
        self.reject(changed, "identity_conflict")
        self.assertEqual(self.snapshot()["cursor"], 1)
        self.now = NOW + 20
        self.assertEqual(self.snapshot()["items"], [])
        self.restart()
        self.assertEqual(self.accept(original), saved)
        self.assertEqual(self.snapshot()["cursor"], 1)

    def test_new_unavailable_heads_do_not_resurrect_expired_or_pruned_positives(self):
        self.close()
        self.limits = Limits(retention_seconds=1, max_publications=2)
        self.open()
        originals = [(factory, self.accept(factory())) for factory in (fact, geometry)]
        self.now = NOW + 1.2
        newer = []
        for factory, original in originals:
            payload = unavailable(factory("unavailable-" + original["id"], seq=2), until=NOW + 2)
            payload["head"]["supersedes"] = publication_ref(original)
            newer.append(self.accept(payload))
        self.assertEqual([item["payload"]["status"] for item in self.snapshot()["items"]], ["unobservable", "unobservable"])
        self.now = NOW + 3
        self.assertEqual(self.snapshot()["items"], [])
        self.restart()
        self.assertEqual(self.snapshot()["items"], [])
        for (factory, original), head in zip(originals, newer):
            late = factory("late-" + original["id"])
            late["head"]["supersedes"] = publication_ref(head)
            self.reject(late, "stale_head")
        self.assertEqual(self.snapshot()["cursor"], 4)
        status, reset = self.server.request("GET", PREFIX + "/events?site_id=synthetic-site&room_id=synthetic-room&after=0")
        self.assertEqual(status, 409, reset)
        self.assertEqual(reset["error"]["code"], "reset_required")

    def test_source_barrier_retries_and_fresh_generation_recovery(self):
        original = fact()
        fact_receipt = self.accept(original)
        geometry_receipt = self.accept(geometry())
        self.now = NOW + 1.1
        withdrawal = barrier([fact_receipt, geometry_receipt])
        saved = self.accept(withdrawal)
        self.assertEqual(self.snapshot()["items"], [])
        self.assertEqual(self.snapshot()["revocation_generation"], 1)
        self.assertEqual(self.accept(withdrawal), saved)
        self.assertEqual(self.accept(original), fact_receipt)
        self.reject(fact("old-generation", seq=2), "source_generation_conflict")
        self.now = NOW + 2.2
        recovered = fact("recovered", seq=3, generation=2)
        recovered["head"]["supersedes"] = publication_ref(fact_receipt)
        self.accept(recovered)
        self.assertEqual([item["id"] for item in self.snapshot()["items"]], ["recovered"])
        self.restart()
        self.assertEqual(self.accept(withdrawal), saved)
        self.assertEqual(self.snapshot()["revocation_generation"], 1)

    def test_closed_semantics_clock_registration_and_expiry_admission(self):
        cases = [
            ("clock_digest", ("clock", "mapping_digest"), "0" * 64, "invalid_clock"),
            ("mount", ("registration", "mount_digest"), "0" * 64, "registration_mismatch"),
            ("calibration", ("registration", "calibration_digest"), "0" * 64, "registration_mismatch"),
            ("coverage", ("registration", "coverage_refs"), ["other-coverage"], "registration_mismatch"),
            ("revision", ("registration", "geometry_revision"), "other-geometry", "registration_mismatch"),
            ("uncertainty_sum", ("parts", "work", "uncertainty", "bound_at_observation_m"), .02, "invalid_uncertainty"),
            ("age_origin", ("parts", "work", "uncertainty", "age", "origin_at"), NOW - 1, "invalid_clock"),
            ("invented_receipt", ("original_receipt_refs", 0, "digest"), "0" * 64, "receipt_mismatch"),
        ]
        for name, path, value, code in cases:
            with self.subTest(name=name):
                payload = geometry("bad-" + name)
                target = payload
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.reject(payload, code, 400)
        for payload in (dict(fact("coordinates-in-value"), value={"center_m": [1.25, 2.5, .9]}),
                        dict(fact("coordinates-in-metadata"), metadata={"center_m": [1.25, 2.5, .9]})):
            self.reject(payload, "invalid_payload", 400)
        expired = geometry("expiry-equality")
        expired["valid_until"] = self.now
        for part in expired["parts"].values():
            part["valid_until"] = self.now
        self.reject(expired, "expired")
        future = fact("future-availability")
        future["available_at"] = NOW + 1
        self.reject(future, "invalid_clock", 400)
        self.assertEqual(self.snapshot()["cursor"], 0)

    def test_semantic_reader_has_no_geometry_or_cross_scope_payload_leak(self):
        self.grants.append(grant("semantic-reader", types=["home-live-fact-1"], capabilities=["read"], operations=["read"]))
        self.restart()
        self.accept(fact())
        self.accept(geometry())
        snapshot = self.snapshot("semantic-reader")
        self.assertEqual([item["payload_type"] for item in snapshot["items"]], ["home-live-fact-1"])
        self.assertNotIn("center_m", json.dumps(snapshot))
        self.assertNotIn("parts", json.dumps(snapshot))
        status, page = self.server.request("GET", PREFIX + "/events?site_id=synthetic-site&room_id=synthetic-room&after=0", token="semantic-reader")
        self.assertEqual(status, 200, page)
        self.assertEqual([item["payload_type"] for item in page["items"]], ["home-live-fact-1"])
        self.assertEqual(page["next_cursor"], 2)
        wrong_room = event(geometry("scope-probe"))
        wrong_room["room_id"] = "other-room"
        wrong_room["payload"] = {"protected_coordinates": [99, 99, 99]}
        for body, token in ((wrong_room, "synthetic-live-token"), (event(geometry("type-probe")), "semantic-reader")):
            status, denied = self.server.request("POST", PREFIX + "/events", body, token=token)
            self.assertEqual(status, 403, denied)
            self.assertEqual(denied["error"]["code"], "forbidden")
            self.assertEqual(set(denied), {"schema", "error"})
            self.assertEqual(set(denied["error"]), {"code", "message"})
            self.assertNotIn("coordinates", json.dumps(denied))
        status, denied = self.server.request("GET", PREFIX + "/snapshot?site_id=synthetic-site&room_id=other-room", token="semantic-reader")
        self.assertEqual(status, 403, denied)
        self.assertNotIn("center_m", json.dumps(denied))
        self.assertEqual(self.snapshot()["cursor"], 2)

    def test_original_root_budget_does_not_clear_after_barrier_or_restart(self):
        self.fresh_configuration(configuration(max_original_roots=2))
        original = fact()
        saved = self.accept(original)
        self.now = NOW + 1.2
        next_fact = fact("beyond-budget", seq=2)
        next_fact["head"]["supersedes"] = publication_ref(saved)
        self.reject(next_fact, "journal_full", 507)
        self.assertEqual(self.snapshot()["cursor"], 1)
        self.accept(barrier([saved]))
        self.now = NOW + 2.2
        self.restart()
        recovery = fact("budget-still-exhausted", seq=3, generation=2)
        recovery["head"]["supersedes"] = publication_ref(saved)
        self.reject(recovery, "journal_full", 507)
        self.assertEqual(self.accept(original), saved)
        self.assertEqual(self.snapshot()["cursor"], 2)

    def test_new_track_epoch_retires_old_anonymous_person_heads(self):
        self.accept(fact())
        self.accept(geometry())
        self.now = NOW + 1.2
        next_person = fact("new-person-epoch", seq=2)
        next_person["person_ref"]["track_epoch"] = "synthetic-track-2"
        next_person["head"]["key"]["person_ref"]["track_epoch"] = "synthetic-track-2"
        self.accept(next_person)
        self.assertEqual([item["id"] for item in self.snapshot()["items"]], ["new-person-epoch"])
        late = fact("late-old-person")
        status, rejected = self.post(late)
        self.assertEqual(status, 409, rejected)
        self.assertEqual([item["id"] for item in self.snapshot()["items"]], ["new-person-epoch"])
        self.restart()
        self.assertEqual([item["id"] for item in self.snapshot()["items"]], ["new-person-epoch"])

    def test_new_subject_epoch_retires_prior_work_relationship(self):
        self.accept(fact())
        self.accept(geometry())
        self.now = NOW + 1.2
        next_subject = fact("new-subject-epoch", seq=2)
        next_subject["subject_ref"]["subject_epoch"] = "synthetic-subject-2"
        next_subject["head"]["key"]["subject_ref"]["subject_epoch"] = "synthetic-subject-2"
        self.accept(next_subject)
        self.assertEqual([item["id"] for item in self.snapshot()["items"]], ["new-subject-epoch"])
        late = geometry("late-old-subject")
        status, rejected = self.post(late)
        self.assertEqual(status, 409, rejected)
        self.assertEqual([item["id"] for item in self.snapshot()["items"]], ["new-subject-epoch"])

    def test_original_root_time_is_shared_across_fact_geometry_event_aliases(self):
        self.accept(fact())
        self.now = NOW + 1.2
        payload = geometry("geometry-alias-retimes-old-frame", seq=2)
        payload["observation_roots"] = [root.replace("frame-2", "frame-1") for root in payload["observation_roots"]]
        payload["observation_root_times"] = {root.replace("frame-2", "frame-1"): observed for root, observed in payload["observation_root_times"].items()}
        payload["sensor_root_bindings"] = {root.replace("frame-2", "frame-1"): roots for root, roots in payload["sensor_root_bindings"].items()}
        def alias_resolver(store, key):
            resolved = receipt_resolver(store, key)
            resolved["observation_root_times"] = {root.replace("frame-2", "frame-1"): observed for root, observed in resolved["observation_root_times"].items()}
            resolved["sensor_root_bindings"] = {root.replace("frame-2", "frame-1"): roots for root, roots in resolved["sensor_root_bindings"].items()}
            return resolved
        self.resolver = alias_resolver
        self.reject(payload, "root_time_conflict")
        self.assertEqual(self.snapshot()["cursor"], 1)
        self.resolver = receipt_resolver
        self.accept(geometry("qualified-new-frame", seq=2))
        self.assertEqual(self.snapshot()["cursor"], 2)

    def test_original_root_physical_binding_is_shared_across_fact_geometry_aliases(self):
        config = configuration()
        config["manifest"]["sources"]["synthetic-source"]["sensor_roots"].append("camera:synthetic-other")
        config["manifest_ref"]["digest"] = independent_digest(config["manifest"])
        MANIFEST.update(config["manifest_ref"])
        permissions = grant()
        permissions["sensor_roots"].append("camera:synthetic-other")
        self.fresh_configuration(config, [permissions])
        self.accept(fact())
        payload = geometry("geometry-alias-reparents-old-frame")
        payload["sensor_roots"] = ["camera:synthetic-other"]
        payload["sensor_root_bindings"] = {root: ["camera:synthetic-other"] for root in payload["observation_roots"]}
        new_refs = []
        records = {}
        for original in payload["original_receipt_refs"]:
            replacement = {**original, "receipt_id": "alias-" + original["receipt_id"], "digest": reference("alias-" + original["receipt_id"])["digest"]}
            resolved = receipt_resolver(original["store_id"], original["receipt_id"])
            resolved["ref"] = replacement
            resolved["sensor_root_bindings"] = {root: ["camera:synthetic-other"] for root in resolved["observation_root_times"]}
            records[(replacement["store_id"], replacement["receipt_id"])] = resolved
            new_refs.append(replacement)
        payload["original_receipt_refs"] = new_refs
        for part in payload["parts"].values():
            part["support_refs"] = [ref for ref in new_refs if ref["component_id"] in {old["component_id"] for old in part["support_refs"]}]
        self.resolver = lambda store, key: records[(store, key)]
        self.reject(payload, "root_conflict")
        self.assertEqual(self.snapshot()["cursor"], 1)

    def test_hidden_source_control_advances_semantic_reader_generation(self):
        self.grants.append(grant("semantic-reader", types=["home-live-fact-1"], capabilities=["read"], operations=["read"]))
        self.restart()
        fact_saved = self.accept(fact())
        geometry_saved = self.accept(geometry())
        self.assertEqual(self.snapshot("semantic-reader")["revocation_generation"], 0)
        self.now = NOW + 1.2
        self.accept(barrier([fact_saved, geometry_saved]))
        observed = self.snapshot("semantic-reader")
        self.assertEqual(observed["revocation_generation"], 1)
        self.assertEqual(observed["items"], [])
        status, page = self.server.request("GET", PREFIX + "/events?site_id=synthetic-site&room_id=synthetic-room&after=2", token="semantic-reader")
        self.assertEqual(status, 200, page)
        self.assertEqual(page["items"], [])
        self.assertEqual(page["revocation_generation"], 1)
        self.assertEqual(page["next_cursor"], 3)

    def test_unimplemented_live_routes_and_types_remain_closed(self):
        for route in ("worker-commits", "intent-events", "evidence-requests", "evidence-replies", "consumer-feedback"):
            with self.subTest(route=route):
                status, denied = self.server.request("POST", PREFIX + "/" + route, {"protected_coordinates": [99, 99, 99]})
                self.assertIn(status, (400, 403), denied)
                self.assertNotIn("protected_coordinates", json.dumps(denied))
        future = event(fact("unimplemented-engagement"))
        future["payload_type"] = "home-live-engagement-state-1"
        status, denied = self.server.request("POST", PREFIX + "/events", future)
        self.assertEqual(status, 403, denied)
        self.assertEqual(denied["error"]["code"], "forbidden")
        self.assertEqual(self.snapshot()["cursor"], 0)

    def test_source_revision_manifest_and_model_authority_are_explicit(self):
        for field, value in (("source_id", "ungranted-source"), ("sensor_roots", ["camera:ungranted"]),
                             ("manifest_ref", reference("ungranted-manifest"))):
            with self.subTest(field=field):
                payload = fact("denied-" + field)
                payload[field] = value
                self.reject(payload, "forbidden", 403)
        for field, value in (("producer_id", "other-producer"), ("revision", "ungranted-revision")):
            with self.subTest(field=field):
                envelope = event(fact("denied-" + field))
                envelope[field] = value
                status, response = self.server.request("POST", PREFIX + "/events", envelope)
                self.assertEqual(status, 403, response)
                self.assertEqual(response["error"]["code"], "forbidden")
        unknown_model = fact("unknown-model")
        unknown_model["model_ref"] = reference("ungranted-model")
        self.reject(unknown_model, "forbidden", 400)
        unknown_method = fact("unknown-method")
        unknown_method["method_ref"] = reference("ungranted-method")
        self.reject(unknown_method, "forbidden", 400)
        uncalibrated = fact("uncalibrated-probability")
        uncalibrated["probability"] = {"p": .99, "calibration_ref": reference("ungranted-calibration"), "applicability_ref": reference("ungranted-applicability")}
        self.reject(uncalibrated, "uncalibrated_probability", 400)
        self.assertEqual(self.snapshot()["cursor"], 0)


if __name__ == "__main__":
    unittest.main()

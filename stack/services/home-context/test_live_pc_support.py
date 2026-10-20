"""Authored PC successor fixtures and independent proof/HA seam checks."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import unittest

from journal import digest
from live_pc_support import validate_pc_support_policy, validate_pc_input_fact, pc_support_clause, PCSupportRefusal, _instant, _derived
from live_test_helpers import configuration, grant, reference, person, subject, geometry, event, receipt_resolver, NOW, SCOPE, GEOMETRY_METHOD, ASSOCIATION_METHOD


PC_METHOD = reference("authored-qualified-pc-input-method")


def authored_pc_fixture(*, seq=1, input_at=None, snapshot_at=None, offset_s=0, uncertainty_s=.01):
    """Complete serializable generated fixture; not a real qualification receipt.

    Root integration tests can reuse config, producer_grant, geometry_event,
    parent_publications, fact, original, record, policy and now. The supplied
    parent publication is authored: actual join tests must publish geometry and
    replace that ref with its real journal acknowledgement before PC admission.
    """
    snapshot_at = NOW + seq - 1 if snapshot_at is None else snapshot_at
    input_at = snapshot_at - .5 if input_at is None else input_at
    config = configuration()
    pc_source = config["manifest"]["sources"]["synthetic-source"]
    geometry_source = deepcopy(pc_source)
    config["manifest"]["sources"]["geometry-source"] = geometry_source
    pc_source["method_refs"].append(deepcopy(PC_METHOD))
    pc_source["sensor_roots"].append("device:authored-pc")
    pc_source["receipt_component_ids"].append("pc-proof")
    config["manifest_ref"]["digest"] = digest(config["manifest"])
    active_rule = {"session_entity": "sensor.pc_sessionstate", "last_active_entity": "sensor.pc_lastactive",
                   "unlocked_values": ["Unlocked"], "max_idle_seconds": 60}
    rule = {"schema": "home-pc-input-rule-1", "version": 1, "id": "authored-pc-rule",
            "site_id": SCOPE["site_id"], "device_id": "authored-pc", "ha_source_id": "victoria-ha-lab",
            "entities": ["sensor.pc_sessionstate", "sensor.pc_lastactive"],
            "derived": {"computer_active": deepcopy(active_rule), "computer_input_recency": {
                **{k: deepcopy(v) for k, v in active_rule.items() if k != "max_idle_seconds"}, "rule": "computer_active_v3"}}}
    rule_ref = {"id": rule["id"], "digest": digest(rule)}
    clock = {"schema": "home-pc-input-clock-qualification-1", "version": 1, "id": "authored-pc-clock",
             "site_id": SCOPE["site_id"], "device_id": "authored-pc", "input_clock_epoch": "pc-clock-epoch-1",
             "input_rule_ref": rule_ref, "method_ref": deepcopy(PC_METHOD), "assessment_ref": reference("authored-clock-assessment"),
             "valid_from": NOW - 100, "available_at": NOW - 50, "valid_until": NOW + 1000,
             "offset_s": offset_s, "uncertainty_s": uncertainty_s}
    clock_ref = {"id": clock["id"], "digest": digest(clock)}
    binding = {"source_id": "synthetic-source", "method_ref": deepcopy(PC_METHOD),
               "association_method_ref": deepcopy(ASSOCIATION_METHOD), "qualification_ref": clock_ref,
               "input_rule_ref": rule_ref, "device_id": "authored-pc", "workstation_id": "authored-desk",
               "subject_ref": subject(), "input_sensor_root": "device:authored-pc", "input_clock_epoch": "pc-clock-epoch-1",
               "ha_source_id": "victoria-ha-lab", "lastactive_entity": "sensor.pc_lastactive",
               "geometry_source_id": "geometry-source", "geometry_method_ref": deepcopy(GEOMETRY_METHOD),
               "proof_component_id": "pc-proof", "max_input_age_s": 15}
    policy = {"schema": "home-live-pc-support-policy-1", "version": 1, "bindings": [binding],
              "input_rules": [rule], "clock_qualifications": [clock]}
    geom = geometry("authored-current-geometry-" + str(seq), seq=seq)
    geom.update(source_id="geometry-source", manifest_ref=deepcopy(config["manifest_ref"]))
    geom["head"]["key"]["source_id"] = "geometry-source"
    parent = {"schema": "home-context-publication-1", "id": geom["id"], "producer_id": "synthetic-producer",
              "stream_epoch": "authored-journal", "cursor": seq, "context_seq": seq, "committed_at": snapshot_at + .1,
              "payload_type": "home-live-geometry-1", "payload_version": "1.0", "payload": geom, "payload_digest": digest(geom)}
    parent_ref = {k: parent[k] for k in ("stream_epoch", "cursor", "producer_id", "id", "payload_digest")} | {"component_id": "parts.work"}
    raw = datetime.fromtimestamp(input_at - offset_s, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    facts = {"sensor.pc_sessionstate": {"state": "Unlocked", "status": "known"},
             "sensor.pc_lastactive": {"state": raw, "status": "known"}}
    facts.update(_derived(rule, facts, snapshot_at))
    content = {"observed_at": snapshot_at, "ha": facts, "acquisition_duration_s": .01}
    hroot = "ha:victoria-ha-lab:authored-snapshot-" + str(seq)
    ha = {"id": 100 + seq, "kind": "ha.observation", "session_id": SCOPE["session_id"], "created_at": snapshot_at + .05,
          "payload": {**content, "site_id": SCOPE["site_id"], "camera_id": "den", "session_id": SCOPE["session_id"],
              "available_at": snapshot_at + .05, "valid_until": snapshot_at + 3,
              "quality": {"source_id": "victoria-ha-lab", "timing_basis": "reconciled_snapshot_acquisition"},
              "snapshot_key": hroot, "snapshot_digest": digest(content), "evidence_roots": [hroot]}}
    input_root = "pc-input:" + digest(["authored-pc", "pc-clock-epoch-1", input_at])[:40]
    input_until = min(input_at + 15 - uncertainty_s, clock["valid_until"])
    original = {"schema": "home-original-pc-input-1", "version": 1, "id": "authored-pc-original-" + str(seq),
                "scope": deepcopy(SCOPE), "source_id": "synthetic-source", "source_generation": 1,
                "method_ref": deepcopy(PC_METHOD), "association_method_ref": deepcopy(ASSOCIATION_METHOD),
                "qualification_ref": clock_ref, "input_rule_ref": rule_ref, "device_id": "authored-pc",
                "workstation_id": "authored-desk", "person_ref": person(), "subject_ref": subject(),
                "input_clock_epoch": "pc-clock-epoch-1", "input_root": input_root, "input_at": input_at,
                "input_valid_until": input_until, "association_ref": parent_ref,
                "observed_at": snapshot_at, "available_at": snapshot_at + .2, "ha_event": ha}
    own_times = {input_root: input_at, hroot: snapshot_at}
    own_bindings = {r: ["device:authored-pc"] for r in own_times}
    ref = {"store_id": "synthetic-receipts", "receipt_id": original["id"], "digest": digest(original),
           "component_id": "pc-proof", "observed_at": original["observed_at"], "available_at": original["available_at"],
           "clock_mapping_id": pc_source["capture_clock"]["mapping_id"]}
    body = {"schema": "home-live-fact-1", "version": 1, "id": "authored-pc-fact-" + str(seq), **deepcopy(SCOPE),
            "manifest_ref": deepcopy(config["manifest_ref"]), "source_id": "synthetic-source", "source_generation": 1,
            "person_ref": person(), "subject_ref": subject(), "association": deepcopy(geom["association"]),
            "head": {"key": {"source_id": "synthetic-source", "payload_type": "home-live-fact-1", "person_ref": person(),
                "subject_ref": subject(), "predicate": "pc_input_interaction", "method_ref": deepcopy(PC_METHOD)},
                "position": {"capture_epoch": pc_source["capture_epoch"], "capture_seq": seq, "revision": 0}, "supersedes": None},
            "observed_at": snapshot_at, "available_at": original["available_at"], "valid_until": min(snapshot_at + 2, input_until),
            "sensor_roots": ["camera:synthetic-oak", "device:authored-pc"],
            "observation_roots": sorted([*own_times, *geom["observation_roots"]]),
            "observation_root_times": {**deepcopy(geom["observation_root_times"]), **own_times},
            "sensor_root_bindings": {**deepcopy(geom["sensor_root_bindings"]), **own_bindings},
            "parent_refs": [deepcopy(parent_ref)], "original_receipt_refs": [ref],
            "clock": {"status": "commissioned", **{k: pc_source["capture_clock"][k] for k in ("mapping_id", "source_epoch", "original_unit")},
                "mapping_digest": pc_source["capture_clock"]["mapping_digest"], "original_value": str(int(snapshot_at * 1000000000)), "uncertainty_s": .001},
            "method_ref": deepcopy(PC_METHOD), "model_ref": None, "layer": "observation", "kind": "pc_input_interaction",
            "status": "supplied", "value": "attributed_recent", "assertion_basis": "inferred", "probability": None,
            "conflicting_refs": [], "reason": "qualified_pc_input_attribution"}
    record = {"ref": deepcopy(ref), "capture_epoch": pc_source["capture_epoch"], "capture_seq": seq,
              "observation_root_times": own_times, "sensor_root_bindings": own_bindings, "pc_input_original": deepcopy(original)}
    producer = grant()
    producer.update(source_ids=["synthetic-source", "geometry-source"], sensor_roots=deepcopy(body["sensor_roots"]))
    producer["live_bindings"] = [{"manifest_ref": deepcopy(config["manifest_ref"]), "source_id": key,
                                 "capabilities": ["capture", "control", "read"]} for key in config["manifest"]["sources"]]
    return {"config": config, "policy": policy, "source": pc_source, "fact": body, "original": original,
            "record": record, "parent_publications": [parent], "geometry_event": event(geom), "producer_grant": producer,
            "now": snapshot_at + .3}


def fixture_receipt_resolver(fixture):
    """Fixed owner test port; root must retain actual journal originals separately."""
    def resolve(store, receipt):
        if (store, receipt) == (fixture["record"]["ref"]["store_id"], fixture["record"]["ref"]["receipt_id"]):
            return deepcopy(fixture["record"])
        return receipt_resolver(store, receipt)
    return resolve


class PCSupportTests(unittest.TestCase):
    def setUp(self):
        self.f = authored_pc_fixture()

    def validate(self, fixture=None):
        f = self.f if fixture is None else fixture
        validate_pc_support_policy(f["policy"], f["config"]["manifest"]["sources"])
        return validate_pc_input_fact(f["fact"], f["source"], f["policy"], [f["record"]], f["parent_publications"], now=f["now"])

    def repin(self):
        self.f["record"]["pc_input_original"] = deepcopy(self.f["original"])
        ref = self.f["record"]["ref"]
        ref["digest"] = digest(self.f["original"])
        self.f["fact"]["original_receipt_refs"] = [deepcopy(ref)]

    def test_complete_authored_proof_and_input_only_clause(self):
        checked = self.validate()
        clause = pc_support_clause(self.f["fact"], self.f["original"], self.f["policy"], now=self.f["now"])
        self.assertEqual(checked["input_root"], self.f["original"]["input_root"])
        self.assertEqual(clause["qualification"], "qualified_pc_input")
        self.assertEqual(clause["progression_roots"], [checked["input_root"]])
        self.assertEqual(clause["observed_at"], self.f["original"]["input_at"])
        self.assertEqual(clause["observation_roots"], self.f["fact"]["observation_roots"])
        self.assertGreater(len(clause["observation_roots"]), len(clause["progression_roots"]))

    def test_independent_raw_derivation_matches_actual_ha_functions_at_boundaries(self):
        lab = Path(__file__).resolve().parents[1] / "perception-lab"
        sys.path.insert(0, str(lab))
        self.addCleanup(lambda: sys.path.remove(str(lab)))
        spec = importlib.util.spec_from_file_location("pc_test_actual_ha_ingest", lab / "ha_ingest.py")
        ha = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ha)
        rule = self.f["policy"]["input_rules"][0]
        derived = ha.derived_rules(rule["derived"], rule["entities"])
        for session in ("Unlocked", "Locked", "unknown", "Unknown"):
            for age in (-5.01, -5, 0, 15, 15.000001, 60, 60.000001, 300, 300.000001):
                with self.subTest(session=session, age=age):
                    raw = datetime.fromtimestamp(NOW - age, timezone.utc).isoformat(timespec="microseconds")
                    facts = {"sensor.pc_sessionstate": {"state": session}, "sensor.pc_lastactive": {"state": raw}}
                    expected = {name: ha.DERIVE[r["rule"]](r, facts, NOW) for name, r in derived.items()}
                    self.assertEqual(_derived(rule, facts, NOW), expected)

    def test_same_input_across_fresh_snapshots_cannot_progress_or_renew_lease(self):
        first = self.f
        later = authored_pc_fixture(seq=2, input_at=first["original"]["input_at"])
        self.validate(later)
        a = pc_support_clause(first["fact"], first["original"], first["policy"])
        b = pc_support_clause(later["fact"], later["original"], later["policy"])
        self.assertEqual(a["progression_roots"], b["progression_roots"])
        self.assertEqual(a["observed_at"], b["observed_at"])
        self.assertEqual(first["original"]["input_valid_until"], later["original"]["input_valid_until"])
        changed = authored_pc_fixture(seq=2)
        self.assertNotEqual(first["original"]["input_root"], changed["original"]["input_root"])

    def test_timezone_equivalent_input_and_fraction_parse_are_exact(self):
        self.assertEqual(_instant("2027-01-15T08:00:00.123456Z"), _instant("2027-01-15T09:00:00.123456+01:00"))
        f = authored_pc_fixture(input_at=NOW - .123456, offset_s=.1, uncertainty_s=.001)
        self.validate(f)

    def test_wrong_raw_state_or_derived_provenance_cannot_be_relabelled(self):
        for kind in ("lock", "lastactive", "provenance", "source", "snapshot_digest"):
            with self.subTest(kind=kind):
                self.f = authored_pc_fixture()
                p = self.f["original"]["ha_event"]["payload"]
                if kind == "lock": p["ha"]["sensor.pc_sessionstate"]["state"] = "Locked"
                if kind == "lastactive": p["ha"]["sensor.pc_lastactive"]["state"] = "not-a-time"
                if kind == "provenance": p["ha"]["derived.computer_input_recency"]["provenance"]["rule"] = "computer_active_v2"
                if kind == "source": p["quality"]["source_id"] = "other-source"
                if kind != "snapshot_digest": p["snapshot_digest"] = digest({k: p[k] for k in ("observed_at", "ha", "acquisition_duration_s")})
                else: p["snapshot_digest"] = "0" * 64
                self.repin()
                with self.assertRaises(PCSupportRefusal): self.validate()

    def test_future_stale_or_expired_input_never_uses_snapshot_freshness(self):
        for fixture in (authored_pc_fixture(input_at=NOW + 1), authored_pc_fixture(input_at=NOW - 20)):
            with self.assertRaises(PCSupportRefusal): self.validate(fixture)
        self.f["now"] = self.f["fact"]["valid_until"]
        with self.assertRaises(PCSupportRefusal): self.validate()

    def test_wrong_device_person_work_and_source_generation_refuse(self):
        for key in ("device_id", "person_ref", "subject_ref", "source_generation"):
            with self.subTest(key=key):
                self.f = authored_pc_fixture()
                if key == "person_ref": self.f["original"][key]["track_epoch"] = "other-epoch"
                elif key == "subject_ref": self.f["original"][key]["subject_epoch"] = "other-work"
                elif key == "source_generation": self.f["original"][key] = 2
                else: self.f["original"][key] = "other-device"
                self.repin()
                with self.assertRaises(PCSupportRefusal): self.validate()

    def test_missing_wrong_or_expired_current_parent_refuses(self):
        for change in ("missing", "work_unavailable", "epoch", "expiry", "commit"):
            with self.subTest(change=change):
                self.f = authored_pc_fixture()
                p = self.f["parent_publications"][0]
                if change == "missing": self.f["parent_publications"] = []
                if change == "work_unavailable": p["payload"]["parts"]["work"]["status"] = "unobservable"
                if change == "epoch": p["payload"]["person_ref"]["track_epoch"] = "other-epoch"
                if change == "expiry": p["payload"]["parts"]["work"]["valid_until"] = NOW + .25
                if change == "commit": p["committed_at"] = NOW + 1
                if change in {"work_unavailable", "epoch", "expiry"}:
                    p["payload_digest"] = digest(p["payload"])
                    self.f["original"]["association_ref"]["payload_digest"] = p["payload_digest"]
                    self.f["fact"]["parent_refs"] = [deepcopy(self.f["original"]["association_ref"])]
                    self.repin()
                with self.assertRaises(PCSupportRefusal): self.validate()

    def test_forged_proof_root_lease_and_receipt_digest_refuse(self):
        for change in ("root", "lease", "digest", "observed"):
            with self.subTest(change=change):
                self.f = authored_pc_fixture()
                if change == "root": self.f["original"]["input_root"] = "pc-input:" + "0" * 40
                if change == "lease": self.f["original"]["input_valid_until"] += 1
                if change == "observed": self.f["original"]["input_at"] = self.f["fact"]["observed_at"]
                self.repin()
                if change == "digest": self.f["record"]["ref"]["digest"] = "0" * 64
                with self.assertRaises(PCSupportRefusal): self.validate()

    def test_unknown_ambiguous_no_person_and_unattributed_never_support(self):
        for value in ("active_unattributed", "observed_inactive"):
            fact = {**self.f["fact"], "value": value}
            self.assertIsNone(pc_support_clause(fact, None, self.f["policy"]))
        for change in ("person", "ambiguous", "work"):
            with self.subTest(change=change):
                self.f = authored_pc_fixture()
                if change == "person": self.f["fact"]["person_ref"] = None
                if change == "ambiguous": self.f["fact"]["association"]["person_status"] = "unresolved"
                if change == "work": self.f["fact"]["association"]["subject_status"] = "not_applicable"
                with self.assertRaises(PCSupportRefusal): self.validate()

    def test_policy_body_digest_rule_version_and_mapping_epoch_are_bound(self):
        for change in ("rule", "clock", "old_rule", "unregistered_method"):
            with self.subTest(change=change):
                self.f = authored_pc_fixture()
                p = self.f["policy"]
                if change == "rule": p["input_rules"][0]["derived"]["computer_active"]["max_idle_seconds"] = 120
                if change == "clock": p["clock_qualifications"][0]["offset_s"] += 1
                if change == "old_rule": p["input_rules"][0]["derived"]["computer_input_recency"]["rule"] = "computer_active_v2"
                if change == "unregistered_method": p["bindings"][0]["method_ref"] = reference("other-method")
                with self.assertRaises(PCSupportRefusal): self.validate()

    def test_new_pc_schema_identifier_boundaries_do_not_inherit_legacy_slash_or_length(self):
        for value, valid in (("a" * 160, True), ("a" * 161, False), ("pc/clock", False),
                             ("_pc-clock", False), ("pc clock", False), ("", False)):
            with self.subTest(value=value, valid=valid):
                f = authored_pc_fixture()
                clock = f["policy"]["clock_qualifications"][0]
                clock["assessment_ref"]["id"] = value
                f["policy"]["bindings"][0]["qualification_ref"]["digest"] = digest(clock)
                if valid:
                    validate_pc_support_policy(f["policy"], f["config"]["manifest"]["sources"])
                else:
                    with self.assertRaisesRegex(PCSupportRefusal, "invalid_pc_identifier"):
                        validate_pc_support_policy(f["policy"], f["config"]["manifest"]["sources"])

    def test_worker_causal_availability_and_shortened_expiry_preserve_clause(self):
        self.validate()
        original_clause = pc_support_clause(self.f["fact"], self.f["original"], self.f["policy"])
        worker_fact = deepcopy(self.f["fact"])
        worker_fact["available_at"] += .05
        worker_fact["valid_until"] -= .001
        reconstructed = pc_support_clause(worker_fact, self.f["original"], self.f["policy"])
        self.assertEqual(reconstructed["progression_roots"], original_clause["progression_roots"])
        self.assertEqual(reconstructed["observed_at"], original_clause["observed_at"])
        self.assertEqual(reconstructed["valid_until"], worker_fact["valid_until"])

    def test_newer_geometry_parent_cannot_retimestamp_older_ha_or_physical_input(self):
        newer = authored_pc_fixture(seq=2)
        original, fact, record = self.f["original"], self.f["fact"], self.f["record"]
        parent = newer["parent_publications"][0]
        ref = deepcopy(newer["original"]["association_ref"])
        original["association_ref"] = ref
        original["available_at"] = NOW + 1.2
        fact.update(parent_refs=[deepcopy(ref)], observed_at=NOW + 1, available_at=NOW + 1.2)
        fact["head"]["position"]["capture_seq"] = 2
        fact["observation_root_times"] = {**parent["payload"]["observation_root_times"], **record["observation_root_times"]}
        fact["sensor_root_bindings"] = {**parent["payload"]["sensor_root_bindings"], **record["sensor_root_bindings"]}
        fact["observation_roots"] = sorted(fact["observation_root_times"])
        record["capture_seq"] = 2
        record["ref"]["available_at"] = original["available_at"]
        self.f["parent_publications"], self.f["now"] = [parent], NOW + 1.3
        self.repin()
        checked = self.validate()
        self.assertEqual(original["observed_at"], NOW)
        self.assertEqual(checked["input_at"], NOW - .5)
        clause = pc_support_clause(fact, original, self.f["policy"])
        self.assertEqual(clause["observed_at"], NOW - .5)

    def test_malformed_private_body_and_missing_material_use_bounded_refusal(self):
        for original in (None, [], {"schema": "home-original-pc-input-1"}):
            with self.subTest(original=original):
                with self.assertRaises(PCSupportRefusal):
                    pc_support_clause(self.f["fact"], original, self.f["policy"])
        with self.assertRaises(PCSupportRefusal):
            validate_pc_input_fact(self.f["fact"], self.f["source"], self.f["policy"], [], [], now=self.f["now"])


if __name__ == "__main__":
    unittest.main()

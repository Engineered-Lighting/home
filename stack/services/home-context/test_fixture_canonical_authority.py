"""Actual Journal + pinned pure C12 codec; authored trusted source/decoder ports.

No native executable, model, host, radio, physical transport or ID allocator is
run. Recorder retention is an explicit unit double, not ordinary replay proof.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from fixture_canonical_authority import (CanonicalAuthorityState, CanonicalFixtureAuthority,
    CanonicalFixtureAuthorityPolicy, CanonicalIssuerPolicy, KINDS)
from fixture_execution_validation import CAP_FIELDS, PIN_NAMES, FixtureAuthorityDenied, canonical_bytes, record_bytes
from journal import Journal
from test_fixture_joint_execution import AuthoredCurrentReference

PINNED_BUILD = Path(r"C:\Users\Marcelo\Documents\Engineered Light Fixture\validation\c12-offline\canonical-capture-6d5f2bc\build")
MANIFEST_SHA = "789533e2af58b4bf1a7f62be75db3ef118c96d8c4336b57cbd3bdd963d2bacf2"
PLAN_SHA = "477de391670b0b85a39df41135ae5e66fe68e87b1416a2e2792cd4780af7379d"


def pinned_pure_codec():
    """Only the reviewed pure plan/context codec; no canonical provider startup."""
    raw = (PINNED_BUILD / "manifest.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA:
        raise ValueError("pinned manifest changed")
    manifest = json.loads(raw)
    filename = Path(manifest["source"]["dir"]) / "tools/c12_runtime_plan.py"
    if manifest["source"]["files"]["tools/c12_runtime_plan.py"] != PLAN_SHA or hashlib.sha256(filename.read_bytes()).hexdigest() != PLAN_SHA:
        raise ValueError("pinned pure codec changed")
    # Execute verified source without generating caches inside immutable export.
    spec = importlib.util.spec_from_loader("canonical_authority_test_pure_plan", loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(filename.read_bytes(), str(filename), "exec"), module.__dict__)
    return module, manifest


@dataclass(frozen=True)
class AuthoredBinding:
    context: bytes
    binding_sha256: str
    context_digest: bytes
    device_id: bytes = bytes(range(16))
    boot_generation: int = 1
    session_id: int = 2
    ownership_generation: int = 4


class CanonicalAuthorityFixture:
    """Exported authored fixture for the coordinator's subsequent joined gate."""
    def __init__(self, directory):
        self.codec, manifest = pinned_pure_codec()
        self.journal = Journal(Path(directory) / "journal.sqlite")
        self.records = []
        self.journal._recorder = self.record
        self.now = 1100000
        self.context = self.codec.example_context()
        self.pins = {**self.context["pins"], **{k: hashlib.sha256(k.encode()).hexdigest() for k in PIN_NAMES[6:]}}
        raw_context = self.codec.encode_context(self.context)
        self.binding = AuthoredBinding(raw_context, "71" * 32, hashlib.sha256(raw_context).digest())
        self.home_scope = dict(site_id="Victoria", room_id="den", session_id="lab-1", domain_epoch="domain-1")
        self.room_binding = dict(configuration_digest="81" * 32, manifest_digest="82" * 32,
                                 worker_version="home-live-context-worker-11.0.0", room_output_schema="home-live-room-output-1")
        self.room_ref = {**self.room_binding, "commit_id": "commit-1", "commit_body_sha256": "83" * 32,
                         "decision_sha256": "84" * 32, "room_output_sha256": "85" * 32, "room_output_revision_id": "room:1"}
        self.provider = dict(manifest_sha256=MANIFEST_SHA, source_sha256=manifest["source_digest"],
                             executable_sha256=manifest["executable"]["sha256"], configuration_sha256=manifest["config_sha256"])
        self.policy = CanonicalFixtureAuthorityPolicy("fixture-A", bytes(range(16)).hex(), self.context, self.pins, "3",
            CanonicalIssuerPolicy("autonomous-ambient", "home-auto-ambient", "8", "authored-ambient-lookup"),
            CanonicalIssuerPolicy("autonomous-task", "home-auto-task", "7", "authored-task-lookup"),
            self.home_scope, self.room_binding, self.provider)
        self.status = dict(schema="c12-runtime-decoded-snapshot-1", document_version=3, phase="idle",
            boot_generation="1", session_id="2", ownership_generation="4", state_generation="8", revision_id="3",
            motion_id="0", context_digest=self.binding.context_digest.hex(), pins=self.context["pins"],
            domains=[dict(generation=0) for _ in range(8)], session_open=True, thermal_latched=False,
            simulated=True, physical_permission=False,
            current_semantic_target=dict(target_centideg=[0, 0], speed_deg_s=[0, 0], motion_timeout_us="0",
                start_deadline_device_us="0", preset_id="0", spotlight_frame=[0, 0, 0]))
        self.cap = dict(schema="c12-runtime-capabilities-1", document_version=2, pins=self.context["pins"],
                        qualified_mask=255, physical_permission=False)
        self.set_originals()
        self.reservations = {}
        self.validator = lambda p, s: True  # Explicit authored independent-validator double.
        self.state = CanonicalAuthorityState(True, self.home_scope, self.room_ref, "5", "6", "07" * 32,
            "08" * 32, "09" * 32, "950000", {key: "4000000" for key in CAP_FIELDS},
            self.ref, self.raw_status, self.raw_cap)
        self.authority = self.construct(create=True)

    def record(self, item):
        self.records.append(deepcopy(item))

    def set_originals(self, *, captured=100000):
        body = {k: v for k, v in self.status.items() if k not in ("original_document_hex", "original_document_sha256", "captured_device_us")}
        self.raw_status = (b"AUTHORED_STATUS3:" + hashlib.sha256(record_bytes(body)).digest()).ljust(998, b"\0")
        self.raw_cap = (b"AUTHORED_CAP2:" + hashlib.sha256(canonical_bytes(self.cap)).digest()).ljust(404, b"\0")
        self.status.update(original_document_hex=self.raw_status.hex(), original_document_sha256=hashlib.sha256(self.raw_status).hexdigest(),
                           captured_device_us=str(captured))
        self.ref = AuthoredCurrentReference(self.binding.binding_sha256, hashlib.sha256(self.raw_status).hexdigest(),
            hashlib.sha256(self.raw_cap).hexdigest(), 1, 2, 4, self.binding.context_digest.hex(),
            int(self.status["state_generation"]), int(self.status["revision_id"]), tuple(x["generation"] for x in self.status["domains"]),
            captured, captured, captured, 250000, "simulated")
        if hasattr(self, "state"):
            self.state = replace(self.state, current_ref=self.ref, raw_status_v3=self.raw_status, raw_capabilities_v2=self.raw_cap)

    def decode_status(self, raw, *, binding, captured_device_us):
        if raw != self.raw_status or binding != self.binding or str(captured_device_us) != self.status["captured_device_us"]:
            raise ValueError("authored status binding")
        return deepcopy(self.status)

    def decode_capabilities(self, raw, pins):
        if raw != self.raw_cap or pins != self.cap["pins"]:
            raise ValueError("authored capabilities binding")
        return deepcopy(self.cap)

    def resolve(self, mode):
        assert mode in ("autonomous-ambient", "autonomous-task")
        # Demonstrate the installed capability is invoked under sole journal lock.
        assert self.journal.lock._is_owned()
        return deepcopy(self.state)

    def construct(self, **kwargs):
        return CanonicalFixtureAuthority(self.journal, policy=self.policy, codec=self.codec, binding=self.binding,
            decode_status=self.decode_status, decode_capabilities=self.decode_capabilities, current_resolver=self.resolve,
            reservation_reader=lambda g, r: deepcopy(self.reservations[(g, r)]),
            command_validator=lambda p, s: self.validator(p, s), clock=lambda: self.now, **kwargs)

    def request(self, mode="autonomous-ambient", *, identity="request-1", grant="4", revision="4"):
        p = self.codec.example_plan()
        p.update(grant_id=grant, grant_body_sha256="0" * 64, revision_id=revision, predecessor_id=self.status["revision_id"],
            expected_state_generation=self.status["state_generation"], domain_generations=[x["generation"] for x in self.status["domains"]],
            context_digest=self.binding.context_digest.hex(), manual_duration_minutes=0,
            source_cut=self.state.source_cut, protection=self.state.protection, authority=self.state.authority,
            **self.state.caps)
        if mode == "autonomous-ambient":
            p.update(kind="LEVELS", domain_mask=1, field_mask=[3] + [0] * 7, frame=[0] * 24,
                     motion_id=self.status["motion_id"], **{key: deepcopy(self.status["current_semantic_target"][key])
                        for key in ("target_centideg", "speed_deg_s", "motion_timeout_us", "start_deadline_device_us", "preset_id")})
        r = dict(request_id=identity, intent_sha="aa" * 32, mode=mode, grant_id=grant, revision_id=revision,
                 state="reserved", content_sha=None, grant_sha=None, disposition=None, barrier=None)
        self.reservations[(grant, revision)] = deepcopy(r)
        return p, r

    def admit(self, mode="autonomous-ambient", *, identity="request-1", plan=None, reservation=None):
        if plan is None:
            plan, reservation = self.request(mode, identity=identity)
        method = self.authority.admit_ambient if mode == "autonomous-ambient" else self.authority.admit_task_joint
        return method(identity, reservation, plan)

    def lookup(self, receipt, *, credential=None):
        issuer = self.policy.ambient_issuer if receipt["grant_body"]["mode"] == "autonomous-ambient" else self.policy.task_issuer
        return self.authority.lookup(issuer.issuer_id, int(issuer.issuer_epoch), credential or issuer.lookup_credential,
                                     int(receipt["grant_body"]["grant_id"]), b"n" * 32)


class CanonicalAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[3] / "artifacts")
        self.addCleanup(self.directory.cleanup)
        self.f = CanonicalAuthorityFixture(self.directory.name)
        self.addCleanup(self.f.journal.close)

    def test_ambient_same_journal_uses_exact_pinned_codec_and_new_records(self):
        f = self.f
        receipt = f.admit()
        g = receipt["grant_body"]
        self.assertEqual(g["mode"], "autonomous-ambient")
        self.assertEqual(g["domain_mask"], 1)
        self.assertEqual(g["field_mask"][7], 0)
        self.assertEqual(bytes.fromhex(receipt["wire_hex"]), f.codec.encode_plan(receipt["plan"]))
        self.assertEqual(bytes.fromhex(receipt["context_hex"]), f.codec.encode_context(f.context))
        self.assertEqual([x["kind"] for x in f.records], ["fixture_canonical_grant_prepare", "fixture_canonical_grant_commit"])
        self.assertEqual(f.journal.db.execute("SELECT COUNT(*) FROM fixture_canonical_grants").fetchone()[0], 1)
        self.assertIsNone(f.journal.db.execute("SELECT name FROM sqlite_master WHERE name='fixture_execution_grants'").fetchone())
        self.assertFalse(receipt["physical_permission"])
        self.assertFalse(f.authority.describe()["id_allocator_implemented"])

    def test_disjoint_task_and_ambient_modes_share_namespace_without_cross_credentials(self):
        f = self.f
        ambient = f.admit()
        plan, reservation = f.request("autonomous-task", identity="task-2", grant="5", revision="5")
        task = f.admit("autonomous-task", identity="task-2", plan=plan, reservation=reservation)
        self.assertEqual(task["grant_body"]["domain_mask"], 128)
        self.assertEqual(f.lookup(ambient)["record"]["grant_body"], ambient["grant_body"])
        self.assertEqual(f.lookup(task)["record"]["grant_body"], task["grant_body"])
        with self.assertRaises(FixtureAuthorityDenied):
            f.lookup(task, credential=f.policy.ambient_issuer.lookup_credential)
        with self.assertRaises(FixtureAuthorityDenied):
            f.authority.lookup(f.policy.ambient_issuer.issuer_id, 8, f.policy.ambient_issuer.lookup_credential, 5, b"n" * 32)

    def test_ambient_cannot_authorize_task_manual_or_unselected_fields(self):
        f = self.f
        for change in (dict(domain_mask=128), dict(domain_mask=129), dict(manual=True), dict(preset_id="1"),
                       dict(kind="MOVE"), dict(field_mask=[3] + [0] * 6 + [8]), dict(field_mask=[3, 1] + [0] * 6)):
            p, r = f.request()
            p.update(change)
            with self.subTest(change=change), self.assertRaises(FixtureAuthorityDenied):
                f.admit(plan=p, reservation=r)
        self.assertFalse(f.records)

    def test_task_route_cannot_authorize_ambient(self):
        p, r = self.f.request("autonomous-task")
        p.update(kind="LEVELS", domain_mask=1, field_mask=[3] + [0] * 7)
        with self.assertRaises(FixtureAuthorityDenied):
            self.f.admit("autonomous-task", plan=p, reservation=r)

    def test_ambient_preserves_actual_task_spotlight_and_all_motion_fields(self):
        f = self.f
        for key, changed in (("motion_id", "1"), ("target_centideg", [1, 0]), ("speed_deg_s", [1, 1]),
                             ("motion_timeout_us", "1"), ("start_deadline_device_us", "1"), ("frame", [0] * 21 + [1, 0, 0])):
            p, r = f.request()
            p[key] = changed
            with self.subTest(key=key), self.assertRaisesRegex(FixtureAuthorityDenied, "retained task"):
                f.admit(plan=p, reservation=r)

    def test_json_state_false_validator_and_unconsumed_reservation_do_not_grant(self):
        f = self.f
        f.authority.current_resolver = lambda _: dict(eligible=True)
        with self.assertRaises(FixtureAuthorityDenied):
            f.admit()
        f.authority.current_resolver = f.resolve
        f.validator = lambda *_: False
        with self.assertRaisesRegex(FixtureAuthorityDenied, "independent"):
            f.admit()
        f.validator = lambda *_: True
        p, r = f.request()
        f.reservations[("4", "4")]["state"] = "dispatching"
        with self.assertRaisesRegex(FixtureAuthorityDenied, "unconsumed"):
            f.admit(plan=p, reservation=r)
        self.assertFalse(f.records)

    def test_raw_hash_scope_configuration_generation_and_all8_domains_refuse(self):
        f = self.f
        original = f.state
        changes = (dict(raw_status_v3=b"x" * 998), dict(raw_capabilities_v2=b"x" * 404),
            dict(home_scope={**f.home_scope, "room_id": "other"}),
            dict(room_commit_ref={**f.room_ref, "configuration_digest": "ff" * 32}),
            dict(current_ref=replace(f.ref, ownership_generation=5)),
            dict(current_ref=replace(f.ref, domain_generations=(0,) * 7 + (1,))))
        for change in changes:
            f.state = replace(original, **change)
            with self.subTest(change=list(change)), self.assertRaises(FixtureAuthorityDenied):
                f.admit()
        f.state = original
        for change in (dict(predecessor_id="0"), dict(expected_state_generation="9"), dict(domain_generations=[0] * 7 + [1])):
            p, r = f.request()
            p.update(change)
            with self.subTest(change=change), self.assertRaises(FixtureAuthorityDenied):
                f.admit(plan=p, reservation=r)
        self.assertFalse(f.records)

    def test_expired_stale_native_or_lagging_clock_cannot_issue(self):
        f = self.f
        original = f.state
        for change in (dict(device_now_lower_us=350000, device_now_upper_us=350000),
                       dict(device_now_lower_us=100000, device_now_upper_us=100001)):
            f.state = replace(original, current_ref=replace(f.ref, **change))
            with self.assertRaisesRegex(FixtureAuthorityDenied, "clock bound"):
                f.admit()
        f.state = original
        f.now = 3900000
        with self.assertRaises(FixtureAuthorityDenied):
            f.admit()

    def test_exact_retry_restart_returns_original_even_expired_changed_retry_refuses(self):
        f = self.f
        p, r = f.request()
        receipt = f.admit(plan=p, reservation=r)
        f.now = 5000000
        f.authority = f.construct()
        self.assertEqual(f.admit(plan=p, reservation=r), receipt)
        self.assertEqual(len(f.records), 2)
        p["issued_home_us"] = "1000001"
        with self.assertRaisesRegex(FixtureAuthorityDenied, "changed immutable"):
            f.admit(plan=p, reservation=r)

    def test_lookup_can_advance_current_clock_without_refreshing_original_availability(self):
        f = self.f
        receipt = f.admit()
        f.state = replace(f.state, current_ref=replace(f.ref, device_now_lower_us=100100, device_now_upper_us=100100))
        f.now += 100
        lookup = f.lookup(receipt)
        self.assertEqual(lookup["record"]["grant_body"]["original_available_home_us"], "1100000")
        self.assertEqual(lookup["record"]["status_observed_home_us"], "1100100")

    def test_changed_room_native_generations_or_caps_supersede_original_lookup(self):
        f = self.f
        receipt = f.admit()
        original = f.state
        for change in (dict(room_commit_ref={**f.room_ref, "commit_id": "commit-2"}),
                       dict(source_generation="7"), dict(caps={**original.caps, CAP_FIELDS[0]: "3999999"})):
            f.state = replace(original, **change)
            with self.subTest(change=list(change)), self.assertRaises(FixtureAuthorityDenied):
                f.lookup(receipt)
        f.state = original
        f.status["revision_id"] = "4"
        f.status["state_generation"] = "9"
        f.status["domains"][0]["generation"] = 1
        f.set_originals()
        with self.assertRaisesRegex(FixtureAuthorityDenied, "superseded"):
            f.lookup(receipt)

    def test_validator_expiry_cannot_be_minted_as_fresh_grant(self):
        f = self.f
        def expires(*_):
            f.now = 3900000
            return True
        f.validator = expires
        with self.assertRaises(FixtureAuthorityDenied):
            f.admit()
        self.assertFalse(f.records)

    def test_current_room_change_during_durable_prepare_is_sticky_and_unissued(self):
        f = self.f
        def changes(record):
            f.record(record)
            if record["kind"] == "fixture_canonical_grant_prepare":
                f.state = replace(f.state, room_commit_ref={**f.room_ref, "commit_id": "commit-2"})
        f.journal._recorder = changes
        with self.assertRaisesRegex(FixtureAuthorityDenied, "during canonical retention"):
            f.admit()
        self.assertEqual(f.journal.db.execute("SELECT COUNT(*) FROM fixture_canonical_grants").fetchone()[0], 0)
        self.assertTrue(f.journal._recorder_failed)

    def test_lost_durable_commit_ack_remains_blocked_across_reconstruction(self):
        f = self.f
        def lost(record):
            f.record(record)
            if record["kind"] == "fixture_canonical_grant_commit":
                raise OSError("authored lost archive ACK")
        f.journal._recorder = lost
        with self.assertRaises(OSError):
            f.admit()
        self.assertEqual(f.journal.db.execute("SELECT COUNT(*) FROM fixture_canonical_grants").fetchone()[0], 1)
        f.journal._recorder_failed = False
        f.journal._recorder = f.record
        f.authority = f.construct()
        with self.assertRaisesRegex(FixtureAuthorityDenied, "unconfirmed"):
            f.admit()

    def test_recording_delay_blocks_lookup_and_never_retimes_original_grant_or_caps(self):
        f = self.f
        creation_time = f.now
        blocked_phases = []
        def delayed(record):
            f.record(record)
            with self.assertRaisesRegex(FixtureAuthorityDenied, "unconfirmed"):
                f.authority.lookup(f.policy.ambient_issuer.issuer_id, 8,
                    f.policy.ambient_issuer.lookup_credential, 4, b"n" * 32)
            blocked_phases.append(record["kind"])
            f.now += 100000
        f.journal._recorder = delayed
        receipt = f.admit()
        # This test's clock sample after return is consumer-side information;
        # it is not inserted retroactively into the immutable G1 body.
        first_consumer_receipt = f.now
        g = receipt["grant_body"]
        self.assertEqual(blocked_phases, ["fixture_canonical_grant_prepare", "fixture_canonical_grant_commit"])
        self.assertGreater(first_consumer_receipt, creation_time)
        self.assertEqual(g["admitted_home_us"], str(creation_time))
        self.assertEqual(g["original_available_home_us"], str(creation_time))
        self.assertEqual(g["caps"], {key: "4000000" for key in CAP_FIELDS})
        self.assertEqual(g["support_expiry_home_us"], "3900000")
        lookup = f.lookup(receipt)
        self.assertEqual(lookup["record"]["status_available_home_us"], str(first_consumer_receipt))
        self.assertEqual(f.admit(), receipt)
        f.now = 3900000
        with self.assertRaises(FixtureAuthorityDenied):
            f.lookup(receipt)

    def test_support_expiring_during_prepare_never_becomes_durable_admission(self):
        f = self.f
        def expires(record):
            f.record(record)
            if record["kind"] == "fixture_canonical_grant_prepare":
                f.now = 3900000
        f.journal._recorder = expires
        with self.assertRaises(FixtureAuthorityDenied):
            f.admit()
        self.assertEqual(f.journal.db.execute("SELECT COUNT(*) FROM fixture_canonical_grants").fetchone()[0], 0)
        self.assertTrue(f.journal._recorder_failed)

    def test_recorder_failure_and_absent_router_never_fall_back_to_legacy(self):
        f = self.f
        def missing_router(record):
            if record["kind"] in KINDS:
                raise ValueError("canonical archive router unavailable")
            self.fail("unexpected old recorder fallback")
        f.journal._recorder = missing_router
        with self.assertRaisesRegex(ValueError, "router unavailable"):
            f.admit()
        self.assertTrue(f.journal._recorder_failed)
        self.assertEqual(f.journal.db.execute("SELECT COUNT(*) FROM fixture_canonical_grants").fetchone()[0], 0)

    def test_clock_rollback_revocation_and_changed_policy_are_not_reset(self):
        f = self.f
        receipt = f.admit()
        f.authority.revoke("4", reason="operator-withdrawal")
        with self.assertRaisesRegex(FixtureAuthorityDenied, "revoked"):
            f.lookup(receipt)
        f.now -= 1
        with self.assertRaisesRegex(FixtureAuthorityDenied, "rollback"):
            f.authority._now()
        f.policy = replace(f.policy, room_binding={**f.room_binding, "manifest_digest": "ff" * 32})
        with self.assertRaisesRegex(FixtureAuthorityDenied, "no migration"):
            f.construct()

    def test_native_device_must_match_fixed_policy_and_other_fixture_can_share_journal(self):
        f = self.f
        f.policy = replace(f.policy, device_id="11" * 16)
        with self.assertRaisesRegex(FixtureAuthorityDenied, "device identity"):
            f.construct(create=True)
        # This asserts independent commissioning namespaces only; no source
        # authority or grant is asserted for the second authored fixture.
        f.binding = replace(f.binding, device_id=bytes.fromhex("11" * 16), binding_sha256="72" * 32)
        f.policy = replace(f.policy, fixture_id="fixture-B")
        other = f.construct(create=True)
        self.assertNotEqual(other.namespace, f.authority.namespace)
        self.assertEqual(f.journal.db.execute("SELECT COUNT(*) FROM fixture_canonical_meta").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()

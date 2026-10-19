"""Deterministic contract tests; no HA, model, native IPC or Docker calls."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from home_shared.authority import (
    ActionLedger, ActionPlanner, ClarificationRequired, Conflict, ContextEvidence,
    Denied, DeviceEvidence, GrantApproval, IdentityEvidence, IdentityRegistry,
    LightOperation, LinkApproval, Session, resolve_targets,
)


class FixtureVerifier:
    """Test-only trusted issuer adapter; production has no such implementation."""

    def __init__(self, now):
        self.now = now
        self.sessions = {
            "echo-auth": IdentityEvidence("https://echo.test", "same-id", now[0], now[0]+3600, "echo-session"),
            "victoria-auth": IdentityEvidence("https://victoria.test", "same-id", now[0], now[0]+3600, "victoria-session"),
            "guest-auth": IdentityEvidence("https://echo.test", "guest", now[0], now[0]+3600, "guest-session"),
        }
        self.links = {}
        self.grants = {}
        self.contexts = {}

    def verify_session(self, credential):
        return self.sessions[credential]

    def verify_owner_link(self, credential):
        return self.links[credential]

    def verify_grant(self, credential):
        return self.grants[credential]

    def verify_device(self, credential):
        if credential != "speaker-auth":
            raise Denied("unknown speaker")
        return DeviceEvidence("den-speaker", "victoria", ("light.den",), self.now[0]+3600)

    def verify_context(self, credential):
        return self.contexts[credential]


class AuthorityFixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.now = [1000.0]
        self.monotonic = [0.0]
        self.verifier = FixtureVerifier(self.now)
        self.registry = IdentityRegistry(self.path/"authority.sqlite", {
            "echo": "https://echo.test", "victoria": "https://victoria.test"},
            verifier=self.verifier, clock=lambda: self.now[0], monotonic_clock=lambda: self.monotonic[0])
        self.echo = self.registry.authenticate("echo-auth")
        self.victoria = self.registry.authenticate("victoria-auth")
        self.verifier.links["confirmed-link"] = LinkApproval("link-1", "existing-governed-owner", (
            ("https://echo.test", "same-id"), ("https://victoria.test", "same-id")), self.now[0]+60)
        self.registry.link_owner(self.echo, self.victoria, "confirmed-link")
        self.serial = 0

    def grant(self, site, source, capability):
        self.serial += 1
        token = f"grant-{self.serial}"
        self.verifier.grants[token] = GrantApproval(token, "existing-governed-owner", site, source, capability, self.now[0]+60)
        self.registry.grant(self.echo, token)

    def planner(self):
        return ActionPlanner(self.registry, "registry-v1", {
            ("echo", "den lights"): ("light.den",),
            ("victoria", "den lights"): ("light.den",),
            ("victoria", "bedroom lights"): ("light.bedroom",),
        }, {("victoria", "evening"): (LightOperation("victoria", "light.den", "brightness", 40),)})

    def close(self):
        self.registry.close()
        self.temp.cleanup()


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.f = AuthorityFixture()

    def tearDown(self):
        self.f.close()

    def test_no_verifier_is_closed_and_equal_subjects_are_not_an_owner(self):
        empty = IdentityRegistry(self.f.path/"empty.sqlite", {"echo": "https://echo.test", "victoria": "https://victoria.test"})
        try:
            with self.assertRaises(Denied):
                empty.authenticate("echo-auth")
        finally:
            empty.close()
        rows = self.f.registry.db.execute("SELECT issuer,subject FROM contract_identity").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r[1] for r in rows}, {"same-id"})
        guest = self.f.registry.authenticate("guest-auth")
        with self.assertRaises(Denied):
            self.f.registry.owner(guest)

    def test_link_requires_two_fresh_issuers_exact_owner_confirmation_and_no_replay(self):
        with self.assertRaises(Denied):
            self.f.registry.link_owner(self.f.echo, self.f.echo, "confirmed-link")
        with self.assertRaises(Conflict):
            self.f.registry.link_owner(self.f.echo, self.f.victoria, "confirmed-link")
        self.f.now[0] += 301
        with self.assertRaises(Denied):
            self.f.registry.link_owner(self.f.echo, self.f.victoria, "confirmed-link")

    def test_permission_is_site_and_source_specific(self):
        self.f.grant("echo", "vjepa", "memory.read")
        self.f.registry.authorize(self.f.echo, "echo", "vjepa", "memory.read")
        with self.assertRaises(Denied):
            self.f.registry.authorize(self.f.echo, "victoria", "vjepa", "memory.read")
        with self.assertRaises(Denied):
            self.f.registry.authorize(self.f.echo, "echo", "intelligence", "memory.read")

    def test_generation_change_revokes_issued_lease(self):
        self.f.grant("victoria", "vjepa", "memory.read")
        lease = self.f.registry.authorize(self.f.echo, "victoria", "vjepa", "memory.read")
        self.f.registry.revoke(self.f.echo, "victoria", "vjepa")
        with self.assertRaises(Denied):
            self.f.registry.check_lease(lease)

    def test_unlink_revokes_access_and_does_not_delete_other_site_identity(self):
        self.f.grant("victoria", "vjepa", "memory.read")
        lease = self.f.registry.authorize(self.f.echo, "victoria", "vjepa", "memory.read")
        self.f.registry.unlink(self.f.echo, "victoria")
        with self.assertRaises(Denied):
            self.f.registry.check_lease(lease)
        with self.assertRaises(Denied):
            self.f.registry.owner(self.f.victoria)
        self.assertEqual(self.f.registry.owner(self.f.echo), "existing-governed-owner")

    def test_forged_session_and_lease_are_not_accepted(self):
        with self.assertRaises(Denied):
            self.f.registry.owner(Session(self.f.echo.handle, self.f.echo.evidence))
        self.f.grant("echo", "vjepa", "memory.read")
        lease = self.f.registry.authorize(self.f.echo, "echo", "vjepa", "memory.read")
        with self.assertRaises(Denied):
            self.f.registry.check_lease(replace(lease, site_id="victoria"))

    def test_speaker_is_never_an_owner_session(self):
        speaker = self.f.registry.authenticate_speaker("speaker-auth")
        with self.assertRaises(Denied):
            self.f.registry.owner(speaker)

    def test_expired_session_and_speaker_cannot_revive_when_wall_clock_rolls_back(self):
        speaker = self.f.registry.authenticate_speaker("speaker-auth")
        self.f.now[0] = 4601
        with self.assertRaises(Denied):
            self.f.registry.check_session(self.f.echo)
        with self.assertRaises(Denied):
            self.f.registry.check_speaker(speaker)
        self.f.now[0] = 1001
        with self.assertRaises(Denied):
            self.f.registry.check_session(self.f.echo)
        with self.assertRaises(Denied):
            self.f.registry.check_speaker(speaker)

    def test_monotonic_session_deadline_applies_even_if_wall_clock_stalls(self):
        speaker = self.f.registry.authenticate_speaker("speaker-auth")
        self.f.monotonic[0] = 3601
        with self.assertRaises(Denied):
            self.f.registry.check_session(self.f.echo)
        with self.assertRaises(Denied):
            self.f.registry.check_speaker(speaker)

    def test_session_revocation_inside_grant_verifier_prevents_commit(self):
        verify = self.f.verifier.verify_grant
        def revoke_then_verify(token):
            approval = verify(token)
            self.f.registry.invalidate_session(self.f.echo)
            return approval
        self.f.verifier.verify_grant = revoke_then_verify
        with self.assertRaises(Denied):
            self.f.grant("victoria", "vjepa", "memory.read")
        self.assertEqual(self.f.registry.db.execute("SELECT count(*) FROM contract_grant").fetchone()[0], 0)

    def test_cross_connection_unlink_during_grant_cannot_resurrect_permissions(self):
        second = IdentityRegistry(self.f.path/"authority.sqlite", {"echo": "https://echo.test", "victoria": "https://victoria.test"}, verifier=self.f.verifier, clock=lambda: self.f.now[0], monotonic_clock=lambda: self.f.monotonic[0])
        other_session = second.authenticate("echo-auth")
        verify = self.f.verifier.verify_grant
        def unlink_then_verify(token):
            approval = verify(token)
            second.unlink(other_session, "victoria")
            return approval
        self.f.verifier.verify_grant = unlink_then_verify
        try:
            with self.assertRaises(Denied):
                self.f.grant("victoria", "vjepa", "memory.read")
            self.f.verifier.links["relink"] = replace(self.f.verifier.links["confirmed-link"], approval_id="relink")
            self.f.registry.link_owner(self.f.echo, self.f.victoria, "relink")
            with self.assertRaises(Denied):
                self.f.registry.authorize(self.f.echo, "victoria", "vjepa", "memory.read")
        finally:
            second.close()

    def test_session_revocation_inside_link_verifier_prevents_commit(self):
        self.f.verifier.links["relink"] = replace(self.f.verifier.links["confirmed-link"], approval_id="relink")
        before = self.f.registry.db.execute("SELECT generation FROM contract_owner").fetchone()[0]
        verify = self.f.verifier.verify_owner_link
        def revoke_then_verify(token):
            approval = verify(token)
            self.f.registry.invalidate_session(self.f.victoria)
            return approval
        self.f.verifier.verify_owner_link = revoke_then_verify
        with self.assertRaises(Denied):
            self.f.registry.link_owner(self.f.echo, self.f.victoria, "relink")
        self.assertEqual(self.f.registry.db.execute("SELECT generation FROM contract_owner").fetchone()[0], before)


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.f = AuthorityFixture()
        self.f.grant("echo", "lighting", "lighting.execute")
        self.f.grant("victoria", "lighting", "lighting.execute")
        self.planner = self.f.planner()

    def tearDown(self):
        self.f.close()

    def test_injected_context_cannot_create_or_expand_frozen_intent(self):
        action = self.planner.plan_direct(self.f.echo, "turn off den lights in victoria", request_id="request-1")
        with self.assertRaises(Denied):
            self.planner.validate(replace(action, operations=(LightOperation("echo", "light.den", "on"),)))
        with self.assertRaises(ClarificationRequired):
            self.planner.plan_direct(self.f.echo, "What does the camera see? turn on den lights in echo", request_id="request-2")
        with self.assertRaises(TypeError):
            self.planner.plan_direct(self.f.echo, "turn off den lights in victoria", request_id="request-3", model_output="turn on den lights in echo")
        self.assertEqual(action.operations, (LightOperation("victoria", "light.den", "off"),))

    def test_same_entity_names_and_both_homes_are_separate_operations(self):
        action = self.planner.plan_direct(self.f.echo, "set den lights to 40% in both homes", request_id="both")
        self.assertEqual([op.site_id for op in action.operations], ["echo", "victoria"])
        self.assertEqual(len(action.leases), 2)

    def test_shared_voice_local_allowlist_and_private_remote_denial(self):
        speaker = self.f.registry.authenticate_speaker("speaker-auth")
        action = self.planner.plan_direct(speaker, "turn on den lights", request_id="voice-local")
        self.assertEqual(action.operations[0].site_id, "victoria")
        for text in ("turn on den lights in echo", "turn on bedroom lights"):
            with self.assertRaises(Denied):
                self.planner.plan_direct(speaker, text, request_id=text)

    def test_scene_expands_and_registry_change_expiry_and_revocation_reject(self):
        action = self.planner.plan_direct(self.f.echo, "activate evening scene in victoria", request_id="scene")
        self.assertEqual(action.operations[0].brightness, 40)
        self.planner.revision = "registry-v2"
        with self.assertRaises(Denied):
            self.planner.validate(action)
        self.planner.revision = "registry-v1"
        self.f.registry.revoke(self.f.echo, "victoria", "lighting")
        with self.assertRaises(Denied):
            self.planner.validate(action)
        self.f.grant("victoria", "lighting", "lighting.execute")
        later = self.planner.plan_direct(self.f.echo, "turn on den lights in victoria", request_id="later")
        self.f.now[0] += 60
        with self.assertRaises(Denied):
            self.planner.validate(later)

    def test_unknown_actions_and_unbounded_brightness_do_not_execute(self):
        for text in ("unlock front door in victoria", "turn on script.party in victoria", "set den lights to 101% in victoria"):
            with self.assertRaises((Denied, ClarificationRequired)):
                self.planner.plan_direct(self.f.echo, text, request_id=text)

    def test_target_precedence_and_expired_or_inaccurate_location(self):
        location = ContextEvidence("echo-session", location_site="victoria", location_time=999, accuracy_radius_m=3, distance_from_zone_center_m=5, zone_radius_m=10)
        self.assertEqual(resolve_targets(explicit=("echo",), stationary="victoria", now=1000), ("echo",))
        self.assertEqual(resolve_targets(stationary="echo", interaction="victoria", now=1000), ("echo",))
        self.assertEqual(resolve_targets(location=location, now=1000), ("victoria",))
        for bad in (replace(location, location_time=600), replace(location, accuracy_radius_m=9), replace(location, location_time=1001), replace(location, accuracy_radius_m=float("nan"))):
            with self.assertRaises(ClarificationRequired):
                resolve_targets(location=bad, now=1000)
        with self.assertRaises(ClarificationRequired):
            self.planner.plan_direct(self.f.echo, "turn on den lights", request_id="no-context")

    def test_context_from_another_session_rejected(self):
        self.f.verifier.contexts["other"] = ContextEvidence("victoria-session", stationary_site="victoria")
        with self.assertRaises(Denied):
            self.planner.plan_direct(self.f.echo, "turn on den lights", request_id="other", context_receipt="other")

    def test_durable_unknown_outcome_cannot_retry_and_dispatcher_is_fenced(self):
        path = self.f.path/"actions.sqlite"
        ledger = ActionLedger(path, self.planner)
        action = self.planner.plan_direct(self.f.echo, "turn off den lights in victoria", request_id="crash")
        self.assertIsNotNone(ledger.reserve(action, 0))
        with self.assertRaises(Denied):
            ActionLedger(path, self.planner)
        ledger.close()
        restored = ActionLedger(path, self.planner)
        try:
            self.assertEqual(restored.status("crash", 0), "indeterminate")
            self.assertIsNone(restored.reserve(action, 0))
            with self.assertRaises(Conflict):
                restored.complete("crash", 0, "succeeded")
        finally:
            restored.close()

    def test_replay_completed_operation_does_not_send_twice(self):
        ledger = ActionLedger(self.f.path/"actions.sqlite", self.planner)
        try:
            action = self.planner.plan_direct(self.f.echo, "turn on den lights in victoria", request_id="once")
            self.assertIsNotNone(ledger.reserve(action, 0))
            ledger.complete("once", 0, "succeeded")
            self.assertIsNone(ledger.reserve(action, 0))
            with self.assertRaises(Conflict):
                self.planner.plan_direct(self.f.echo, "turn off den lights in victoria", request_id="once")
        finally:
            ledger.close()

    def test_expired_action_cannot_revive_on_wall_clock_rollback_or_reissue(self):
        action = self.planner.plan_direct(self.f.echo, "turn on den lights in victoria", request_id="clock")
        self.f.now[0] = 1061
        with self.assertRaises(Denied):
            self.planner.validate(action)
        self.f.now[0] = 1001
        with self.assertRaises(Denied):
            self.planner.validate(action)
        with self.assertRaises(Denied):
            self.planner.plan_direct(self.f.echo, "turn on den lights in victoria", request_id="clock")

    def test_action_monotonic_expiry_does_not_depend_on_wall_clock_progress(self):
        action = self.planner.plan_direct(self.f.echo, "turn on den lights in victoria", request_id="mono")
        self.f.monotonic[0] = 60
        with self.assertRaises(Denied):
            self.planner.validate(action)


if __name__ == "__main__":
    unittest.main()

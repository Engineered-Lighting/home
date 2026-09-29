#!/usr/bin/env python3
"""Standalone tests for the signed, light-only actuation endpoint."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Import the pure module without the HA-dependent package initializer.
package = types.ModuleType("home_agent_edge")
package.__path__ = [str(Path(__file__).resolve().parent)]
sys.modules.setdefault("home_agent_edge", package)

import home_agent_edge.lighting as lighting  # noqa: E402
from home_agent_edge.lighting import (  # noqa: E402
    EXECUTE_URL,
    INVENTORY_URL,
    OUTCOME_URL,
    LightingEndpoint,
    LightingLedger,
    LightingPolicy,
    LightingRejected,
    sign,
)

SECRET = "ab" * 32
NOW = 1_800_000_000_000
lighting.VERIFY_S = 0.05
lighting.VERIFY_STEP_S = 0.01


def light(state, brightness=None, modes=("brightness",), **extra):
    return {"state": state, "brightness": brightness, "supported_color_modes": list(modes), **extra}


class Harness:
    def __init__(self, directory: str, *, states=None, fail=None, delay=0.0, apply=True) -> None:
        self.policy = LightingPolicy.from_config({"site_id": "victoria", "secret": SECRET,
            "entities": ["light.kitchen", "light.porch", "light.den"],
            "ledger_path": str(Path(directory) / "ledger.sqlite")})
        self.ledger = LightingLedger(self.policy.ledger_path)
        self.calls: list[tuple[str, dict]] = []
        self.now = NOW
        self.states = states if states is not None else {
            "light.kitchen": light("on", 128, name="Kitchen"),
            "light.porch": light("off", modes=("onoff",), name="Porch"),
            "light.den": light("on", 255, entity_id=["light.a", "light.b"], name="Den"),
            "light.garage": light("on", 255, name="Garage"),
        }

        async def call(service, data):
            self.calls.append((service, data))
            if delay:
                await asyncio.sleep(delay)
            if fail:
                raise fail
            if apply:
                current = self.states[data["entity_id"]]
                if service == "turn_off":
                    current.update(state="off", brightness=None)
                else:
                    pct = data.get("brightness_pct")
                    current.update(state="on", brightness=round(pct * 255 / 100) if pct else current.get("brightness") or 255)

        self.endpoint = LightingEndpoint(self.policy, self.ledger, states=self.states.get,
                                         call_service=call, now=lambda: self.now)

    def signed(self, path: str, value: dict, *, secret: bytes | None = None) -> tuple[bytes, str]:
        body = json.dumps(value).encode()
        return body, sign(secret or self.policy.secret, path, body)

    def execute_body(self, **overrides) -> dict:
        value = {"version": 1, "site_id": "victoria", "issued_at": self.now, "revision": self.policy.revision,
                 "request_id": str(uuid.UUID(int=1)), "operation_index": 0, "entity_id": "light.kitchen",
                 "operation": "off", "brightness": None, "expires_at": NOW + 60_000}
        value.update(overrides)
        return value

    def execute(self, **overrides) -> dict:
        body, signature = self.signed(EXECUTE_URL, self.execute_body(**overrides))
        return asyncio.run(self.endpoint.execute(body, signature))

    def outcome(self, request_id: str, index: int = 0) -> str:
        self.now += 1
        body, signature = self.signed(OUTCOME_URL, {"version": 1, "site_id": "victoria", "issued_at": self.now,
                                                    "request_id": request_id, "operation_index": index})
        return asyncio.run(self.endpoint.outcome(body, signature))["status"]


class LightingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def harness(self, **kwargs) -> Harness:
        harness = Harness(tempfile.mkdtemp(dir=self.directory.name), **kwargs)
        self.addCleanup(harness.ledger.close)
        return harness

    def rejected(self, status: int, code: str, action) -> None:
        with self.assertRaises(LightingRejected) as caught:
            action()
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code))

    def test_policy_requires_site_secret_and_a_unique_light_allowlist(self) -> None:
        base = {"site_id": "victoria", "secret": SECRET, "entities": ["light.kitchen"]}
        for bad in ({**base, "site_id": "paris"}, {**base, "secret": "AB" * 32}, {**base, "secret": "ab"},
                    {**base, "entities": []}, {**base, "entities": ["switch.fan"]},
                    {**base, "entities": ["light.a", "light.a"]}, {**base, "entities": ["scene.evening"]},
                    {**base, "ledger_path": "relative.sqlite"}, {**base, "extra": True}, "lighting"):
            with self.assertRaises(ValueError):
                LightingPolicy.from_config(bad)
        policy = LightingPolicy.from_config(base)
        self.assertNotIn(SECRET, repr(policy))
        self.assertNotIn("abab", repr(policy))

    def test_revision_follows_the_allowlist_not_its_order(self) -> None:
        one = LightingPolicy.from_config({"site_id": "echo", "secret": SECRET, "entities": ["light.b", "light.a"]})
        two = LightingPolicy.from_config({"site_id": "echo", "secret": SECRET, "entities": ["light.a", "light.b"]})
        three = LightingPolicy.from_config({"site_id": "echo", "secret": SECRET, "entities": ["light.a"]})
        self.assertEqual(one.revision, two.revision)
        self.assertNotEqual(one.revision, three.revision)

    def test_unsigned_malformed_or_wrongly_signed_requests_do_not_act(self) -> None:
        h = self.harness()
        body, _ = h.signed(EXECUTE_URL, h.execute_body())
        for signature in (None, "", "é" * 64, "AB" * 32, "ab" * 31):
            self.rejected(401, "signature_invalid", lambda s=signature: asyncio.run(h.endpoint.execute(body, s)))
        _, wrong = h.signed(EXECUTE_URL, h.execute_body(), secret=b"x" * 32)
        self.rejected(401, "signature_invalid", lambda: asyncio.run(h.endpoint.execute(body, wrong)))
        # A signature for another path cannot be reused here.
        _, other = h.signed(OUTCOME_URL, h.execute_body())
        self.rejected(401, "signature_invalid", lambda: asyncio.run(h.endpoint.execute(body, other)))
        self.assertEqual(h.calls, [])

    def test_skewed_issue_times_other_sites_and_non_integer_versions_are_refused(self) -> None:
        h = self.harness()
        for overrides in ({"issued_at": NOW - 60_001}, {"issued_at": NOW + 60_001}, {"site_id": "echo"},
                          {"version": True}, {"version": 1.0}):
            self.rejected(400, "invalid_request", lambda o=overrides: h.execute(**o))
        self.assertEqual(h.calls, [])

    def test_exact_replays_are_refused_until_the_issue_time_leaves_the_window(self) -> None:
        h = self.harness()
        issued = NOW + 60_000
        body, signature = h.signed(INVENTORY_URL, {"version": 1, "site_id": "victoria", "issued_at": issued})
        asyncio.run(h.endpoint.inventory(body, signature))
        for later in (NOW + 1, NOW + 120_000):
            h.now = later
            self.rejected(409, "replayed_request", lambda: asyncio.run(h.endpoint.inventory(body, signature)))
        h.now = NOW + 120_001
        self.rejected(400, "invalid_request", lambda: asyncio.run(h.endpoint.inventory(body, signature)))

    def test_off_on_and_brightness_map_to_light_services_and_are_verified(self) -> None:
        h = self.harness()
        self.assertEqual(h.execute()["status"], "succeeded")
        self.assertEqual(h.execute(request_id=str(uuid.UUID(int=2)), operation="on")["status"], "succeeded")
        self.assertEqual(h.execute(request_id=str(uuid.UUID(int=3)), operation="brightness", brightness=40)["status"],
                         "succeeded")
        self.assertEqual(h.calls, [("turn_off", {"entity_id": "light.kitchen"}),
                                   ("turn_on", {"entity_id": "light.kitchen"}),
                                   ("turn_on", {"entity_id": "light.kitchen", "brightness_pct": 40})])

    def test_a_call_whose_state_never_changes_is_indeterminate_not_succeeded(self) -> None:
        h = self.harness(apply=False)
        self.assertEqual(h.execute()["status"], "indeterminate")
        self.assertEqual(len(h.calls), 1)

    def test_only_allowlisted_lights_and_bounded_operations(self) -> None:
        h = self.harness()
        for overrides in ({"entity_id": "light.garage"}, {"entity_id": "lock.front_door"}, {"operation": "toggle"},
                          {"operation": "brightness", "brightness": 0}, {"operation": "brightness", "brightness": 101},
                          {"operation": "brightness", "brightness": None}, {"operation": "on", "brightness": 50},
                          {"operation": "brightness", "brightness": True}):
            self.rejected(400, "operation_not_allowed", lambda o=overrides: h.execute(**o))
        self.assertEqual(h.calls, [])

    def test_light_groups_are_refused_and_reported_unsupported(self) -> None:
        h = self.harness()
        self.rejected(400, "operation_not_allowed", lambda: h.execute(entity_id="light.den"))
        h.states["light.den"] = light("on", 255, is_hue_group=True)
        self.rejected(400, "operation_not_allowed", lambda: h.execute(entity_id="light.den", issued_at=NOW + 1))
        self.assertEqual(h.calls, [])

    def test_brightness_on_a_non_dimmable_light_is_refused_before_any_call(self) -> None:
        h = self.harness()
        self.rejected(400, "operation_not_supported",
                      lambda: h.execute(entity_id="light.porch", operation="brightness", brightness=10))
        self.assertEqual(h.execute(entity_id="light.porch", operation="on")["status"], "succeeded")
        self.assertEqual(h.calls, [("turn_on", {"entity_id": "light.porch"})])

    def test_changed_allowlist_and_expired_or_far_future_requests_are_refused(self) -> None:
        h = self.harness()
        self.rejected(409, "allowlist_changed", lambda: h.execute(revision="0" * 64))
        self.rejected(409, "request_expired", lambda: h.execute(expires_at=NOW))
        self.rejected(409, "request_expired", lambda: h.execute(expires_at=NOW + 120_001))
        self.assertEqual(h.calls, [])

    def test_a_request_is_sent_once_and_repeats_report_the_record(self) -> None:
        h = self.harness()
        self.assertEqual(h.execute()["status"], "succeeded")
        h.now += 1
        self.assertEqual(h.execute(issued_at=h.now)["status"], "succeeded")
        self.assertEqual(len(h.calls), 1)
        # The same operation key with different content is a conflict, not a new send.
        self.rejected(409, "request_conflict", lambda: h.execute(issued_at=h.now + 1, operation="on"))
        self.assertEqual(len(h.calls), 1)

    def test_concurrent_identical_requests_send_once(self) -> None:
        h = self.harness(delay=0.05)
        first, _ = h.signed(EXECUTE_URL, h.execute_body())
        second, _ = h.signed(EXECUTE_URL, h.execute_body(issued_at=NOW + 1))

        async def both():
            return await asyncio.gather(
                h.endpoint.execute(first, sign(h.policy.secret, EXECUTE_URL, first)),
                h.endpoint.execute(second, sign(h.policy.secret, EXECUTE_URL, second)))

        statuses = sorted(r["status"] for r in asyncio.run(both()))
        self.assertEqual(statuses, ["dispatching", "succeeded"])
        self.assertEqual(len(h.calls), 1)

    def test_errors_and_timeouts_become_indeterminate_and_are_never_resent(self) -> None:
        h = self.harness(fail=RuntimeError("service raised"))
        self.assertEqual(h.execute()["status"], "indeterminate")
        h.now += 1
        self.assertEqual(h.execute(issued_at=h.now)["status"], "indeterminate")
        self.assertEqual(len(h.calls), 1)
        original = lighting.CALL_TIMEOUT_S
        lighting.CALL_TIMEOUT_S = 0.01
        try:
            slow = self.harness(delay=1.0)
            self.assertEqual(slow.execute()["status"], "indeterminate")
        finally:
            lighting.CALL_TIMEOUT_S = original

    def test_unavailable_lights_fail_without_a_service_call(self) -> None:
        h = self.harness(states={"light.kitchen": {"state": "unavailable"}})
        self.assertEqual(h.execute()["status"], "failed")
        self.assertEqual(h.calls, [])

    def test_interrupted_dispatch_reopens_as_indeterminate(self) -> None:
        h = self.harness()
        self.assertIsNone(h.ledger.reserve(str(uuid.UUID(int=9)), 0, "d" * 64, NOW))
        h.ledger.close()
        reopened = LightingLedger(h.policy.ledger_path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.lookup(str(uuid.UUID(int=9)), 0), "indeterminate")
        # Completing after the reopen does not overwrite the unknown outcome.
        self.assertEqual(reopened.complete(str(uuid.UUID(int=9)), 0, "succeeded", NOW), "indeterminate")

    def test_a_caller_disconnect_does_not_abandon_an_accepted_operation(self) -> None:
        h = self.harness(delay=0.1)
        body, signature = h.signed(EXECUTE_URL, h.execute_body())

        async def disconnect_midway() -> None:
            request = asyncio.ensure_future(h.endpoint.execute(body, signature))
            await asyncio.sleep(0.02)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            await asyncio.sleep(0.3)

        asyncio.run(disconnect_midway())
        self.assertEqual(h.ledger.lookup(str(uuid.UUID(int=1)), 0), "succeeded")
        self.assertEqual(len(h.calls), 1)

    def test_an_absent_outcome_is_final_and_blocks_a_late_execute(self) -> None:
        h = self.harness()
        self.assertEqual(h.outcome(str(uuid.UUID(int=1))), "absent")
        self.rejected(409, "request_withdrawn", lambda: h.execute(issued_at=h.now))
        self.assertEqual(h.calls, [])
        self.assertEqual(h.outcome(str(uuid.UUID(int=1))), "absent")

    def test_outcome_reports_the_recorded_state(self) -> None:
        h = self.harness()
        h.execute()
        self.assertEqual(h.outcome(str(uuid.UUID(int=1))), "succeeded")
        self.assertEqual(len(h.calls), 1)

    def test_retention_prunes_only_old_records(self) -> None:
        h = self.harness()
        self.assertIsNone(h.ledger.reserve(str(uuid.UUID(int=5)), 0, "d" * 64, NOW))
        later = NOW + lighting.LEDGER_RETENTION_MS + 1
        self.assertIsNone(h.ledger.reserve(str(uuid.UUID(int=6)), 0, "d" * 64, later))
        self.assertIsNone(h.ledger.lookup(str(uuid.UUID(int=5)), 0))
        self.assertEqual(h.ledger.lookup(str(uuid.UUID(int=6)), 0), "dispatching")

    def test_inventory_lists_only_allowlisted_lights_with_revision(self) -> None:
        h = self.harness()
        body, signature = h.signed(INVENTORY_URL, {"version": 1, "site_id": "victoria", "issued_at": NOW})
        result = asyncio.run(h.endpoint.inventory(body, signature))
        self.assertEqual(result["revision"], h.policy.revision)
        self.assertEqual(result["lights"], [
            {"entity_id": "light.den", "name": "Den", "state": "unsupported", "brightness_pct": None, "dimmable": False},
            {"entity_id": "light.kitchen", "name": "Kitchen", "state": "on", "brightness_pct": 50, "dimmable": True},
            {"entity_id": "light.porch", "name": "Porch", "state": "off", "brightness_pct": None, "dimmable": False},
        ])

    def test_signature_matches_the_core_client_vector(self) -> None:
        # The same vector is asserted in the Core client tests (test_lighting_kernel.py).
        self.assertEqual(EXECUTE_URL, "/api/home_agent_edge/lighting/v1/execute")
        self.assertEqual(sign(bytes.fromhex("ab" * 32), EXECUTE_URL, b'{"version":1}'), "032530b2b4433cff16b0a80d9716dca6291fc15ccfbd40fb5467f296b021147b")

    def test_duplicate_keys_and_oversized_bodies_are_refused(self) -> None:
        h = self.harness()
        body = b'{"version":1,"version":1,"site_id":"victoria","issued_at":%d}' % NOW
        self.rejected(400, "invalid_request",
                      lambda: asyncio.run(h.endpoint.inventory(body, sign(h.policy.secret, INVENTORY_URL, body))))
        big = b" " * 3000
        self.rejected(413, "body_too_large",
                      lambda: asyncio.run(h.endpoint.inventory(big, sign(h.policy.secret, INVENTORY_URL, big))))


if __name__ == "__main__":
    unittest.main()

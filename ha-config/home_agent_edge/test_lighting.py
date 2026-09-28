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


class Harness:
    def __init__(self, directory: str, *, states=None, fail=None, delay=0.0) -> None:
        self.policy = LightingPolicy.from_config({"site_id": "victoria", "secret": SECRET,
            "entities": ["light.kitchen", "light.porch"], "ledger_path": str(Path(directory) / "ledger.sqlite")})
        self.ledger = LightingLedger(self.policy.ledger_path)
        self.calls: list[tuple[str, dict]] = []
        self.now = NOW
        self.states = states if states is not None else {
            "light.kitchen": {"state": "on", "brightness": 128, "name": "Kitchen"},
            "light.porch": {"state": "off", "brightness": None, "name": "Porch"},
            "light.garage": {"state": "on", "brightness": 255, "name": "Garage"},
        }

        async def call(service, data):
            self.calls.append((service, data))
            if delay:
                await asyncio.sleep(delay)
            if fail:
                raise fail

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


class LightingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def harness(self, **kwargs) -> Harness:
        harness = Harness(self.directory.name, **kwargs)
        self.addCleanup(harness.ledger.close)
        return harness

    def rejected(self, status: int, action) -> LightingRejected:
        with self.assertRaises(LightingRejected) as caught:
            action()
        self.assertEqual(caught.exception.status, status)
        return caught.exception

    def test_policy_requires_site_secret_and_a_unique_light_allowlist(self) -> None:
        base = {"site_id": "victoria", "secret": SECRET, "entities": ["light.kitchen"]}
        for bad in ({**base, "site_id": "paris"}, {**base, "secret": "AB" * 32}, {**base, "secret": "ab"},
                    {**base, "entities": []}, {**base, "entities": ["switch.fan"]},
                    {**base, "entities": ["light.a", "light.a"]}, {**base, "entities": ["scene.evening"]},
                    {**base, "ledger_path": "relative.sqlite"}, {**base, "extra": True}):
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

    def test_unsigned_or_wrongly_signed_requests_do_not_act(self) -> None:
        h = self.harness()
        body, _ = h.signed(EXECUTE_URL, h.execute_body())
        self.rejected(401, lambda: asyncio.run(h.endpoint.execute(body, None)))
        _, wrong = h.signed(EXECUTE_URL, h.execute_body(), secret=b"x" * 32)
        self.rejected(401, lambda: asyncio.run(h.endpoint.execute(body, wrong)))
        # A signature for another path cannot be reused here.
        _, other = h.signed(OUTCOME_URL, h.execute_body())
        self.rejected(401, lambda: asyncio.run(h.endpoint.execute(body, other)))
        self.assertEqual(h.calls, [])

    def test_stale_issue_time_other_site_and_exact_replays_are_refused(self) -> None:
        h = self.harness()
        self.rejected(400, lambda: h.execute(issued_at=NOW - 61_000))
        self.rejected(400, lambda: h.execute(site_id="echo"))
        body, signature = h.signed(INVENTORY_URL, {"version": 1, "site_id": "victoria", "issued_at": NOW})
        asyncio.run(h.endpoint.inventory(body, signature))
        self.rejected(409, lambda: asyncio.run(h.endpoint.inventory(body, signature)))
        self.assertEqual(h.calls, [])

    def test_off_on_and_brightness_map_to_light_services(self) -> None:
        h = self.harness()
        self.assertEqual(h.execute()["status"], "succeeded")
        self.assertEqual(h.execute(request_id=str(uuid.UUID(int=2)), operation="on")["status"], "succeeded")
        self.assertEqual(h.execute(request_id=str(uuid.UUID(int=3)), operation="brightness", brightness=40)["status"],
                         "succeeded")
        self.assertEqual(h.calls, [("turn_off", {"entity_id": "light.kitchen"}),
                                   ("turn_on", {"entity_id": "light.kitchen"}),
                                   ("turn_on", {"entity_id": "light.kitchen", "brightness_pct": 40})])

    def test_only_allowlisted_lights_and_bounded_operations(self) -> None:
        h = self.harness()
        for overrides in ({"entity_id": "light.garage"}, {"entity_id": "lock.front_door"}, {"operation": "toggle"},
                          {"operation": "brightness", "brightness": 0}, {"operation": "brightness", "brightness": 101},
                          {"operation": "brightness", "brightness": None}, {"operation": "on", "brightness": 50},
                          {"operation": "brightness", "brightness": True}):
            self.rejected(400, lambda o=overrides: h.execute(**o))
        self.assertEqual(h.calls, [])

    def test_changed_allowlist_and_expired_or_far_future_requests_are_refused(self) -> None:
        h = self.harness()
        self.rejected(409, lambda: h.execute(revision="0" * 64))
        self.rejected(409, lambda: h.execute(expires_at=NOW))
        self.rejected(409, lambda: h.execute(expires_at=NOW + 121_000))
        self.assertEqual(h.calls, [])

    def test_a_request_is_sent_once_and_repeats_report_the_record(self) -> None:
        h = self.harness()
        self.assertEqual(h.execute()["status"], "succeeded")
        h.now += 1
        self.assertEqual(h.execute(issued_at=h.now)["status"], "succeeded")
        self.assertEqual(len(h.calls), 1)
        # The same operation key with different content is a conflict, not a new send.
        self.rejected(409, lambda: h.execute(issued_at=h.now + 1, operation="on"))
        self.assertEqual(len(h.calls), 1)

    def test_errors_and_timeouts_become_indeterminate_and_are_never_resent(self) -> None:
        h = self.harness(fail=RuntimeError("service raised"))
        self.assertEqual(h.execute()["status"], "indeterminate")
        h.now += 1
        self.assertEqual(h.execute(issued_at=h.now)["status"], "indeterminate")
        self.assertEqual(len(h.calls), 1)
        import home_agent_edge.lighting as lighting
        original = lighting.CALL_TIMEOUT_S
        lighting.CALL_TIMEOUT_S = 0.01
        try:
            slow = Harness(tempfile.mkdtemp(dir=self.directory.name), delay=1.0)
            self.addCleanup(slow.ledger.close)
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

    def test_cancellation_is_recorded_as_indeterminate_and_propagates(self) -> None:
        h = self.harness(delay=5.0)
        body, signature = h.signed(EXECUTE_URL, h.execute_body())

        async def cancel_midway() -> None:
            task = asyncio.ensure_future(h.endpoint.execute(body, signature))
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(cancel_midway())
        self.assertEqual(h.ledger.lookup(str(uuid.UUID(int=1)), 0), "indeterminate")

    def test_outcome_reports_absent_then_the_recorded_state(self) -> None:
        h = self.harness()
        key = {"version": 1, "site_id": "victoria", "request_id": str(uuid.UUID(int=1)), "operation_index": 0}
        body, signature = h.signed(OUTCOME_URL, {**key, "issued_at": NOW})
        self.assertEqual(asyncio.run(h.endpoint.outcome(body, signature))["status"], "absent")
        h.execute()
        body, signature = h.signed(OUTCOME_URL, {**key, "issued_at": NOW + 1})
        self.assertEqual(asyncio.run(h.endpoint.outcome(body, signature))["status"], "succeeded")
        self.assertEqual(len(h.calls), 1)

    def test_inventory_lists_only_allowlisted_lights_with_revision(self) -> None:
        h = self.harness()
        body, signature = h.signed(INVENTORY_URL, {"version": 1, "site_id": "victoria", "issued_at": NOW})
        result = asyncio.run(h.endpoint.inventory(body, signature))
        self.assertEqual(result["revision"], h.policy.revision)
        self.assertEqual(result["lights"], [
            {"entity_id": "light.kitchen", "name": "Kitchen", "state": "on", "brightness_pct": 50},
            {"entity_id": "light.porch", "name": "Porch", "state": "off", "brightness_pct": None},
        ])

    def test_duplicate_keys_and_oversized_bodies_are_refused(self) -> None:
        h = self.harness()
        body = b'{"version":1,"version":1,"site_id":"victoria","issued_at":%d}' % NOW
        self.rejected(400, lambda: asyncio.run(h.endpoint.inventory(body, sign(h.policy.secret, INVENTORY_URL, body))))
        big = b" " * 3000
        self.rejected(413, lambda: asyncio.run(h.endpoint.inventory(big, sign(h.policy.secret, INVENTORY_URL, big))))


if __name__ == "__main__":
    unittest.main()

"""Fixed scoped Home client for an explicitly injected HA adapter facade.

No URLs, sockets, device keys, discovery or default transport exist here. The
HA producer owns reservation ordering, authentication, wire codec and firmware.
"""
from __future__ import annotations

import copy
import time

from fixture_reports import canonical_bytes, decimal_u64, digest, require, validate_scope


class FixtureClient:
    def __init__(self, scope, *, transport=None, credential=None, call_timeout_ms=1000, monotonic=time.monotonic):
        validate_scope(scope)
        require(type(call_timeout_ms) is int and 1 <= call_timeout_ms <= 5000, "client_call_bound_required")
        self.scope = copy.deepcopy(scope)
        self._transport = transport
        self._credential = credential
        self._timeout = call_timeout_ms / 1000
        self._monotonic = monotonic

    def describe(self):
        description = {"schema": "home-fixture-runtime-client-1", "version": 1,
                       "operations": ["describe", "submit", "withdraw", "observe"],
                       "fixed_scope": copy.deepcopy(self.scope), "physical_permission": False,
                       "network_or_device_io_implemented": False, "connected": False}
        if self._transport is not None:
            self._ready()
            description["adapter"] = self._bounded(self._transport.describe())
            description["connected"] = True
        return description

    def _ready(self):
        require(self._transport is not None and self._credential is not None, "fixture_transport_unconfigured")
        require(getattr(self._transport, "evidence_source", None) == "simulated", "real_transport_unavailable")
        require(canonical_bytes(getattr(self._transport, "scope", None)) == canonical_bytes(self.scope),
                "adapter_fixed_scope_mismatch")

    @staticmethod
    def _bounded(value):
        require(type(value) is dict and len(canonical_bytes(value)) <= 32768, "adapter_response_bound")
        require(value.get("physical_permission", False) is False, "adapter_physical_permission_unsupported")
        if "evidence_source" in value:
            require(value["evidence_source"] == "simulated", "adapter_provenance_mismatch")
        return copy.deepcopy(value)

    def _call(self, name, *args):
        self._ready()
        before = self._monotonic()
        result = getattr(self._transport, name)(self._credential, *copy.deepcopy(args))
        # This does not promise native cancellation. The dispatcher already
        # persisted ATTEMPTING, so a late response is an unconfirmed effect.
        require(0 <= self._monotonic() - before <= self._timeout, "adapter_call_deadline_unconfirmed")
        return self._bounded(result)

    def reserve_execution_ids(self, request_id, intent_sha256, mode):
        require(type(request_id) is str and 0 < len(request_id) <= 128, "reservation_identity_required")
        digest(intent_sha256)
        require(mode in ("autonomous-task", "manual-ambient", "manual-preset"), "reservation_mode_unsupported")
        result = self._call("reserve_execution_ids", {"request_id": request_id, "intent_sha256": intent_sha256,
                            "mode": mode, "scope": copy.deepcopy(self.scope)})
        require(result.get("request_id") == request_id, "reservation_identity_mismatch")
        require(decimal_u64(result.get("grant_id")) > 0 and decimal_u64(result.get("revision_id")) > 0,
                "reservation_ids_required")
        return result

    def submit(self, plan):
        require(type(plan) is dict and plan.get("context_digest") == self.scope["context_digest"], "submit_context_mismatch")
        return self._call("submit", {"plan": plan})

    def withdraw(self, domain_mask, *, close=False):
        require(type(domain_mask) is int and 0 < domain_mask <= 255 and type(close) is bool, "withdrawal_scope_invalid")
        require(not close or domain_mask == 255, "close_requires_all_domains")
        return self._call("withdraw", {"domain_mask": domain_mask, "close": close})

    def observe(self):
        return self._call("observe")

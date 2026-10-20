"""Shared schema for authenticated, durable gateway rejection receipts.

Shape validation is not authentication or proof of persistence. The gateway
creates these receipts only from committed terminal ledger rows. Transports
authenticate the provisioned endpoint and callers bind request/site identity;
the source journal additionally compares the originally persisted payload digest.
"""
from __future__ import annotations

import re
from types import MappingProxyType


REJECTION_REASONS = MappingProxyType({
    "rejected_capacity": "capacity",
    "rejected_closed": "closed",
    "rejected_expired": "expired",
    "rejected_invalid_image": "invalid_image",
    "rejected_superseded": "superseded",
})


def validate_rejection(response: dict, *, device_id: str,
                       request_digest: str | None = None) -> bool:
    if not isinstance(response, dict):
        return False
    outcome = response.get("outcome")
    if (not isinstance(outcome, str) or outcome not in REJECTION_REASONS
            or response.get("status") != "terminal" or "observation" in response):
        return False
    receipt = response.get("rejection")
    if (not isinstance(receipt, dict)
            or set(receipt) != {"version", "device_id", "request_digest", "reason"}
            or type(receipt["version"]) is not int or receipt["version"] != 1
            or receipt["device_id"] != device_id
            or not isinstance(receipt["request_digest"], str)
            or re.fullmatch(r"[0-9a-f]{64}", receipt["request_digest"]) is None
            or receipt["reason"] != REJECTION_REASONS[outcome]):
        return False
    return request_digest is None or receipt["request_digest"] == request_digest

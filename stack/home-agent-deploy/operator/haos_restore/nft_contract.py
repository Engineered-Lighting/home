#!/usr/bin/env python3
"""Produce a stable digest of an nft JSON ruleset's security structure."""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any


VOLATILE_KEYS = {"bytes", "elem", "expires", "handle", "packets"}


def normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: normalize(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_KEYS and key != "metainfo"
        }
    if isinstance(value, list):
        normalized = [normalize(item) for item in value]
        return [item for item in normalized if item not in ({}, None)]
    return value


def contract_digest(payload: str) -> str:
    parsed = json.loads(payload)
    normalized = normalize(parsed)
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> int:
    try:
        print(contract_digest(sys.stdin.read()))
        return 0
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"nft contract: {exc}", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())

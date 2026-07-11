from __future__ import annotations

import secrets
import time
import uuid


def uuid7() -> uuid.UUID:
    """Return an RFC 9562 UUIDv7 without requiring Python 3.14.

    UUIDv7 is time-sortable. Randomness, not ordering, remains the uniqueness
    boundary; database constraints are still authoritative.
    """

    milliseconds = time.time_ns() // 1_000_000
    if milliseconds >= 1 << 48:
        raise OverflowError("Unix timestamp exceeds UUIDv7 range")
    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = milliseconds << 80
    value |= 0x7 << 76
    value |= random_a << 64
    value |= 0b10 << 62
    value |= random_b
    return uuid.UUID(int=value)

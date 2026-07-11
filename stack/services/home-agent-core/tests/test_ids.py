from __future__ import annotations

from app.ids import uuid7


def test_uuid7_has_expected_version_and_variant() -> None:
    values = [uuid7() for _ in range(20)]
    assert all(value.version == 7 for value in values)
    assert all(value.variant == "specified in RFC 4122" for value in values)
    assert len(set(values)) == len(values)

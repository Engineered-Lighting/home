from __future__ import annotations

from collections import namedtuple
from pathlib import Path

from app.resources import (
    inspect_disk_budget,
    is_privacy_essential_write,
    is_retired_legacy_identity_import,
)

Usage = namedtuple("Usage", "total used free")


def _at(percent: float):
    free = int(percent * 10)
    return lambda _path: Usage(1000, 1000 - free, free)


def test_locked_disk_threshold_boundaries() -> None:
    assert inspect_disk_budget(Path("/unused"), disk_usage=_at(21)).state == "normal"
    assert inspect_disk_budget(Path("/unused"), disk_usage=_at(20)).state == "warn"
    assert (
        inspect_disk_budget(Path("/unused"), disk_usage=_at(15)).state
        == "stop_optional"
    )
    assert inspect_disk_budget(Path("/unused"), disk_usage=_at(10)).state == "read_only"
    assert inspect_disk_budget(
        Path("/missing"), disk_usage=lambda _path: (_ for _ in ()).throw(OSError())
    ).state == "unavailable"


def test_only_privacy_controls_bypass_degraded_write_gate() -> None:
    assert is_privacy_essential_write("/v1/erasure-requests/id/confirm")
    assert is_privacy_essential_write("/v1/preferences/location_memory")
    assert not is_privacy_essential_write("/v1/people/id/privacy-directives")
    assert is_privacy_essential_write("/v1/principal-binding-request/cancel")
    assert is_privacy_essential_write("/v1/facts/id/forget-preview")
    assert is_privacy_essential_write("/v1/forget-preview")
    assert not is_privacy_essential_write("/v1/memory-transactions")
    assert not is_privacy_essential_write("/v1/facts/id/correction-preview")
    assert not is_privacy_essential_write("/v1/ingest/envelopes")


def test_only_exact_retired_identity_paths_bypass_write_middleware() -> None:
    assert is_retired_legacy_identity_import("/v1/people")
    assert is_retired_legacy_identity_import("/v1/people/verify-reviewed")
    assert is_retired_legacy_identity_import(
        "/v1/people/00000000-0000-4000-8000-000000000001/privacy-directives"
    )
    assert not is_retired_legacy_identity_import("/v1/people/id/preferences")
    assert not is_retired_legacy_identity_import("/v1/people/id/aliases/extra")
    assert not is_retired_legacy_identity_import("/v1/people-attacker/id/aliases")

from __future__ import annotations

from types import SimpleNamespace
import uuid

import pytest
from pydantic import ValidationError

from app.errors import CapabilityDisabledError, ForbiddenError
from app.models import PrivateLocalityPreviewRequest
from app.store import CoreStore


PRINCIPAL = {
    "principal_id": uuid.UUID("018f6f42-3a8b-7c11-8123-123456789abc"),
    "person_id": uuid.UUID("018f6f42-3a8b-7c11-8123-123456789abd"),
}


def _store(rollout_mode: str = "shadow") -> CoreStore:
    store = object.__new__(CoreStore)
    store.settings = SimpleNamespace(rollout_mode=rollout_mode)
    return store


def _request(**updates) -> PrivateLocalityPreviewRequest:
    values = {
        "canonical_name": " Itaipava ",
        "latitude": -22.4,
        "longitude": -43.14,
        "radius_m": 1_000,
        "travel_greeting_eligible": True,
        "confirmation_nonce": uuid.UUID(
            "018f6f42-3a8b-4c11-8123-123456789abc"
        ),
    }
    values.update(updates)
    return PrivateLocalityPreviewRequest(**values)


def test_private_locality_preview_binds_exact_geometry_and_implications() -> None:
    store = _store()
    preview = store.preview_private_locality(PRINCIPAL, _request())

    assert preview.state == "needs_confirmation"
    assert preview.canonical_name == "Itaipava"
    assert preview.privacy_scope == "private"
    assert preview.locator.latitude == -22.4
    assert preview.locator.longitude == -43.14
    assert preview.locator.radius_m == 1_000
    assert preview.locator.retention == "encrypted_until_erased"
    assert preview.travel_greeting_eligible is True
    assert "property ownership" in preview.does_not_create
    assert "current person presence" in preview.does_not_create
    assert len(preview.preview_digest) == 64

    moved = store.preview_private_locality(
        PRINCIPAL,
        _request(latitude=-22.5),
    )
    resized = store.preview_private_locality(
        PRINCIPAL,
        _request(radius_m=2_000),
    )
    greeting_off = store.preview_private_locality(
        PRINCIPAL,
        _request(travel_greeting_eligible=False),
    )
    assert len(
        {
            preview.preview_digest,
            moved.preview_digest,
            resized.preview_digest,
            greeting_off.preview_digest,
        }
    ) == 4


def test_private_locality_preview_requires_shadow_and_confirmed_principal() -> None:
    with pytest.raises(CapabilityDisabledError, match="requires rollout mode shadow"):
        _store("record_only").preview_private_locality(PRINCIPAL, _request())
    with pytest.raises(ForbiddenError, match="confirmed principal is required"):
        _store().preview_private_locality({}, _request())


@pytest.mark.parametrize("name", [" ", "Itaipava\nprivate"])
def test_private_locality_name_is_bounded_and_control_free(name: str) -> None:
    with pytest.raises(ValidationError):
        _request(canonical_name=name)

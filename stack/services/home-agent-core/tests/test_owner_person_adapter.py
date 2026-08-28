"""Adding a person: what the caller may decide, and what it may not.

The retired import let a caller hand over a row. Here the caller says who the
person is and how the household may treat them, and everything else -- the
status, the identifiers, the provenance -- is decided by the kernel.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import uuid

import pytest
from pydantic import ValidationError

from app.models import OwnerPersonCreate
from app.owner_person_adapter import _create_kernel_call, _document_digest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def _uuid7() -> uuid.UUID:
    value = (1 << 80) | 0xABC
    value &= ~(0xF << 76)
    value |= 0x7 << 76
    value &= ~(0x3 << 62)
    value |= 0x2 << 62
    return uuid.UUID(int=value)


def _person(**overrides: object) -> OwnerPersonCreate:
    values: dict[str, object] = {"ceremony_id": _uuid7(), "display_name": "Nia"}
    values.update(overrides)
    return OwnerPersonCreate(**values)  # type: ignore[arg-type]


def test_the_caller_cannot_choose_status_or_identifiers() -> None:
    for forbidden in ("status", "person_id", "legacy_source_ref"):
        with pytest.raises(ValidationError):
            _person(**{forbidden: "active"})


def test_auto_expire_must_carry_an_expiry_and_only_auto_expire_may() -> None:
    """An auto-expiring person with no expiry never expires."""

    with pytest.raises(ValidationError):
        _person(directive="auto_expire")
    with pytest.raises(ValidationError):
        _person(directive="silent", directive_expires_at=dt.datetime.now(dt.UTC))
    assert _person(
        directive="auto_expire", directive_expires_at=dt.datetime.now(dt.UTC)
    ).directive == "auto_expire"


def test_a_blank_name_is_refused_and_a_padded_one_is_trimmed() -> None:
    with pytest.raises(ValidationError):
        _person(display_name="")
    call = _create_kernel_call(ha_user_id="ha-a", value=_person(display_name="  Nia  "))
    assert call.display_name == "Nia"


def test_identifiers_are_derived_distinct_and_account_bound() -> None:
    value = _person()
    first = _create_kernel_call(ha_user_id="ha-a", value=value)
    again = _create_kernel_call(ha_user_id="ha-a", value=value)
    other = _create_kernel_call(ha_user_id="ha-b", value=value)

    assert first == again, "derivation must be deterministic to allow replay"
    assert first.person_id != other.person_id
    derived = {first.person_id, first.attestation_artifact_id, first.directive_id}
    assert len(derived) == 3
    assert all(identifier.version == 7 for identifier in derived)


def test_the_digest_covers_the_name_and_the_privacy_state() -> None:
    """A receipt must not be reinterpretable as vouching for a different person,
    or for the same person under a more permissive directive."""

    base = _document_digest(ha_user_id="ha-a", value=_person())
    assert base != _document_digest(ha_user_id="ha-a", value=_person(display_name="Nyah"))
    assert base != _document_digest(
        ha_user_id="ha-a", value=_person(privacy_scope="household")
    )
    assert base != _document_digest(
        ha_user_id="ha-a", value=_person(directive="do_not_track")
    )
    assert base != _document_digest(ha_user_id="ha-b", value=_person())
    assert len(base) == 64


def test_the_route_avoids_the_retired_people_surface() -> None:
    """A security boundary asserts the browser client never references
    /api/agent/v1/people, because those are the retired import routes. The path
    must not collide with that substring."""

    api = (APP / "api.py").read_text()
    assert '"/household-person"' in api
    # The route must not begin with the forbidden prefix. Checked here rather
    # than against the web client, which is outside this package and is not
    # mounted when these tests run in the service image.
    assert '"/people"' not in api.split("def create_household_person")[0][-2000:]


def test_the_route_is_pinned_and_wired() -> None:
    api = (APP / "api.py").read_text()
    main = (APP / "main.py").read_text()
    assert 'OWNER_PERSON_ADAPTER_REVISION = "0027_owner_person_e5n"' in api
    assert "owner_person_adapter" in api
    assert "application.state.owner_person_adapter" in main

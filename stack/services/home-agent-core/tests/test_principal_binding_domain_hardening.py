from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.store import CoreStore, binding_proposal_digest


def _proposal_material() -> dict[str, object]:
    return {
        "request_id": uuid.UUID("018f0000-0000-7000-8000-000000000001"),
        "review_code": "ABCDEFGHJKLMNPQR",
        "ha_user_id": "ha-user-internal-uuid",
        "person_id": uuid.UUID("018f0000-0000-7000-8000-000000000002"),
        "reviewed_display_label": "Marcelo",
        "person_snapshot_digest": "1" * 64,
        "operator_request_id": uuid.UUID(
            "018f0000-0000-7000-8000-000000000003"
        ),
        "stage_receipt_digest": "2" * 64,
        "staged_at": datetime(2026, 7, 12, 18, 0, 0, 123456, tzinfo=UTC),
        "expires_at": datetime(2026, 7, 12, 18, 15, 0, 123456, tzinfo=UTC),
        "policy_version": "home-agent-policy-v1",
        "policy_digest": "3" * 64,
    }


def test_binding_proposal_digest_is_canonical_across_timezone_offsets() -> None:
    material = _proposal_material()
    expected = binding_proposal_digest(**material)
    brazil = timezone(timedelta(hours=-3))
    material["staged_at"] = material["staged_at"].astimezone(brazil)  # type: ignore[union-attr]
    material["expires_at"] = material["expires_at"].astimezone(brazil)  # type: ignore[union-attr]

    assert binding_proposal_digest(**material) == expected


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("request_id", uuid.UUID("018f0000-0000-7000-8000-000000000011")),
        ("review_code", "ABCDEFGHJKLMNPQ2"),
        ("ha_user_id", "different-ha-user"),
        ("person_id", uuid.UUID("018f0000-0000-7000-8000-000000000012")),
        ("reviewed_display_label", "Different Person"),
        ("person_snapshot_digest", "4" * 64),
        (
            "operator_request_id",
            uuid.UUID("018f0000-0000-7000-8000-000000000013"),
        ),
        ("stage_receipt_digest", "5" * 64),
        ("staged_at", datetime(2026, 7, 12, 18, 0, 1, tzinfo=UTC)),
        ("expires_at", datetime(2026, 7, 12, 18, 14, 59, tzinfo=UTC)),
        ("policy_version", "home-agent-policy-v2"),
        ("policy_digest", "6" * 64),
    ],
)
def test_binding_proposal_digest_commits_every_typed_field(
    field: str, replacement: object
) -> None:
    material = _proposal_material()
    expected = binding_proposal_digest(**material)
    material[field] = replacement

    assert binding_proposal_digest(**material) != expected


def test_binding_proposal_digest_rejects_naive_timestamps() -> None:
    material = _proposal_material()
    material["staged_at"] = datetime(2026, 7, 12, 18, 0, 0)

    with pytest.raises(ValueError, match="timezone-aware"):
        binding_proposal_digest(**material)


def test_subject_get_and_confirm_recompute_the_typed_digest() -> None:
    proposal_get = inspect.getsource(CoreStore._principal_binding_proposal_view)
    confirm = inspect.getsource(CoreStore.confirm_principal_binding_proposal)

    for source in (proposal_get, confirm):
        assert "binding_proposal_digest(" in source
        assert "hmac.compare_digest(" in source
        for required_argument in (
            "request_id=",
            "review_code=",
            "ha_user_id=",
            "person_id=",
            "reviewed_display_label=",
            "person_snapshot_digest=",
            "operator_request_id=",
            "stage_receipt_digest=",
            "staged_at=",
            "expires_at=",
        ):
            assert required_argument in source
        assert "self.settings.policy_version" in source
        assert "self.settings.policy_digest" in source


def test_stage_uses_semantic_digest_not_an_opaque_random_token() -> None:
    source = inspect.getsource(CoreStore.stage_principal_binding_proposal)

    assert "proposal_digest = binding_proposal_digest(" in source
    assert "secrets.token_hex" not in source


def test_blocking_privacy_import_cancels_ready_review_in_same_transaction() -> None:
    source = inspect.getsource(CoreStore.import_reviewed_privacy_directive)

    assert "serializable=True" in source
    assert '"do_not_track"' in source
    assert '"ignored"' in source
    assert '"silent"' in source
    assert 'value.directive == "auto_expire"' in source
    assert '"auto_expire",' in source
    assert "value.expires_at <= now" not in source
    assert "_cancel_ready_binding_proposals_for_person(" in source


def test_non_active_person_import_cancels_ready_review_in_same_transaction() -> None:
    source = inspect.getsource(CoreStore.import_reviewed_person_status)

    assert "serializable=True" in source
    assert 'value.status != "active"' in source
    assert "_cancel_ready_binding_proposals_for_person(" in source


def test_every_binding_decision_treats_future_auto_expiry_as_blocking() -> None:
    for method in (
        CoreStore._binding_status_in_transaction,
        CoreStore._principal_binding_proposal_view,
        CoreStore.stage_principal_binding_proposal,
        CoreStore.confirm_principal_binding_proposal,
    ):
        assert "_binding_blocking_directives(" in inspect.getsource(method)

    directive_source = inspect.getsource(CoreStore._binding_blocking_directives)
    assert '"auto_expire"' in directive_source
    assert "expires_at" not in directive_source


def test_binding_cancellation_uses_kernel_and_confirm_locks_in_order() -> None:
    cancellation = inspect.getsource(
        CoreStore._cancel_ready_binding_proposals_for_person
    )
    confirm = inspect.getsource(CoreStore.confirm_principal_binding_proposal)

    assert "privacy.cancel_principal_binding_work_for_person" in cancellation
    assert "proposals_cancelled" in cancellation
    assert "requests_cancelled" in cancellation
    assert confirm.index("select(schema.principal_binding_proposals)") < confirm.index(
        "select(schema.principal_binding_requests)"
    ) < confirm.index("select(schema.people)")

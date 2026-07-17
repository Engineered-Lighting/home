from __future__ import annotations

import ast
import dataclasses
import inspect
import re
import uuid
from datetime import UTC, datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

import pytest

import app.principal_binding as principal_binding
from app.principal_binding import (
    CAPABILITY_STATUS,
    CONTRACT_VERSION,
    DEPLOYMENT_STATUS,
    HA_SNAPSHOT_ORIGIN,
    RULE_VERSION,
    AuthenticatedHaUserSnapshot,
    DormantPrincipalBindingIntent,
    ReviewedPersonNominee,
    PrincipalBindingContext,
    PrincipalBindingGraphSnapshot,
    PrincipalBindingPreview,
    PrincipalBindingProtocolError,
    compile_principal_binding_preview,
    verify_principal_binding_preview,
)


KEY = bytes.fromhex("42" * 32)
OTHER_KEY = bytes.fromhex("24" * 32)
BASE_TIME = datetime(2026, 7, 17, 15, 0, 0, 123456, tzinfo=UTC)


def _new_id(ordinal: int) -> uuid.UUID:
    return uuid.UUID(f"018f1000-0000-7000-8000-{ordinal:012x}")


def _stable_id(ordinal: int) -> uuid.UUID:
    return uuid.UUID(f"12345678-1234-4abc-8def-{ordinal:012x}")


def _digest(char: str) -> str:
    return char * 64


def _ha_user() -> AuthenticatedHaUserSnapshot:
    return AuthenticatedHaUserSnapshot(
        snapshot_id=_new_id(2),
        ha_user_id=_stable_id(3),
        display_label="Marcelo’s Home Assistant account",
        snapshot_commitment=_digest("a"),
        authorization_commitment=_digest("b"),
        observed_at=BASE_TIME,
        snapshot_origin=HA_SNAPSHOT_ORIGIN,
        authentication_status="authenticated",
        user_status="active",
    )


def _nominee(*, person_id: uuid.UUID = _stable_id(4)) -> ReviewedPersonNominee:
    return ReviewedPersonNominee(
        nominee_id=_new_id(5),
        migration_run_id=_new_id(6),
        finalization_id=_new_id(6),
        person_id=person_id,
        person_display_label="Marcelo",
        nominee_commitment=_digest("c"),
        projection_commitment=_digest("d"),
        finalization_commitment=_digest("e"),
        review_receipt_commitment=_digest("f"),
        privacy_state_commitment=_digest("1"),
        erasure_state_commitment=_digest("2"),
        status_observed_at=BASE_TIME,
        nomination_role="operator_review_candidate",
        review_status="reviewed",
        finalization_status="finalized",
        nomination_authority_status="unverified",
        person_status="active",
        retrieval_blocked=False,
        privacy_directives=(),
        erasure_status="clear",
        open_erasure_request_count=0,
        pending_erasure_task_count=0,
    )


def _graph(
    *,
    ha_user_id: uuid.UUID = _stable_id(3),
    person_id: uuid.UUID = _stable_id(4),
) -> PrincipalBindingGraphSnapshot:
    return PrincipalBindingGraphSnapshot(
        ha_user_id=ha_user_id,
        person_id=person_id,
        graph_digest=_digest("3"),
        observed_at=BASE_TIME,
        reviewed_person_nominee_count=1,
        active_ha_user_binding_count=0,
        active_person_binding_count=0,
        other_open_request_count=0,
        other_ready_proposal_count=0,
        conflicting_person_nominee_count=0,
    )


def _context() -> PrincipalBindingContext:
    return PrincipalBindingContext(
        request_id=_new_id(1),
        ha_user=_ha_user(),
        person_nominees=(_nominee(),),
        graph=_graph(),
        policy_version="home-agent-mvp-v1",
        policy_digest=_digest("4"),
        staged_at=BASE_TIME,
        expires_at=BASE_TIME + timedelta(minutes=15),
    )


def _compile(
    context: PrincipalBindingContext | None = None,
    *,
    at: datetime = BASE_TIME + timedelta(seconds=1),
) -> PrincipalBindingPreview:
    return compile_principal_binding_preview(
        context or _context(), digest_key=KEY, trusted_operation_time=at
    )


def _verify(
    context: PrincipalBindingContext | None = None,
    *,
    at: datetime = BASE_TIME + timedelta(minutes=1),
) -> DormantPrincipalBindingIntent:
    actual = context or _context()
    preview = _compile(actual)
    return verify_principal_binding_preview(
        preview,
        supplied_digest=preview.preview_digest,
        current_context=actual,
        digest_key=KEY,
        trusted_operation_time=at,
    )


def test_compiler_is_private_dormant_and_non_authoritative() -> None:
    preview = _compile()

    assert preview.contract_version == CONTRACT_VERSION
    assert preview.rule_version == RULE_VERSION
    assert preview.deployment_status == DEPLOYMENT_STATUS == "non_deployable"
    assert preview.capability_status == CAPABILITY_STATUS == "capability_disabled"
    assert preview.privacy_scope == "private"
    assert preview.nominee_count == 1
    assert preview.authoritative is False
    assert preview.commit_ready is False
    assert preview.enables_writes is False
    assert preview.me_identity_established is False
    assert preview.binding_created is False
    assert preview.location_memory_enabled is False
    assert preview.travel_greetings_enabled is False
    assert preview.external_receipts_verified is False
    assert preview.fresh_state_origin_verified is False
    assert preview.nomination_authority_verified is False
    assert "Marcelo’s Home Assistant account" in preview.statement
    assert "Marcelo" in preview.statement
    assert "reviewed person Marcelo proposed as your identity" in preview.statement
    assert "not an established 'me' identity" in preview.safety_note
    assert "creates no binding" in preview.safety_note
    assert len(preview.preview_digest) == 64
    for payload in (
        preview.private_payload(),
        preview.content_free_audit_payload(),
    ):
        assert payload["location_memory_enabled"] is False
        assert payload["travel_greetings_enabled"] is False
        assert payload["me_identity_established"] is False
        assert payload["binding_created"] is False
        assert payload["external_receipts_verified"] is False
        assert payload["fresh_state_origin_verified"] is False
        assert payload["nomination_authority_verified"] is False


def test_verifier_returns_only_a_locked_dormant_intent() -> None:
    intent = _verify()

    assert intent.contract_version == CONTRACT_VERSION
    assert intent.rule_version == RULE_VERSION
    assert intent.deployment_status == "non_deployable"
    assert intent.nominee_count == 1
    assert intent.requires_serializable_commit is True
    assert intent.requires_fresh_authenticated_ha_snapshot is True
    assert intent.requires_fresh_binding_graph_snapshot is True
    assert intent.requires_transaction_time_state_read is True
    assert intent.requires_external_authenticated_gesture is True
    assert intent.requires_single_use_artifact_consumption is True
    assert intent.me_identity_established is False
    assert intent.binding_created is False
    assert intent.authoritative is False
    assert intent.commit_ready is False
    assert intent.enables_writes is False
    assert intent.location_memory_enabled is False
    assert intent.travel_greetings_enabled is False
    assert intent.external_receipts_verified is False
    assert intent.fresh_state_origin_verified is False
    assert intent.nomination_authority_verified is False
    assert len(intent.verification_state_digest) == 64
    assert intent.capability_status == "capability_disabled"


def test_absent_identity_and_authority_claims_are_hmac_bound(monkeypatch) -> None:
    captured: list[dict[str, object]] = []
    canonical_json = principal_binding.canonical_json

    def capture(value: dict[str, object]) -> bytes:
        captured.append(value)
        return canonical_json(value)

    monkeypatch.setattr(principal_binding, "canonical_json", capture)
    _verify()

    materials = {
        value["domain"]: value
        for value in captured
        if isinstance(value.get("domain"), str)
    }
    locked_fields = (
        "me_identity_established",
        "binding_created",
        "external_receipts_verified",
        "fresh_state_origin_verified",
        "nomination_authority_verified",
    )
    for domain in (
        "home-agent:principal-binding:dormant-preview:v2",
        "home-agent:principal-binding:fresh-verification-state:v2",
    ):
        locked = materials[domain]["locked_authority"]
        assert isinstance(locked, dict)
        assert {field: locked[field] for field in locked_fields} == {
            field: False for field in locked_fields
        }


@pytest.mark.parametrize("nominees", [(), (_nominee(), _nominee())])
def test_exactly_one_reviewed_person_nominee_is_required(
    nominees: tuple[ReviewedPersonNominee, ...],
) -> None:
    with pytest.raises(
        PrincipalBindingProtocolError, match="invalid_person_nominee_set"
    ):
        _compile(dataclasses.replace(_context(), person_nominees=nominees))


def test_nominee_container_must_be_an_exact_tuple() -> None:
    class DeceptiveTuple(tuple[ReviewedPersonNominee, ...]):
        def __len__(self) -> int:
            return 1

    for malformed in (
        [_nominee()],
        DeceptiveTuple((_nominee(), _nominee(person_id=_stable_id(99)))),
    ):
        context = dataclasses.replace(_context(), person_nominees=malformed)
        with pytest.raises(
            PrincipalBindingProtocolError, match="invalid_person_nominee_set"
        ):
            _compile(context)


def test_protocol_rejects_deceptive_string_subclasses() -> None:
    class DeceptiveStr(str):
        def __eq__(self, other: object) -> bool:
            return True

        def __ne__(self, other: object) -> bool:
            return False

        __hash__ = str.__hash__

    context = _context()
    deceptive_ha = dataclasses.replace(
        context.ha_user,
        snapshot_origin=DeceptiveStr("client_claim"),
        authentication_status=DeceptiveStr("anonymous"),
        user_status=DeceptiveStr("inactive"),
    )
    with pytest.raises(PrincipalBindingProtocolError, match="invalid_ha_snapshot"):
        _compile(dataclasses.replace(context, ha_user=deceptive_ha))

    deceptive_nominee = dataclasses.replace(
        context.person_nominees[0],
        nomination_authority_status=DeceptiveStr("authoritative"),
    )
    with pytest.raises(PrincipalBindingProtocolError, match="invalid_person_nominee"):
        _compile(dataclasses.replace(context, person_nominees=(deceptive_nominee,)))

    deceptive_label = dataclasses.replace(
        context.person_nominees[0],
        person_display_label=DeceptiveStr("Marcelo"),
    )
    with pytest.raises(PrincipalBindingProtocolError, match="invalid_display_label"):
        _compile(dataclasses.replace(context, person_nominees=(deceptive_label,)))

    deceptive_commitment = dataclasses.replace(
        context.person_nominees[0],
        nominee_commitment=DeceptiveStr(_digest("c")),
    )
    with pytest.raises(PrincipalBindingProtocolError, match="invalid_commitment"):
        _compile(dataclasses.replace(context, person_nominees=(deceptive_commitment,)))

    deceptive_directive = dataclasses.replace(
        context.person_nominees[0],
        privacy_directives=(DeceptiveStr("private"),),
    )
    with pytest.raises(PrincipalBindingProtocolError, match="privacy_blocked"):
        _compile(dataclasses.replace(context, person_nominees=(deceptive_directive,)))

    with pytest.raises(PrincipalBindingProtocolError, match="invalid_policy"):
        _compile(
            dataclasses.replace(
                context, policy_version=DeceptiveStr(context.policy_version)
            )
        )


def test_protocol_rejects_deceptive_uuid_subclasses() -> None:
    class DeceptiveUuid(uuid.UUID):
        def __eq__(self, other: object) -> bool:
            return True

        def __ne__(self, other: object) -> bool:
            return False

        __hash__ = uuid.UUID.__hash__

    context = _context()
    graph = dataclasses.replace(
        context.graph,
        person_id=DeceptiveUuid(str(_stable_id(99))),
    )
    with pytest.raises(PrincipalBindingProtocolError, match="invalid_identifier"):
        _compile(dataclasses.replace(context, graph=graph))


def test_protocol_rejects_deceptive_datetime_subclasses() -> None:
    class DeceptiveDatetime(datetime):
        def astimezone(self, tz: object = None) -> datetime:
            return BASE_TIME

    deceptive_time = DeceptiveDatetime(2026, 7, 17, 15, 0, 0, 123456, tzinfo=UTC)
    context = _context()
    ha_user = dataclasses.replace(context.ha_user, observed_at=deceptive_time)
    with pytest.raises(
        PrincipalBindingProtocolError, match="invalid_trusted_state_time"
    ):
        _compile(dataclasses.replace(context, ha_user=ha_user))

    with pytest.raises(
        PrincipalBindingProtocolError, match="invalid_trusted_operation_time"
    ):
        compile_principal_binding_preview(
            context,
            digest_key=KEY,
            trusted_operation_time=deceptive_time,
        )


def test_migration_run_and_finalization_share_one_activity_id() -> None:
    context = _context()
    nominee = context.person_nominees[0]
    assert nominee.migration_run_id == nominee.finalization_id
    assert _compile(context).nominee_count == 1

    mismatched = dataclasses.replace(nominee, finalization_id=_new_id(7))
    with pytest.raises(PrincipalBindingProtocolError, match="invalid_identifier"):
        _compile(dataclasses.replace(context, person_nominees=(mismatched,)))


_SEMANTIC_ID_ROLES = (
    "request",
    "snapshot",
    "nominee",
    "run_and_finalization",
    "ha_user",
    "person",
)


@pytest.mark.parametrize(("first", "second"), combinations(_SEMANTIC_ID_ROLES, 2))
def test_distinct_semantic_id_roles_cannot_collide(first: str, second: str) -> None:
    context = _context()
    nominee = context.person_nominees[0]
    semantic_ids = {
        "request": context.request_id,
        "snapshot": context.ha_user.snapshot_id,
        "nominee": nominee.nominee_id,
        "run_and_finalization": nominee.migration_run_id,
        "ha_user": context.ha_user.ha_user_id,
        "person": nominee.person_id,
    }
    collision = _new_id(99)
    semantic_ids[first] = collision
    semantic_ids[second] = collision
    ha_user = dataclasses.replace(
        context.ha_user,
        snapshot_id=semantic_ids["snapshot"],
        ha_user_id=semantic_ids["ha_user"],
    )
    nominee = dataclasses.replace(
        nominee,
        nominee_id=semantic_ids["nominee"],
        migration_run_id=semantic_ids["run_and_finalization"],
        finalization_id=semantic_ids["run_and_finalization"],
        person_id=semantic_ids["person"],
    )
    graph = dataclasses.replace(
        context.graph,
        ha_user_id=semantic_ids["ha_user"],
        person_id=semantic_ids["person"],
    )
    changed = dataclasses.replace(
        context,
        request_id=semantic_ids["request"],
        ha_user=ha_user,
        person_nominees=(nominee,),
        graph=graph,
    )
    with pytest.raises(PrincipalBindingProtocolError, match="invalid_identifier"):
        _compile(changed)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("snapshot_origin", "client_claim"),
        ("authentication_status", "anonymous"),
        ("user_status", "inactive"),
    ],
)
def test_only_the_server_authenticated_active_ha_snapshot_is_accepted(
    field: str, replacement: str
) -> None:
    context = _context()
    ha_user = dataclasses.replace(context.ha_user, **{field: replacement})
    with pytest.raises(PrincipalBindingProtocolError, match="invalid_ha_snapshot"):
        _compile(dataclasses.replace(context, ha_user=ha_user))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("nomination_role", "household_member"),
        ("review_status", "pending"),
        ("finalization_status", "draft"),
        ("nomination_authority_status", "authoritative"),
        ("person_status", "archived"),
    ],
)
def test_nominee_must_be_an_unverified_reviewed_operator_candidate(
    field: str, replacement: str
) -> None:
    context = _context()
    nominee = dataclasses.replace(context.person_nominees[0], **{field: replacement})
    with pytest.raises(PrincipalBindingProtocolError, match="invalid_person_nominee"):
        _compile(dataclasses.replace(context, person_nominees=(nominee,)))


@pytest.mark.parametrize(
    "field",
    [
        "nominee_commitment",
        "projection_commitment",
        "finalization_commitment",
        "review_receipt_commitment",
        "privacy_state_commitment",
        "erasure_state_commitment",
    ],
)
def test_every_nominee_commitment_is_required(field: str) -> None:
    context = _context()
    nominee = dataclasses.replace(context.person_nominees[0], **{field: "private"})
    with pytest.raises(PrincipalBindingProtocolError, match="invalid_commitment"):
        _compile(dataclasses.replace(context, person_nominees=(nominee,)))


@pytest.mark.parametrize(
    "field",
    [
        "active_ha_user_binding_count",
        "active_person_binding_count",
        "other_open_request_count",
        "other_ready_proposal_count",
        "conflicting_person_nominee_count",
    ],
)
def test_any_current_graph_conflict_fails_closed(field: str) -> None:
    context = _context()
    graph = dataclasses.replace(context.graph, **{field: 1})
    with pytest.raises(PrincipalBindingProtocolError, match="binding_graph_conflict"):
        _compile(dataclasses.replace(context, graph=graph))


@pytest.mark.parametrize("count", [0, 2, -1, True])
def test_graph_must_report_exactly_one_reviewed_person_nominee(
    count: object,
) -> None:
    context = _context()
    graph = dataclasses.replace(context.graph, reviewed_person_nominee_count=count)
    with pytest.raises(
        PrincipalBindingProtocolError, match="invalid_person_nominee_set"
    ):
        _compile(dataclasses.replace(context, graph=graph))


def test_graph_subjects_must_match_the_server_snapshots() -> None:
    context = _context()
    for graph in (
        dataclasses.replace(context.graph, ha_user_id=_stable_id(99)),
        dataclasses.replace(context.graph, person_id=_stable_id(100)),
    ):
        with pytest.raises(
            PrincipalBindingProtocolError, match="invalid_binding_graph"
        ):
            _compile(dataclasses.replace(context, graph=graph))


@pytest.mark.parametrize(
    "directive",
    ["do_not_track", "ignored", "silent", "private", "auto_expire", "unknown"],
)
def test_blocking_or_unknown_privacy_directives_fail_closed(directive: str) -> None:
    context = _context()
    nominee = dataclasses.replace(
        context.person_nominees[0], privacy_directives=(directive,)
    )
    with pytest.raises(PrincipalBindingProtocolError, match="privacy_blocked"):
        _compile(dataclasses.replace(context, person_nominees=(nominee,)))


def test_no_directive_is_eligible_before_subject_binding() -> None:
    assert _compile().privacy_scope == "private"
    context = _context()
    nominee = dataclasses.replace(
        context.person_nominees[0], privacy_directives=("private", "private")
    )
    with pytest.raises(PrincipalBindingProtocolError, match="privacy_blocked"):
        _compile(dataclasses.replace(context, person_nominees=(nominee,)))

    for malformed in ([], "", set()):
        nominee = dataclasses.replace(
            context.person_nominees[0], privacy_directives=malformed
        )
        with pytest.raises(PrincipalBindingProtocolError, match="privacy_blocked"):
            _compile(dataclasses.replace(context, person_nominees=(nominee,)))


def test_retrieval_or_erasure_state_blocks_the_preview() -> None:
    context = _context()
    retrieval_blocked = dataclasses.replace(
        context.person_nominees[0], retrieval_blocked=True
    )
    with pytest.raises(PrincipalBindingProtocolError, match="privacy_blocked"):
        _compile(dataclasses.replace(context, person_nominees=(retrieval_blocked,)))

    for replacement in (
        {"erasure_status": "pending"},
        {"open_erasure_request_count": 1},
        {"pending_erasure_task_count": 1},
    ):
        nominee = dataclasses.replace(context.person_nominees[0], **replacement)
        with pytest.raises(PrincipalBindingProtocolError, match="erasure_blocked"):
            _compile(dataclasses.replace(context, person_nominees=(nominee,)))


def test_preview_and_trusted_state_windows_are_strictly_bounded() -> None:
    context = _context()
    invalid_contexts = (
        dataclasses.replace(context, expires_at=context.staged_at),
        dataclasses.replace(
            context, expires_at=context.staged_at + timedelta(minutes=15, seconds=1)
        ),
        dataclasses.replace(context, staged_at=context.staged_at.replace(tzinfo=None)),
    )
    for changed in invalid_contexts:
        with pytest.raises(PrincipalBindingProtocolError, match="invalid_time_window"):
            _compile(changed)

    stale_ha = dataclasses.replace(
        context.ha_user, observed_at=BASE_TIME - timedelta(minutes=15)
    )
    with pytest.raises(PrincipalBindingProtocolError, match="stale_trusted_state"):
        _compile(dataclasses.replace(context, ha_user=stale_ha))

    post_stage_ha = dataclasses.replace(
        context.ha_user, observed_at=BASE_TIME + timedelta(microseconds=1)
    )
    with pytest.raises(PrincipalBindingProtocolError, match="stale_trusted_state"):
        _compile(dataclasses.replace(context, ha_user=post_stage_ha))


def test_compile_and_verify_require_fresh_trusted_operation_time() -> None:
    context = _context()
    for observed_at, code in (
        (BASE_TIME - timedelta(microseconds=1), "preview_not_current"),
        (context.expires_at, "preview_expired"),
        (context.expires_at + timedelta(seconds=1), "preview_expired"),
    ):
        with pytest.raises(PrincipalBindingProtocolError) as exc:
            compile_principal_binding_preview(
                context, digest_key=KEY, trusted_operation_time=observed_at
            )
        assert exc.value.code == code

    preview = _compile(context)
    with pytest.raises(PrincipalBindingProtocolError, match="preview_expired"):
        verify_principal_binding_preview(
            preview,
            supplied_digest=preview.preview_digest,
            current_context=context,
            digest_key=KEY,
            trusted_operation_time=context.expires_at,
        )


def test_digest_is_deterministic_and_timezone_canonical() -> None:
    context = _context()
    brazil = timezone(timedelta(hours=-3))
    shifted = dataclasses.replace(
        context,
        ha_user=dataclasses.replace(
            context.ha_user, observed_at=context.ha_user.observed_at.astimezone(brazil)
        ),
        person_nominees=(
            dataclasses.replace(
                context.person_nominees[0],
                status_observed_at=context.person_nominees[
                    0
                ].status_observed_at.astimezone(brazil),
            ),
        ),
        graph=dataclasses.replace(
            context.graph, observed_at=context.graph.observed_at.astimezone(brazil)
        ),
        staged_at=context.staged_at.astimezone(brazil),
        expires_at=context.expires_at.astimezone(brazil),
    )
    assert _compile(context).preview_digest == _compile(context).preview_digest
    assert _compile(shifted).preview_digest == _compile(context).preview_digest


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("authorization_commitment", _digest("6")),
        ("display_label", "Marcelo HA"),
    ],
)
def test_ha_snapshot_axes_are_digest_bound(field: str, replacement: object) -> None:
    context = _context()
    ha_user = dataclasses.replace(context.ha_user, **{field: replacement})
    changed = dataclasses.replace(context, ha_user=ha_user)
    assert _compile(changed).preview_digest != _compile(context).preview_digest


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("person_display_label", "Marcelo Lima"),
        ("nominee_commitment", _digest("7")),
        ("projection_commitment", _digest("8")),
        ("finalization_commitment", _digest("9")),
        ("review_receipt_commitment", _digest("a")),
        ("privacy_state_commitment", _digest("0")),
        ("erasure_state_commitment", _digest("b")),
    ],
)
def test_nominee_axes_are_digest_bound(field: str, replacement: object) -> None:
    context = _context()
    nominee = dataclasses.replace(context.person_nominees[0], **{field: replacement})
    changed = dataclasses.replace(context, person_nominees=(nominee,))
    assert _compile(changed).preview_digest != _compile(context).preview_digest


def test_graph_and_policy_are_digest_bound() -> None:
    context = _context()
    changed_graph = dataclasses.replace(context.graph, graph_digest=_digest("5"))
    assert (
        _compile(dataclasses.replace(context, graph=changed_graph)).preview_digest
        != _compile(context).preview_digest
    )
    assert (
        _compile(
            dataclasses.replace(context, policy_digest=_digest("6"))
        ).preview_digest
        != _compile(context).preview_digest
    )
    assert (
        _compile(
            dataclasses.replace(context, policy_version="home-agent-mvp-v2")
        ).preview_digest
        != _compile(context).preview_digest
    )


def test_preview_hmac_depends_on_the_key_and_wrong_key_is_rejected() -> None:
    context = _context()
    preview = _compile(context)
    other = compile_principal_binding_preview(
        context,
        digest_key=OTHER_KEY,
        trusted_operation_time=BASE_TIME + timedelta(seconds=1),
    )
    assert other.preview_digest != preview.preview_digest
    with pytest.raises(PrincipalBindingProtocolError, match="preview_mismatch"):
        verify_principal_binding_preview(
            preview,
            supplied_digest=preview.preview_digest,
            current_context=context,
            digest_key=OTHER_KEY,
            trusted_operation_time=BASE_TIME + timedelta(minutes=1),
        )


def test_verification_accepts_new_read_metadata_with_unchanged_semantics() -> None:
    original = _context()
    preview = _compile(original)
    refreshed_at = BASE_TIME + timedelta(minutes=1)
    current = dataclasses.replace(
        original,
        ha_user=dataclasses.replace(
            original.ha_user,
            snapshot_id=_new_id(20),
            snapshot_commitment=_digest("5"),
            observed_at=refreshed_at,
        ),
        person_nominees=(
            dataclasses.replace(
                original.person_nominees[0], status_observed_at=refreshed_at
            ),
        ),
        graph=dataclasses.replace(original.graph, observed_at=refreshed_at),
    )
    intent = verify_principal_binding_preview(
        preview,
        supplied_digest=preview.preview_digest,
        current_context=current,
        digest_key=KEY,
        trusted_operation_time=BASE_TIME + timedelta(minutes=2),
    )
    assert intent.proposal_digest == preview.preview_digest
    assert intent.ha_snapshot_id == _new_id(20)
    assert intent.ha_snapshot_observed_at == refreshed_at
    assert intent.nominee_status_observed_at == refreshed_at
    assert intent.graph_observed_at == refreshed_at

    original_state = verify_principal_binding_preview(
        preview,
        supplied_digest=preview.preview_digest,
        current_context=original,
        digest_key=KEY,
        trusted_operation_time=BASE_TIME + timedelta(minutes=2),
    )
    later_operation = verify_principal_binding_preview(
        preview,
        supplied_digest=preview.preview_digest,
        current_context=current,
        digest_key=KEY,
        trusted_operation_time=BASE_TIME + timedelta(minutes=3),
    )
    assert intent.verification_state_digest != original_state.verification_state_digest
    assert intent.verification_state_digest != later_operation.verification_state_digest


@pytest.mark.parametrize(
    "observed_at",
    [
        BASE_TIME - timedelta(minutes=14),
        BASE_TIME + timedelta(minutes=3),
    ],
)
def test_verification_rejects_stale_or_future_refreshed_state(
    observed_at: datetime,
) -> None:
    context = _context()
    preview = _compile(context)
    current = dataclasses.replace(
        context,
        ha_user=dataclasses.replace(context.ha_user, observed_at=observed_at),
    )
    with pytest.raises(PrincipalBindingProtocolError, match="stale_trusted_state"):
        verify_principal_binding_preview(
            preview,
            supplied_digest=preview.preview_digest,
            current_context=current,
            digest_key=KEY,
            trusted_operation_time=BASE_TIME + timedelta(minutes=2),
        )


@pytest.mark.parametrize(
    "preview_change",
    [
        {"title": "Link this account"},
        {"statement": "Link a different person"},
        {"safety_note": "This creates a binding"},
        {"action_label": "Enable writes"},
        {"expires_at": BASE_TIME + timedelta(minutes=14)},
    ],
)
def test_private_preview_tampering_is_rejected(
    preview_change: dict[str, object],
) -> None:
    context = _context()
    valid = _compile(context)
    preview = dataclasses.replace(valid, **preview_change)
    with pytest.raises(PrincipalBindingProtocolError, match="preview_mismatch"):
        verify_principal_binding_preview(
            preview,
            supplied_digest=valid.preview_digest,
            current_context=context,
            digest_key=KEY,
            trusted_operation_time=BASE_TIME + timedelta(minutes=1),
        )


def test_preview_subclass_cannot_override_the_authenticated_payload() -> None:
    class SubstitutedPreview(PrincipalBindingPreview):
        def private_payload(self) -> dict[str, object]:
            return valid.private_payload()

    context = _context()
    valid = _compile(context)
    substituted = SubstitutedPreview(
        request_id=valid.request_id,
        title="Link an attacker-selected account",
        statement="Link an attacker-selected person",
        safety_note="This creates authority",
        action_label="Enable writes",
        expires_at=valid.expires_at,
        preview_digest=valid.preview_digest,
    )
    with pytest.raises(PrincipalBindingProtocolError, match="preview_mismatch"):
        verify_principal_binding_preview(
            substituted,
            supplied_digest=valid.preview_digest,
            current_context=context,
            digest_key=KEY,
            trusted_operation_time=BASE_TIME + timedelta(minutes=1),
        )


def test_digest_tampering_and_fresh_state_drift_are_rejected() -> None:
    context = _context()
    preview = _compile(context)
    with pytest.raises(PrincipalBindingProtocolError, match="preview_mismatch"):
        verify_principal_binding_preview(
            preview,
            supplied_digest=_digest("0"),
            current_context=context,
            digest_key=KEY,
            trusted_operation_time=BASE_TIME + timedelta(minutes=1),
        )

    current_graph = dataclasses.replace(context.graph, graph_digest=_digest("5"))
    with pytest.raises(PrincipalBindingProtocolError, match="preview_mismatch"):
        verify_principal_binding_preview(
            preview,
            supplied_digest=preview.preview_digest,
            current_context=dataclasses.replace(context, graph=current_graph),
            digest_key=KEY,
            trusted_operation_time=BASE_TIME + timedelta(minutes=1),
        )


def test_unicode_labels_are_nfc_and_control_free() -> None:
    context = _context()
    valid = dataclasses.replace(
        context.person_nominees[0], person_display_label="Marcélo"
    )
    assert (
        "Marcélo"
        in _compile(dataclasses.replace(context, person_nominees=(valid,))).statement
    )

    for label in ("Marce\u0301lo", "Marcelo\u202e", " Marcelo", ""):
        nominee = dataclasses.replace(
            context.person_nominees[0], person_display_label=label
        )
        with pytest.raises(
            PrincipalBindingProtocolError, match="invalid_display_label"
        ):
            _compile(dataclasses.replace(context, person_nominees=(nominee,)))


def test_errors_and_audit_payload_are_content_free() -> None:
    context = _context()
    bad = dataclasses.replace(context.graph, active_person_binding_count=1)
    with pytest.raises(PrincipalBindingProtocolError) as exc:
        _compile(dataclasses.replace(context, graph=bad))
    assert str(exc.value) == "binding_graph_conflict"
    assert "Marcelo" not in str(exc.value)
    assert str(context.person_nominees[0].person_id) not in str(exc.value)

    preview = _compile(context)
    private = str(preview.private_payload())
    audit = str(preview.content_free_audit_payload())
    assert "Marcelo" in private
    assert "Marcelo" not in audit
    assert str(context.ha_user.ha_user_id) not in audit
    assert str(context.person_nominees[0].person_id) not in audit
    assert not hasattr(preview, "public_payload")


def test_sensitive_repr_output_is_redacted() -> None:
    context = _context()
    preview = _compile(context)
    intent = _verify(context)
    rendered = " ".join((repr(context), repr(preview), repr(intent)))
    for sensitive in (
        "Marcelo",
        str(context.ha_user.ha_user_id),
        str(context.person_nominees[0].person_id),
        context.person_nominees[0].nominee_commitment,
        context.graph.graph_digest,
        preview.preview_digest,
        intent.verification_state_digest,
    ):
        assert sensitive not in rendered


def test_locked_safety_fields_cannot_be_replaced() -> None:
    preview = _compile()
    intent = _verify()
    for target in (preview, intent):
        for field_name, value in (
            ("authoritative", True),
            ("commit_ready", True),
            ("enables_writes", True),
            ("me_identity_established", True),
            ("binding_created", True),
            ("location_memory_enabled", True),
            ("travel_greetings_enabled", True),
            ("external_receipts_verified", True),
            ("fresh_state_origin_verified", True),
            ("nomination_authority_verified", True),
            ("capability_status", "enabled"),
            ("deployment_status", "deployable"),
        ):
            with pytest.raises(ValueError):
                dataclasses.replace(target, **{field_name: value})

    with pytest.raises(ValueError):
        dataclasses.replace(intent, requires_transaction_time_state_read=False)


def test_protocol_accepts_no_client_authority_or_confirmation_fields() -> None:
    function_parameters = set(
        inspect.signature(compile_principal_binding_preview).parameters
    ) | set(inspect.signature(verify_principal_binding_preview).parameters)
    for forbidden in (
        "actor_id",
        "principal_id",
        "person_id",
        "confirmed",
        "confirmation",
        "nonce",
        "valid_from",
        "caller_time",
    ):
        assert forbidden not in function_parameters
    assert "trusted_operation_time" in function_parameters

    input_fields = {
        item.name
        for shape in (
            AuthenticatedHaUserSnapshot,
            ReviewedPersonNominee,
            PrincipalBindingGraphSnapshot,
            PrincipalBindingContext,
        )
        for item in dataclasses.fields(shape)
    }
    assert "confirmed" not in input_fields
    assert "confirmation" not in input_fields
    assert "nonce" not in input_fields
    assert "valid_from" not in input_fields
    assert "caller_time" not in input_fields


def test_malformed_digest_key_and_preview_fail_content_free() -> None:
    with pytest.raises(PrincipalBindingProtocolError, match="invalid_digest_key"):
        compile_principal_binding_preview(
            _context(),
            digest_key=b"short",
            trusted_operation_time=BASE_TIME + timedelta(seconds=1),
        )

    context = _context()
    preview = dataclasses.replace(_compile(context), preview_digest=None)
    with pytest.raises(PrincipalBindingProtocolError) as exc:
        verify_principal_binding_preview(
            preview,
            supplied_digest=_digest("0"),
            current_context=context,
            digest_key=KEY,
            trusted_operation_time=BASE_TIME + timedelta(minutes=1),
        )
    assert exc.value.code == "preview_mismatch"
    assert "Marcelo" not in str(exc.value)


def test_protocol_module_is_pure_and_contains_no_writer_primitive() -> None:
    module_path = Path(__file__).resolve().parents[1] / "app/principal_binding.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    forbidden_import_roots = {
        "asyncio",
        "httpx",
        "psycopg",
        "requests",
        "socket",
        "sqlalchemy",
        "subprocess",
    }
    forbidden_calls = {"open", "exec", "eval", "compile", "__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(
                alias.name.split(".", 1)[0] not in forbidden_import_roots
                for alias in node.names
            )
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".", 1)[0] not in forbidden_import_roots
        if isinstance(node, (ast.AsyncFunctionDef, ast.Await)):
            pytest.fail("dormant principal-binding compiler must remain synchronous")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_calls


def test_protocol_has_no_production_import_or_route() -> None:
    repository = Path(__file__).resolve().parents[4]
    roots = (
        repository / "stack/services/home-agent-core/app",
        repository / "stack/services/home-agent-bff",
        repository / "app/src",
        repository / "app/src-tauri/src",
        repository / "web-gateway",
        repository / "ha-config/home_agent_edge",
    )
    forbidden = tuple(
        re.compile(pattern)
        for pattern in (
            r"\bfrom\s+(?:app\.)?principal_binding\s+import\b",
            r"\bimport\s+(?:app\.)?principal_binding\b",
            r"(?:import_module|__import__)\(\s*[\"'](?:app\.)?principal_binding[\"']",
            r"[\"'][^\"'\r\n]*/principal[-_]bindings?(?:[/\"'])",
        )
    )
    inspected: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            relative_parts = {
                part.casefold() for part in path.relative_to(root).parts[:-1]
            }
            if relative_parts & {"test", "tests", "__tests__"}:
                continue
            if ".test." in path.name or ".spec." in path.name:
                continue
            if path.suffix.lower() not in {
                ".py",
                ".js",
                ".mjs",
                ".ts",
                ".tsx",
                ".jsx",
                ".rs",
            }:
                continue
            if path.name == "principal_binding.py":
                continue
            source = path.read_text(encoding="utf-8")
            inspected.append(path)
            assert not any(pattern.search(source) for pattern in forbidden), path
            if path.suffix.lower() == ".py":
                tree = ast.parse(source, filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        assert all(
                            not alias.name.endswith("principal_binding")
                            for alias in node.names
                        ), path
                    if isinstance(node, ast.ImportFrom):
                        assert not (
                            (node.module or "").endswith("principal_binding")
                            or any(
                                alias.name == "principal_binding"
                                for alias in node.names
                            )
                        ), path
    assert inspected

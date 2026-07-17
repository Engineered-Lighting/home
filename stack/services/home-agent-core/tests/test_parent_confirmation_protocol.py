from __future__ import annotations

import ast
import dataclasses
import inspect
import re
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.parent_confirmation import (
    CAPABILITY_STATUS,
    CONTRACT_VERSION,
    DEPLOYMENT_STATUS,
    EMPTY_OPEN_PARENT_FACT_SET_DIGEST,
    PROHIBITED_IMPLICATIONS,
    RULE_VERSION,
    CurrentChildBindingSnapshot,
    ParentConfirmationContext,
    ParentConfirmationProtocolError,
    StagedParentCandidate,
    compile_parent_confirmation_preview,
    verify_parent_confirmation_preview,
)


KEY = bytes.fromhex("11" * 32)
BASE_TIME = datetime(2026, 7, 16, 18, 0, 0, 123456, tzinfo=UTC)


def _new_id(ordinal: int) -> uuid.UUID:
    return uuid.UUID(f"018f0000-0000-7000-8000-{ordinal:012x}")


def _stable_person_id(ordinal: int) -> uuid.UUID:
    return uuid.UUID(f"12345678-1234-4abc-8def-{ordinal:012x}")


def _digest(char: str) -> str:
    return char * 64


def _candidate(
    ordinal: int,
    label: str,
    review_code: str,
    *,
    child_person_id: uuid.UUID = _stable_person_id(2),
    directives: tuple[str, ...] = (),
    retrieval_blocked: bool = False,
) -> StagedParentCandidate:
    return StagedParentCandidate(
        staged_candidate_id=_new_id(100 + ordinal),
        parent_person_id=_stable_person_id(10 + ordinal),
        child_person_id=child_person_id,
        parent_display_label=label,
        review_code=review_code,
        legacy_label_artifact_id=_new_id(200 + ordinal),
        source_snapshot_digest=_digest(str(ordinal)),
        operator_review_receipt_digest=_digest(chr(ord("a") + ordinal)),
        legacy_role_label="parent",
        legacy_perspective="unknown",
        person_status="active",
        retrieval_blocked=retrieval_blocked,
        privacy_directives=directives,
    )


def _context() -> ParentConfirmationContext:
    return ParentConfirmationContext(
        request_id=_new_id(1),
        child_binding=CurrentChildBindingSnapshot(
            principal_id=_new_id(3),
            child_person_id=_stable_person_id(2),
            child_display_label="Marcelo",
            binding_receipt_digest=_digest("b"),
            authenticated=True,
            binding_status="confirmed",
            person_status="active",
            retrieval_blocked=False,
            privacy_directives=("private",),
        ),
        candidates=(
            _candidate(1, "Amelia", "ABCDEFGHJKLMNPQR"),
            _candidate(2, "Marcelo Sr.", "STUVWXYZ23456789"),
        ),
        migration_run_id=_new_id(4),
        migration_review_receipt_commitment=_digest("d"),
        migration_projection_manifest_commitment=_digest("e"),
        migration_receipt_expires_at=BASE_TIME + timedelta(hours=1),
        current_open_parent_fact_count=0,
        current_open_parent_fact_set_digest=EMPTY_OPEN_PARENT_FACT_SET_DIGEST,
        policy_version="home-agent-mvp-v1",
        policy_digest=_digest("f"),
        staged_at=BASE_TIME,
        expires_at=BASE_TIME + timedelta(minutes=15),
    )


def _compile(context: ParentConfirmationContext | None = None):
    return compile_parent_confirmation_preview(
        context or _context(),
        digest_key=KEY,
        evaluated_at=BASE_TIME + timedelta(seconds=1),
    )


def _verify(context: ParentConfirmationContext | None = None):
    actual = context or _context()
    preview = _compile(actual)
    return verify_parent_confirmation_preview(
        preview,
        supplied_digest=preview.preview_digest,
        current_context=actual,
        digest_key=KEY,
        trusted_operation_time=BASE_TIME + timedelta(minutes=1),
    )


def test_compiler_renders_one_private_atomic_dormant_preview() -> None:
    preview = _compile()

    assert preview.contract_version == CONTRACT_VERSION
    assert preview.rule_version == RULE_VERSION
    assert preview.deployment_status == DEPLOYMENT_STATUS == "non_deployable"
    assert preview.atomic_edge_count == 2
    assert preview.authoritative is False
    assert preview.commit_ready is False
    assert preview.enables_writes is False
    assert preview.external_receipts_verified is False
    assert preview.capability_status == CAPABILITY_STATUS == "capability_disabled"
    assert preview.privacy_scope == "private"
    assert preview.not_asserted == PROHIBITED_IMPLICATIONS
    assert "context only" in preview.legacy_context_note
    assert "Both relationships apply together or neither applies." in preview.statement
    assert "ABCDEFGHJKLMNPQR" in preview.statement
    assert "STUVWXYZ23456789" in preview.statement
    assert len(preview.preview_digest) == 64


def test_candidate_input_order_does_not_change_preview_or_digest() -> None:
    first = _context()
    reversed_context = dataclasses.replace(
        first, candidates=tuple(reversed(first.candidates))
    )

    assert _compile(reversed_context) == _compile(first)


@pytest.mark.parametrize(
    "candidates",
    [
        (),
        (_candidate(1, "Amelia", "ABCDEFGHJKLMNPQR"),),
        (
            _candidate(1, "Amelia", "ABCDEFGHJKLMNPQR"),
            _candidate(2, "Marcelo Sr.", "STUVWXYZ23456789"),
            _candidate(3, "Third", "23456789ABCDEFGH"),
        ),
    ],
)
def test_compiler_requires_exactly_two_candidates(candidates) -> None:
    with pytest.raises(ParentConfirmationProtocolError) as exc:
        _compile(dataclasses.replace(_context(), candidates=candidates))
    assert exc.value.code == "invalid_candidate_set"


@pytest.mark.parametrize(
    "replacement",
    [
        {"parent_person_id": _stable_person_id(2)},
        {"child_person_id": _stable_person_id(999)},
        {"legacy_role_label": "parent_of"},
        {"legacy_perspective": "Marcelo"},
        {"person_status": "archived"},
        {"authenticated": "yes"},
    ],
)
def test_invalid_edge_or_binding_shapes_fail_content_free(replacement) -> None:
    context = _context()
    if "authenticated" in replacement:
        child = dataclasses.replace(context.child_binding, **replacement)
        context = dataclasses.replace(context, child_binding=child)
        expected = "invalid_child_binding"
    else:
        changed = dataclasses.replace(context.candidates[0], **replacement)
        context = dataclasses.replace(
            context, candidates=(changed, context.candidates[1])
        )
        expected = "invalid_candidate_set"
    with pytest.raises(ParentConfirmationProtocolError) as exc:
        _compile(context)
    assert str(exc.value) == expected
    assert "Amelia" not in str(exc.value)
    assert str(_stable_person_id(2)) not in str(exc.value)


@pytest.mark.parametrize(
    "field",
    [
        "staged_candidate_id",
        "parent_person_id",
        "legacy_label_artifact_id",
        "review_code",
    ],
)
def test_duplicate_candidate_identity_or_provenance_is_rejected(field: str) -> None:
    context = _context()
    changed = dataclasses.replace(
        context.candidates[1], **{field: getattr(context.candidates[0], field)}
    )
    with pytest.raises(ParentConfirmationProtocolError, match="invalid_candidate_set"):
        _compile(
            dataclasses.replace(context, candidates=(context.candidates[0], changed))
        )


@pytest.mark.parametrize(
    "directive",
    ["do_not_track", "ignored", "silent", "private", "auto_expire", "future_unknown"],
)
def test_every_parent_privacy_directive_blocks_confirmation(directive: str) -> None:
    context = _context()
    blocked = dataclasses.replace(
        context.candidates[0], privacy_directives=(directive,)
    )
    with pytest.raises(ParentConfirmationProtocolError, match="privacy_blocked"):
        _compile(
            dataclasses.replace(context, candidates=(blocked, context.candidates[1]))
        )


def test_child_private_is_allowed_but_other_child_privacy_directives_block() -> None:
    assert _compile().privacy_scope == "private"
    context = _context()
    for directive in ("do_not_track", "ignored", "silent", "auto_expire"):
        child = dataclasses.replace(
            context.child_binding, privacy_directives=(directive,)
        )
        with pytest.raises(ParentConfirmationProtocolError, match="privacy_blocked"):
            _compile(dataclasses.replace(context, child_binding=child))


def test_child_and_either_parent_retrieval_blocks_fail_closed() -> None:
    context = _context()
    child = dataclasses.replace(context.child_binding, retrieval_blocked=True)
    with pytest.raises(ParentConfirmationProtocolError, match="invalid_child_binding"):
        _compile(dataclasses.replace(context, child_binding=child))

    for index in (0, 1):
        candidates = list(context.candidates)
        candidates[index] = dataclasses.replace(
            candidates[index], retrieval_blocked=True
        )
        with pytest.raises(
            ParentConfirmationProtocolError, match="invalid_candidate_set"
        ):
            _compile(dataclasses.replace(context, candidates=tuple(candidates)))


def test_stable_uuid4_people_are_valid_but_new_request_ids_require_uuid7() -> None:
    preview = _compile()
    assert preview.relationships[0].predicate == "parent_of"

    with pytest.raises(ParentConfirmationProtocolError, match="invalid_identifier"):
        _compile(dataclasses.replace(_context(), request_id=uuid.uuid4()))


@pytest.mark.parametrize("count", [1, 2, -1])
def test_any_existing_open_parent_fact_blocks_atomic_preview(count: int) -> None:
    context = dataclasses.replace(_context(), current_open_parent_fact_count=count)
    with pytest.raises(
        ParentConfirmationProtocolError, match="existing_parent_fact_state"
    ):
        _compile(context)


def test_empty_parent_fact_snapshot_digest_is_exact() -> None:
    context = dataclasses.replace(
        _context(), current_open_parent_fact_set_digest=_digest("0")
    )
    with pytest.raises(
        ParentConfirmationProtocolError, match="existing_parent_fact_state"
    ):
        _compile(context)


def test_labels_and_review_codes_must_be_unambiguous() -> None:
    context = _context()
    duplicate_label = dataclasses.replace(
        context.candidates[1], parent_display_label="amelia"
    )
    with pytest.raises(
        ParentConfirmationProtocolError, match="ambiguous_display_identity"
    ):
        _compile(
            dataclasses.replace(
                context, candidates=(context.candidates[0], duplicate_label)
            )
        )

    non_nfc = dataclasses.replace(
        context.candidates[0], parent_display_label="Ame\u0301lia"
    )
    with pytest.raises(ParentConfirmationProtocolError, match="invalid_display_label"):
        _compile(
            dataclasses.replace(context, candidates=(non_nfc, context.candidates[1]))
        )

    invalid_code = dataclasses.replace(
        context.candidates[0], review_code="CONFUSING0000000"
    )
    with pytest.raises(ParentConfirmationProtocolError, match="invalid_review_code"):
        _compile(
            dataclasses.replace(
                context, candidates=(invalid_code, context.candidates[1])
            )
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("request_id", _new_id(900)),
        ("migration_run_id", _new_id(901)),
        ("migration_review_receipt_commitment", _digest("2")),
        ("migration_projection_manifest_commitment", _digest("3")),
        ("migration_receipt_expires_at", BASE_TIME + timedelta(minutes=30)),
        ("policy_version", "home-agent-mvp-v2"),
        ("policy_digest", _digest("4")),
        ("staged_at", BASE_TIME + timedelta(seconds=1)),
        ("expires_at", BASE_TIME + timedelta(minutes=14)),
    ],
)
def test_every_context_axis_is_digest_bound(field: str, replacement: object) -> None:
    context = _context()
    changed = dataclasses.replace(context, **{field: replacement})
    assert _compile(changed).preview_digest != _compile(context).preview_digest


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("parent_person_id", _stable_person_id(900)),
        ("child_person_id", _stable_person_id(901)),
        ("parent_display_label", "Different Parent"),
        ("review_code", "23456789ABCDEFGH"),
        ("source_snapshot_digest", _digest("5")),
        ("operator_review_receipt_digest", _digest("6")),
        ("staged_candidate_id", _new_id(998)),
        ("legacy_label_artifact_id", _new_id(997)),
    ],
)
def test_every_candidate_axis_is_digest_bound_or_rejected(
    field: str, replacement: object
) -> None:
    context = _context()
    changed = dataclasses.replace(context.candidates[0], **{field: replacement})
    changed_context = dataclasses.replace(
        context, candidates=(changed, context.candidates[1])
    )
    if field == "child_person_id":
        with pytest.raises(ParentConfirmationProtocolError):
            _compile(changed_context)
    else:
        assert (
            _compile(changed_context).preview_digest != _compile(context).preview_digest
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("principal_id", _new_id(902)),
        ("child_display_label", "Marcelo Lima"),
        ("binding_receipt_digest", _digest("7")),
        ("privacy_directives", ()),
    ],
)
def test_every_child_binding_axis_is_digest_bound(
    field: str, replacement: object
) -> None:
    context = _context()
    child = dataclasses.replace(context.child_binding, **{field: replacement})
    changed = dataclasses.replace(context, child_binding=child)
    assert _compile(changed).preview_digest != _compile(context).preview_digest


def test_digest_is_canonical_across_equivalent_timezone_offsets() -> None:
    context = _context()
    brazil = timezone(timedelta(hours=-3))
    shifted = dataclasses.replace(
        context,
        staged_at=context.staged_at.astimezone(brazil),
        expires_at=context.expires_at.astimezone(brazil),
        migration_receipt_expires_at=context.migration_receipt_expires_at.astimezone(
            brazil
        ),
    )
    assert _compile(shifted).preview_digest == _compile(context).preview_digest


def test_preview_window_and_migration_receipt_are_bounded() -> None:
    context = _context()
    for changed in (
        dataclasses.replace(context, expires_at=context.staged_at),
        dataclasses.replace(
            context, expires_at=context.staged_at + timedelta(minutes=16)
        ),
        dataclasses.replace(
            context,
            migration_receipt_expires_at=context.expires_at - timedelta(microseconds=1),
        ),
        dataclasses.replace(context, staged_at=context.staged_at.replace(tzinfo=None)),
    ):
        with pytest.raises(
            ParentConfirmationProtocolError, match="invalid_time_window"
        ):
            _compile(changed)


def test_compile_and_verify_require_current_trusted_operation_time() -> None:
    context = _context()
    for observed_at, code in (
        (context.staged_at - timedelta(microseconds=1), "preview_not_current"),
        (context.expires_at, "preview_expired"),
        (context.expires_at + timedelta(seconds=1), "preview_expired"),
    ):
        with pytest.raises(ParentConfirmationProtocolError) as exc:
            compile_parent_confirmation_preview(
                context, digest_key=KEY, evaluated_at=observed_at
            )
        assert exc.value.code == code

    preview = _compile(context)
    with pytest.raises(ParentConfirmationProtocolError, match="preview_expired"):
        verify_parent_confirmation_preview(
            preview,
            supplied_digest=preview.preview_digest,
            current_context=context,
            digest_key=KEY,
            trusted_operation_time=context.expires_at,
        )


def test_verifier_returns_one_grouped_dormant_two_edge_intent() -> None:
    intent = _verify()

    assert intent.contract_version == CONTRACT_VERSION
    assert intent.rule_version == RULE_VERSION
    assert intent.deployment_status == "non_deployable"
    assert intent.requires_atomic_commit is True
    assert intent.atomic_commit_enforced is False
    assert intent.authoritative is False
    assert intent.commit_ready is False
    assert intent.enables_writes is False
    assert intent.external_receipts_verified is False
    assert intent.capability_status == "capability_disabled"
    assert intent.requires_serializable_commit is True
    assert intent.requires_fresh_authenticated_confirmation_artifact is True
    assert intent.requires_atomic_confirmation_artifact_consume is True
    assert len(intent.edges) == 2
    assert intent.open_parent_fact_set_digest == EMPTY_OPEN_PARENT_FACT_SET_DIGEST


def test_every_future_fact_axis_is_locked_on_each_dormant_edge() -> None:
    for edge in _verify().edges:
        assert edge.contract_version == CONTRACT_VERSION
        assert edge.deployment_status == "non_deployable"
        assert edge.predicate == "parent_of"
        assert edge.required_authority == "explicit_related_party"
        assert edge.required_support == "explicit_authority"
        assert edge.required_contradiction == "none"
        assert edge.required_freshness == "not_applicable"
        assert edge.required_coverage == "not_applicable"
        assert edge.required_resolution == "accepted"
        assert edge.privacy_scope == "private"
        assert edge.valid_from_rule == "confirmation_transaction_time"
        assert edge.confirmation_support_role == "confirmation"
        assert edge.legacy_support_role == "legacy_context"
        assert edge.authoritative is False
        assert edge.commit_ready is False
        assert edge.enables_writes is False

    locked = {
        item.name for item in dataclasses.fields(_verify().edges[0]) if not item.init
    }
    assert {
        "required_authority",
        "required_support",
        "required_resolution",
        "authoritative",
        "commit_ready",
        "enables_writes",
    }.issubset(locked)


@pytest.mark.parametrize(
    "preview_change",
    [
        {"title": "Confirm something else"},
        {"statement": "One edge only"},
        {"legacy_context_note": "Legacy labels are authority"},
        {"action_label": "Confirm one"},
        {"expires_at": BASE_TIME + timedelta(minutes=14)},
    ],
)
def test_private_preview_tampering_is_rejected(preview_change) -> None:
    context = _context()
    preview = dataclasses.replace(_compile(context), **preview_change)
    with pytest.raises(ParentConfirmationProtocolError, match="preview_mismatch"):
        verify_parent_confirmation_preview(
            preview,
            supplied_digest=preview.preview_digest,
            current_context=context,
            digest_key=KEY,
            trusted_operation_time=BASE_TIME + timedelta(minutes=1),
        )


def test_locked_safety_fields_cannot_be_replaced() -> None:
    preview = _compile()
    for field_name, value in (
        ("authoritative", True),
        ("commit_ready", True),
        ("enables_writes", True),
        ("capability_status", "enabled"),
        ("deployment_status", "deployable"),
    ):
        with pytest.raises(ValueError):
            dataclasses.replace(preview, **{field_name: value})


def test_digest_tampering_and_current_context_drift_are_rejected() -> None:
    context = _context()
    preview = _compile(context)
    with pytest.raises(ParentConfirmationProtocolError, match="preview_mismatch"):
        verify_parent_confirmation_preview(
            preview,
            supplied_digest=_digest("0"),
            current_context=context,
            digest_key=KEY,
            trusted_operation_time=BASE_TIME + timedelta(minutes=1),
        )

    drifted = dataclasses.replace(context, policy_digest=_digest("9"))
    with pytest.raises(ParentConfirmationProtocolError, match="preview_mismatch"):
        verify_parent_confirmation_preview(
            preview,
            supplied_digest=preview.preview_digest,
            current_context=drifted,
            digest_key=KEY,
            trusted_operation_time=BASE_TIME + timedelta(minutes=1),
        )


def test_malformed_preview_types_fail_with_content_free_mismatch() -> None:
    context = _context()
    valid = _compile(context)
    malformed = (
        dataclasses.replace(valid, preview_digest=None),
        dataclasses.replace(valid, relationships=(object(),)),
    )
    for preview in malformed:
        with pytest.raises(ParentConfirmationProtocolError) as exc:
            verify_parent_confirmation_preview(
                preview,
                supplied_digest=valid.preview_digest,
                current_context=context,
                digest_key=KEY,
                trusted_operation_time=BASE_TIME + timedelta(minutes=1),
            )
        assert exc.value.code == "preview_mismatch"
        assert "Marcelo" not in str(exc.value)


def test_no_caller_supplied_confirmation_time_or_committed_valid_from_exists() -> None:
    parameters = inspect.signature(verify_parent_confirmation_preview).parameters
    assert "confirmed_at" not in parameters
    assert "trusted_operation_time" in parameters
    assert "valid_from" not in {
        item.name for item in dataclasses.fields(_verify().edges[0])
    }


def test_private_and_content_free_payloads_are_explicitly_separate() -> None:
    preview = _compile()
    private = str(preview.private_payload())
    audit = str(preview.content_free_audit_payload())
    assert "Amelia" in private and "Marcelo Sr." in private
    assert "Amelia" not in audit and "Marcelo" not in audit
    assert not hasattr(preview, "public_payload")


def test_sensitive_repr_output_is_redacted() -> None:
    context = _context()
    preview = _compile(context)
    intent = _verify(context)
    rendered = " ".join((repr(context), repr(preview), repr(intent)))
    for sensitive in (
        "Amelia",
        "Marcelo Sr.",
        str(_stable_person_id(2)),
        _digest("b"),
        preview.preview_digest,
    ):
        assert sensitive not in rendered


def test_prohibited_implications_are_visible_and_absent_from_edge_shape() -> None:
    preview = _compile()
    rendered = " ".join(preview.not_asserted)
    for phrase in ("ownership", "legal title", "residence", "presence", "association"):
        assert phrase in rendered
    edge_fields = {item.name for item in dataclasses.fields(_verify().edges[0])}
    for prohibited_field in (
        "owner",
        "residence",
        "present",
        "place_id",
        "associated_with",
    ):
        assert prohibited_field not in edge_fields


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
            r"\bfrom\s+(?:app\.)?parent_confirmation\s+import\b",
            r"\bimport\s+(?:app\.)?parent_confirmation\b",
            r"(?:import_module|__import__)\(\s*[\"'](?:app\.)?parent_confirmation[\"']",
            r"[\"'][^\"'\r\n]*/(?:relationships/)?parent[-_]confirmations?(?:[/\"'])",
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
            if path.name == "parent_confirmation.py":
                continue
            source = path.read_text(encoding="utf-8")
            inspected.append(path)
            assert not any(pattern.search(source) for pattern in forbidden), path
            if path.suffix.lower() == ".py":
                tree = ast.parse(source, filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        assert all(
                            not alias.name.endswith("parent_confirmation")
                            for alias in node.names
                        ), path
                    if isinstance(node, ast.ImportFrom):
                        assert not (
                            (node.module or "").endswith("parent_confirmation")
                            or any(
                                alias.name == "parent_confirmation"
                                for alias in node.names
                            )
                        ), path
    assert inspected

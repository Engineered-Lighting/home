from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "stack/home-agent-deploy/operator"
if str(OPERATOR) not in sys.path:
    sys.path.insert(0, str(OPERATOR))


def _load(name: str):
    path = OPERATOR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


payload = _load("reviewed_identity_payload")
source_plan = _load("phase3_activation_source_plan")
privacy_evidence = _load("phase3_privacy_cutover_evidence")
observer = _load("phase3_privacy_cutover_observer")

NOW = datetime(2026, 8, 13, 20, 0, tzinfo=UTC)
RUN_ID = "00000000-0000-7000-8000-000000000101"
EVIDENCE_ID = "00000000-0000-7000-8000-000000000102"
FREEZE_ID = "00000000-0000-7000-8000-000000000103"


def _source_report() -> dict[str, object]:
    return {
        "contract": source_plan.CONTRACT,
        "source_revision": source_plan.SOURCE_REVISION,
        "target_revision": source_plan.TARGET_REVISION,
        "blockers": [],
        "source_acceptance_receipt_issuable": True,
        "source_pack_matches_hosted_acceptance": True,
        "source_pin_bootstrap_installed": True,
        "identity_privacy_cutover_evidence_writer_installed": True,
        "identity_privacy_cutover_observer_installed": True,
        "identity_semantic_cutover_packet_compiler_installed": True,
        "activation_executor_installed": True,
        "authoritative": False,
        "enables_writes": False,
        "source_pack_digest": "a" * 64,
        "current_commit": "b" * 40,
    }


def _environment() -> str:
    return "\n".join(
        (
            f"HOME_AGENT_EXPECTED_DB_REVISION={source_plan.SOURCE_REVISION}",
            "HOME_AGENT_ROLLOUT_MODE=record_only",
            "HOME_AGENT_BACKUP_TOPOLOGY=local",
        )
    )


def _writer() -> tuple[bytes, bytes]:
    evidence_commitment = "c" * 64
    freeze_commitment = "d" * 64
    packet = {
        "contract": observer.WRITER_PACKET_CONTRACT,
        "authoritative": False,
        "run_id": RUN_ID,
        "finalization_id": RUN_ID,
        "writer_evidence": {
            "evidence_id": EVIDENCE_ID,
            "evidence_commitment": evidence_commitment,
        },
        "enforced_writer_freeze": {
            "freeze_id": FREEZE_ID,
            "verified_at": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "enforcement_status": "enforced_offline",
            "semantic_write_status": "frozen",
            "legacy_context_cutoff_status": "enforced_cutoff",
            "recognition_mode": "nonauthoritative_only",
            "write_probe_result": "blocked",
            "policy_digest": "e" * 64,
            "release_manifest_digest": "f" * 64,
            "freeze_commitment": freeze_commitment,
        },
    }
    raw = payload.canonical_bytes(packet)
    receipt = payload.canonical_bytes(
        {
            "contract": observer.WRITER_RECEIPT_CONTRACT,
            "status": "verified",
            "run_id": RUN_ID,
            "evidence_id": EVIDENCE_ID,
            "freeze_id": FREEZE_ID,
            "evidence_commitment": evidence_commitment,
            "freeze_commitment": freeze_commitment,
            "evidence_packet_sha256": hashlib.sha256(raw).hexdigest(),
        }
    )
    return raw, receipt


def _edge(*, refreshed_at: datetime = NOW - timedelta(minutes=1)) -> bytes:
    return payload.canonical_bytes(
        {
            "contract": observer.EDGE_RECEIPT_CONTRACT,
            "policy_digest": "1" * 64,
            "blocked_entity_count": 2,
            "blocked_user_count": 1,
            "purged_payload_count": 3,
            "purge_result": "purged_before_accept",
            "refreshed_at": refreshed_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
    )


def _compile(**overrides) -> bytes:
    writer, receipt = _writer()
    values = {
        "source_report": _source_report(),
        "environment_text": _environment(),
        "raw_writer_evidence": writer,
        "raw_writer_receipt": receipt,
        "raw_edge_receipt": _edge(),
        "now": NOW + timedelta(minutes=1),
    }
    values.update(overrides)
    return observer.compile_observation(**values)


def test_observer_binds_live_edge_freeze_environment_and_source() -> None:
    raw = _compile()
    value = payload.parse_canonical_json(raw)
    assert value["contract"] == observer.CONTRACT
    assert value["run_id"] == RUN_ID
    assert value["freeze_id"] == FREEZE_ID
    assert [item["check_category"] for item in value["categories"]] == list(
        privacy_evidence.CATEGORY_ORDER
    )
    assert all(
        set(item["assertions"])
        == set(privacy_evidence.CATEGORY_ASSERTIONS[item["check_category"]])
        and all(item["assertions"].values())
        for item in value["categories"]
    )
    text = raw.decode("utf-8")
    assert "device_tracker" not in text
    assert "user-" not in text
    assert "Marcelo" not in text


def test_observer_rejects_stale_or_post_freeze_edge_receipts() -> None:
    with pytest.raises(observer.PrivacyCutoverObserverError, match="stale"):
        _compile(raw_edge_receipt=_edge(refreshed_at=NOW - timedelta(minutes=6)))
    with pytest.raises(observer.PrivacyCutoverObserverError, match="stale"):
        _compile(raw_edge_receipt=_edge(refreshed_at=NOW + timedelta(minutes=1)))


def test_observer_rejects_non_record_only_or_unaccepted_source() -> None:
    with pytest.raises(observer.PrivacyCutoverObserverError, match="record-only"):
        _compile(environment_text=_environment().replace("record_only", "shadow"))
    report = _source_report()
    report["blockers"] = ["not_ready"]
    with pytest.raises(observer.PrivacyCutoverObserverError, match="source pack"):
        _compile(source_report=report)


def test_observer_rejects_writer_or_edge_tampering() -> None:
    writer, receipt = _writer()
    tampered = payload.parse_canonical_json(writer)
    tampered["enforced_writer_freeze"]["write_probe_result"] = "passed"
    with pytest.raises(observer.PrivacyCutoverObserverError, match="not frozen"):
        _compile(raw_writer_evidence=payload.canonical_bytes(tampered))

    edge = payload.parse_canonical_json(_edge())
    edge["purge_result"] = "unknown"
    with pytest.raises(observer.PrivacyCutoverObserverError, match="purge"):
        _compile(raw_edge_receipt=payload.canonical_bytes(edge))

    tampered_receipt = payload.parse_canonical_json(receipt)
    tampered_receipt["evidence_packet_sha256"] = "0" * 64
    with pytest.raises(observer.PrivacyCutoverObserverError, match="lineage"):
        _compile(raw_writer_receipt=payload.canonical_bytes(tampered_receipt))

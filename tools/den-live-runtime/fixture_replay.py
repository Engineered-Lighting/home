"""Causal integrity/replay of hardware execution originals.

Hardware behavior is recomputed only by an injected canonical consumer. Without
that consumer this module can verify retained causal material, never claim a
firmware or physical replay. No alternate device state machine lives here.
"""
from __future__ import annotations

import copy

from fixture_reports import (FixtureRuntimeRefusal, canonical_bytes, decimal_u64, digest, parse_original,
                             require, report_view, sha256, validate_scope)


def replay_archive(archive, *, encode_plan=None, encode_context=None, grant_canonical_bytes=canonical_bytes,
                   replay_consumer=None, max_records=50000):
    """Verify exact originals and optionally recompute each attempted operation.

    Consumer receives (original_operation, ordered_original_reports, recorded
    outcome), returns an exactly matching causal outcome dictionary. It must be
    a caller-pinned instance of the actual producer's codec/firmware runner.
    Missing originals, unresolved effects, extra outcomes and changed outputs
    return incomplete/refused rather than manufacturing acceptance evidence.
    """
    try:
        require(type(archive) is dict and set(archive) == {"schema", "version", "scope", "quiesced", "records"},
                "archive_fields_invalid")
        require(archive["schema"] == "home-fixture-runtime-archive-1" and archive["version"] == 1,
                "archive_family_mismatch")
        validate_scope(archive["scope"])
        require(type(archive["quiesced"]) is bool and type(archive["records"]) is list
                and len(archive["records"]) <= max_records, "archive_bound")
        require(callable(encode_plan) and callable(encode_context) and callable(grant_canonical_bytes),
                "replay_canonical_codec_required")
        previous = "0" * 64
        operations, reports = {}, []
        for sequence, record in enumerate(archive["records"], 1):
            require(type(record) is dict and set(record) == {"sequence", "kind", "entity_id", "available_home_us",
                                                            "payload", "previous_sha256", "record_sha256"},
                    "archive_record_fields_invalid")
            require(type(record["sequence"]) is int and record["sequence"] == sequence
                    and record["previous_sha256"] == previous, "archive_order_incomplete")
            require(len(canonical_bytes(record)) <= 132096, "archive_record_byte_bound")
            if record["available_home_us"] is None:
                require(record["kind"] == "outcome" and record["payload"].get("state") == "unknown"
                        and record["payload"].get("outcome", {}).get("original_receipt_time_unavailable") is True,
                        "archive_missing_receipt_clock")
            else:
                decimal_u64(record["available_home_us"])
            body = {key: value for key, value in record.items() if key != "record_sha256"}
            require(sha256(canonical_bytes(body)) == digest(record["record_sha256"]), "archive_record_digest_mismatch")
            previous = record["record_sha256"]
            kind, entity, payload = record["kind"], record["entity_id"], record["payload"]
            if kind == "prepared":
                require(entity not in operations and type(payload) is dict
                        and set(payload) == {"operation", "original"}, "archive_duplicate_prepared")
                original = payload["original"]
                require(record["available_home_us"] == original["original_available_home_us"], "archive_original_receipt_changed")
                if payload["operation"] == "submit":
                    wire, context = bytes.fromhex(original["wire_hex"]), bytes.fromhex(original["context_hex"])
                    require(encode_plan(copy.deepcopy(original["plan"])) == wire
                            and sha256(wire) == original["wire_sha256"], "archive_original_wire_mismatch")
                    require(encode_context() == context and sha256(context) == archive["scope"]["context_digest"],
                            "archive_original_context_mismatch")
                    require(sha256(grant_canonical_bytes(original["grant_body"])) == original["grant_body_sha256"]
                            == original["plan"]["grant_body_sha256"], "archive_original_grant_mismatch")
                    require(original["reservation"]["request_id"] == entity, "archive_original_reservation_missing")
                    for name in ("grant_id", "revision_id"):
                        require(original["reservation"][name] == original["plan"][name] == original["grant_body"][name],
                                "archive_original_reservation_mismatch")
                else:
                    require(payload["operation"] == "withdraw" and canonical_bytes(original["scope"])
                            == canonical_bytes(archive["scope"]), "archive_withdrawal_scope_mismatch")
                identity = copy.deepcopy(original)
                del identity["original_available_home_us"]
                identity_sha = sha256(canonical_bytes({"operation": payload["operation"], "body": identity}))
                operations[entity] = {"original": payload, "identity_sha": identity_sha,
                                      "attempt": None, "outcome": None, "reports": []}
            elif kind == "attempt":
                require(entity in operations and operations[entity]["attempt"] is None
                        and operations[entity]["outcome"] is None, "archive_attempt_without_original")
                require(payload == {"operation": operations[entity]["original"]["operation"],
                                     "original_identity_sha256": operations[entity]["identity_sha"]}, "archive_attempt_identity_mismatch")
                operations[entity]["attempt"] = record
            elif kind == "outcome":
                require(entity in operations and operations[entity]["outcome"] is None, "archive_outcome_without_original")
                require(type(payload) is dict and set(payload) == {"state", "outcome"}, "archive_outcome_fields_invalid")
                require(payload["state"] in {"terminal", "unknown", "refused", "abandoned"}, "archive_outcome_state_invalid")
                if payload["state"] in {"terminal", "unknown"}:
                    require(operations[entity]["attempt"] is not None, "archive_outcome_without_attempt")
                else:
                    require(operations[entity]["attempt"] is None and payload["outcome"].get("effect") == "not_attempted",
                            "archive_false_no_effect_claim")
                operations[entity]["outcome"] = payload
            elif kind == "report":
                require(payload["report_id"] == entity and canonical_bytes(payload["scope"])
                        == canonical_bytes(archive["scope"]), "archive_report_scope_mismatch")
                require(not any(item["report_id"] == entity for item in reports), "archive_duplicate_report")
                require(record["available_home_us"] == payload["first_received_home_us"], "archive_report_receipt_changed")
                report_view(payload)
                reports.append(payload)
                # Reports retain journal order and do not become a command ACK.
            elif kind == "reconciliation":
                require(entity in operations and operations[entity]["outcome"] is not None
                        and operations[entity]["outcome"]["state"] == "unknown"
                        and set(payload) == {"original_receipt"}, "archive_invalid_reconciliation")
                # Its authenticity must be re-established by the pinned replay
                # consumer, rather than inferred from historical JSON alone.
                require(replay_consumer is not None, "archive_reconciliation_verifier_required")
                operations[entity]["outcome"] = {"state": "terminal", "outcome": payload["original_receipt"]}
            else:
                raise FixtureRuntimeRefusal("archive_record_kind_unsupported")
        unresolved = [identity for identity, operation in operations.items()
                      if operation["outcome"] is None or operation["outcome"]["state"] == "unknown"]
        if unresolved or not archive["quiesced"]:
            return {"status": "incomplete", "reason": "archive_unconfirmed_effects_or_not_drained",
                    "unconfirmed": unresolved, "acceptance_eligible": False}
        attempted = 0
        for operation in operations.values():
            if operation["attempt"] is None:
                continue
            attempted += 1
            if replay_consumer is not None:
                computed = replay_consumer(copy.deepcopy(operation["original"]), copy.deepcopy(reports),
                                           copy.deepcopy(operation["outcome"]["outcome"]))
                require(canonical_bytes(computed) == canonical_bytes(operation["outcome"]["outcome"]),
                        "canonical_consumer_output_mismatch")
        return {"status": "reproduced" if replay_consumer is not None else "causal_archive_verified",
                "operations": len(operations), "attempts": attempted, "reports": len(reports),
                "firmware_recomputed": replay_consumer is not None, "physical_verified": False,
                "acceptance_eligible": False, "terminal_record_sha256": previous}
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return {"status": "incomplete", "reason": getattr(exc, "reason", "archive_original_material_invalid"),
                "acceptance_eligible": False}

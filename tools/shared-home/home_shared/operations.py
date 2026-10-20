"""Offline preservation and acceptance evidence. Never authorizes a deployment."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from typing import Iterable


def source_manifest(root: Path, relative_paths: Iterable[str]) -> dict:
    """Hash an explicit source allowlist. Do not discover/read credential files.

    This receipt covers ONLY supplied files, not a complete runtime or backup.
    A live DB hash would not establish a consistent database recovery set.
    """
    root = root.resolve(strict=True)
    files = []
    for relative in relative_paths:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("source path must remain under root")
        if any(part.startswith(".") for part in path.parts) or path.suffix.lower() not in {
            ".py", ".js", ".jsx", ".mjs", ".cjs", ".md", ".html", ".toml", ".txt"
        }:
            raise ValueError("path is outside explicit source-file classes")
        absolute = (root / path).resolve(strict=True)
        if not absolute.is_relative_to(root) or not absolute.is_file():
            raise ValueError("source path escapes root or is not a file")
        # Stream hashes: large content does not need to enter memory or logs.
        checksum = hashlib.sha256()
        with absolute.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                checksum.update(block)
        files.append({"path": path.as_posix(), "bytes": absolute.stat().st_size, "sha256": checksum.hexdigest()})
    return {"version": 1, "kind": "explicit-local-source-inventory", "files": files,
            "complete_deployed_baseline": False, "deployment_authorized": False}


PRESERVATION_REQUIREMENTS = (
    "deployed_dirty_and_untracked_source", "source_reconciliation_review", "immutable_images_available",
    "model_tokenizer_revisions_and_files", "dependency_locks", "resolved_nonsecret_configuration",
    "secret_certificate_version_references", "consistent_apartment_sqlite_wal_backup",
    "consistent_intelligence_sqlite_wal_backup", "linked_media_manifest", "governed_postgres_backup",
    "current_erasure_ledger", "current_tombstones", "schema_compatibility", "approved_restore_rehearsal",
)


def preservation_gaps(receipt: dict) -> list[str]:
    """Advisory completeness, never a self-asserted grant to run on LA."""
    evidence = receipt.get("evidence", {})
    return [key for key in PRESERVATION_REQUIREMENTS
            if not isinstance(evidence.get(key), dict)
            or evidence[key].get("status") != "verified"
            or not evidence[key].get("artifact_sha256")
            or len(evidence[key]["artifact_sha256"]) != 64
            or not evidence[key].get("reviewed_by")]


def restoration_readable(*, backup_deletion_generation: int, authoritative_deletion_generation: int,
                         applied_deletion_generation: int, source_policy_revalidated: bool,
                         recovery_set_verified: bool) -> bool:
    generations = (backup_deletion_generation, authoritative_deletion_generation, applied_deletion_generation)
    return (all(type(value) is int and value >= 0 for value in generations)
            and applied_deletion_generation == authoritative_deletion_generation
            and backup_deletion_generation <= authoritative_deletion_generation
            and source_policy_revalidated is True and recovery_set_verified is True)


@dataclass(frozen=True)
class Opportunity:
    request_id: str
    kind: str
    scheduled_at: float
    detection_at: float
    completed_at: float | None
    observed_at: float | None
    status: str
    overlaps_interactive: bool = False


def _numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def evaluate_pilot(opportunities: list[Opportunity], *, stationary_minutes: list[float],
                   reviewed_samples: int, materially_accurate_samples: int,
                   wrong_site_disclosures: int, fabricated_identity_assertions: int) -> dict:
    """Positive acceptance: rejected/timeouts/expired attempts stay in denominator."""
    if len({item.request_id for item in opportunities}) != len(opportunities):
        raise ValueError("duplicate scheduled opportunity")
    counters = (reviewed_samples, materially_accurate_samples, wrong_site_disclosures, fabricated_identity_assertions)
    if (any(type(value) is not int or value < 0 for value in counters)
            or materially_accurate_samples > reviewed_samples):
        raise ValueError("invalid sample counts")
    valid = 0
    valid_overlaps = 0
    failures = Counter()
    for item in opportunities:
        if item.kind not in ("arrival", "refresh") or not all(_numeric(x) for x in (item.scheduled_at, item.detection_at)):
            raise ValueError("invalid opportunity")
        success = (item.status == "ok" and _numeric(item.completed_at) and _numeric(item.observed_at)
                   and 0 <= item.completed_at - item.scheduled_at <= 15
                   and item.observed_at >= item.scheduled_at - 3
                   and item.observed_at <= item.completed_at < item.observed_at + 180
                   and (item.kind != "arrival" or 0 <= item.completed_at - item.detection_at <= 20))
        valid += int(success)
        valid_overlaps += int(success and item.overlaps_interactive)
        if not success:
            failures[item.status if item.status != "ok" else "timing_or_freshness"] += 1
    rate = valid / len(opportunities) if opportunities else 0
    checks = {
        "100_opportunities": len(opportunities) >= 100,
        "95_percent_fresh_within_15_seconds": rate >= .95,
        "20_arrivals": sum(x.kind == "arrival" for x in opportunities) >= 20,
        "3_stationary_sessions_20_minutes": sum(_numeric(x) and x >= 20 for x in stationary_minutes) >= 3,
        "20_valid_interactive_overlaps": valid_overlaps >= 20,
        "30_reviewed_samples": reviewed_samples >= 30,
        "90_percent_material_accuracy": reviewed_samples > 0 and materially_accurate_samples / reviewed_samples >= .9,
        "zero_wrong_site_disclosures": wrong_site_disclosures == 0,
        "zero_fabricated_identity_assertions": fabricated_identity_assertions == 0,
    }
    return {"passed": all(checks.values()), "checks": checks, "scheduled": len(opportunities),
            "valid": valid, "success_rate": rate, "failures": dict(failures),
            "valid_interactive_overlaps": valid_overlaps,
            "deployment_authorized": False, "la_regression_and_observation_windows_required": True}


def percentile95(samples: list[float]) -> float:
    if not samples or any(not _numeric(x) or x < 0 for x in samples):
        raise ValueError("nonnegative finite samples required")
    return sorted(samples)[math.ceil(.95 * len(samples)) - 1]


def compare_interactive(baseline: list[float], pilot: list[float], *, baseline_errors: int,
                        pilot_errors: int, baseline_attempts: int, pilot_attempts: int) -> dict:
    for errors, attempts in ((baseline_errors, baseline_attempts), (pilot_errors, pilot_attempts)):
        if type(errors) is not int or type(attempts) is not int or not 0 <= errors <= attempts or attempts <= 0:
            raise ValueError("invalid error denominator")
    before, after = percentile95(baseline), percentile95(pilot)
    if len(baseline) > baseline_attempts or len(pilot) > pilot_attempts:
        raise ValueError("latency samples exceed attempt counts")
    # Latencies are seconds, matched workload class/window evidence remains external.
    return {"baseline_p95_seconds": before, "pilot_p95_seconds": after,
            "latency_passed": after - before <= max(.1 * before, .250),
            "error_rate_passed": pilot_errors / pilot_attempts - baseline_errors / baseline_attempts <= .01,
            "baseline_samples": len(baseline), "pilot_samples": len(pilot),
            "acceptance_status": "provisional_without_reviewed_workload_and_window_evidence",
            "deployment_authorized": False}

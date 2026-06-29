from __future__ import annotations

import json
import sqlite3
from typing import Any

from .experiments import (
    build_activation_manifest,
    build_activation_preflight,
    evaluate_experiment_design,
    generate_experiment_designs,
    get_experiment_detail,
)
from .ingest import canonical_json, stable_hash, utc_now
from .proposals import build_proposal_body
from .readiness import evaluate_candidate_readiness


COUNT_TABLES = (
    "raw_events",
    "preference_observations",
    "preference_candidates",
    "lighting_decisions",
    "journals",
    "wiki_claims",
    "jobs",
    "proposals",
    "experiments",
    "decisions",
)


def _decode_json(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _candidate(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["context"] = _decode_json(d.pop("context_json", None), {})
    d["suggested_target"] = _decode_json(d.pop("suggested_target_json", None), {})
    d["blockers"] = _decode_json(d.pop("blockers_json", None), [])
    d["quality_warnings"] = _decode_json(d.pop("quality_warnings_json", None), [])
    d["evidence_ids"] = _decode_json(d.pop("evidence_ids_json", None), [])
    return d


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in COUNT_TABLES:
        out[table] = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
    return out


def _scratch_copy(conn: sqlite3.Connection) -> sqlite3.Connection:
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    conn.backup(scratch)
    scratch.execute("PRAGMA foreign_keys=ON")
    return scratch


def _remediation_step(
    *,
    phase: str,
    code: str,
    title: str,
    why_it_matters: str,
    current: Any,
    target: Any,
    next_action: str,
    owner: str,
    source: str,
    blocking: bool = True,
) -> dict[str, Any]:
    return {
        "phase": phase,
        "code": code,
        "title": title,
        "why_it_matters": why_it_matters,
        "current": current,
        "target": target,
        "next_action": next_action,
        "owner": owner,
        "source": source,
        "blocking": blocking,
    }


def _add_unique_step(
    steps: list[dict[str, Any]],
    seen: set[str],
    step: dict[str, Any],
) -> None:
    code = step["code"]
    if code in seen:
        return
    seen.add(code)
    steps.append(step)


def _candidate_blocker_step(blocker: str, candidate: dict[str, Any]) -> dict[str, Any]:
    normalized = str(blocker or "").strip().lower()
    if "age across days" in normalized:
        return _remediation_step(
            phase="evidence_readiness",
            code="candidate:insufficient_age_across_days",
            title="Let the pattern survive another day",
            why_it_matters="Durable preferences need repeated evidence across days, not one test session.",
            current={
                "days_seen": candidate.get("days_seen"),
                "blocker": blocker,
            },
            target="days_seen >= 2 with matching non-shadow evidence",
            next_action=(
                "Keep ingesting natural override_event and pending_preference records; "
                "do not ask the user to label this manually."
            ),
            owner="system",
            source="candidate.blockers",
        )
    if "duplicate" in normalized:
        return _remediation_step(
            phase="evidence_quality",
            code="candidate:duplicate_heavy_pending_captures",
            title="Wait for cleaner post-dedupe captures",
            why_it_matters=(
                "A cluster dominated by repeated captures can overstate how strong the "
                "preference signal is."
            ),
            current={
                "duplicate_pending_ratio": candidate.get("duplicate_pending_ratio"),
                "capture_suppressed_total": candidate.get("capture_suppressed_total"),
                "blocker": blocker,
            },
            target="duplicate_pending_ratio <= readiness threshold after M4 dedupe settles",
            next_action=(
                "Continue observing with M4 suppression active and inspect occupancy flicker "
                "before trusting this cluster."
            ),
            owner="system",
            source="candidate.blockers",
        )
    if "variance" in normalized:
        return _remediation_step(
            phase="evidence_quality",
            code="candidate:high_variance",
            title="Do not collapse mixed behavior into one rule",
            why_it_matters="High variance usually means the context is too broad or the target is activity-dependent.",
            current={
                "delta_variance": candidate.get("delta_variance"),
                "blocker": blocker,
            },
            target="low variance within a narrower zone/context cluster",
            next_action="Split by more context before proposing a durable lighting target.",
            owner="future_aggregator",
            source="candidate.blockers",
        )
    if "opposite" in normalized or "conflict" in normalized:
        return _remediation_step(
            phase="evidence_quality",
            code="candidate:conflicting_overrides",
            title="Resolve opposite-direction corrections first",
            why_it_matters="A rule that helps some touches but causes opposite corrections is not an improvement.",
            current={
                "opposite_direction_rate": candidate.get("opposite_direction_rate"),
                "blocker": blocker,
            },
            target="opposite-direction corrections stay below readiness threshold",
            next_action="Collect more evidence and keep this out of proposal generation until conflicts separate cleanly.",
            owner="system",
            source="candidate.blockers",
        )
    return _remediation_step(
        phase="evidence_readiness",
        code="candidate:" + stable_hash(blocker)[:12],
        title="Clear candidate aggregation blocker",
        why_it_matters="Aggregation blockers prevent this pattern from becoming a safe proposal.",
        current=blocker,
        target="candidate has no aggregation blockers",
        next_action="Keep the candidate in observation mode and rerun readiness after new evidence arrives.",
        owner="system",
        source="candidate.blockers",
    )


def _readiness_blocker_step(blocker: dict[str, Any]) -> dict[str, Any]:
    code = blocker.get("code") or "unknown_readiness_blocker"
    defaults = {
        "candidate_not_blocked": (
            "Wait until aggregation blockers clear",
            "Candidate-level blockers are still active.",
            "candidate status is candidate and blockers=[]",
            "Do not promote this candidate until the aggregator marks it candidate-ready.",
            "system",
        ),
        "enough_primary_evidence": (
            "Collect more primary evidence",
            "A proposal needs enough matching-context observations to avoid overfitting one moment.",
            "evidence_count >= readiness threshold",
            "Keep ingesting natural override_event and pending_preference records.",
            "system",
        ),
        "durable_pending_preference": (
            "Wait for durable pending preference evidence",
            "Override sessions explain intent, but proposals need capture-on-vacant pending_preference evidence before changing policy.",
            "evidence_tier=pending_preference and pending_evidence_count >= readiness threshold",
            "Keep observing until pending_preference captures confirm this session pattern.",
            "system",
        ),
        "enough_days_seen": (
            "Wait for multi-day confirmation",
            "Lighting preferences should survive normal day-to-day variation.",
            "days_seen >= readiness threshold",
            "Let the pattern accumulate across another day before proposing an experiment.",
            "system",
        ),
        "linked_manual_override": (
            "Require at least one manual touch",
            "Manual override is the label; final captures alone are weaker evidence.",
            "linked_override_count >= readiness threshold",
            "Wait for or ingest an override_event linked to this context.",
            "system",
        ),
        "effect_size": (
            "Require a meaningful correction",
            "Tiny brightness differences are likely noise and should not become policy.",
            "absolute median delta meets readiness threshold",
            "Keep observing until the correction size is clearly meaningful.",
            "system",
        ),
        "low_variance": (
            "Separate inconsistent behavior",
            "A high-variance cluster can hide multiple preferences.",
            "delta variance below readiness threshold",
            "Split or wait for cleaner evidence before proposing a single target.",
            "future_aggregator",
        ),
        "low_opposite_direction_rate": (
            "Reduce opposite-direction risk",
            "An experiment should not create a new pattern of corrections in the other direction.",
            "opposite-direction rate below readiness threshold",
            "Hold the candidate until conflict evidence is resolved.",
            "system",
        ),
        "low_shadow_ratio": (
            "Prefer real behavior over shadow evidence",
            "Shadow-mode evidence is useful but should not dominate activation decisions.",
            "shadow ratio below readiness threshold",
            "Wait for more non-shadow observations.",
            "system",
        ),
        "low_duplicate_density": (
            "Avoid duplicate-dominated evidence",
            "Repeated captures from one context window can inflate confidence.",
            "duplicate density below readiness threshold",
            "Let M4 dedupe run and wait for cleaner natural captures.",
            "system",
        ),
        "no_blocking_quality_warnings": (
            "Clear quality warnings",
            "Blocking quality warnings mean evidence may be stale, synthetic, flickery, or incomplete.",
            "no blocking quality warnings",
            "Inspect the warning codes and wait for cleaner observations.",
            "system",
        ),
        "evaluation_ready_for_activation_review": (
            "Pass experiment evaluation first",
            "Activation preflight cannot proceed while the read-only evaluation still has hard blockers.",
            "evaluation readiness is ready_for_activation_review",
            "Clear evaluation blockers before building any activation package.",
            "system",
        ),
    }
    title, why, target, action, owner = defaults.get(
        code,
        (
            "Clear readiness blocker",
            blocker.get("message") or "A readiness gate is still failing.",
            blocker.get("expected"),
            "Rerun the sandbox explanation after the underlying gate passes.",
            "system",
        ),
    )
    return _remediation_step(
        phase="evidence_readiness",
        code=f"readiness:{code}",
        title=title,
        why_it_matters=why,
        current={
            "actual": blocker.get("actual"),
            "message": blocker.get("message"),
        },
        target=target,
        next_action=action,
        owner=owner,
        source="readiness.hard_blockers",
    )


def _artifact_step(artifact: dict[str, Any]) -> dict[str, Any]:
    name = artifact.get("artifact") or "unknown_artifact"
    defaults = {
        "human_trial_activation_approval": (
            "Require explicit trial activation approval",
            "Approval to design an experiment is not approval to run it.",
            "a future decision record explicitly scoped to trial activation",
            "Keep this missing until a later activation milestone defines the exact user approval flow.",
            "user",
        ),
        "activation_endpoint": (
            "Build an activation API only after the package is complete",
            "A start endpoint must not exist before rollback, monitoring, and approval gates exist.",
            "future activation endpoint with hard preflight enforcement",
            "Do not add an apply/start endpoint in this milestone.",
            "future_codex",
        ),
        "implementation_manifest": (
            "Materialize the exact implementation package",
            "Activation must enumerate exact helper/shadow writes before anything can run.",
            "reviewed manifest with concrete writes, still gated by preflight",
            "Use the read-only M16 manifest as the review artifact; do not apply it.",
            "future_codex",
        ),
        "rollback_checkpoint": (
            "Capture rollback state before any trial",
            "A trial must be reversible before it is allowed to start.",
            "checkpoint with helper state, shadow config hash, and prior policy revision",
            "Design checkpoint capture before any future activation endpoint.",
            "future_codex",
        ),
        "monitor_job": (
            "Install trial monitoring before activation",
            "The system needs automatic failure detection during the trial window.",
            "hourly monitor job with stop conditions and rollback recommendation",
            "Build monitor specs before any real trial can start.",
            "future_codex",
        ),
    }
    title, why, target, action, owner = defaults.get(
        name,
        (
            "Provide missing activation artifact",
            artifact.get("description") or "A required activation artifact is missing.",
            "artifact present and reviewed",
            "Keep activation blocked until this artifact exists.",
            "future_codex",
        ),
    )
    return _remediation_step(
        phase="activation_package",
        code=f"artifact:{name}",
        title=title,
        why_it_matters=why,
        current={
            "status": artifact.get("status"),
            "description": artifact.get("description"),
        },
        target=target,
        next_action=action,
        owner=owner,
        source="preflight.required_artifacts",
    )


def _manifest_steps(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not manifest:
        return []
    steps: list[dict[str, Any]] = []
    if manifest.get("can_apply") is not True:
        steps.append(
            _remediation_step(
                phase="apply_path",
                code="manifest:apply_disabled",
                title="Keep the manifest read-only",
                why_it_matters="The manifest is a review object, not an executable change.",
                current={
                    "can_apply": manifest.get("can_apply"),
                    "status": manifest.get("status"),
                },
                target="can_apply remains false until a future activation milestone passes all gates",
                next_action="Use the write set for review only; do not create an apply path here.",
                owner="system",
                source="manifest.can_apply",
            )
        )
    if manifest.get("apply_endpoint_present") is not True:
        steps.append(
            _remediation_step(
                phase="apply_path",
                code="manifest:apply_endpoint_absent",
                title="No apply endpoint exists",
                why_it_matters="This guarantees a sandbox or draft manifest cannot mutate Home Assistant.",
                current={"apply_endpoint_present": manifest.get("apply_endpoint_present")},
                target="future endpoint exists only after approval, rollback, and monitor gates are implemented",
                next_action="Leave the endpoint absent until the activation milestone is explicitly requested.",
                owner="future_codex",
                source="manifest.apply_endpoint_present",
            )
        )
    write_set = manifest.get("write_set") or []
    if write_set:
        steps.append(
            _remediation_step(
                phase="activation_package",
                code="manifest:helper_entities_unverified",
                title="Verify helper entities before any future apply",
                why_it_matters="Every hypothetical HA service call requires an existing helper entity.",
                current={
                    "write_count": len(write_set),
                    "requires_existing_entity": all(item.get("requires_existing_entity") for item in write_set),
                },
                target="all helper entities exist and rollback captures their prior state",
                next_action="Use the manifest write_set as a checklist in a later activation preflight.",
                owner="future_codex",
                source="manifest.write_set",
                blocking=False,
            )
        )
    return steps


def _select_candidate(
    conn: sqlite3.Connection,
    *,
    candidate_id: str | None = None,
) -> sqlite3.Row | None:
    if candidate_id:
        return conn.execute(
            "SELECT * FROM preference_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
    return conn.execute(
        """
        SELECT *
        FROM preference_candidates
        ORDER BY
          CASE status WHEN 'candidate' THEN 0 ELSE 1 END,
          evidence_count DESC,
          linked_override_count DESC,
          days_seen DESC,
          updated_at DESC
        LIMIT 1
        """
    ).fetchone()


def run_synthetic_experiment_chain(
    conn: sqlite3.Connection,
    *,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Run design -> evaluation -> preflight -> manifest in a scratch DB only."""

    before = _counts(conn)
    scratch = _scratch_copy(conn)
    try:
        candidate_row = _select_candidate(scratch, candidate_id=candidate_id)
        if candidate_row is None:
            after = _counts(conn)
            return {
                "ok": True,
                "sandbox": True,
                "status": "no_candidates",
                "persisted_to_production": False,
                "production_unchanged": before == after,
                "production_counts_before": before,
                "production_counts_after": after,
                "chain": None,
            }

        candidate = _candidate(candidate_row)
        readiness = evaluate_candidate_readiness(candidate)
        proposal_body = build_proposal_body(candidate, readiness)
        proposal_body["sandbox"] = {
            "synthetic": True,
            "forced_approval": True,
            "persisted_to_production": False,
            "reason": (
                "M17 rehearses the experiment chain in an in-memory copy of the "
                "database; readiness failures are preserved in the returned body."
            ),
        }
        proposal_body["approval_scope"] = {
            "experiment_design_only": True,
            "synthetic": True,
            "activation_allowed": False,
        }
        proposal_id = f"sandbox-proposal:{stable_hash(candidate['id'] + canonical_json(candidate.get('context') or {}))[:24]}"
        now = utc_now()
        scratch.execute(
            """
            INSERT INTO proposals
                (id, candidate_id, status, title, body_json, readiness_json,
                 evidence_ids_json, created_at, updated_at)
            VALUES (?, ?, 'approved_for_experiment', ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal_id,
                candidate["id"],
                proposal_body["title"],
                canonical_json(proposal_body),
                canonical_json(readiness),
                canonical_json(candidate.get("evidence_ids") or []),
                now,
                now,
            ),
        )
        scratch.commit()

        design_run = generate_experiment_designs(scratch)
        experiment_row = scratch.execute(
            "SELECT * FROM experiments WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if experiment_row is None:
            raise RuntimeError("synthetic experiment design was not generated")
        experiment_id = experiment_row["id"]
        experiment_detail = get_experiment_detail(scratch, experiment_id)
        evaluation = evaluate_experiment_design(scratch, experiment_id)
        preflight = build_activation_preflight(scratch, experiment_id)
        manifest = build_activation_manifest(scratch, experiment_id)
        scratch_counts = _counts(scratch)
        after = _counts(conn)
        return {
            "ok": True,
            "sandbox": True,
            "status": "completed",
            "persisted_to_production": False,
            "production_unchanged": before == after,
            "production_counts_before": before,
            "production_counts_after": after,
            "scratch_counts": scratch_counts,
            "chain": {
                "candidate_id": candidate["id"],
                "candidate_status": candidate.get("status"),
                "candidate_ready": bool(readiness.get("ready")),
                "synthetic_proposal_id": proposal_id,
                "experiment_id": experiment_id,
                "design_created": design_run.get("created"),
                "design_updated": design_run.get("updated"),
                "design_unchanged": design_run.get("unchanged"),
                "review_ready": bool((evaluation or {}).get("readiness", {}).get("ready_for_activation_review")),
                "can_activate": bool((preflight or {}).get("can_activate")),
                "can_apply": bool((manifest or {}).get("can_apply")),
            },
            "candidate": candidate,
            "readiness": readiness,
            "synthetic_proposal": {
                "id": proposal_id,
                "status": "approved_for_experiment",
                "title": proposal_body["title"],
                "body": proposal_body,
                "evidence_ids": candidate.get("evidence_ids") or [],
            },
            "experiment": experiment_detail,
            "evaluation": evaluation,
            "preflight": preflight,
            "manifest": manifest,
        }
    finally:
        scratch.close()


def explain_synthetic_experiment_chain(
    conn: sqlite3.Connection,
    *,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Explain what prevents the synthetic chain from becoming an activation."""

    result = run_synthetic_experiment_chain(conn, candidate_id=candidate_id)
    production_unchanged = bool(result.get("production_unchanged"))
    status = result.get("status")
    candidate = result.get("candidate") or {}
    readiness = result.get("readiness") or {}
    evaluation = result.get("evaluation") or {}
    preflight = result.get("preflight") or {}
    manifest = result.get("manifest") or {}
    chain = result.get("chain")

    steps: list[dict[str, Any]] = []
    seen: set[str] = set()

    if status == "no_candidates":
        _add_unique_step(
            steps,
            seen,
            _remediation_step(
                phase="evidence_readiness",
                code="candidate:no_candidates",
                title="Collect enough preference evidence to form a candidate",
                why_it_matters="The sandbox chain needs a preference candidate before it can rehearse proposals or experiments.",
                current="no preference_candidates rows",
                target="at least one candidate with repeated override/pending-preference evidence",
                next_action="Run ingest after more natural lighting override evidence arrives.",
                owner="system",
                source="sandbox.status",
            ),
        )

    for blocker in candidate.get("blockers") or []:
        _add_unique_step(steps, seen, _candidate_blocker_step(blocker, candidate))

    for blocker in readiness.get("hard_blockers") or []:
        _add_unique_step(steps, seen, _readiness_blocker_step(blocker))

    for blocker in (evaluation.get("readiness") or {}).get("hard_blockers") or []:
        step = _readiness_blocker_step(blocker)
        step["phase"] = "experiment_review"
        step["source"] = "evaluation.readiness.hard_blockers"
        _add_unique_step(steps, seen, step)

    for blocker in preflight.get("hard_blockers") or []:
        step = _readiness_blocker_step(blocker)
        step["phase"] = "activation_package"
        step["source"] = "preflight.hard_blockers"
        _add_unique_step(steps, seen, step)

    for artifact in preflight.get("required_artifacts") or []:
        if str(artifact.get("status") or "").startswith("missing"):
            _add_unique_step(steps, seen, _artifact_step(artifact))

    for step in _manifest_steps(manifest):
        _add_unique_step(steps, seen, step)

    if not production_unchanged:
        _add_unique_step(
            steps,
            seen,
            _remediation_step(
                phase="safety",
                code="safety:production_changed",
                title="Stop and investigate sandbox isolation",
                why_it_matters="A sandbox explainer must never mutate production tables.",
                current={
                    "before": result.get("production_counts_before"),
                    "after": result.get("production_counts_after"),
                },
                target="production_counts_before == production_counts_after",
                next_action="Disable the endpoint until the isolation bug is fixed.",
                owner="codex",
                source="production_counts",
            ),
        )

    chain_ready_now = bool(
        chain
        and chain.get("candidate_ready")
        and chain.get("review_ready")
        and chain.get("can_activate")
        and chain.get("can_apply")
    )
    ready_now = chain_ready_now and production_unchanged
    zone = candidate.get("zone") or "selected zone"
    candidate_status = candidate.get("status") or "unknown"
    if status == "no_candidates":
        headline = "No candidate is available for a synthetic experiment chain."
    elif not chain:
        headline = "The sandbox chain did not produce an experiment chain."
    elif not chain.get("candidate_ready"):
        blockers = candidate.get("blockers") or [
            blocker.get("code")
            for blocker in readiness.get("hard_blockers") or []
        ]
        headline = (
            f"The sandbox can rehearse the chain, but the {zone} candidate is not ready: "
            + "; ".join(str(x) for x in blockers[:3])
        )
    elif not chain.get("review_ready"):
        headline = (
            f"The {zone} candidate is proposal-ready, but the experiment review still has blockers."
        )
    elif not chain.get("can_activate"):
        headline = (
            f"The {zone} experiment is review-ready, but activation is blocked by missing artifacts."
        )
    elif not chain.get("can_apply"):
        headline = (
            f"The {zone} activation package is blocked because the manifest is read-only."
        )
    else:
        headline = (
            f"The {zone} chain is internally ready, but this endpoint remains a read-only rehearsal."
        )

    next_safe_milestone = {
        "code": "keep_read_only_until_blockers_clear",
        "title": "Keep this in read-only review",
        "reason": "M18 explains blockers and does not grant approval, activation, or apply authority.",
    }
    if candidate and not (chain or {}).get("candidate_ready"):
        next_safe_milestone = {
            "code": "evidence_maturity_watch",
            "title": "Watch evidence maturity before proposing activation work",
            "reason": (
                "The candidate needs cleaner, older, or less duplicate-heavy evidence before "
                "the system should spend effort on activation packaging."
            ),
        }
    elif chain and chain.get("review_ready") and not chain.get("can_activate"):
        next_safe_milestone = {
            "code": "activation_package_design",
            "title": "Design the missing activation package, still read-only",
            "reason": (
                "The experiment can be reviewed, but it lacks explicit activation approval, "
                "rollback checkpoint capture, monitor job, and start API."
            ),
        }

    return {
        "ok": True,
        "sandbox": True,
        "status": status,
        "persisted_to_production": bool(result.get("persisted_to_production")),
        "production_unchanged": production_unchanged,
        "ready_now": ready_now,
        "summary": {
            "headline": headline,
            "candidate_status": candidate_status,
            "blocking_step_count": len([step for step in steps if step.get("blocking")]),
            "non_blocking_step_count": len([step for step in steps if not step.get("blocking")]),
        },
        "candidate": {
            "id": candidate.get("id"),
            "zone": candidate.get("zone"),
            "status": candidate.get("status"),
            "evidence_count": candidate.get("evidence_count"),
            "days_seen": candidate.get("days_seen"),
            "linked_override_count": candidate.get("linked_override_count"),
            "duplicate_pending_ratio": candidate.get("duplicate_pending_ratio"),
            "blockers": candidate.get("blockers") or [],
            "quality_warnings": candidate.get("quality_warnings") or [],
        },
        "chain": chain,
        "blocking_phases": {
            "candidate_ready": bool((chain or {}).get("candidate_ready")),
            "experiment_review_ready": bool((chain or {}).get("review_ready")),
            "activation_ready": bool((chain or {}).get("can_activate")),
            "apply_ready": bool((chain or {}).get("can_apply")),
        },
        "remediation_plan": steps,
        "non_actions": [
            {
                "code": "do_not_create_approval",
                "reason": "Synthetic approval is confined to the scratch DB and is not a human decision.",
            },
            {
                "code": "do_not_apply_manifest",
                "reason": "The manifest is hypothetical and can_apply is false.",
            },
            {
                "code": "do_not_edit_yaml",
                "reason": "Preference experiments must start as helper/shadow trials, not YAML rewrites.",
            },
            {
                "code": "do_not_write_ha",
                "reason": "M18 is explain-only and has no HA service-call path.",
            },
        ],
        "next_safe_milestone": next_safe_milestone,
        "safety_proof": {
            "production_unchanged": production_unchanged,
            "persisted_to_production": bool(result.get("persisted_to_production")),
            "production_counts_before": result.get("production_counts_before"),
            "production_counts_after": result.get("production_counts_after"),
            "scratch_counts": result.get("scratch_counts"),
        },
        "source_chain_status": {
            "sandbox_status": status,
            "evaluation_status": (evaluation.get("readiness") or {}).get("status"),
            "preflight_status": preflight.get("status"),
            "manifest_status": manifest.get("status"),
        },
    }

# Home Intelligence Core

> **Quarantined legacy system.** The supported stack deployment is read-only,
> internal-network-only, receives neither a Home Assistant token nor the HA
> configuration tree, and has scheduler/memory/capture disabled. Historical HA
> pull settings below must not be re-enabled or used as an Agent Core context
> source.

Read-only long-horizon intelligence sidecar for the legacy Home app.

This service ingests lighting override evidence and lighting decision logs,
stores them in an append-first SQLite ledger, aggregates repeated manual
corrections into preference candidates, and exposes a small HTTP API for the
Home app.

Guardrails in this first phase:

- no Home Assistant writes
- no automation edits
- no cloud calls
- no active policy changes
- every candidate links back to evidence rows

Default inputs:

- `/config/lighting_preferences_pending.jsonl`
- `/config/lighting_decisions.log`

Useful endpoints:

- `GET /healthz` (host default: `http://<ubuntu-host>:8095/healthz`)
- `POST /api/intelligence/evidence/pull/run`
- `GET /api/intelligence/evidence-sources`
- `GET /api/intelligence/evidence-link-audit?hours=24`
- `POST /api/intelligence/ingest/run`
- `GET /api/intelligence/today`
- `GET /api/intelligence/soak-review?hours=24`
- `GET /api/intelligence/override-sessions?zone=office&hours=24`
- `GET /api/intelligence/scheduler`
- `POST /api/intelligence/memory/run`
- `GET /api/intelligence/journals`
- `GET /api/intelligence/wiki-claims`
- `GET /api/intelligence/wiki-claims/{claim_id}`
- `GET /api/intelligence/candidates`
- `GET /api/intelligence/candidates/{candidate_id}/readiness`
- `GET /api/intelligence/proposal-preview/{candidate_id}`
- `POST /api/intelligence/proposals/run`
- `GET /api/intelligence/proposals`
- `GET /api/intelligence/proposals/{proposal_id}`
- `POST /api/intelligence/proposals/{proposal_id}/decisions`
- `POST /api/intelligence/experiments/design/run`
- `GET /api/intelligence/experiments`
- `GET /api/intelligence/experiments/evaluations`
- `GET /api/intelligence/experiments/preflights`
- `GET /api/intelligence/experiments/manifests`
- `GET /api/intelligence/experiments/{experiment_id}`
- `GET /api/intelligence/experiments/{experiment_id}/evaluation`
- `GET /api/intelligence/experiments/{experiment_id}/preflight`
- `GET /api/intelligence/experiments/{experiment_id}/manifest`
- `POST /api/intelligence/sandbox/experiment-chain/run`
- `POST /api/intelligence/sandbox/experiment-chain/explain`
- `POST /api/intelligence/candidates/{candidate_id}/decisions`
- `POST /api/intelligence/wiki-claims/{claim_id}/decisions`
- `GET /api/intelligence/decisions`
- `GET /api/intelligence/observations?candidate_id=...`
- `POST /api/intelligence/suppressions`
- `GET /api/intelligence/multimodal/status`
- `POST /api/intelligence/multimodal/pilot/run`
- `GET /api/intelligence/observation-packets`
- `GET /api/intelligence/observation-packets/map`
- `POST /api/intelligence/observation-packets/capture`
- `POST /api/intelligence/observation-packets/capture/recent`
- `GET /api/intelligence/observation-packets/{packet_id}`
- `POST /api/intelligence/observation-packets/{packet_id}/label`
- `POST /api/intelligence/qwen-labels/run`
- `POST /api/intelligence/qwen-labels/validate`
- `GET /api/intelligence/qwen-labels/prompt-gate`
- `GET /api/intelligence/qwen-labels/prompt-trials`
- `POST /api/intelligence/qwen-labels/prompt-trials/run`
- `GET /api/intelligence/label-evals/status`
- `GET /api/intelligence/label-evals`
- `GET /api/intelligence/label-evals/queue`
- `POST /api/intelligence/label-evals/seed`
- `GET /api/intelligence/label-evals/{set_id}`
- `GET /api/intelligence/label-evals/{set_id}/score`
- `POST /api/intelligence/label-evals/items/{item_id}/audit`
- `POST /api/intelligence/label-evals/items/{item_id}/accept-qwen`
- `GET /api/intelligence/training-clips/manifests`
- `POST /api/intelligence/training-clips/manifests`
- `GET /api/intelligence/training-clips/manifests/{manifest_id}`

Proposal engine guardrails:

- only candidates passing every readiness gate become `draft` proposals
- blocked candidates are reported but not persisted as active proposals
- generated proposals include evidence ids, rollback, success metric, and critique
- proposal actions write the decision ledger only; activation/experiments are deferred

Decision ledger guardrails:

- approvals mean `approved_for_experiment` design only; they do not start experiments
- rejections, snoozes, edit requests, suppressions, and wiki-claim corrections are append-only decisions
- `do_not_learn` creates a suppression rule and refreshes local aggregation/proposals
- every decision records actor, reason/note, prior state, new state, and a safety block

Experiment design guardrails:

- only `approved_for_experiment` proposals can produce experiment designs
- generated experiments are `inactive_design` records
- M13 has no activation endpoint and no start/stop experiment API
- experiment designs include hypothesis, baseline, trial window, success metrics, guardrails, rollback, and evidence ids
- success is defined as lower manual override rate in the matching context without higher opposite-direction or zone-wide override rates
- designs are idempotent; repeated generation updates only when the approved proposal or design body changes
- non-regeneratable experiment states are skipped, never silently revived
- evaluation endpoints are read-only and compute review readiness, baseline counts, no-start activation state, and the future outcome scoring plan
- M14 still has no activation endpoint, no trial-window persistence, and no policy write path
- preflight endpoints are read-only and enumerate the missing activation package: explicit trial approval, activation API, implementation manifest, rollback checkpoint, and monitor job
- M15 preflight can compute baseline/rollback/monitor specs, but `can_activate` is always false
- manifest endpoints are read-only and generate the exact hypothetical helper/shadow write set plus rollback checkpoint schema for review
- M16 manifests are `draft_read_only`; `can_apply` is always false and there is no apply endpoint
- the M17 sandbox endpoint copies the current SQLite DB into memory, inserts a synthetic approved proposal there, runs design/evaluation/preflight/manifest, and discards the copy
- sandbox responses include production before/after counts and `production_unchanged`; no sandbox records are persisted
- the M18 sandbox explainer uses that same scratch rehearsal to translate candidate, evaluation, preflight, and manifest blockers into a remediation checklist
- M18 is explain-only: it does not create approvals, apply manifests, edit YAML, write HA state, or persist sandbox proposals/experiments
- M19 separates evidence pull from aggregation: `evidence_pull` runs frequently, `ingest_and_aggregate` remains slower, and local `/config` files are labeled `fallback_snapshot`
- M19 prefers authenticated read-only HA API cursor pull via `INTELLIGENCE_HA_BASE_URL` + `INTELLIGENCE_HA_TOKEN`; it never calls HA services or writes HA state
- M20 collapses rapid same-context `override_event` bursts into one final-value session for candidate scoring while preserving every raw event in the ledger. Candidates expose `raw_evidence_count`, `override_session_count`, and `burst_event_count` so slider bursts stay auditable without becoming multiple preference votes.
- M21 exposes those override sessions through a read-only API and candidate drilldown UI so every collapsed burst can be inspected back to its raw touch events.
- M22 separates user intent from automation aftermath inside a session. When a nonzero user-landed value is followed by an automation off tail, candidate scoring uses `learning_value_pct` from the user-landed event while preserving the physical final state for audit.
- M23 makes the evidence hierarchy explicit: `pending_preference` evidence outranks session intent, and session intent outranks raw physical final state. Candidates expose `evidence_tier`, `pending_evidence_count`, `session_intent_count`, and `automation_tail_session_count`.
- M24 requires durable `pending_preference` evidence before proposal readiness. Session-intent candidates can inform candidate views and blockers, but they remain below the proposal line until the capture-on-vacant loop confirms the pattern.
- M25 reconciles pending captures linked to automation-tail sessions during scoring only. If the pending record points at the session's final automation aftermath event, aggregation uses the linked session's `learning_value_pct` and learning policy context while preserving the raw pending record for audit.
- M26 exposes a read-only evidence link audit so pending captures can be inspected against their matched override session. The Home app shows linked, unlinked, automation-tail, and reconciled pending captures without changing HA state or scoring policy.
- M27 adds read-only multimodal observation foundations. Event-linked observation packets can store local camera frames, HA metadata snapshots, Qwen3-VL label results, privacy/retention state, and human audit actions. Qwen labels are visual context features only; manual overrides and pending preferences remain the preference labels.
- M28 starts a controlled multimodal capture pilot: `office` evidence only, `living_room` ring buffer only, 90-second local frame retention, 6 packet/label jobs per hour, and no current-frame fallback for automatic event captures. Pilot captures and labels are for audit/clustering only and cannot affect proposal readiness or lighting policy.
- M29 seeds the verifier for future prompt and SkillOpt changes. Held-out label eval sets snapshot packets, Qwen labels, and capture timing; human audits are stored separately; scoring compares audited labels by axis. Prompt changes are still blocked until enough audited items exist.
- M30 exposes the held-out verifier as an audit queue. Qwen labels can be accepted into human-audited eval items, but the prompt gate remains blocked until at least 50 audited items exist.
- M31 records prompt-trial attempts and blocks prompt optimization behind the verifier gate. Comparisons require strict improvement, no privacy regression, JSON-valid candidate labels, ties rejected, and still have no prompt activation endpoint.
- M32 creates future V-JEPA training clip manifests only. Manifests carry a sampling plan, linked observations, quality blockers, and privacy eligibility; export and training are explicitly disabled.

Scheduler environment:

- `INTELLIGENCE_SCHEDULER_ENABLED=1`
- `INTELLIGENCE_EVIDENCE_PULL_INTERVAL_S=60`
- `INTELLIGENCE_INGEST_INTERVAL_S=3600`
- `INTELLIGENCE_MEMORY_INTERVAL_S=86400`
- `INTELLIGENCE_HA_BASE_URL=http://192.168.0.125:8123`
- `INTELLIGENCE_HA_TOKEN=<redacted>` (or `HA_TOKEN`)
- `INTELLIGENCE_VLLM_URL=http://vllm:8000`
- `INTELLIGENCE_QWEN_MODEL=qwen3-vl-30b`
- `INTELLIGENCE_MULTIMODAL_ENABLED=1`
- `INTELLIGENCE_MULTIMODAL_RING_ENABLED=1`
- `INTELLIGENCE_MULTIMODAL_CAMERAS=living_room`
- `INTELLIGENCE_MULTIMODAL_PILOT_ENABLED=1`
- `INTELLIGENCE_MULTIMODAL_PILOT_ZONES=office`
- `INTELLIGENCE_MULTIMODAL_MAX_CAPTURES_PER_HOUR=6`
- `INTELLIGENCE_MULTIMODAL_MAX_QWEN_JOBS_PER_HOUR=6`

Persistence and deployment:

- mount `/data` from a host-level directory, not from `services/intelligence/data`
- Ubuntu default: `/opt/home-ai-voice/intelligence-data:/data`
- local compose default: `${INTELLIGENCE_DATA_HOST_DIR:-./intelligence-data}:/data`
- backup before service deploys with `scripts/backup-intelligence-db.sh`
- deploy from Windows with `tools/deploy-intelligence.ps1`
- the deploy script excludes `data/` and runs `scripts/smoke-intelligence.py`
- smoke validation checks `/healthz`, scheduler state, and the decision table count

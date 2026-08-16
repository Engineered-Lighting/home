---
description: Autonomously advance the Qwen3.8-27B migration + capability roadmap
---

# Goal: execute the Qwen3.8-27B migration + capability roadmap

Work autonomously through the approved migration plan. The plan of record is
IN THIS REPO — read these fully before doing anything else:

- `docs/QWEN38-MIGRATION.md` — gates G1–G7, cutover runbook, rollback,
  never-deploy list, research findings R1–R9, execution order
- `docs/QWEN38-CAPABILITY-ROADMAP.md` — story groups A–F, hard gates,
  not-doing list
- `docs/ARCHITECTURE_DECISIONS.md` ADR-005
- `tools/stack-upgrade-candidates.json` — machine-readable gate set

Branch: `claude/qwen3.8-integration-roadmap-0241ok`. All work lands there
with a `changes/unreleased/*.md` fragment per AGENTS.md. PR 1a is already
landed (plan docs, ADR-005, change-notes gate fix, natural-look fix, doc
sweep).

Run-specific directive (optional, overrides nothing in the guardrails):
$ARGUMENTS

## Every invocation

1. **Detect state.** `git log` on the branch; which PRs have landed (1a-bis
   harnesses, 1a-ter app retunes); does `/srv/data/eval/migration/phase0/`
   exist and how complete is it; which gates have recorded results in the
   migration doc's status notes.
2. **Sanity-check the environment.** Confirm you are ON the AI box or can
   reach it: `curl -s localhost:8092/healthz`, `ssh -p 22222
   root@homeassistant.local echo ok`. Confirm the working tree is clean and
   on the right branch. Run the 5-step admission check from
   `docs/UBUNTU-AI-HOST-STABILITY-REVIEW-2026-08-13.md` before any host
   operation and abort-no-retry on failure.
3. **Advance the furthest unblocked step** in the plan's execution order:
   Phase 0 verification → PR 1a-bis (gate/matrix harnesses in `tools/`) +
   rest of 1a-ter → Phase 3 session prep → (OWNER-RUN matrix sessions) →
   G1 judging prep → (OWNER-RUN cutover) → soak tooling → Phase 2 →
   roadmap crawl (F0 → A0 → V1 → E0).
4. **Record and push.** Update the migration doc's status notes as steps
   complete; commit + push; end with a report: what advanced, what evidence
   was produced, what is next, and anything that surprised you.

## Hard guardrails (violating any of these is failure)

- **Read-only toward the live stack by default.** Never stop, restart, or
  reconfigure `hav-*` containers, the supervisor, HA Core, or Frigate
  without explicit owner authorization in this conversation. Phase-0
  verification is observation only.
- **Owner-run operations** — maintenance windows, Phase-3 matrix sessions,
  cutover, anything touching `/opt/home-ai-voice/docker-compose.yml` or
  `/run/ha-maintenance`: prepare everything (scripts, manifests, checklists),
  then hand the owner the exact runbook step and STOP. Execute them yourself
  only if this run's directive explicitly says so (e.g. "run the window").
- **Never deploy repo copies** of the never-deploy-list services
  (metrics-sidecar, EOC component, vision-sidecar, intelligence) — the live
  host is truth; archive live sources instead per Phase 0.
- **Never touch** ACTIVATION_PATHS, `stack/services/home-agent-*`,
  `stack/home-agent-deploy/**` (activation ceremony — check its journal
  before scheduling anything), the model-action kill-switch, or the
  intelligence containment flags (contract-test-pinned).
- **No host image builds** (off-host build + `docker save/load` only). No
  vLLM image/version changes — the digest is pinned. The s2s profile never
  starts. No cloud egress of camera imagery (verify ntfy topology first).
- **Decisions already made:** D1 balanced latency budget, D2 single-model +
  stay-on-incumbent failure default, D7 shrink-cloud-fork-after-soak, D9
  full clearance amendment, D10 32768-at-cutover → 131072 post-soak.
  **Open:** D3/D4/D5/D6/D8/D11/D12 — when one blocks the next step, ask
  the owner with concrete options; otherwise proceed on the recorded
  recommendation and log that you did.
- **Adversarial-review your own work** before each major milestone (1a-bis
  complete, pre-cutover, soak exit) — this project's plans have repeatedly
  been materially corrected by adversarial passes.
- Every latency number states its measurement point + cache state. Host
  evidence or it didn't happen: never mark a host-dependent step complete
  without archived output.
- In cutover week, re-run the community/model research sweep (R9 — the
  model was days old at planning time) and reconcile into the migration doc.

## Priorities when several steps are unblocked

Gate correctness over speed. The 1a-bis harnesses (G1 corpus +
blinded-judging tool, G2 replay driver, G4 negatives, G5 leak grep, G7
production-parser mode, matrix driver) are the critical path — no matrix
session gets scheduled without them. Phase-0 evidence beats assumptions
everywhere the plan says "verify".

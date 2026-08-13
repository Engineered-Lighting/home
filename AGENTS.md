# Agent Instructions

This repo is the source of truth for agent behavior. Follow these rules whether
you are Codex, Claude, or another coding assistant.

## Change Notes

Any user-facing, deploy-relevant, release-relevant, or operationally meaningful
change must include a markdown fragment under `changes/unreleased/`.

Use the format documented in `changes/README.md`.

Examples that need a fragment:

- Home desktop app behavior or UI changes
- Home web gateway changes
- Tauri packaging, release, deploy, or workflow changes
- Backend, AI stack, Home Assistant integration, or service contract changes
- Fixes a user would notice

Examples that usually do not need a fragment:

- Typo-only docs edits
- Test-only refactors
- Internal cleanup with no behavior, deploy, or release impact

If a change intentionally has no release note, mark the PR with the
`no-release-note` label and explain why in the PR body.

## Release Policy

- `CHANGELOG.md` is generated from `changes/unreleased/*.md`.
- Do not manually rewrite release sections in `CHANGELOG.md` unless fixing the
  release tooling itself.
- Desktop/Tauri releases are manual and reviewed: prepare a release PR, merge
  it, then manually tag from `main`.
- `app/data/apartment` contains local runtime data and is not bundled into the
  desktop installer in v1.

## Ubuntu AI Host Quarantine

- Never run the Home Agent E1/E2 PostgreSQL Docker gate, image builds, broad
  integration suites, stress tests, or model/GPU workloads on
  `EngineeredLightingServer1` / `home-app`. The 2026-07-12 unclean halt remains
  permanently relevant to high-churn work even after the replacement-platform
  review documented in
  `docs/UBUNTU-AI-HOST-STABILITY-REVIEW-2026-08-13.md`.
- Do not bypass or weaken the runner's host, Linux-execution, Docker-endpoint,
  daemon-identity, or resource-limit checks.
- Deterministic unit/contract tests belong on the workstation and heavy
  database gates belong on the pinned GitHub-hosted workflow.
- Tauri authority changes require the pinned `home-agent-native-boundary`
  Windows workflow; do not treat a workstation without the pinned Rust
  toolchain or an ad hoc release build as native acceptance.
- Reviewed production-maintenance paths may run on the replacement platform:
  the installed resource-bounded local backup, checksum-verified off-host
  copy, one supervised resource-bounded manual restore drill, and ordinary
  service operations required by the Home Agent runbook. Before each such
  operation, confirm `systemctl is-system-running` is `running`, required
  containers are healthy, storage has safe headroom, temperatures are normal,
  and the current boot has no new MCE, hardware-error, lockup, thermal, OOM,
  panic, I/O, or segfault record. Abort on any failed check.

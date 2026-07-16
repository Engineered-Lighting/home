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
  integration suites, or model/GPU workloads on `EngineeredLightingServer1` /
  `home-app` while the 2026-07-12 unclean halt remains unresolved.
- Do not bypass or weaken the runner's host, Linux-execution, Docker-endpoint,
  daemon-identity, or resource-limit checks.
- Use lightweight read-only diagnostics only when the host is needed. Run
  deterministic unit/contract tests on the workstation and heavy database
  gates on the pinned GitHub-hosted workflow.

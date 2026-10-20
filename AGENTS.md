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

## Ubuntu AI Host: Owner-Authorized Tests and Maintenance

- On September 26, 2026, the system owner explicitly approved the tests needed
  to connect the Victoria and LA AI stacks, explicitly overrode the named-host
  GPU quarantine, and requested this policy amendment. This standing approval
  permits bounded model/GPU trials, isolated dependency preparation, necessary
  integration validation, and reviewed service maintenance on
  `EngineeredLightingServer1` / `home-app`, including Home gateway and Lab
  restarts needed to deploy and verify the authorized work. Do not ask again
  merely because these hosts were previously quarantined.
- This section supersedes the former blanket model/GPU prohibition and the
  unconditional requirement for `systemctl is-system-running` to be `running`.
  A `degraded` state attributable only to the inspected, documented observer
  health/recovery failures and retained historical incident evidence does not
  by itself block this authorized work. Recheck the actual failed units and
  current health before each operation; unknown or new failures require diagnosis.
- Preserve the 2026-07-12 unclean halt and subsequent OOM/failure history,
  including the review in
  `docs/UBUNTU-AI-HOST-STABILITY-REVIEW-2026-08-13.md`. Do not clear failures,
  rewrite incident baselines, or restart services merely to obtain a green status.
- Use explicit per-operation resource limits, bounded concurrency and runtime,
  isolated test dependencies, and a rollback/cleanup path. Keep existing relied-on
  services and data intact. A successful bounded trial does not establish safe
  continuous production admission; validate sustained resource use separately.
- Home Agent E1/E2 PostgreSQL Docker gates, image builds, broad integration
  suites and stress tests remain off these production hosts. Use the approved
  workstation or pinned CI platforms for those workloads.
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
  service operations required by the Home Agent runbook. Before each operation,
  inspect system state under the exception above, confirm required containers
  are healthy, storage has safe headroom and temperatures are normal, and check
  for new MCE, hardware-error, lockup, thermal, OOM, panic, I/O or segfault records
  since the last documented inspection. Historical faults remain recorded and
  relevant; they are not automatically new faults on every subsequent check.
  Abort on new faults or unsafe resource conditions. This owner authorization
  does not bypass tool approval review or any enforced execution boundary.

## Always-On Den Activity Lighting (virtual only)

- On October 19, 2026, the system owner approved continuous operation of the den
  activity-lighting MVP (issue #320). This standing approval covers only:
  - the lighting brain on the LA AI host (`EngineeredLightingServer1`, alias
    `hav-ubuntu`);
  - the occupancy-gated activity loop in perception-lab on
    `victoria-den-jetson`;
  - the Victoria HA/Frigate bridge;
  - their reviewed deploys, restarts and rollbacks.
- **Admission is one soak per release.** A release is admitted to continuous
  operation after a single supervised soak of at least 3 hours passes its
  written criteria:
  - no new kernel, OOM, thermal or hardware faults;
  - memory and GPU use stay flat within the stated bounds;
  - temperatures stay below the existing limits;
  - the brain and producers stay healthy with bounded restarts;
  - the MVP behaviour checks hold.
  The soak receipt is recorded with the release. After admission, no per-trial
  "test day" or per-session preflight ceremony is required, and the loop rolls
  its bounded recordings by itself.
- **Continuous operation is bounded by automatic back-off, not by session
  caps.** A background monitor watches temperatures, memory, GPU memory,
  failed units and new fault records. On pressure it backs off in steps:
  1. lower the JEV, Qwen and V-JEPA cadence;
  2. pause the deep stack, leaving ambient only;
  3. stop the activity loop.
  It records why, and alerts the owner through a Home banner and a Home
  Assistant notification. It never clears failures, rewrites incident
  baselines, or restarts unrelated services.
- **Physical output stays disabled.** This approval is virtual-only: decisions
  drive Home's virtual fixtures. Physical fixture output needs a separate owner
  approval, and the qualified/strict timing limits return before it.
- **Change control.**
  - A release that changes resource use or the services' scope needs a new soak.
  - Configuration changes within the documented settings (levels, CCT,
    timings, cadence) do not.
  - Each host operation still follows the section above: recheck health and
    failed units, use explicit resource limits, and keep a rollback path.
  - This approval does not bypass tool approval review or any enforced
    execution boundary.

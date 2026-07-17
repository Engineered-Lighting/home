# Ubuntu AI host incident — 2026-07-12

## Outcome

`EngineeredLightingServer1` suffered an unclean host-level halt shortly after a
high-churn Home Agent PostgreSQL Docker gate. The evidence supports treating
that gate as the probable trigger, but it does not prove whether the final
mechanism was Linux/Docker resource churn, a silent total lockup, CPU/firmware
instability, or PSU/motherboard protection.

A later bounded re-read of the persistent journal found two earlier signals
that the initial evidence capture missed: a kernel machine-check event at
2026-07-12 19:04:27 PDT and a `uvicorn` process segfault in `libcrypto.so.3` at
20:27:28 PDT. Those events do not identify the final reset mechanism, but they
make pre-existing CPU/platform instability materially more likely. The
high-churn gate remains the probable final trigger, not a proven sole cause.

The gate must not run on this AI host. It is now hard-quarantined there and has
resource/churn limits for CI or disposable test hosts.

## Reboot confirmation

- Power-button event: 2026-07-16 17:29:05 -03:00
  (2026-07-16 20:29:05 UTC).
- Kernel boot time: 2026-07-16 20:29:06 UTC.
- Hostname: `EngineeredLightingServer1`.
- Tailscale IPv4: `100.87.94.18`.
- New boot ID: `f403c948-519c-4af1-88ea-432423c750df`.
- SSH and Tailscale passed the initial, +60-second, and +180-second checks with
  the same boot ID and increasing uptime.
- A later read-only smoke check passed at 718 seconds uptime.
- `systemctl is-system-running` initially reported `degraded` because
  `home-agent-bff-egress-verify.service` failed. A later read-only check also
  found `home-agent-db-backup.service` failed. Neither unit was restarted.

## Previous-boot evidence

The prior boot began at 2026-07-11 15:18:40 PDT. Its persistent journal ends
abruptly at 2026-07-12 22:06:18 PDT, without shutdown, reboot, sync, panic, or
orderly service-stop records. `last -x` marks the session as `crash`.

Immediately before the journal stopped, the exact Codex-launched command was:

```text
python3 tools/run-home-agent-e1-postgres-gate.py
```

The corresponding Docker resource prefix was
`home-agent-e1-130fd9603a0c-*`. The command reported success and labeled cleanup
after 58.2 seconds, but the host journal stopped seconds later.

Observed workload characteristics:

- Lifecycle, admission, and E2 PostgreSQL phases ran back-to-back.
- At least 41 disposable test endpoints were created in roughly 56 seconds.
- The journal recorded 86 container shim disconnections.
- 493 of the final 500 kernel records were Docker bridge/veth churn.
- The host remained responsive for only a few seconds after cleanup.

Earlier in the same boot, the persistent kernel journal records:

```text
2026-07-12T19:04:27-07:00 mce: [Hardware Error]: Machine check events logged
2026-07-12T20:27:28-07:00 uvicorn: segfault ... in libcrypto.so.3 ... CPU 8
```

The journal does not retain a decoded MCE bank/status record, so the
machine-check cannot be attributed to a particular core, cache, memory path, or
voltage domain from remote evidence alone. The segfault is likewise evidence of
instability, not proof that `libcrypto` caused the host halt.

No persisted evidence was found for:

- kernel panic, hard/soft lockup, or kernel watchdog failure;
- thermal critical event or throttling;
- OOM kill;
- NVMe, filesystem, or general I/O failure;
- a persisted crash dump for either the segfault or final halt.

`/sys/fs/pstore` and `/var/crash` were empty, and `coredumpctl` was unavailable.
A catastrophic hardware reset or total lockup can leave none of these records,
so hardware/firmware instability remains plausible.

## Safeguards

1. `tools/run-home-agent-e1-postgres-gate.py` rejects
   `EngineeredLightingServer1` and `home-app` before contacting Docker.
2. Every ordinary Linux invocation is refused. Only the pinned GitHub-hosted
   workflow is admitted with an explicit flag and exact runner context;
   self-hosted CI is refused.
3. Docker daemon identity is checked as a second named-host boundary, covering
   a container attached to the physical host's Docker socket. No environment
   variable overrides either named-host quarantine.
4. Removing the AI-host quarantine requires a reviewed code change after a
   separate on-site stability review.
5. Client and PostgreSQL test containers now have CPU, memory, PID,
   file-descriptor, and no-new-privileges limits.
6. A cooldown is enforced between disposable client containers to reduce
   network-namespace and veth churn.
7. Heavy Docker gates must run in the pinned CI job or on a non-Linux
   disposable host.
8. No Docker tests, builds, upgrades, service restarts, BIOS changes, or AI
   workloads were run during recovery.

## Preserved local evidence

The redacted recovery artifacts are stored outside the repository under:

```text
%LOCALAPPDATA%\EngineeredLighting\ubuntu-reboot-watch\
```

The primary files are:

- `20260716T202905Z-evidence.txt`
- `20260716T202905Z-previous-journal-tail.txt`
- `20260716T202905Z-events.ndjson`
- `20260716T202905Z-status.json`
- `20260716T234623-0300-evidence-supplement.txt`

Do not publish the raw artifacts without another redaction pass.

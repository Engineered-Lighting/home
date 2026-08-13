# Ubuntu AI host replacement-platform stability review — 2026-08-13

## Verdict

The Intel Core i9-14900K platform involved in the 2026-07-12 unclean halt is no
longer installed. `EngineeredLightingServer1` now runs an AMD Ryzen 9 9950X on
an X870I AORUS PRO ICE board with BIOS FA4 and microcode `0xb404035`.

The replacement platform is conditionally cleared for reviewed, bounded
production maintenance. This clearance covers the installed local backup,
checksum-verified off-host copy, one supervised manual restore drill, and
ordinary runbook service operations. It does not clear the host for E1/E2
PostgreSQL gates, image builds, broad integration suites, stress tests, or
model/GPU workloads.

## Evidence

- The inspected boot started at 2026-08-11 01:47:15 local time and had remained
  online for more than 46 hours.
- `systemctl is-system-running` reported `running`; there were no failed units.
- Home Agent PostgreSQL, API, ingest, worker, BFF, origin, and Edge ingress
  containers remained online throughout the review; all containers with
  health checks reported healthy.
- The immediately preceding boot ended through a complete orderly systemd
  shutdown, filesystem sync, encrypted-volume unmount, and reboot sequence.
- Neither post-replacement boot contained a machine-check error, hardware
  error, watchdog lockup, thermal trip, OOM kill, kernel panic, I/O error, or
  segfault in the bounded journal review.
- Representative idle temperatures were approximately 38–58 °C, with NVMe
  composite temperatures below 47 °C.
- The root filesystem had approximately 585 GB free at 67% utilization; the
  dedicated Home Agent encrypted volume remained mounted and available.

This evidence does not prove the platform is suitable for synthetic stress or
high-churn disposable Docker work. The permanent high-churn quarantine and
named-host runner refusals remain in force.

## Maintenance admission check

Before each permitted maintenance operation:

1. Require `systemctl is-system-running` to report `running`.
2. Require every production Home Agent container with a health check to report
   healthy and Edge ingress to report running.
3. Confirm safe filesystem headroom and normal current temperatures.
4. Search the current kernel/system journal for new machine-check,
   hardware-error, lockup, thermal, OOM, panic, I/O, or segfault evidence.
5. Abort without retrying if any check fails or the host becomes unstable.

The backup and restore tools retain their own CPU, memory, process, filesystem,
locking, immutable-label, and checksum guards. This review does not authorize
bypassing any of them.

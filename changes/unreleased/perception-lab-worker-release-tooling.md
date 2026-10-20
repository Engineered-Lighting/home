---
title: Reviewed Perception Lab worker release, deploy and delivery-report tools
target: internal
type: added
---

A new builder packages one reviewed worker source tree (the import closure of the Jetson and LA worker apps) for both hosts under a shared runtime_revision, and adds the LA lifecycle supervisor files only when the worker declares the supervisor's exact readiness reply. New dry-run-by-default LA and Jetson deploy tools stage those releases and, only with `--apply --activate`, switch them with adjacent backups and printed rollback: LA repoints the worker source and its runtime_revision labels before installing the supervisor and restarts only the lifecycle supervisor, while Victoria repoints `managed.json`, relabels arm A (and optionally arm B's expected label) and restarts the controller, which reads that provisioning only at startup. Victoria backups are keyed to the change (the release, plus arm B's new label with `--la-archive`), so arm B can follow a later LA release, or be restored, under the same Jetson release. Both tools re-check leases or sessions after the stop and write nothing if one began, and their health checks fail on anything the restart left indeterminate. A read-only report turns one session's per-arm delivery counts, reasons, cadence, longest gap, latency, lookups, JEV outcomes and worker cleanup into content-free JSON for receipts.

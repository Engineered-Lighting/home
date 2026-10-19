---
title: Recover paired perception after source expiry between stages
target: backend
type: fixed
---

Return a terminal cancellation when an input expires before a stage starts and any previous stage has proven completion, so later fresh frames can proceed. Keep genuinely uncertain execution reserved, retain old ledgers, isolate new Jetson worker epochs after process-group termination, and add content-free failure diagnostics.

Also return a durable cancellation for integrity-checked inputs that expire during upload, so ordinary source expiry does not leave the sender waiting for unknown execution.

Reject an over-budget crowd frame after proven detector completion without invoking pose or stalling subsequent frames; retain the existing person and resource limits.

Show cancelled, rejected and unknown-execution counts alongside skipped inputs in the session delivery summary.

Treat Stop during local preprocessing as cancellation when the matching request has not crossed the durable network-dispatch boundary; preserve uncertain dispatched work.

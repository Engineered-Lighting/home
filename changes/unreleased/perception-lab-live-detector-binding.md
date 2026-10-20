---
title: Bind the Lab live placement experiment to admitted workers
target: backend
type: added
---

Add explicit Victoria controller composition for the matching Jetson and LA detector pair, reusing bounded queues, registered camera frames, and authenticated worker transports. A short operator readiness lease binds exact worker epochs and expires without starting models from the UI; production hardware acceptance remains separate. Display measured source-clock uncertainty alongside live source-to-controller timing.

Accept the camera's causal schema-3 JPEG contract through the commissioned clock publisher and shared validator, preserving the original receipt and rejecting substituted images or invalid timing brackets.

Document the observed Jetson CPU-delegation blocker and replace the unsupported systemd 249 runtime-property command with a scoped runtime unit-file override, requiring effective enforcement verification before worker admission.

Use a Python 3.10-compatible whole-request HTTP body deadline for the Jetson worker. Preserve the existing timeout and request-size bounds.

Use the committed recording start time when publishing paired results. Runners prepared before recording starts no longer discard completed model results while calculating replay timing.

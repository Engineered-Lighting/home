---
title: LA live consumer reconciles an unknown outcome and reopens at most 3 times an hour
target: backend
type: changed
---

When the long-lived live worker returns a late reply for an unanswered live exchange, which proves the exchange ended (a worker exit alone does not), the LA camera pilot now marks that row terminal as unknown_resolved with no answer and reopens the live consumer when the health verdict is clean, at most 3 times per rolling hour; a failed verdict defers the reopen to a later live request, and only the hourly limit or another close reason keeps live closed with an alarm line. Camera chat keeps its own unknown-outcome rule, heat is handled by the per-request thermal_shed refusal, and setting live_reopens_per_hour to 0 in private/pilot-runtime.json restores the previous behaviour.

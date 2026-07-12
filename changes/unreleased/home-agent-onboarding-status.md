---
title: Explain fail-closed Home Agent onboarding
target: stack
type: added
---

Adds an authenticated, content-free onboarding status contract and private web
surface. A signed-in Home Assistant user without a confirmed principal binding
now sees the fixed record-only rollout requirements, readiness, and bounded
blocker codes instead of a generic authorization error. The route does not
return identity, location, evidence, exact activity counts or timestamps, or
policy content; location memory and travel greetings remain explicitly off.
The shared panel also clears all principal-private state and rejects late async
results across logout, account changes, containment, and stale refreshes.

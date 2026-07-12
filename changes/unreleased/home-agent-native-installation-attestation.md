---
title: Require native installation attestation
target: backend
type: changed
---

Requires every remaining native Home Agent request to present a one-time,
challenge-bound ES256 proof from an offline-enrolled Windows installation.
The BFF binds the proof to the exact HA user, public target URL, method, route,
body, and access token before emitting a versioned Core channel; missing,
revoked, replayed, relayed, or mismatched installations fail closed without
enabling initiative or action capabilities. The BFF firewall admits only
conntrack-established return traffic for its loopback publication while new
BFF-to-host connections remain restricted to the reviewed HA OAuth tuple.

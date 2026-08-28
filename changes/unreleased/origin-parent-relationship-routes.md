---
title: Forward the parent-relationship routes at the Agent origin
target: internal
type: fixed
---

The origin and the BFF each maintain their own browser-route allowlist. The
duplication is deliberate — the origin must never widen what it forwards just
because the BFF gained a capability — but nothing held the two lists together,
and they drifted in the narrowing direction. The BFF served the three
`parent-relationship-proposal` routes while the origin's `BROWSER_API_ROUTES`
never gained them.

Every browser request for the parent-relationship ceremony therefore returned
`404 route_not_allowed`. The panel, unable to read its own state, rendered a
fail-closed "contained / unavailable" card — which reads as a broken
deployment rather than a missing allowlist entry, and blocked the Phase 3
step 34 confirmation until the allowlist was patched and the origin rebuilt.

Adds the three routes and a parity test asserting the origin forwards every
browser route the BFF serves, so the next capability cannot go unreachable
silently. The parity check found no other gaps: these three were the only
divergence across 21 BFF browser routes.

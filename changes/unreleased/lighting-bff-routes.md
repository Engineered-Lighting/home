---
title: Agent routes for confirmed cross-home lighting
target: backend
type: added
---

The Agent service can forward lighting requests, confirmations, outcome
lookups and the one-time lighting consent to Core for the signed-in owner,
with the same origin, CSRF and fresh sign-in checks as preferences. They stay
off until the lighting service is provisioned.

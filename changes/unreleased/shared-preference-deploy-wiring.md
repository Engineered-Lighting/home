---
title: Wire shared-preference linking into the deployed BFF and add Victoria sign-in egress
target: backend
type: added
---

Add the deployment pieces that turn on account linking and shared preferences:
- Compose overrides for the Echo BFF link runtime and the new Origin image.
- A separately provisioned Victoria linking service, with its own guarded egress
  to Victoria Home Assistant.
- A generator for both linking profiles and the Victoria ingress certificate.
- A narrow 0031→0047 migration runner.
- An encrypted backup job for the new journals and keys.
- A cutover runbook.

The BFF egress firewall contract gains a second reviewed profile; the existing
BFF profile is unchanged.

---
title: Activate shared-preference database logins and start private listeners reliably
target: backend
type: added
---

Add a reviewed operator command that gives each shared-preference service its
own database login with exactly the identity functions it needs and a bounded
connection limit, and returns them to dormant on deactivation. Private
consent and linking listeners now wait briefly for the worker's first
maintenance heartbeat at startup instead of failing immediately.

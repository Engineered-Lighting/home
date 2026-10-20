---
title: Connect bounded Victoria snapshots to observation storage
target: backend
type: added
---

Add a fixed loopback camera client with bearer authentication, bounded decoding and deadline checks, and persist validated observations under their provisioned source before publication. Workstation tests cover transport failures, stale results and duplicate delivery; live deployment and commissioning remain pending.

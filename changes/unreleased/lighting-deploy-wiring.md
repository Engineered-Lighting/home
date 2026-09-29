---
title: Deployment wiring for the private lighting service
target: internal
type: added
---

The shared-preference deployment gains an opt-in lighting override
(`lighting.json`), profile generation for the lighting listener and the Echo
BFF's lighting transport (`--lighting`), and runbook steps. Existing profiles
and compose commands are unchanged unless lighting is requested.

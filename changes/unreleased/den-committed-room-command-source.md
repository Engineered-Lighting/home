---
title: Bind prospective fixture commands to accepted room samples
target: backend
type: added
---

Add a read-only boundary that verifies a complete worker commit against the
existing Journal's durable receipt, current checkpoint and room publication.
Command consumers receive sampled domain values with exclusive expiry; desired
endpoints, stale cuts and unavailable samples cannot become current commands.
This does not activate fixture transport or establish native achieved state.

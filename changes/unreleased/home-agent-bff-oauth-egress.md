---
title: Make Home Agent OAuth exchange restart-safe
target: stack
type: fixed
---

Pins the BFF's sole outbound Docker identity and reconciles one exact
default-deny host-firewall path to the local Tailscale Home Assistant OAuth
listener, with a synchronous UFW lifecycle guard, content-free live validation,
and periodic drift detection.

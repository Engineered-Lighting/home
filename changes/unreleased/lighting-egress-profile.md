---
title: Host firewall egress profile for the Core lighting service
target: backend
type: added
---

The reviewed HA egress firewall helper gains a `lighting` profile. It lets the
private Core lighting service reach both Echo HA (`:10000`) and Victoria HA
(`:10001`) on this node's Tailscale address through its own bridge
`ha-light-egr0` (`172.27.0.10`) and guard chain `HOME_AGENT_LIGHT_INPUT`, with
one exact accept per HA URL. A profile can now list a fixed, ordered set of HA
URLs, and every URL must name the same Tailscale host. The BFF and Victoria
linking rules are byte-for-byte unchanged. The UFW lifecycle hook also guards
lighting once `HOME_AGENT_LIGHTING_EGRESS_IP` is provisioned, so the hook's
pinned digest changes: install the helper and hook together. A new
`home-agent-lighting-egress-verify` timer rechecks the boundary.

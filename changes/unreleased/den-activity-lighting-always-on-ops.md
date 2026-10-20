---
title: Always-on operations for den activity lighting
target: backend
type: added
---

The web gateway now exposes the den lighting brain under
`/api/lighting-brain/v1/`. Home reads the state and its live stream with its
session; overrides also need the `X-Home-Lighting-Brain-CSRF` header; and the
Victoria producers post events with their own bearer token. A new
`/api/den-ops/v1/` status endpoint carries a back-off banner for Home.
`stack/ops/den-activity-lighting/` adds these, none of them deployed yet:
- service units for the brain, the Victoria bridge and the activity loop;
- a monitor that backs off in steps on heat, memory, GPU or kernel faults and
  alerts through Home and Home Assistant;
- a soak runner with written pass criteria;
- a go-live runbook draft.

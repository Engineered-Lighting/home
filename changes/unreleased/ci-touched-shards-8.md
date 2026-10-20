---
title: Touched-surface CI runs in eight shards
target: internal
type: changed
---

The touched-surface browser contracts now run in eight parallel shards instead of six, using measured hosted suite times, so a large touched plan no longer overruns the 10-minute job limit. No suite, deadline or assertion changed.

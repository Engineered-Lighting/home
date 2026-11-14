---
title: Touched-surface CI runs in sixteen shards
target: internal
type: changed
---

The touched-surface browser contracts now run in sixteen parallel shards instead of eight, and the slow chat-context unit suite has a measured weight, so a pull request that touches the core apartment files (about 164 harnesses) finishes inside the 10-minute job limit even on a slow runner. No suite, deadline or assertion changed.

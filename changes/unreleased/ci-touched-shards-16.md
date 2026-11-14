---
title: Touched-surface CI runs in twelve shards
target: internal
type: changed
---

The touched-surface browser contracts now run in twelve parallel shards instead of eight, so a pull request that touches the core apartment files (about 164 harnesses) finishes inside the 10-minute job limit. No suite, deadline or assertion changed.

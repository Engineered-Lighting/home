---
title: Require fresh occupied evidence throughout the Den loaded hour
target: backend
type: fixed
---

Den operational admission now requires fresh occupancy alongside a running loop, recording rolls and advancing Qwen completions. Missing sampling intervals break the loaded hour, and receipt version 4 refuses earlier receipts that could qualify without these observations.

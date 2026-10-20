---
title: Qualify paired capture before live context publication
target: backend
type: added
---

Adds an opt-in, bounded RGB/depth adapter that preserves original capture timing and sensor lineage, checks commissioned clock/calibration/registration bindings, and publishes qualification or explicit refusal through the live context journal. Controlled verification does not connect a camera or establish person/task geometry, useful lighting or physical acceptance; existing capture ownership and recording behavior are preserved.

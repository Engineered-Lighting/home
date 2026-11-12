---
title: den-live runs continuously with no admission soak
target: docs
type: changed
---

AGENTS.md records the owner's November 12 approval for den-live to run continuously on the LA AI host, with no 3-hour admission soak. It asks Qwen only while Frigate reports the den occupied and someone is seated. Its guards today are its own limits: one request at a time, at least 2 s apart, at most 30 a minute, and the unit's memory and CPU caps. It can follow the background monitor's back-off once it can read the monitor's level.

---
title: Keep Den monitoring active with unsupported Jetson sensors
target: backend
type: fixed
---

Skip explicitly excluded thermal channels before opening their temperature files. Unsupported or malformed sysfs reads become missing evidence so required-sensor failures can stop the activity loop through the monitor instead of crashing it.

Add a continuous-operation entry point that starts under operator Stop and refuses legacy configurations without healthy media cleanup before background work begins.

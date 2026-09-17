---
title: Overnight lights stay off once the house is asleep
target: backend
type: fixed
---

The overnight asleep latch now fires when it should and stops being undone by
side paths. The four raw Frigate motion sensors no longer block it (only
person occupancy does), and the 5-minute tick requires the whole house to
have been quiet for the full 15 minutes instead of one instant, which caused a
false latch on 2026-09-13 at 22:42. The morning latch, the good-morning
energize and the return-home scene are gated on the latch being off, the
colour-temperature actuator only adjusts lights that are already on (it used to
relight all eight at 20:00 and 22:30), and the sofa gradient's cooldown gate now
matches the pilots so a manual touch at night sticks. Deploy: regenerate, copy
the four YAML files to Home Assistant with a `.bak.<ts>` of each, `ha core
check`, then reload template, automation and script. Rollback: restore the
`.bak.<ts>` copies and reload.

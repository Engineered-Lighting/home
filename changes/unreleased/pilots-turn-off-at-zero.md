---
title: Pilots turn a zone off when the classifier predicts zero
target: backend
type: fixed
---

The ten generated Living Lights pilots now read the classifier's predicted
brightness with a `-1` sentinel, so a missing attribute is distinguishable
from a real zero: the present and default branches issue no light call when
the attribute is absent, turn every target off when the prediction is exactly
zero (the TV floor), and keep their turn-on calls above zero. The fast
present call never fires at or below zero, the raise-only pass-through and
anticipated branches are skipped at or below zero (a `max` against an off
light used to render `brightness_pct: 0`, which Home Assistant turns into a
turn-off command), and the override branch stays turn-on only because the
override lifecycle depends on it. The learning and manual-detection packages
read the TV through `binary_sensor.living_lights_tv_playing` instead of their
own `media_player.lg_tv` membership list, and the sofa gradient actuator only
runs while the pilot's base level is above zero, keeping its 5 % per-light
floor only then, so a zone the pilot turned off for a film is never relit.
Deploy: regenerate the pilots, learning, manual detection and gradient
packages with the observability package that defines the `tv_playing` sensor,
copy them with a `.bak.<ts>` of each, `ha core check`, then reload template
and automation. Rollback: restore the `.bak.<ts>` copies and reload.

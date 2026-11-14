---
title: The den coordinator plans the room's bulbs and shows the plan, sending nothing until Bulbs is On
target: deploy
type: added
---

The den spotlight preview (`tools/den-live/preview.py`) now plans the room's bulbs from the same situation as the spots, and publishes the plan with every snapshot (`bulbs`) and in `/coordinator` (`bulb_plan`). Nothing reaches the bulbs: the plan is a shadow until the owner turns the new Bulbs switch On, and den_lighting stays the only writer of the room lights.
- **The reading scene:** one person reading, so each bulb that's on dims to 30 % at 2700 K over 4 s. It's never brighter than the bulb already was, and never warmer than the bulb can go. Standing up, leaving, Off or Stop releases it back to den_lighting's own rules. Several people get no reading scene.
- **The Bulbs switch** (`POST /coordinator/bulbs {on}`) starts Off and is saved in `coordinator/bulbs-control.json`; a missing or unreadable file is Off. The overlay that would send (`hue_overlay.py`, den_lighting 1.2's claim and plan) is built only with the coordinator block's own `bulbs: true` and the Qwen ask's Home Assistant link. It reads through a client that can't write, refuses every den_lighting call while Bulbs is Off except the release of a plan of its own, and is woken on every change of the plan.

---
title: Draft Victoria HA sensors that record the den bulbs' levels
target: internal
type: added
---

Adds `tools/den-core/victoria_den_bulb_scenes.yaml`, a draft Home Assistant
package for Victoria. It has four read-only template sensors that copy each den
bulb's on/off state, brightness and colour temperature, which HA's recorder
otherwise drops for lights. den-core replays could then compare levels for
evenings recorded after the owner installs it. Nothing in the repo deploys it,
and it sends nothing to any bulb.

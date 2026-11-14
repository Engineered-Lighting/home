---
title: Home draws the den spot's glide and fades the way the fixture makes them
target: deploy
type: changed
---

Home's den view now plays the coordinator's spot the way the fixture would, instead of jumping to each 10 Hz snapshot.
- **Glides** are drawn every frame on the board's own jerk-limited S-curve, from the `glide` that den-live's light direction publishes on a moving row. A render clock maps the coordinator's time to Home's by the lowest transit seen. The S-curve is tested against the vectors den-live writes.
- **Levels** ease between the level train's committed steps over 120 ms, about one coordinator tick.
- **The room's glow:** a snapshot's `ambient` goes on every virtual fixture's glow zones and downlight. When the coordinator directs none, each fixture's own glow comes back.
- **Stop, Off and expiry still blank at once.**
- A snapshot from an older producer, with no `glide` or `ambient`, draws exactly as before. No new file is added, so the service worker's list is unchanged.

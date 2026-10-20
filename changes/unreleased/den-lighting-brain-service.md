---
title: Den lighting brain service
target: backend
type: added
---

A new lighting brain service decides what the den's four virtual fixtures do from occupancy, zone, activity, pose and health events: ambient when someone is in a zone (dimmer and warmer at night), an over-shoulder reading spotlight on the sofa, ambient only with two people or when JEV is down, and a manual-change pause. Every change comes with a "why" and a 20-item decision log. It is not deployed or wired into the web gateway yet, and it never drives physical fixtures.

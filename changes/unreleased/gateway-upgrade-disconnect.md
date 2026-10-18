---
title: Keep the Home gateway running when upgraded clients disconnect
target: web
type: fixed
---

Browser connection resets now close both sides of an upgraded connection without stopping the Home gateway. Incomplete Home Assistant authentication buffers are released on disconnect, and upstream failures no longer append HTTP errors after an upgraded response has started.

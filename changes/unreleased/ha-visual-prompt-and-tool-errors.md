---
title: HA visual prompt and tool error hardening
target: backend
type: fixed
---

Tuned live visual-planning instructions so simple occupancy questions avoid
unnecessary perception refreshes, while compound "right now" visual requests
still gather fresh vision. Missing or unexposed service targets now return a
structured tool error instead of surfacing as an HTTP 500.

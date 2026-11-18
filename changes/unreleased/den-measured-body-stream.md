---
title: Live figures use current measured depth
target: backend
type: fixed
---

The live figure stream removes the head and upper neck before sending current depth points. It clears when the person cannot be reliably tracked, measured, or excluded, and expires each frame one second after capture.

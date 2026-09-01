---
title: Honor reduced-motion everywhere, including the 3D camera
target: web
type: fixed
---

The OS "reduce motion" setting previously affected almost nothing: every
keyframe loop (caret blink, pulses, waveform, glow breathing) and CSS
transition ran regardless, and the 3D apartment camera's full-viewport
swoops — a genuine vestibular trigger — ignored it entirely since CSS cannot
reach WebGL. A global stylesheet block now collapses all CSS motion to a
jump cut, and the 3D rig gates its orbit tweens, fit flights, fly-to-camera
swoops, and ambient hover parallax on the same media query.

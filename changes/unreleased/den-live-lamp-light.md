---
title: den-live tracks and asks under the den's evening lamp instead of calling it too dark
target: backend
type: changed
---

den-live called the den "too dark" whenever the camera picture's mean
brightness fell under 40 (of 255), which a warm evening lamp always does. On
the den camera the lamp-lit den measures 11–22 and, with only a screen lit, 5–7,
and at those levels the detector finds a person on every frame and Qwen answers
as it does by day. The line is now 6, back over 9, each held 3 seconds, so a
lamp-lit evening is tracked and asked about as by day. A frame under the line
still reads "too dark", never "empty".

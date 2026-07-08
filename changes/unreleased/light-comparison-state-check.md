---
title: Require live state checks for light comparisons
target: backend
type: fixed
---

Light and device state comparison prompts now explicitly require checking current
Home Assistant attributes before answering, reducing "I can check" dead ends.

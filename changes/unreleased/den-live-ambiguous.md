---
title: A stray detection no longer blanks the Den figure
target: backend
type: changed
---

The live figure now steps aside only when two people are tracked, or when two detections back one person's track. A stray detection that supports no track, such as a box on a reflection, no longer clears the figure for a moment.

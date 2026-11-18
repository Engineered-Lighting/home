---
title: The Den figure no longer blinks when the same person's association revision moves
target: backend
type: fixed
---

When only the tracked person's association revision changes, Den Live keeps the figure on show (never past its own 2 s lease) until the new revision's first body reply. A ready replaces it as the next frame, and any other outcome clears it at once, while replies made under the old revision are still dropped and track, epoch, run or binding changes still clear immediately.

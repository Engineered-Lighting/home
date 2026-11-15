---
title: The Den figure no longer vanishes when someone sits down or gets up
target: backend
type: fixed
---

When the owner stopped, sat down or got up, the tracker sometimes started a second track for them and kept the old one coasting for up to 6 seconds. den-live read the two tracks as two people and blanked the figure, about 17 times a day for 5 to 6 seconds each. The figure is now ambiguous only when two people are actually seen in the same frame. A coasting track with no detection doesn't count, and neither does the tracker's doubt about who it is when only one person is seen. The figure holds through the switch and goes on under the new track. Someone else walking through still shows, and the Qwen ask still waits for a confident track.

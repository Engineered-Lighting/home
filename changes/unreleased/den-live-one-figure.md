---
title: The Den figure vanishes less when someone sits down or gets up
target: backend
type: fixed
---

When the owner stopped, sat down or got up, the tracker sometimes started a second track for them and kept the old one coasting for up to 6 seconds. den-live read the two tracks as two people and blanked the figure. Now a track with no detection counts as someone else unless it was last seen before the person in view was first seen, and was never seen together with them. A cleanly left-behind duplicate passes, so the figure holds through the switch and goes on under the new track. Two people who have been seen together, or seen one after the other while both were around, still blank the figure, even when one is missed for a frame, so two people are never drawn as one. A split where the tracker hands detections back and forth between the old and new track still blanks, as before. The Qwen ask still waits for a confident track.

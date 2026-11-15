---
title: The Den figure no longer vanishes when someone sits down or gets up
target: backend
type: fixed
---

When the owner stopped, sat down or got up, the tracker sometimes started a second track for them and kept the old one coasting for up to 6 seconds. den-live read the two tracks as two people and blanked the figure, about 17 times a day for 5 to 6 seconds each. A track with no detection now counts as someone else only if it was ever seen in the same frame as the person in view. A left-behind duplicate never is, so the figure holds through the switch and goes on under the new track. Two people who have been seen together still blank the figure, even when one of them is missed for a frame, so two people are never drawn as one. The Qwen ask still waits for a confident track.

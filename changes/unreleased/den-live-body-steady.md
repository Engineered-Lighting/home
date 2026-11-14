---
title: Keep the Den figure steady when the head is shown
target: backend
type: changed
---

When the owner's body profile shows the head, den-live no longer needs the camera's capture-age receipt. It dates each body frame the way the people stream dates its frames, so the figure works on the camera's faster slim release. A profile that cuts the head still needs the receipt.

A frame where the same person's pose or depth briefly drops out no longer blanks the figure. The last frame stays until its own lease ends, or until the next frame replaces it. An empty room, two people, a new track or a stopped detector still clear the figure at once.

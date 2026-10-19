---
title: Show session-bound Lab camera images in the fenced live view
target: web
type: fixed
---

The fenced Victoria live view can read JPEG and PNG images from its existing Lab session after verifying the session's media generation. Reads require bounded, verified image descriptors and byte ranges; a generation or immutable image identity change permanently revokes access for that page, while other Lab routes and native access remain blocked.

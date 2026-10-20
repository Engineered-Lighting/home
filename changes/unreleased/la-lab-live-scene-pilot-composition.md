---
title: Optional Lab live-scene consumer in the LA camera pilot
target: backend
type: added
---

The LA camera-pilot service can now also serve the Victoria Lab continuous
live-scene (Qwen) consumer on its existing private mTLS listener, sharing the
pilot's ledger so a live request is never admitted while camera chat is in
flight. It stays off unless `private/live-scene.json` enables it; without that
file the camera pilot is composed exactly as before.

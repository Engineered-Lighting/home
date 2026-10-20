---
title: Read verified lighting layers beside the active room pack
target: web
type: added
---

Add an authenticated, read-only handler for modeled lighting layers bound to the active Victoria room pack. It verifies receiver file sizes and SHA-256 digests, refuses writes and unrelated private files, and keeps responses out of shared caches.

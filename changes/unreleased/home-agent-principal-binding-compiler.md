---
title: Compile principal binding previews offline
target: internal
type: added
---

Adds a dormant, pure compiler and verifier for a private Home Assistant
account-to-person binding preview. The preview requires a fresh, server-supplied
authenticated-HA snapshot, exactly one reviewed finalized `me` candidate,
conflict-free graph cardinalities, and clear privacy and erasure state. It
verifies no external receipt and remains replayable until a future single-use,
transaction-time kernel. It remains capability-disabled, non-authoritative,
non-committable, and disconnected from all production routes and writers.

---
title: Add an offline den-core check that a run behaved as designed against den_lighting
target: internal
type: added
---

Adds `tools/den-core/validate.py`, one offline command that reads a den-core run log and says, check by check, whether den-core behaved as designed against its den_lighting baseline. It checks that the log replays byte for byte, that need's reading on and off moments equal den_lighting's own dwell, that decide differs from den_lighting only by its labelled placeholder reading look, that decide keeps held bulbs pinned and skipped, and that it stays silent during a game and while occupancy is unknown. It is a consistency check of den-core's own code, not a lighting-quality gate; reading-shadow remains the gate. It contacts no host and sends no light commands.

---
title: Recover interrupted Lab media deletion
target: backend
type: fixed
---

Persist explicit media-deletion intent and invalidate playback before removing
files. Resume unfinished deletions on controller startup, retain audit receipts,
and block deletion while shared-detector execution remains unresolved. This
preserves the existing media-only control; labels and results are retained.

---
title: Withhold reading when newer Qwen evidence cannot be bound
target: backend
type: fixed
---

Reading qualification checks the latest known Qwen source before selecting
compatible history. A newer capture, overlapping reader window or scene ahead
of the latest detector can no longer reveal an older apparently usable book.
The configured always-on source must remain available to qualify reading.

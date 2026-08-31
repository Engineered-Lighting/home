---
title: Correct the Constraints That Kept the Widened Relationship Vocabulary Unusable
target: backend
type: fixed
---

The change that widened the relationship vocabulary would have applied cleanly and still left friends, siblings, roommates, neighbours and colleagues impossible to record.

Two rules in the receipt table pin which relationships may be written, and the new migration reached neither of them. It named a rule that does not exist, so it changed nothing and added a permissive rule alongside the original — and because every rule has to pass, the original kept refusing everything new. A second rule tied each relationship to how many entries it writes, and recognised only the two that already worked, so it refused the rest outright.

Both now admit the full vocabulary, and the second keeps the distinction it was written for: relationships that read the same in both directions write two entries, "parent of" writes one.

A test replays the constraint names across every migration in order and checks what survives, so a rule that is renamed, duplicated, or left behind is caught before it reaches a database rather than after a deployment reports success.

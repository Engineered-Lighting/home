---
title: Ladder level 1 runner for the lighting belief questions
target: backend
type: added
---

Adds `tools/public_story_eval.py`, the offline runner that sends public-dataset description rows through the five lighting belief questions behind the egress gate (dry run by default, `--execute` with a hard call cap, cached answers and receipts confined to the private experiments directory, packet cameras named after each question's room, recall at the a-priori threshold as the level-1 metric with a per-scene breakdown), and `EGRESS-RECORD.md`, the schema, scopes, file mode, toggle mirror and env switch of the egress record.

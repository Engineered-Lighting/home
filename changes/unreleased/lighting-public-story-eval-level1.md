---
title: Ladder levels 1 and 2 runner for the lighting belief questions
target: backend
type: added
---

Adds `tools/public_story_eval.py`, the offline runner that sends public-dataset rows through the five lighting belief questions behind the egress gate (dry run by default, `--execute` with a hard call cap, cached answers and receipts confined to the private experiments directory, packet cameras named after each question's room, recall at the a-priori threshold as the level-1 metric with a per-scene breakdown), and `EGRESS-RECORD.md`, the schema, scopes, file mode, toggle mirror and env switch of the egress record.

`--level 2` adds the bounded visual level: each row names the rich observation the observer's batch runner wrote for that window, and the packet is built from that typed observation the way the belief publisher will build it (dimension descriptions become the camera's claims, posture and activities become its account, the dataset's caption, class names and targets are never read into it). Because the leak guard knows household vocabulary and not dataset vocabulary, an explicit check refuses any row whose packet carries a class id, a class name or a target word, and reports it per row; so is a missing or empty rich observation, which never aborts the run. Scoring keeps level 1's probability-only reduction, three-way targets and sensitivity table, chooses recall or precision-and-recall per question from the labels actually present, and adds the two consequence flags the plan names (`disruptive_brightening`, `darkness`) and calibration bins with n. The answer cache key includes the level and the rich-observation digest, so the levels never share an entry.

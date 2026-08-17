---
title: Add an overnight architecture deep-dive goal command
target: internal
type: added
---

Adds `/goal-architecture`, an experiment loop for designing the house's next
model architecture rather than a planning exercise: download a model,
measure it, revisit an assumption, plan the next experiment, repeat.

It carries the measured baselines forward so a fresh session does not
re-derive them, points at the harnesses and frozen corpora that already
exist, and encodes the methods this project learned expensively — verify a
data source before building on it, change one variable at a time, build the
reproducer before the third attempt, and sample enough to actually see an
intermittent fault.

It also carries the guardrail that cost a real voice outage: never write
Home Assistant's storage files while it is running, because a config-entry
reload is not equivalent to a restart.

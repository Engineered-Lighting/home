---
title: Prepare admission support for pinned asynchronous model scheduling
target: backend
type: changed
---

Allow the exact vLLM 0.20.2 asynchronous scheduler in the prepared engine adapter,
with a pinned source hash and matching runtime mode. Completion still requires
an empty engine queue, observed scheduler cleanup and synchronized worker proof.
This is local compatibility work; the running model and scheduling settings are
unchanged, and live GPU validation remains required.

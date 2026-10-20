---
title: Victoria live Qwen accepts faster cadence settings and LA's thermal and reconcile outcomes
target: backend
type: changed
---

The Lab controller's live Qwen pump now accepts an interval of 0.5-10 s and up to 120 requests a minute, with the defaults unchanged at 4 s and 15 a minute, one request in flight and only new frames sent. A `thermal_shed` refusal from LA is a typed skip with a 30 s backoff like `admission_closed`, an `unknown_resolved` outcome from LA's reconcile ends a request with no fact recorded, and an LA row reported indeterminate still halts the recording pump for the rest of the recording, as before.

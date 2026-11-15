---
title: den-live's Qwen asks no longer lose their turn when the detector's process is busy
target: backend
type: fixed
---

About half of den-live's Qwen asks on LA never went out (`stills_worker_busy`): the live body's pass shares the detector process's one optional slot with the ask's stills request, and a refusal cost the ask its 2 s turn, so a seated owner got about 15 answers a minute instead of 30. Now a refused stills request asks again on each next frame for up to 1 s, and each ask's `asks.jsonl` line says how long it waited (`stills_wait_s`) and how often the process was busy (`stills_busy`).

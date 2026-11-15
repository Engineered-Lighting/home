---
title: den-live's Qwen asks go out when the body's pass shares the detector's process
target: backend
type: fixed
---

About half of den-live's Qwen asks on LA never went out (`stills_worker_busy`): the live body's pass is sent ahead of the ask on each frame and took the detector process's one optional slot, so a seated owner got about 15 answers a minute instead of 30, and none at all whenever the body ran on every frame. Now a refused stills request makes the process keep its next free slot for it (the body's pass skips that frame) while the ask asks again on each next frame for up to 1 s. A stopped process is `stills_worker_stopped`, not `stills_worker_busy`, and each `asks.jsonl` line says how long the request waited (`stills_wait_s`) and how often the process was busy (`stills_busy`).

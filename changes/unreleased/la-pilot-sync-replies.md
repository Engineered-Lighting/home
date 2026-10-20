---
title: LA camera pilot answers in the submit reply and reads its engine from model-pin.json
target: backend
type: changed
---

The LA camera pilot and its live-scene consumer now wait up to 8 s for their own answer and return it in the same 202, so camera chat and live Qwen skip the status poll; their backend log lines also report health_ms, exec_ms and total_ms. The pinned vLLM identity moves from code into private/model-pin.json, which live-scene.json must match at start and which a standalone model_health() preflight checks read-only, with an optional "auto_repin" rule that keeps camera chat running across any number of vLLM restarts and later pilot restarts, while live stays closed until live-scene.json and Victoria's runtime_revision name the new engine epoch.

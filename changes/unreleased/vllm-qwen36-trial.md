---
title: Prepare the Qwen3.6-35B-A3B vLLM trial with video input
target: backend
type: added
---

Added a pinned Qwen3.6-35B-A3B-FP8 compose override for the AI host's vLLM
service. It enables one video item per request and switches to the
qwen3_coder tool parser, while keeping the served name `qwen3-vl-30b` so
existing clients are unchanged. Also added host preflight, pinned download
and smoke-check scripts, and a runbook with a one-command rollback. The repo's
base vLLM service now matches production: image pinned by digest, GPU
fraction 0.50 and up to 8 images per request.

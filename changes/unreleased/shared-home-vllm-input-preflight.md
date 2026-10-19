---
title: Bind shared inference execution to prepared input limits
target: backend
type: added
---

Add a fixed-endpoint input preflight client and explicit vLLM hook that stops after stock input processing, before engine dispatch. Version 2 dispatch tokens separate preparation from execution and bind execution to a checked input-token limit. Deployment and model-backed acceptance remain gated.

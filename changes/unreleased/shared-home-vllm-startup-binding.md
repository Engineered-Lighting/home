---
title: Connect shared inference hooks to pinned vLLM startup
target: backend
type: added
---

Package guarded startup for the engine receipt ledger and authenticated API hooks, with protected provisioning, explicit child-process registration and an engine handshake before listening. Correct worker cleanup RPC binding to match vLLM's worker wrapper. Runtime compatibility and deployment acceptance remain required before enabling Victoria inference.

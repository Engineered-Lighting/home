---
title: LA camera pilot keeps a warm model path between requests
target: backend
type: changed
---

The LA camera pilot now reuses a host health verdict for up to 2 s, scans the kernel journal at most every 30 s, and keeps one long-lived interpreter per consumer (camera chat, live scene) in the pinned vLLM container, which opens a fresh loopback connection for every request. Its private TLS ingress no longer starts a timer thread per connection, and the optional private/pilot-runtime.json switches each of these back to the per-request behaviour.

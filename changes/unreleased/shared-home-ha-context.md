---
title: Add inactive Victoria observation context integration
target: backend
type: added
---

Add an opt-in Home Assistant sensor integration that consumes fixed Victoria observation topics and expires current context using live heartbeats, source generations, absolute timestamps and a one-second timer. The raw diagnostic is hidden and disabled by default; no HA configuration, services or physical-action authority are changed.

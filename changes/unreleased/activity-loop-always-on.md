---
title: Always-on den activity loop publishes activity events to the lighting brain
target: backend
type: added
---

The perception lab can run an always-on, occupancy-gated activity loop for the
den, enabled by a private configuration file. While the room is occupied it
keeps a live analysis recording open with JEV reasoning, rolling to a fresh
recording before each recording's time limit, and pauses two minutes after the
room empties. It publishes activity, approximate person pose, health and cost
events to the lighting brain over a bounded, events-only outbox with a producer
token. It never sends lighting commands. Qwen live facts from the always-on LA
perceiver reach JEV again (they had been absent since 2026-10-09), still subject
to the existing recipe and household policy grants. The loop has status, Stop
and resume routes.

In this always-on mode only, a JEV request whose outcome stays unknown no
longer blocks JEV indefinitely. After 120 s the loop abandons its own stuck
request, records a receipt in the recording's ledger, and lets JEV resume. Each
consecutive release waits twice as long until JEV answers again. Health reports
JEV as blocked, then resuming, then healthy. A setting turns this off, and stuck
requests from Lab recordings still wait for an explicit reconcile.

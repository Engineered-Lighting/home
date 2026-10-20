---
title: Victoria Lab controller can run an always-on LA Qwen perceiver while the den is occupied
target: backend
type: added
---

An optional `la_live_perceiver` block in the Lab controller's live config sends den frames to LA Qwen at the existing 4 s cadence whenever the in-memory occupancy gate is open and the household policy lists `la-live-inference-1`, also outside Lab recordings, capturing into a RAM-only ring of about 10 s through the controller's own camera client with one attempt per capture, an idle gap of at least 0.5 s after each and no capture while it could not send, so camera chat waits at most one ring capture. It writes words-only `la-fact-log-1` day files kept 14 days and stops sending while they cannot be written, pauses with a 60 s identity re-probe when LA reports a request indeterminate, refuses to start while the recording `qwen_live` block is enabled, the fact log directory is not writable or the occupancy entity is not the HA source's camera-derived one, and changes nothing without the block.

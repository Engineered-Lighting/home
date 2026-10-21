---
title: Add an independent LA skeleton viewing path
target: backend
type: added
---

Add an opt-in, bounded Den tracking lane using the existing camera owner and pinned LA detector/pose models. It keeps exact RGB/depth frame identity, retires stale observations, and streams compact joints independently of Qwen, JEV, recording rolls and video decoding. Home records actual skeleton draw and GPU completion with monotonic camera-to-render timing brackets and rendered update rate. Room placement remains approximate and grants no lighting or physical authority. Continuous admission still requires separate operational validation.

Carry compressed, hash-bound synchronized depth alongside each fast RGB frame and project joints on LA, avoiding a second camera round trip. Keep decompression and frame/session/calibration checks ahead of model admission, preserve original evidence expiry, and disable camera HTTP Nagle buffering. The sealed release declares its required worker startup environment.

Keep clock refresh capacity separate from long-lived viewers and preserve an earlier clock sample only within its existing validity limit when a refresh fails, with bounded retry. Add opt-in metadata-only worker/gateway tracing and bounded browser per-observation diagnostics through validation, scene application and actual GPU completion. Unconfigured viewers do not load the fast layer.

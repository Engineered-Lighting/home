---
title: Synthetic voice probe and bounded cadence A/B for the LA live perceiver
target: internal
type: added
---

`tools/la-latency/voice_probe.py` runs on LA and measures voice time to first token through the metrics sidecar, the route HA voice uses, along with LA GPU temperature, CPU Tctl and vLLM's queue gauges, in the `harvest.py` sample format that `report.py` reads. `tools/la-latency/cadence_ab.py` plans the S1.4 A/B as a dry run by default and, with `--apply`, alternates the Jetson perceiver between 4 s and 1 s cadence windows, always restores the original `managed-live.json` and stops the probe (also on errors and Ctrl-C), and reports PASS, FAIL or INSUFFICIENT against the S1.4 guard, counting failed voice and camera requests and requiring that the candidate's live load actually ran.

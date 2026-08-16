---
title: Add the Qwen3.8 acceptance-gate harnesses and correct two gates against measurement
target: internal
type: added
---

Adds the shared gate core `tools/qwen38_gates.py` (exact one-sided
non-inferiority testing over discordant pairs, reasoning-leak detection,
and the production grounded-box parsers), `tools/gate-g5-leak-grep.py`, and
a `--production-parser` scoring mode for
`tools/probe-grounded-reasoning.py`, with unit tests covering the ways each
gate could wrongly pass.

Measuring the incumbent with the new harnesses corrected two gates:

- G7 cannot discriminate as written. The deployed `/reason` prompt extracts
  zero grounding boxes on the incumbent (0 of 13 runs, confirmed through
  the live endpoint, which returns no primitives), so a paired
  non-inferiority test sits on a floor of zero. G7 is restated against an
  absolute threshold, and the live grounded-look degradation is recorded as
  a pre-existing, model-independent regression so the cutover cannot be
  blamed for it.
- G5, G6, and decision D11 all rest on the sidecar's `/trace` NDJSON, which
  has no producer: its records are latency telemetry with no message
  bodies, the newest is three months old, and the only writer is the s2s
  bridge, which never runs. G5 now scores harness-captured responses
  instead, and the leak scanner treats an empty scan as inconclusive rather
  than as a pass.

The harnesses also confirmed that the app's two grounded-box stripper
regexes reject output the sidecar accepts, so a truncated trace renders raw
`<box>` markup to the user. The widening fix is queued in the app
workstream.

Adds `tools/qwen38_capture.py`, the measurement instrument that replaces
the dead `/trace`: it refuses to record a latency without its measurement
point and cache state, distinguishes "no cache hit" from "nobody measured",
and reads the engine's prefix-cache counters. Measuring the incumbent with
the real 33,760-character production prompt shows prefix caching is worth
about 5x on the LLM leg of a voice turn (0.07s warm against 0.36s with the
cache defeated), which is why the matrix now runs its cache cells first.
The engine does not report cached tokens in the response body, so the
research finding's instrumentation instruction is corrected to the
Prometheus counters that do exist.

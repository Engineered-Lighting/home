---
title: Add a direct reproducer for the reasoning leak
target: internal
type: added
---

Two cutover attempts were rolled back on reasoning tags reaching spoken
output, and both diagnoses were made from ten-minute cutover cycles and both
were wrong. Adds `tools/qwen38-leak-repro.py`, which drives the engine
directly and ablates one ingredient at a time, so a hypothesis costs seconds
instead of a maintenance window.

Reading the archived live conversation component showed the probes used so
far were missing the most obvious ingredient: the real path streams its
completions, while every earlier probe was buffered. The reproducer covers
six shapes, from the old buffered probe up to the full path — streaming, the
33,760-character system prompt, the 23-tool catalogue, and a tool result in
history.

It also reports whether the separate reasoning response fields are ever
populated. That is the decisive column: the live component consumes only the
content delta and never reads a reasoning field, so if no parser is routing
thinking elsewhere, the engine has nowhere to put it except the text handed
to speech synthesis.

Validated against the current model, where it correctly reproduces nothing
across all six shapes and confirms no parser is active.

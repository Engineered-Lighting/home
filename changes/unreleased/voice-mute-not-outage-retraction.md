---
title: Retract the voice-outage diagnosis — it was the mute gate working
target: internal
type: fixed
---

Two "voice outages" recorded on 2026-08-16/17 were not outages. The assistant
was muted, deliberately, and returned exactly what it is designed to return.

`conversation.extended_openai_conversation` answering with a well-formed
`action_done`, empty speech, ~0.01 s and **zero** LLM calls is the intended
signature of the Jarvis mute gate, which returns silence to close Voice PE's
mic. The component says so itself in `/config/asr_debug.log`: all twelve probes
of the second "outage" appear there as `JARVIS_MUTED_DROP`, with the composite
`binary_sensor.jarvis_muted_effective_2` on because
`input_boolean.jarvis_auto_mute_tv` is enabled and the TV and receiver were
both on. With the sensor back to `off`, the identical probes answered in
0.54–4.09 s with 5 LLM calls.

This withdraws three claims made earlier in `AI-ARCHITECTURE-EXPERIMENTS.md`:
that recreating the `vllm` container kills the conversation agent; that this
explained the 2026-08-16 outage; and that an HA Core restart was the fix.
Session 2 recreated `vllm` several times with voice answering immediately
afterwards, which should have falsified the first claim when it was made. The
restart that appeared to fix it was coincident with the mute lapsing. The
original `.storage`-write hypothesis is equally unproven — one unverified theory
was replaced with another and asserted more confidently.

The 2026-08-16 cause is now recorded as **unknown**: `asr_debug.log` retains
only entries written after the most recent restart, so it cannot be checked
retrospectively.

Corrects the restore runbook, which had been given a mandatory "restart HA Core
after any vllm change" step it did not need, and replaces it with the check that
actually discriminates. Also records that the metrics-sidecar traffic-counter
test is necessary but **not sufficient**: a zero delta cannot tell "muted" from
"broken", which is how this was misread twice. Two purpose-built instruments
already existed and neither was consulted before a cause was asserted.

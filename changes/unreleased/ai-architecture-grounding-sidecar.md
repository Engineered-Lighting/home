---
title: Stand up a Qwen3.8 grounding sidecar, and find the grounding gap is a prompt
target: internal
type: added
---

Adds `vllm-grounding` to the live compose: a second vLLM instance serving
Qwen3.8-27B-FP8 as `qwen38-grounding` on port 8003, with thinking available and
`--reasoning-parser qwen3` mandatory, funded by moving the incumbent to
`--gpu-memory-utilization 0.50`. It takes no voice traffic, for the reasons
recorded in the previous session: fast Qwen3.8 fabricates house state, faithful
Qwen3.8 is eighteen times slower than the incumbent.

Startup is serialised with `depends_on: vllm: service_healthy`. Both instances
pre-allocate at boot — 47.8 GiB plus 33.5 GiB plus ~11.6 GiB of non-LLM tenants
is 92.9 of 95.6 GiB — which fits only if they profile in order. On a
simultaneous cold boot they race, and the instance that must never lose is the
one carrying voice.

The measurement that justifies the sidecar also complicates it. Scored with the
production parsers on the same frame and question, under the prompt that ships
today, Qwen3.8 extracts 4/6 where the incumbent extracts 0/6. But the incumbent
is not incapable — under prompt v1 it extracts 5/6 on the identical frame. So
grounding returning nothing today is substantially a **prompt** problem, and
rewording one instruction recovers most of the capability for free, with no
second model and no VRAM. Qwen3.8 is still better, and it is better *under the
deployed prompt*, which is the robust position; but the cheap fix should be
taken first and is tracked separately per the migration doc's own decision.

Records two further findings. The vision-sidecar calls `vllm:8000` directly, so
vision and grounding traffic **bypass the metrics-sidecar entirely** — verified
by watching the proxy's request counter stay at zero across a real `/describe`.
E7's premise that the metrics-sidecar is a viable single choke point does not
hold for this traffic, so routing grounding to the new instance needs a
different attachment point than planned. The sidecar is therefore standing but
not yet wired to anything.

And the one that changes procedure: **restarting the `vllm` container kills
Home Assistant's conversation agent until HA Core is restarted.** Established
twice, deterministically. The agent returns a well-formed empty response in
0.01 s with zero LLM calls while ambient captioning keeps working, so every
health check passes with the house mute. `reload_config_entry` returns 200 and
does not fix it. This also explains the 2026-08-16 outage, which was never a
storage-write problem — vllm had restarted half an hour earlier. The restore
runbook now carries an HA Core restart as a mandatory step after any vllm
change, not as a contingency.

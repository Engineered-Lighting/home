---
title: Optional Den model admits half-second sample holds and warms up through binding lapses
target: backend
type: changed
---

For the bounded V-JEPA-LM research trial the worker now admits exactly the sampling rule `causal_slot_center_sample_hold/v2;max_hold_s=0.5`: requests must name it in a new `sampling` field, each of the 32 uniform slots may hold a frame for up to 0.5 s (was 0.4 s) and the first frame may precede the window by that hold, while the old rule, a missing field or a profile naming another rule is refused. Operator warmup no longer stops or waits when only the person-bound Den source lapses, though Ops, Qwen, attestation, resource and admission failures still stop it. The 8B profile now allows 900 s for a cold start and keeps an idle model resident for 900 s inside the unchanged 30-minute cap, so both profiles carry new adapter and profile digests and need a new admission receipt. A running warmup now stops only after 3 s without a good control read, naming the failed check as `control_unavailable:<code>` in health `warmup_reason`, and its gate waits up to 30 s for an idle Qwen instant instead of failing on a busy one; both profiles carry adapter digest 0ad398b3.

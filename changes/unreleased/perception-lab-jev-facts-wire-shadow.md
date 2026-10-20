---
title: Perception Lab builds the facts-only JEV packet (v6) in shadow beside the live v5 packet
target: backend
type: added
---

The Lab can now build packet v6 (`den-facts-6`, projection `structured-facts-8`, questions `den-activity-8-facts`). The state holds only closed-vocabulary words: the latest frame, a 60 s camera storyline, the Qwen reading and its history, per-activity support and conflicts, device facts, ambient priors and coverage. It carries no pixels, joints, scores, times, identifiers or free text. On real sessions the state is about 2.2–4.8 KB. The v5 packet was 47 KB.

The raw projected records stay local in a request sidecar. The v6 policy walks the state against a fixed schema, which refuses any number, unknown key or unlisted word. It then checks the registered question set, the per-block grants, the sidecar's provenance and its digest binding. Each sidecar record and storyline frame is re-derived from its ledger row once, newest first and at most 32 frames per request. Finally the state is recomputed from the sidecar.

Live behaviour is unchanged: v5 remains the only packet sent. With `"shadow_wire": "den-facts-6"` in the Lab JEV provisioning file, each live v5 request is followed by a local v6 build and policy check. The result is a `reasoning.shadow` ledger event with the verdict, sizes, budget level and the words-only state. The shadow has no transport and never sends.

Sending v6 needs the household grant `den-facts-6` in `structured_sources`, plus a later switch. It is refused as `wire_not_permitted` without the grant. v6 admissions record their evidence digest in a new `lab_jev_egress_facts` side table, so `lab_jev_egress` keeps its columns and a rollback still works.

New offline tools:
- `jev_replay.py` rebuilds recorded requests from the ledger read-only, checks fidelity, builds v6 and simulates the facts-key scheduler.
- `jev_replay.py --audit` runs the v6 policy checks over history.
- `jev_smoothing.py` holds the code-owned HMM and hysteresis timeline.

None of these make a JEV call.

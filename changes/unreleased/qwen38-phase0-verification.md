---
title: Verify the Qwen3.8 migration's Phase 0 assumptions against the live host
target: internal
type: added
---

Adds `tools/qwen38-phase0-archive.sh`, a read-only on-host collector that
archives the live AI-box truth into `/srv/data/eval/migration/phase0/`, and
`tools/qwen38-toolspec-audit.py`, which audits an EOC functions spec for the
`qwen3_coder` zero-argument doom loop (vllm#50989) and xgrammar-unsupported
JSON Schema features. Records the results in the migration plan and corrects
the capability roadmap where the evidence contradicted it.

What the evidence changed:

- Frigate recording is off on all five cameras and `/media/frigate` holds
  202k event snapshots and zero video files, so `has_clip` is false for
  every event. Roadmap A1 gains an unblocked snapshot-first slice; A1b, A2,
  A3, and E1's clip narrative are now blocked on decision D8, which is
  promoted from conditional to blocking with concrete options.
- ntfy is the public `ntfy.sh`, not a self-hosted instance, so the soak
  alarm and roadmap E0/E1 payload rule binds at no-names-no-images and E1
  drops its snapshot attachment.
- The live EOC subentry caps `max_function_calls_per_conversation` at 3,
  below what roadmap C1 assumes, and two live tools take zero arguments —
  now mandatory cases in the G3-pre streaming replay.
- The incumbent engine runs the FLASH_ATTN backend, not FlashInfer, and
  `VLLM_USE_DEEP_GEMM` is unset, so research finding R7's checks are
  restated against the state that has actually been live.
- The candidate checkpoint's KV geometry confirms the 64 KiB/token figure
  the long-context decision D10 rests on, computed offline from the cached
  config rather than assumed.

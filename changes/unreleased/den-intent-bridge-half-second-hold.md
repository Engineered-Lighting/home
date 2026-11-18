---
title: Den movement-intent bridge survives worker refusals and builds half-second-hold clips
target: backend
type: fixed
---

One clip the optional V-JEPA-LM worker definitively refuses (HTTP 400/422), or a request it no longer holds (404 `job_not_found`), is now dropped with a counted fixed reason instead of stopping every later clip, and no hint is ever published for it; other transport errors keep their pins and retry as before. For the bounded research trial the bridge skips frames whose capture time maps after their arrival (counted, never re-timed), does not submit a clip holding a frame that arrived after its cutoff, holds a frame for up to 0.5 s per slot (was 0.4 s) and names the rule `causal_slot_center_sample_hold/v2;max_hold_s=0.5` in every request; a hint must echo that rule. It needs the matching worker contract and profile.

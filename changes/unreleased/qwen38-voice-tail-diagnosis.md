---
title: Diagnose the voice latency tail and rule out the prompt as its lever
target: internal
type: fixed
---

The voice tail was attributed to the Home Assistant pipeline. Measuring
against the engine's own counters showed that was wrong: non-model overhead
is roughly constant at a fraction of a second, and turn duration tracks
generated tokens at a correlation of 0.981. The tail is decode, and decode
is reply length.

Asked whether any lights are on, the assistant recites every entity and
volunteers colour temperatures nobody asked for. The prompt already forbids
exactly this and is obeyed everywhere else in the same session, failing only
on live-state queries, because the rule that commands a state check is the
last thing in an 8,682-token prompt and says nothing about how to report
what it finds.

Adds `tools/patch-subentry-prompt.py`, which appends a reporting clause to
that rule in the live subentry: dry-run by default, anchored on the rule's
heading so it refuses rather than patching blind, timestamped backup,
config-entry reload without a Home Assistant restart, and a revert flag.

The clause was applied, measured, and reverted. A clean before-and-after on
identical house state moved the tail by 13% while leaving the behaviour it
targeted unchanged, which is noise rather than a fix. Two findings survive
and are worth more than the patch would have been: the tail is
house-state-dependent, measuring 3.73 seconds in later conditions against
the six seconds first recorded, so it is "slow when the house is busy"
rather than a fixed defect; and prompt instructions are not the lever, since
two independent rules already say to summarise and both lose. The likely
real lever is the tool layer, which hands the model one row per entity and
gets back exactly what it supplied.

---
title: Diagnose the voice latency tail and prepare its fix
target: internal
type: fixed
---

The voice tail was attributed to the Home Assistant pipeline. Measuring
against the engine's own counters showed that was wrong: non-model overhead
is roughly constant at a fraction of a second, and turn duration tracks
generated tokens at a correlation of 0.981. The tail is decode, and decode
is reply length.

Asked whether any lights are on, the assistant recites all twelve entities,
sometimes as a markdown bullet list read aloud by speech synthesis, and
volunteers colour temperatures nobody asked for. The prompt already forbids
exactly this, and is obeyed everywhere else in the same session — a room
listing answers in 33 characters. It fails only on live-state queries,
because the rule that commands a state check is the last thing in an
8,682-token prompt and says nothing about how to report what it finds.

Adds `tools/patch-subentry-prompt.py`, which appends a reporting clause to
that rule in the live subentry. Dry-run by default, anchored on the rule's
heading so it refuses rather than patching blind, timestamped backup,
config-entry reload without a Home Assistant restart, and a revert flag.
Applying it is an owner-run change, and it invalidates the voice baseline
the moment it lands.

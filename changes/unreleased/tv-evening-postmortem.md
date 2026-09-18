---
title: TV evening post-mortem
target: internal
type: added
---

`tools/tv-evening-postmortem.py` scores story T on a real evening from what the
house recorded: watching episodes (from `tv_playing` with the sofa's stable
occupancy today, from `binary_sensor.living_lights_tv_watching` once the belief
publisher publishes it), living-room darkness within 30 s, living-room turn-ons
while watching with the ledger's writer attribution so a person's own command is
not counted as a defect, errand route level and turn-off, activity in a vacant
zone, the evening's cost in light changes, and story S's asleep guard on the same
evening. It is read-only (GET-only Home Assistant API, a `mode=ro` ledger URI),
reports every metric with its denominator, refuses to score an evening the
recorder does not cover or that has no episode (exit 2), and writes a
never-overwritten receipt (`evening-<date>.json` and `.md`, 0600 in a 0700
directory) with the source each verdict rests on.

An adversarial review of the first build found twelve defects, four of them
paths by which a broken evening could print PASS. All twelve are fixed.

The four false passes: an evening where every bar skipped was reported as a
pass having measured nothing; a recorder that stopped partway through the
evening was scored as fully covered, because only the first row of each series
was inspected, and the lights' last known state then granted darkness for a
film nobody recorded; a zone that was ALREADY lit when an errand opened was
counted as a route pass, although nothing had raised it; and a light reading
`unavailable` was counted as off, so an integration blip could grant darkness.
A pass now requires that something was measured and that the darkness bar in
particular was, silence while an episode was open is named as a stopped
recorder, a route response has to be a raise (and a zone already brighter than
the cap is a failure, not a skip), and an indeterminate light stops the
episode being scored.

The errand denominator diverged from the simulator, which holds an errand
eligible for the oracle's AWAY_HOLD after an episode ends; the definition now
lives in `tools/lighting-sim/analyze_evening.py` as `errand_eligible` and both
tools call it. Occupancy chatter multiplied that denominator, so visits closer
together than the turn-off bar are merged into one errand, and episodes closer
together than the darkness bar into one episode, by a `coalesce` the simulator
shares; the receipt reports raw and merged counts side by side.

The route cap was back-filled from the helper's value TODAY when the recorder
had none for the evening. It is not: an errand with no recorded cap is
unscored, and `--assume-route-pct` states the assumption in the receipt. The
same rule now covers a living-room light with no recorder row, which no longer
silently grants darkness over a subset of the room; `--ignore-missing-lights`
scores it as a stated assumption.

Also: an unscorable run no longer burns the evening's receipt name, so a
corrected run can still score it, and `--rescore` writes a stamped second copy;
the classifier evidence printed beside a turn-on failure is fetched without
`significant_changes_only`, so it describes the right moment; each metric names
the population it used and the receipt separates watching minutes from watching
minutes with the sofa occupied; and an evening that predates the M2 deploy says
so in words instead of leaving the reader to infer it from the TV source.

In `tools/night-postmortem-join.py`, shared with this tool, writer attribution
tested the command id before the user id, so an automation writing through the
override channel (the good-morning energize writes one) was classified as a
person's explicit command and dropped out of the defect count. The user id is
now tested first and a command with no user behind it is
`explicit_command_automation`, which stays counted.

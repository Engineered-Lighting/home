---
title: Attempt the model cutover and roll back on spoken reasoning leakage
target: internal
type: fixed
---

The candidate model was brought up in production and rolled back about ten
minutes later. The house is on the previous model; total exposure was under
the maintenance flag with ambient captioning quiesced.

Most assertions passed, and several were better than the current model. The
cache pool came up at 497,097 tokens, confirming on real hardware the memory
geometry every long-context conclusion in the plan rests on — more headroom
than the current model despite a larger dense checkpoint. The zero-argument
tool hazard did not reproduce. Grounded bounding boxes extracted on three of
three frames where the current model extracts none, and the app's strippers
handled all of them.

The blocker was reasoning leaking into spoken output: literal think-closing
tags inside the text handed to speech synthesis, with the whole answer
generated three times. It was intermittent, which is worse than a consistent
fault, and it appeared only through the Home Assistant conversation path —
the direct engine probes were clean. A smoke check that only exercised the
engine would have shipped it.

The leading explanation is the one the plan's own trap index names: the
engine silently drops chat-template arguments it does not recognise, so the
thinking-suppression settings may never have been applied. The next attempt
should render the template offline to confirm whether those arguments are
consumed before changing anything else.

Also fixes the smoke check's reporting, which printed the first seventy
characters of the reply rather than the matched text. The leak sat further
in, so a correct alarm read as a false positive — an operator who dismisses
a real rollback signal because the evidence was cropped is worse off than
one with no check at all.

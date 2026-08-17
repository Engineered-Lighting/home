---
title: Route model thinking by whether a request carries tools
target: backend
type: added
---

Adds a written, tested patch for the metrics sidecar that decides per
request whether the model should reason, and the tests that pin its
decision table. Not built and not deployed; the sidecar is image-baked and
on the never-deploy list, so it ships as an off-host build.

The new model has two configurations and neither works alone. With thinking
suppressed it emits closing think tags into spoken text about fifteen
percent of the time; with thinking enabled that stops completely but
background camera captions go from a fifth of a second to over four,
against a budget of one and a half.

Measurement showed the leak is not caused by suppressing thinking. It is
caused by suppressing thinking while a tool catalogue is attached — with no
tools it never leaked across twenty samples. That splits cleanly along a
line the request already draws: captions, grounded looks and labelling
carry no tools and never need reasoning, while the voice path carries
twenty-three tools and is the only place reasoning helps.

The proxy is the right home for the rule because every caller already passes
through it, so one change covers the vision sidecar, the labeller and the
conversation component without touching three separate never-deploy
surfaces. The decision needs no judgement from the model and adds no extra
round trip.

Caller intent always wins: a request that sets its own template arguments
passes through untouched, which keeps the leak reproducer honest. An
environment switch disables the routing entirely without a rebuild. On the
current model the patch is a no-op, so it can ship independently rather than
becoming one more variable inside a cutover.

It does not fix voice latency, which keeps thinking on by design and has not
yet been measured in that configuration.

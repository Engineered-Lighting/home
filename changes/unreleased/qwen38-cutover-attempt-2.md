---
title: Make the cutover smoke sample enough to catch an intermittent fault
target: internal
type: fixed
---

A second cutover attempt was made with a smaller three-line change, ruling
out the extra engine flag added the first time. The smoke check passed and
the candidate was still leaking: hammering the voice path afterwards found
reasoning tags and duplicated answers on three of twenty turns. Rolled back;
the house is on the previous model.

The check itself was the more serious problem. Its voice leg sampled twice
against a fault that occurs about fifteen percent of the time, which misses
it roughly seventy percent of the time — so it returned a confident pass for
a configuration that could not ship. A zero-tolerance gate has to sample
enough to see the failure it exists to catch. The leg now repeats each
utterance, reports a rate rather than a per-turn verdict, and also flags
duplicated answers as the same defect in different clothing.

Two hypotheses are now dead. The chat template does consume the
thinking-suppression argument, verified offline against the checkpoint, so
it is not being silently dropped. And removing the extra engine flag did not
fix the leak, so that was a contributing mistake rather than the cause. What
remains is routing the model's thinking into a separate response field
instead of the spoken text, which the plan already describes as its own
step.

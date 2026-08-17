---
title: Retract the premature recommendation to abandon the candidate model
target: internal
type: fixed
---

An earlier entry recommended abandoning the candidate on the grounds that
reasoning-for-correctness and reasoning-off-for-latency are the same control
in opposite directions. That conclusion was drawn after testing one of the
three levers this plan itself names, and it was wrong.

Speculative decoding was never tried, despite the checkpoint shipping the
head for it and despite decode being exactly the axis that failed. Prompt
shrink was never tried. And a cheaper option was dismissed without a test:
the leak is a literal delimiter in the response text, and the sidecar
already proxies every completion, so recovering the final segment
downstream does what a reasoning parser does upstream — and works precisely
where the parser cannot, because the template pre-fills a closed block that
leaves the parser nothing to match.

Validated offline against the real leaked outputs captured during the two
failed attempts: three of three recovered to clean, non-duplicated text,
and none of three already-clean outputs was damaged. That opens a
configuration nobody measured — reasoning off everywhere, which is the fast
path, with downstream recovery of the fraction that leak.

The latency measurements stand; only the conclusion drawn from them was
premature. They rule out the expensive branch, not the model. The candidate
remains better than the current one wherever reasoning is not required.

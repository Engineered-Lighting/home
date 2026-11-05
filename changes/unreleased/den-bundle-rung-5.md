---
title: Den person bundles and router v0 (rung 5)
target: internal
type: added
---

`tools/den-bundle` adds rung 5 of the live architecture. It assembles one
person's evidence from track and observation records into a `person_bundle`
for the Qwen interpreter: verbatim association-gated keypoints, a padded crop
request, `association_safe` false through any ambiguity, and stale or absent
sources flagged rather than waited for. Router v0 shares requests round-robin
across tracks within today's caps (one every 4 s, 15 a minute, one in flight,
a 30 s back-off), stamping its rule's version and sha256 on every request. A
gate checker, synthetic fixtures and tests run on a fake clock and a fake
sender. Nothing calls LA or any device, and a scored run refuses while five
open numbers (window, staleness gap, crop pad and floor, request timeout) await
the owner.

---
title: Perception Lab recipes vary one lever over a shared evidence baseline, and JEV gets ambient context
target: backend
type: added
---

Every Lab recipe now declares a shared evidence baseline and exactly one lever. The catalog is refused at load if a recipe's two arms differ anywhere outside that lever. Evidence families outside the lever reach both arms with identical grants. All 18 recipe ids stay the same, but each recipe digest changes because the digest covers the lever. Activations record the resolved family grants, the lever and the baseline digest.

Each JEV request now carries one shared context block. It lists every evidence family's coverage (supplied, stale, withheld by the experiment, not in the baseline, or not provisioned) and a `shared_context_digest`, which is equal for both arms in the same tick. When the household policy grants `ambient-context-1` and the JEV config sets `site_timezone`, the block also carries ambient priors: part of day, weekday or weekend, an hour label, daylight from HA `sun.sun`, and the HA weather condition. JEV is told these are priors, not observations of activity. They never make a packet current.

New allowlisted HA facts:

- `den_audio_playback`
- `den_light`
- `computer_input_recency`, from the derived rule `computer_active_v2`, alongside `computer_active_v1`
- `owner_home`: home or away only, bound from person entities, and sent only under a separate `owner-presence-1` grant

A latest-only arm now keeps the newest snapshot from each HA source, so a second HA source cannot push another out.

The versions are now `structured-semantic-5`, `den-semantics-4` and `den-activity-6-semantic`. The graph shows an ambient context source and each request's shared-context digest, the setup catalog and graph show the recipe lever, and the release allowlist ships `ambient_context.py`.

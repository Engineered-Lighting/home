---
title: Offline den-core replay spine with den_lighting as the frozen baseline
target: internal
type: added
---

Adds `tools/den-core`, an offline, standard-library replay path for the den's
decision foundations. It defines one event format (den-event-1), file adapters
and a synthetic evening, a deterministic runner that logs every step, and a
check that a logged run replays byte for byte. den_lighting's own `policy.py`
is loaded unchanged as the baseline policy. It runs offline only, sends no
light commands and changes no service.

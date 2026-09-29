---
title: Lighting listener uses its own database login file
target: internal
type: fixed
---

The generated lighting listener profile now reads the lighting role's login
from `lighting_database_url`, leaving `database_url` for Core's own API login
that every Core listener requires. The runbook lists the full staged set.

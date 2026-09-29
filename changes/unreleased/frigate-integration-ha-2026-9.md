---
title: Document the Frigate integration version needed for Home Assistant 2026.9
target: docs
type: added
---

The stack upgrade runbook now says the Frigate custom integration must be 5.15.5
or newer, with 5.15.6 preferred, before a home runs Home Assistant 2026.9. Older
versions leave every camera except the first unloaded. The runbook also covers
the symptom, how to check the installed code, and the coordinated update and
restart path.

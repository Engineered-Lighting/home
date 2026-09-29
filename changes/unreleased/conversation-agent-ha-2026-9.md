---
title: Keep the Home Assistant conversation agent loading on Home Assistant 2026.9
target: backend
type: fixed
---

Home Assistant 2026.9 replaced the voluptuous-openapi library with probatio, so
the Extended OpenAI Conversation agent failed to load with "No module named
'voluptuous_openapi'" after the Los Angeles upgrade that restores the map
tiles. The agent now uses probatio's converter and still falls back to
voluptuous-openapi on older Home Assistant versions.

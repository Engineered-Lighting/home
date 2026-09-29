---
title: Show the household in the Home People map again
target: web
type: fixed
---

Since the Home Agent moved to its own address, the Home People map could not
read the household and showed "identity store not ready". Home now reads it
through a small, invisible, read-only People bridge served by the Home Agent,
using the owner's Agent sign-in. The bridge only reads the household and
relationships, and only the provisioned Home origin may embed it. If the Agent
sign-in has expired, People shows the existing sign-in prompt.

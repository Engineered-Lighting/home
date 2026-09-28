---
title: Prepare isolated shared-preference runtime deployment
target: backend
type: added
---

Add an opt-in deployment configuration for the two homes' private preference and
identity services and the linking coordinator. Each requires reviewed addresses,
isolated secret and journal directories, and explicit CPU, RAM and process limits.
This configuration does not migrate databases, provision grants or enable sharing.

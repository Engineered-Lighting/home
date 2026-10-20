---
title: Restrict Lab JEV requests to structured den observations
target: backend
type: fixed
---

Project JEV inputs into bounded object and pose fields before provider authorization, dropping nested identity, media, annotation, and arbitrary attribute fields. HA and room facts remain excluded until their dedicated allowlists are provisioned; this change does not enable cloud inference.

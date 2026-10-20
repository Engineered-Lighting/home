---
title: Validate repeated Victoria captions using the commissioned clock
target: backend
type: added
---

Add a bounded commissioning command that reuses the existing Victoria camera,
LA model and private Home caption sink with Chrony freshness evidence. Preserve
per-request receipts before inference and stop on failed or uncertain delivery;
this command does not enable unattended production admission.

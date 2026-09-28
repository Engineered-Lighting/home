---
title: Prepare narrowly scoped shared preference authority checks
target: backend
type: added
---

Add a dormant database function that resolves linked preference owners and checks both homes' current grants without granting the application access to shared identity tables. Deployment still requires production-role validation and separate runtime activation.

Constrain newly enabled API provenance writes to owned preference confirmations and their matching facts or corrections. Existing ingestion writers retain their permissions; application write permission remains a separate activation step.

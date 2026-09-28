---
title: Prepare narrowly scoped shared preference authority checks
target: backend
type: added
---

Add a dormant database function that resolves linked preference owners and checks both homes' current grants without granting the application access to shared identity tables. Deployment still requires production-role validation and separate runtime activation.

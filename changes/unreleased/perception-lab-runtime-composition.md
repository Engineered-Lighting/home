---
title: Compose Lab workers against the app-owned recording ledger
target: backend
type: added
---

Add a trusted runtime factory and Victoria composition helper for recording,
source ingestion, paired perception, JEV and saved analysis. Construction remains
inert; clean shutdown drains workers before releasing the shared store.

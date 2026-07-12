---
title: Make the Home Agent Phase 2 gate observable
target: backend
type: added
---

Add an authenticated, read-only Phase 2 readiness contract that reports the
seven-day record-only window and the 500-envelope or three-controlled-journey
thresholds. Only reviewed location transitions advance the envelope path;
conversation metadata, snapshots, gaps, duplicates, and quarantine records do
not. Controlled journeys require explicit operator selection and are validated
for prior location consent, continuous device-tracker evidence, stable dwell,
departure, and half-open gap-free coverage without exposing location content.

---
title: Make the Home Agent Phase 2 gate observable
target: backend
type: added
---

Add an authenticated, read-only Phase 2 readiness contract that reports the
seven-day record-only window and the required 500 qualifying redacted-envelope
threshold. Only reviewed location transitions advance the envelope path;
conversation metadata, snapshots, gaps, duplicates, and quarantine records do
not. Controlled journeys remain informational replay evidence and never replace
the 500-envelope gate. When reviewed separately, they require explicit operator
selection and are validated for prior location consent, continuous
device-tracker evidence, stable dwell, departure, and half-open gap-free
coverage without exposing location content.

---
title: Collect private LA pose and depth candidates before qualification
target: backend
type: added
---

Add a bounded owner-local collection command that reads an existing Perception
Lab session through its authenticated fixed TLS routes. It retains every matching
LA output attempt, exact paired RGB/NPZ/calibration bytes and original clocks,
including missing, refused and stale predictions. Nominal camera-frame joint
samples keep unknown error bounds and never become person identity, room anatomy,
protection, engagement or lighting authority. Ctrl+C stops only this collector.

From the checkout root, run
`python -B -m tools.den-live-runtime.lab_assessment_collect` to inspect preparation
requirements; add `--collect-existing-session --session-id SESSION --token-file
PRIVATE_TOKEN --ca-file CA --output-file PRIVATE_RESULTS.jsonl` to attach to an
already recording Lab session. Use `--read-results PRIVATE_RESULTS.jsonl` for its
computed availability/missingness report. Private JSONL includes raw household
media and must remain owner-local. No second Store, model or camera is opened.
Reads check remaining collector admission/elapsed budget and Stop between bounded
chunks. HTTP opening and headers retain the existing socket inactivity timeout;
this collector does not promise hard whole-call cancellation or wall-time
termination. Runtime supervision owns the hard process/session cap. Client UTC
freshness arithmetic is explicitly unassessed against controller clocks and
does not establish qualified lease or service availability.

This is an N21 diagnostic data collector. It does not satisfy the existing
room-envelope assessment schema, measure independent camera/estimator error,
qualify hidden anatomy, or complete a real lighting acceptance gate. Existing
producer versions, public schemas and operational refusal rules are unchanged.
The controlled factory fixtures supply explicit fictional raw EEPROM projection
data and an authored managed-worker lifecycle receipt; they use no commissioned
depth-clock or spatial-error replacement. The client matches the incumbent
activation's four model identity fields to the complete original ready profile.

The nominal collector keeps its private unavailable-part names and precise UTC
conversion local. It does not import Home capture, acquisition, Journal or desk
authority merely to obtain constants or a clock helper. Existing Lab pair,
metadata and numerical projection dependencies remain required; this does not
install them or qualify their measurements.

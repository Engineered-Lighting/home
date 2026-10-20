---
title: Retain existing camera originals for private Den assessment
target: backend
type: added
---

Add a bounded optional assessment tap after the existing Lab RGB/model offer.
It records exact matches and misses from the camera owner's current numerical
and calibration endpoints without changing capture, model ownership or granting
geometry, identity or lighting authority.

An explicit `existing-api-assessment-1` private collection mode waits for capture
terminals, retains exact late-acquired NPZ/calibration associations and in-window
misses, and reports missing terminals or unknown window membership as incomplete.
The previous three-role mode remains available; neither grants operational
geometry.

Private retention preserves original bytes and first receipt times, refuses
changed retries, stops further reads after failure or exhaustion, and reports
uncertain storage acknowledgments as incomplete. Full write-closure capacity
is checked before decoding, with the existing Store enforcing each write;
multi-object retention is not an atomic transaction. Target activation and real
measurement remain separate work. Controlled Windows tests stub only directory
fsync; real data-file fsync remains exercised, and Linux namespace durability is
required before a retained acknowledgment.

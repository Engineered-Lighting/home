---
title: Add the dormant E4 legacy identity writer fence
target: backend
type: added
---

Home Agent now includes a dormant offline generation-2 SQLite ceremony that
scrubs and compacts legacy audit content, proves schema/integrity/WAL
durability, and makes every legacy semantic table query-only pending cutover.
An atomic private witness outside SQLite, when preserved separately during
database restores, makes an old database image fail closed instead of
reopening migration mode. Exact metadata and case-fold collision checks also
fail closed.
It does not promote Core authority. Legacy People reads are HA-admin-only,
uncached, and reverified when the fenced files change. Operational Frigate
identifiers live in a separate collision-checked database, respect legacy
privacy vetoes, and remain disabled after the physical fence until a current
Core privacy policy is available. Database/witness hardlinks and operational
path substitution fail closed through an exact random instance marker checked
on both the opened handle and a fresh visible-path reader. Privacy changes and
periodic Frigate polling purge opaque mappings that are no longer permitted,
even while network synchronization is disabled.
The fenced plaintext identity export is disabled, direct browser face-crop
URLs and thumbnails are removed, and the remaining Frigate metadata read uses
one exact authenticated no-store route. It validates and bounds the untrusted
payload, reduces crop identifiers to per-bucket counts, and filters every
bucket through current privacy policy before returning it. People data is
tagged with the current open/credential/identity scope before render, while
loads and mutations use the same scope so a previous principal cannot appear
or trigger a late refresh.

---
title: Run the Home Agent Core and operator unit tests in CI
target: internal
type: fixed
---

Ninety-five Home Agent Core and operator test files ran in no CI job; the E1
PostgreSQL gate runs only the files it names. A new hosted `home agent core and
operator unit tests` workflow now discovers both test directories, installs
Core's hash-pinned development lock, and runs on every change to the Home Agent
stack, app, and web gateway. Six stale assertions are retargeted at their real
boundaries: function ownership and overloads in the Alembic kernel checks,
signature declarations, the 32-character revision limit, the 0047 owner route
capabilities, and the restore drill's maintenance receipt. Eighteen Core files
with PostgreSQL-only assertions are listed in the gate contract until the E1
gate runs them.

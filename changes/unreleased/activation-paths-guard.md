---
title: Guard the Home Agent activation source mechanically
target: internal
type: added
---

The Home Agent Phase 3 activation ceremony pins its source: the host
verifies a clean diff against an accepted commit over a 67-path list. A
single byte from an unrelated branch reaching main under any of those paths
breaks that pin and stalls a live ceremony that has already failed
fail-closed once. That is too sharp an edge to leave to memory.

Adds `tools/check-activation-paths.py`, which parses the path list straight
from the ceremony's own module rather than copying it — a hardcoded
allowlist would go stale the moment the other workstream adds a path, and a
stale list that still reports success is worse than no check. It runs
against a branch, a range, or staged changes, and installs as a pre-commit
hook.

It also reports files matching the gate workflow's push filter. Those are
not forbidden; they are only unsafe to merge while a pin freeze is
announced, so they warn by default and become fatal with an explicit flag.

Verified in all three directions: this branch's 52 files are clean, a
historical commit that did touch pinned paths is blocked, and the freeze
escalation fires.

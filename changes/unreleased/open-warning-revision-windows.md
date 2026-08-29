---
title: Reduce the Per-Migration Edit in the Grant Script
target: web
type: changed
---

Four checks that only report, never block, no longer need editing every time the database changes. They previously named each allowed database version one at a time, so adding a migration meant hand-editing every list — and missing one broke the deploy at the version the database had reached rather than at the change responsible.

Those four now say "this version or later". Every check that can still stop a deploy keeps its explicit list, so a new version is still named deliberately before those contracts admit it, and none of the permission rules changed.

This halves the work rather than removing it: ten checks that can still fail a deploy remain explicit, by choice.

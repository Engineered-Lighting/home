---
title: Source admission rejects untested verifier changes
target: backend
type: fixed
---

Phase 3 source admission now permits a hosted result to update only its commit
and workflow-run pins while continuing to reject every untested executable
change in the activation source pack.

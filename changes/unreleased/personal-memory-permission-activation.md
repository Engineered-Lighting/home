---
title: Activate only the reviewed preference API permissions
target: backend
type: added
---

Add a bounded operator command that verifies the exact preference functions and
lineage guard before activating their API permissions. Deactivation revokes only
those permissions, preserving existing identity grants and stored data. Source
sharing grants and service deployment remain separate steps.

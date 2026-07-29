---
title: Restore drills can verify live encrypted backups
target: backend
type: changed
---

The guarded local restore drill now stages an encrypted repository snapshot
behind the shared writer lock, allowing production PostgreSQL to remain online
while recovery is verified in an isolated, networkless workspace.

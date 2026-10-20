---
title: Require bound terminal proof for live reasoning recovery
target: backend
type: added
---

Add an explicit server-provisioned reconciliation hook for uncertain reasoning
requests. Preserve reservations without matching terminal proof, commit recovery
atomically, and prevent late answers or automatic redispatch during recovery.

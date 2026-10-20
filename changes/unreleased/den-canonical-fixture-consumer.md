---
title: Retain exact canonical fixture commands in offline Den controls
target: backend
type: added
---

Add an explicitly pinned four-fixture consumer for the canonical fake-I/O capture
ABI, retaining original CAN and lighting-driver operations before validation or
publication. Preserve historical recordings and fail closed on incomplete
streams; this software successor does not enable physical fixture operation.

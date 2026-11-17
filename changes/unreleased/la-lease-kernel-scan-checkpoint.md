---
title: LA worker lease kernel-fault scan catches up from a stale checkpoint
target: backend
type: fixed
---

The LA worker lease preflight now reads the kernel journal in chunks of at most 6 hours and records each clean chunk at once. Before, it read everything since its checkpoint in one scan bounded to 60 s. Once the checkpoint fell weeks behind, that scan could never finish, and every lease failed as `preflight_rejected` until the checkpoint was advanced by hand. Now a stale checkpoint catches up over a few leases. A scan that runs out of time fails the lease as `preflight_kernel_scan_incomplete`. A fault anywhere still fails it as `preflight_kernel_fault` and is never recorded as clean. Takes effect when the LA lifecycle supervisor is redeployed.

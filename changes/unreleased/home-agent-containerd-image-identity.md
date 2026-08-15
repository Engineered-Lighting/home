---
title: Verify imported Home Agent images across Docker stores
target: backend
type: fixed
---

Home Agent deployment now binds signed image config digests to the immutable
IDs exposed by both classic and containerd-backed Docker image stores before
moving deployment tags or starting reviewed services.

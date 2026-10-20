---
title: Build bounded portable Lab session archives
target: backend
type: added
---

Add a streaming archive builder for session metadata, reviewed labels and original
registered media, with hash checks and explicit completion. Interrupted, revoked,
deleted or changed snapshots cannot silently finish; download UI integration is
still pending.

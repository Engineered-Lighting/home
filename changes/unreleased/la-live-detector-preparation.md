---
title: Prepare bounded LA detector ingress for the paired home trial
target: backend
type: added
---

Reuse the existing authenticated remote worker protocol and verified LA YOLO26s
runtime for a 120-second, single-request-at-a-time trial. The factory checks the
dedicated LA host, enforced process limits, pinned model and runtime, scoped
Victoria grant and durable execution ledger before loading. Endpoint provisioning
and coordinated live acceptance remain separate from this preparation.

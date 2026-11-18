---
title: Bind optional-model queue admission to the real Qwen instance
target: backend
type: fixed
---

Optional model admission now requires a fresh root-observed queue receipt bound to the approved local Qwen container, process and private network route. The former loopback metrics source is rejected because that port serves sidecar telemetry; models remain unadmitted until source identity, permissions and resources are qualified.

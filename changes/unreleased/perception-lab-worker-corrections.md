---
title: Perception Lab worker readiness, provenance and mask-budget corrections
target: backend
type: fixed
---

The LA lifecycle supervisor now requires an exact-profile readiness reply and refuses before launch (`preflight_worker_readiness`) a staged worker that does not declare it, so the worker release must be staged before or with the supervisor; it also stops probing the worker once a lease is ready, records known preflight gates as specific lease reasons, and gains an operator-only CLI that fails an expired lease whose launch was never proven, as `reconciled_worker_absent`, only after recording that no worker unit is present. RTMW3D now receives unconverted BGR as the installed route does and reports its own preprocessing identity and applied ONNX Runtime threads, and detector/pose composites take their preprocessing identity from the pose worker, carry their model version and preprocessing identity in each result, and keep the deployed RTMPose composite digest unchanged. Dense YOLO26s-seg masks are quantized to 0.1 px or dropped with boxes kept instead of turning a completed run into an indeterminate one, and stored scoreless HumanArt/YOLOX person boxes can supply shared pose crops.

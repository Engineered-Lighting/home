---
title: Preserve CPU mask boundaries during optional CUDA inference
target: backend
type: fixed
---

The optional CUDA mask path preserves the pinned CPU mask boundaries: Ultralytics 8.4 rounds small-mask crop boxes on CPU but uses fractional boundaries on CUDA. A local predictor overrides only result construction, delegating with CPU prediction and prototype tensors through `super().construct_result(pred.cpu(), img, orig_img, img_path, proto.cpu() if proto is not None else None)`. The network runs in FP32 with TF32 disabled. Mask timing includes tensor transfer, reconstruction and polygon extraction.

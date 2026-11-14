---
title: Run den-live's detector on LA's GPU, with the CPU as its fallback
target: internal
type: changed
---

den-live's detector (YOLO26s and RTMPose-m) runs on LA's GPU when CUDA can, with the same pinned weights at FP32.
- On a host with an NVIDIA GPU, the installer builds the detector's venv from the new `requirements-cuda.lock`: CUDA
  12.8 torch (PyTorch's exact wheels, each held to its sha256), `onnxruntime-gpu` and CUDA's runtime wheels, at the
  versions LA's September detector trial resolved and ran. It's about 4.6 GB to download the first time (about 4.2 GB
  more than the CPU build) and 7 to 9 GB on disk. It checks for 12 GB free under `/opt` first. A host without one keeps
  the CPU build.
- The detector uses the GPU only with 3 GB of it free, and caps torch's allocator at 2 GB, because the GPU is vLLM's
  first. Each model falls back to the CPU on its own if CUDA fails to load or warm it up. The start-up line and the
  installer say where each runs.
- The unit sees the NVIDIA device nodes and no other device (`PrivateDevices=no`, `DevicePolicy=closed`,
  `DeviceAllow=` the nvidia nodes), and its memory limit goes from 3 GB to 4 GB: the detector's process holds about
  1.7 GB on the GPU.
- den-detect compares distribution names as PEP 503 normalises them, so pip's and uv's spellings agree.

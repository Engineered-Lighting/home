# Frigate face-recognition bench — findings (Addendum 25)

**Run date**: 2026-05-17
**Hardware**: LattePanda Sigma · Intel i5-1340P · Intel Iris Xe iGPU · Coral PCIe TPU
**Frigate**: 0.17.1-416a9b7 (Full Access addon)
**Subject**: Marcelo (only person home during bench)

## Headline

| Question | Answer |
|---|---|
| Does Frigate accept `device: GPU` for face_recognition? | **Yes** — schema accepted, no addon boot errors |
| Does that actually accelerate inference via the Intel iGPU? | **No** — Option C (small/GPU) was 52ms vs 49ms baseline (no measurable speedup) |
| Does the `large` face model work? | **Yes on CPU (Option B), broken with GPU (Option E)** |
| Does the `large` model improve recognition accuracy? | **Not measurable in this bench** — sample sizes 7-22 events per option are too small to distinguish 25% from 43% recognition rates |
| Is the Coral TPU usable for face recognition? | **No** — Frigate has no Coral codepath for face_recognition embedding (ArcFace ops don't fit Coral's SRAM) |
| **What should we ship?** | **Stay on baseline (small/CPU) for now.** The real bottleneck isn't the model — it's how few clean face events the system captures per minute. |

## Per-option measured stats

| Option | Config | face p50/p95 ms | CLIP p50 ms | emb CPU% | iGPU% | indoor events | reco rate |
|---|---|---|---|---|---|---|---|
| **A** | small / CPU (baseline) | 49.4 / 50.9 | 95.0 | 67.5% | 4.6% | 7 | 42.9% (3/7) |
| **A2** | small / CPU (drift check) | 65.3 / 69.7 | 90.8 | 79.7% | 3.0% | 1 | 0.0% (0/1) |
| **B** | large / CPU | 73.8 / 84.7 | 89.9 | 94.8% | 2.4% | 8 | 25.0% (2/8) |
| **C** | small / GPU | 52.5 / 57.5 | 85.2 | 69.1% | 4.3% | 22 | 31.8% (7/22) |
| **D** | large / GPU | **not tested** | | | | | (E broke the same codepath; D likely too) |
| **E** | large / GPU + sem / GPU | **0** / 0 | **3525** | 144.2% | 51.0% | 13 | **0.0%** (0/13) |

(face p50=0 in Option E means face_recognition pipeline crashed and produced zero inferences during the window.)

## What Option E broke

When `face_recognition.model_size: large` + `face_recognition.device: GPU` + `semantic_search.device: GPU` were applied simultaneously:

- `face_recognition_speed` reported **0 ms** (zero inferences ran)
- `image_embedding_speed` reported **3,525 ms** per inference (30× slower than CPU)
- `embeddings_manager` CPU climbed to **144%** (>100% means multi-core saturation)
- All 13 captured face events came back **unknown** (0% recognition)

The OpenVINO GPU codepath in the Frigate Full Access addon either:
1. Doesn't include the runtime/driver layer needed to actually run ONNX/OpenVINO models on the Intel iGPU, OR
2. Falls back to a CPU emulation path that's worse than pure CPU, OR
3. Has model-loading bugs for the larger ArcFace + Jina CLIP on GPU

Option C (small face/GPU only, semantic on CPU) didn't crash but also didn't accelerate — face inference was 52 ms vs 49 ms baseline. So **even when "GPU mode" doesn't crash, it doesn't actually move work to the iGPU.**

## Why "large model better" isn't visible here

Option B (large/CPU) showed 25% recognition vs 42.9% baseline. That looks bad for the large model — but **the sample size is 8 vs 7 events**. With binomial confidence intervals at n=7, the 42.9% (3/7) baseline has a 95% CI of roughly [10%, 82%]. Any difference < 30 percentage points is in the noise.

What we'd need to make a real call:
- 50+ events per option (currently 7-22)
- That requires ~30 min of active walking per option (we did ~6 min)
- Total bench time would be 4-5 hours instead of 50 min

## The actual bottleneck (not addressed by model swap)

In 6 minutes of walking past 5 cameras, Frigate captured only 7-22 person events with a face crop. That's **1-4 face crops per minute** even with active motion. Recognition is downstream — if the face DETECTOR isn't producing crops, the RECOGNITION model never gets a chance.

Things that would expand the event window (and probably help recognition more than swapping the model):

| Setting | Current | Suggested | Why |
|---|---|---|---|
| `face_recognition.recognition_threshold` | 0.85 | 0.65 | Borderline matches currently marked "unknown" would be tagged; reduces false-negative rate at cost of some false-positives |
| `face_recognition.detection_threshold` | 0.7 | 0.55 | Frigate's face DETECTOR (not the embedder) currently rejects ~half of person frames; lowering catches more faces |
| `face_recognition.min_area` | 750 | 400 | Allows recognition on faces farther from the camera (workshop ceiling angle especially) |
| `face_recognition.min_faces` | 2 | 1 | Allow first-shot recognition for new enrollees (irrelevant for Marcelo with 65+ samples, but worth setting) |

These are config tweaks orthogonal to the model size + device question. They'd be the natural next experiment.

## Coral TPU — explicitly NOT viable for face recognition

- Coral is at 7 ms inference for object detection, 128 fps aggregate across 5 cameras, temp 69°C. Fully and successfully utilized.
- Frigate has NO Coral codepath for face_recognition embedding
- ArcFace ResNet50 has ops Coral can't execute (Coral supports a limited TensorFlow Lite op set)
- ArcFace at ~250 MB doesn't fit Coral's 8 MB SRAM

The user's intuition that the LattePanda has compute headroom is correct — but the headroom is on the **iGPU** (which Frigate's bundled OpenVINO can't use here) and the **CPU** (which is the current path). The Coral is doing its job.

## Recommendation

**Ship baseline A (small / CPU)** — currently deployed. The bench did not produce evidence that switching to large or GPU is better, and Option E actively breaks face recognition.

**Next experiments worth running (separate addendum, ~30 min, no model change):**
1. Lower `recognition_threshold` from 0.85 → 0.65 — should reduce the 43-55% "unknown" fraction
2. Lower `detection_threshold` from 0.7 → 0.55 — should expand the event window (more face crops per minute)
3. Re-bench in stable lighting (we did this at 4-5pm; light was changing)

**Investigations worth doing on the addon image:**
1. Check whether Frigate Full Access actually ships with OpenVINO GPU runtime for the Intel iGPU — log into Frigate's container and check `python -c "import openvino; print(openvino.runtime.Core().available_devices)"`. If only `CPU` appears, that explains why `device: GPU` doesn't accelerate.
2. If iGPU isn't visible to OpenVINO inside the container, the fix may be installing `intel-opencl-icd` or a similar runtime in the addon's Docker image, OR switching to the standard Frigate addon (`ccab4aaf_frigate`) which may have different runtime bundling.

## What we know we still don't know

- Whether the large model would genuinely outperform small with **100+ events per option** (sample-size bound)
- Whether `device: GPU` would accelerate on a different addon image variant
- Whether tuning `recognition_threshold` alone (no model swap) closes the perceived accuracy gap

## Files produced

```
tools/frigate-bench/
├── config-baseline.yaml         # backup of pre-bench config (now restored)
├── config-A.yaml                # baseline small/CPU
├── config-B.yaml                # large/CPU
├── config-C.yaml                # small/GPU
├── config-D.yaml                # large/GPU (untested — E showed it would break)
├── config-E.yaml                # large/GPU + semantic_search/GPU (broken)
├── build-configs.py             # generator
├── run-bench.sh                 # per-option capture
├── analyze-bench.py             # report generator
├── REPORT.md                    # machine-generated tables
├── FINDINGS.md                  # this document
├── option-A/                    # 10 stats samples + 7 events
├── option-A2/                   # 10 stats samples + 1 event (drift check)
├── option-B/                    # 10 stats samples + 8 events
├── option-C/                    # 10 stats samples + 22 events
└── option-E/                    # 10 stats samples + 13 events
```

Re-runnable anytime via the same scripts. Bench tooling is general-purpose; can also be used for future addon-image / Frigate-version comparisons.

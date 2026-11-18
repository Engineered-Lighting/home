# Optional Den model worker

This subsystem is an isolated, offline V-JEPA-LM worker and an asynchronous
loopback interface. It supplies a semantic intent prior; it produces no metric
trajectory, confidence probability, spotlight target, or physical command.
Existing Qwen and Den environments are not changed.

The selected candidate is `kkt94/Llama-V-JEPA-LM-Qwen3-8B` at
`d059602a9cdb2d198301f103aa0c7362ff943cb7`. The 4B benchmark control is
`kkt94/Llama-V-JEPA-LM-Qwen3-4B` at
`da5e0c682b1df2877dce25082bfa322c94ee0970`. Both profiles remain
**not admitted** without a complete reviewed local provisioning receipt.
Known publisher weight/code/tokenizer hashes are in `profiles/`; unknown
file hashes are explicitly null. No artifacts or environments are fetched by
this code. Publisher README terms state research/non-commercial use despite
the Apache metadata tag; operator acceptance is required for this trial.
The profile also pins this adapter's executable source/requirements closure
as `adapter_bundle_sha256`. Editing that closure requires a new reviewed pin
and provisioning receipt; `adapter_bundle_digest()` computes its candidate hash.

## Interface

The stdlib host starts with `python -I -B tools/den-models/serve.py --config
/absolute/operator.json`. It imports no torch, transformers, numpy, or image
decoder until an admitted child load. The real worker requires its own Python
3.12 virtual environment and the direct versions in `requirements.lock`.
That file does not claim a complete hashed wheel closure: provisioning still
must pin and review transitive wheels/CUDA builds. No installs occur at launch.

`--check` prints bounded admission/health metadata without loading a model.
Optional operator `--warmup` performs one frameless synthetic tuple inference
before live jobs, discards its output, and emits no hint. Cold full-file
verification/loading has a separate startup deadline; live hints must expire
within ten seconds of their source cutoff (the Den bridge uses two seconds).
The first live clip can expire while loading if warmup was not completed.

Every endpoint requires `Authorization: Bearer <64 lowercase hex characters>`
read from an operator-owned mode-0600 local file. The server binds only
`127.0.0.1`; neither paths nor arbitrary remote URLs are accepted through HTTP.
Transport/body/socket/result-store sizes and scheduling concurrency are bounded.

* `POST /v1/intent-jobs`: returns a job status immediately, HTTP 202.
* `GET /v1/intent-jobs/<request_id>`: polls status and optional `hint`.
* `DELETE /v1/intent-jobs/<request_id>`: cancels without claiming native work
  has already stopped. The job stays `stopping` until terminal proof.
* `GET /healthz` and `GET /metrics`: cached bounded metadata, no filesystem,
  weight, runtime RECORD scan, or driver probe on the health path; no frame contents,
  credentials, prompts, generated text, or per-person metric labels.

`den_models.client.Client` is the small loopback client. Call it off the
geometry loop. `Runtime` can also be embedded with an injected control reader,
GPU/host probes, execution broker, and residency-accounting callback.

The exact request keys and `IntentHint` dataclass are in
`den_models/contracts.py`. The request contains exactly 32 causal sample
slots, a fixed square original-RGB ROI, the profile digest, and complete
`FrameRef` source dictionaries. Every source generation, track association,
transform, scan, and calibration revision is bound and echoed. Numeric frame
and association counters are canonical decimal strings in the envelope.
`source_generation` is SHA256 of compact JSON `[service_run_id,camera_epoch]`
with an integer epoch. All times use the shared host monotonic clock.

Four-second walking and eight-second reference windows use uniform slot
centers. Each slot uses the newest source frame captured at or before that
slot; repeated 5 Hz source frames are explicitly marked and preserved.
Source arrival must also be at or before the clip cutoff. The full track
bounding box must remain inside the one fixed ROI for every frame. Do not
recenter each frame: it changes apparent motion. Crops cannot restore detail
missing from the 640x360 source.

`store_rgb_blob(root,rgb_bytes,width,height)` creates canonical content-addressed
P6 RGB PPM files and returns `blob_ref`/`sha256`. This is a CPU-only helper for
the bridge. The operator controls one frame root. Requests cannot escape it,
follow symlinks, switch channels, or supply executable decoders. The caller
must retain all submitted blobs until the job is terminal; expiry/cancellation
does not alone prove that the child has finished reading them.

The real backend crops that same ROI from each source frame, resizes to 384
with antialiased bicubic interpolation, clamps, applies ImageNet normalization,
and supplies the publisher tuple `(tensor[3,32,384,384],elapsed_times[32])`.
This directly bypasses the publisher's path-loader center crop. Times are
elapsed from the clip start; repeated source frames repeat their true times.
The encoder is bidirectional within an entirely past clip and is not an
incremental streaming or future-feature predictor.

The default `choose` call uses only the fixed five movement options
`continue`, `stop`, `turn_left`, `turn_right`, `unclear`. Returned option-letter
logits are raw and uncalibrated; an argmax is not a measured probability or
safety threshold. `chat` has a hard 64-new-token limit and always emits
`intent=unclear`; generated prose is never parsed into an actuator command.
Only one question is executed per submitted clip. Cached encoder evidence is
not silently reused across clips or dependency changes.

## Scheduling and resource boundaries

There is one child, one running native operation, and one replaceable latest
pending clip. Queued clips expire or invalidate on dependency changes. Only
the attested live `control_producer.py` receipt is an admission source. Manual
JSON that declares an idle queue or a healthy host is rejected. The worker
does not change Qwen or the Ops monitor.

The control producer continuously reads both real vLLM queue gauges from
`http://127.0.0.1:<configured-port>/metrics` using a direct connection with no
proxies or redirects, the real local `den-ops-status-1` status file, current
bridge source evidence, driver free memory, `/proc/meminfo` MemAvailable, and
the cooperative execution lease. It sums all distinct running/waiting series;
missing/malformed/unknown gauges never establish an idle queue. Ops
`checked_at` and `report.evidence.ok` are required; a backoff level above zero,
latched L3, stale/missing status, or unknown evidence blocks optional work.
The actual level/latch remains in the receipt, including last known L3 on a
temporarily unreadable source. It is never reset to zero here.

The bridge publishes `den.intent-source/v1` with exactly `schema`,
`observed_at`, `valid_until`, and `dependencies` in a separate private file.
Observation time comes from the actual observer offer on LA's monotonic clock;
background publication cannot refresh it. Expiry is at most two seconds from
that offer, additionally bounded by source frame freshness in the bridge.
The producer validates all nine binding keys and the run/epoch generation
hash. A fresh empty dependency list is valid camera/observer evidence with no
eligible subject and can support operator warmup. Missing/stale source
evidence blocks; it does not fabricate a person or extend old authority.

The producer writes atomic mode-0600 receipts at a bounded cadence, with
separate source observation times, actual queue counts, actual Ops state,
driver/RAM observations, and `admission_blocked` reasons. ControlState checks
private path/owner/permissions, the producer's live PID and boot/start identity,
reviewed producer source hash, LA monotonic clock domain, source freshness,
and exact execution-lease path digest. A different broker path is rejected.
An explicitly configured shared private GID permits mode-0640 dependency input
files under the matching root-owned scoped 2770 setgid directory. A different
Den service UID must be explicitly listed in `trusted_source_uids`; matching
the GID alone grants no writer authority. Resolve that numeric UID during host
preflight; a later UID change fails closed. Root/current UID are always allowed.
The nonsecret numeric Ops status may be 0644 under a protected parent with no
group/world writes. Root/current UID writers are allowed; another actual Ops
writer requires its exact configured `ops_owner_uid`. These relaxed Ops rules
do not apply to dependency receipts or control files. The example UID fields
are null, granting no cross-UID admission until qualified. Control output stays
0600 under the producer/worker's same UID. Source failures produce blocked diagnostics;
the last healthy receipt cannot silently remain current.

The configured broker scope is `single_optional_execution`. All cooperating
optional callers must use that same O_EXCL execution lease. An observed empty
private broker has zero unallocated cooperative reservations; a valid live
held lease excludes a competing cold allocation until terminal proof. A held
lease does not invalidate its own active job. A malformed/stale owner blocks.
Thus `unallocated_reserve_bytes=0` describes this exclusive cooperative scope,
not all GPU applications. Existing allocated Qwen/Den memory is already in
driver free memory. No global enforcement over other applications is claimed.

Configure `control-producer.example.json` with the actual local paths, vLLM
port, GPU and Ops host. Unknown/unconfigured sources fail closed. Run
`python -I -B tools/den-models/den_models/control_producer.py --config
/PRIVATE/control-producer.json`; a resource-bounded user-unit example is
included. The singleton uses a kernel-released lock separate from the GPU
permit. `--once` is diagnostic only: after that producer exits its receipt
cannot admit work because producer liveness is required. No services are
installed or enabled by these files.

The examples separate Den-writable inputs from model-private state. The
root-owned `/run/home-den-model-input` parent is 0750; its `frames` and `source`
subdirectories are scoped root:qualified-input-GID 2770 setgid directories.
The Den bridge writes 0640 frames/source receipts there only with its explicit
qualified shared GID. The model worker reads `/run/home-den-model-input/frames`
and the control producer reads `/run/home-den-model-input/source/source.json`.
Control, execution lease, residency record and bearer stay under model-UID
0700 `/run/home-den-models`. Do not place those authority files in the shared
input directories. The standalone `store_rgb_blob` helper remains 0600;
the Den bridge owns the explicitly configured group-sharing writer.

Immediately before a cold load the driver must report 16 GiB free **plus** the
incremental manifest reservation (22 GiB for 8B, 14 GiB for 4B). A resident
worker needs the 16 GiB floor plus other unallocated reservations, without
charging its own residency twice. Cold host memory requires 24 GiB reservation
plus an 8 GiB available floor; warm work requires the 8 GiB floor. The worker
rechecks both observations before loading. The current loader uses
`low_cpu_mem_usage=True` then `.to(cuda)`; direct-CUDA streaming load is not
claimed or enabled. It has no automatic fallback that weakens the host gate.
The observed 17.8 GiB publisher single-clip figure is not an NVML peak bound.

Memory residency and GPU execution are separate. A successful native job
calls `torch.cuda.synchronize` before its private-pipe completion proof and
releases its execution lease, while model residency remains accounted. Idle
residency expires after 120 seconds and child lifetime after 30 minutes.
Cancellation, timeout, invalidation, malformed completion, or worker loss
retains the execution lease until the child exits and is reaped. Merely
cancelling a Python future or closing an HTTP connection is not completion.
Memory reservation is removed only after reap. The idle model can coexist
with another admitted participant's execution.

The default `FileExecutionLease` uses a private O_EXCL file containing an
opaque token, owner PID, boot/start identity, and request digest. It never
steals stale leases. Existing Qwen/Den callers are governed only if their
adapter joins this same broker. This subsystem does not claim to enforce
global GPU exclusion over unrelated processes. For an existing broker inject
`try_acquire(request_id)->token|None` and
`release(token,proof)->bool`; proofs are `cuda_synchronized`,
`no_native_context`, or `process_reaped`.

`run_bounded.py` prints a Linux `systemd-run --user` command by default;
`--execute` is explicit and subject to the owner's host preflight policy.
It requires the actual storage block device and verifies its major:minor
against operator config. Caps: CPU 4 cores, RAM 24 GiB, swap 0, tasks 128,
read I/O 100 MiB/s for that device, runtime 30 minutes, and control-group kill.
The child verifies inherited cgroup-v2 CPU/RAM/swap/tasks/I/O caps before heavy
imports. The PyTorch allocator fraction is an extra allocator guard, not a
driver-level partition. These boundaries do not establish sustained admission.

## Provisioning receipt

Materialize exactly the pinned snapshot files at
`snapshot_root/<profile_id>/<40-character-HF-revision>/`; ordinary HF-cache
symlinks are refused. Put a separate `<profile_id>.json` receipt in
`receipt_root`. It must have exactly these keys:

```json
{"schema":"den.model-admission/v1","profile_digest":"64_HEX_CANONICAL_PROFILE_DIGEST",
 "revision":"40_HEX_PINNED_HF_COMMIT","admission_scope":"bounded_trial",
 "expires_at_utc":0,"runtime_manifest_sha256":"64_HEX_RUNTIME_MANIFEST_DIGEST",
 "code_reviewed":true,"research_noncommercial_use_accepted":true,
 "files":{"EVERY_SNAPSHOT_FILENAME":"ITS_FULL_SHA256"}}
```

The example's zero expiry deliberately admits nothing. Provisioning must
record every file hash, preserve known publisher hashes, review all remote
code/tokenizer entry points, resolve the missing runtime wheel closure, and
qualify host headroom before issuing a bounded trial receipt. The worker hashes
every materialized file before trusting local remote code. Loading is explicitly
`local_files_only=True` with HF offline environment settings, and the tokenizer
is installed explicitly with `.set_tokenizer` to avoid an unpinned lazy lookup.
Both HF snapshot revision and complete receipt digest appear in every hint.
An unresolved old residency record prevents server restart; it is not deleted
to manufacture readiness.

The separate `<profile_id>.runtime.json` has exact keys `schema` (value
`den.model-runtime/v1`), `profile_digest`, `python` (`3.12`), `cuda` (`13.0`),
`reviewed` (true), `distributions`, `wheel_sha256`, and `record_sha256`. The last
three maps contain the same full set of normalized installed distribution
names, including transitive/CUDA packages and packaging tools. Values are
exact versions, the provisioned wheel's full SHA256, and SHA256 of
`importlib.metadata.distribution(name).read_text('RECORD').encode()` respectively.
The worker compares the complete installed distribution/version/RECORD closure
before heavy imports. The admission receipt pins this manifest's canonical JSON
SHA256. Missing wheel hashes or a changed environment refuse loading. Record
hashes bind the reviewed installed inventory; they are not a substitute for
verifying downloaded wheel hashes during provisioning.

Run CPU contracts with `python -B -m unittest discover -s tools/den-models/tests -v`.
These tests exercise the real scheduling/contracts/API with fake native work;
they do not claim a successful Blackwell inference, measured latency, or peak
memory result.

## Local specialist evaluation

The separate `den_models/specialist_cli.py` accepts only local reviewed
profiles, snapshots, admission receipts, hashed PPM manifests, a fresh control
file, and a cooperative execution lease. It includes real RTMW/Hand5 ONNX,
MediaPipe Face Landmarker, and CPU YuNet/SFace adapters, with explicit output
mapping and CPU/native completion proof. SFace outputs embeddings without
assigning a person's identity. YOLOE is catalog-only here and reports its
unsupported execution status. Every supplied specialist candidate remains
`not_admitted`; missing artifact hashes, reference outputs, runtime closures,
and observed peak receipts must be resolved before an evaluation can load it.

For example, after provisioning and qualification:

```sh
python -B tools/den-models/den_models/specialist_cli.py \
  --profile rtmw-l-384 --snapshot-root /LOCAL/SNAPSHOT \
  --receipt /LOCAL/REVIEWED-RECEIPT.json --manifest /LOCAL/HASHED-CLIP.json \
  --lease-file /PRIVATE/gpu-execution.lease --control-file /PRIVATE/control.json \
  --io-device MAJOR:MINOR --check
```

`--check` performs no native load. The exact admission/manifest contracts live
in `specialists.check_admission` and `specialists.validate_manifest`; they are
deliberately separate from V-JEPA-LM's snapshot and frame contracts. No
specialist silently substitutes its topology for the existing COCO17 consumer.

Primary publisher sources: [8B pinned snapshot](https://huggingface.co/kkt94/Llama-V-JEPA-LM-Qwen3-8B/tree/d059602a9cdb2d198301f103aa0c7362ff943cb7),
[4B pinned snapshot](https://huggingface.co/kkt94/Llama-V-JEPA-LM-Qwen3-4B/tree/da5e0c682b1df2877dce25082bfa322c94ee0970),
[paper](https://arxiv.org/html/2611.17831v1).

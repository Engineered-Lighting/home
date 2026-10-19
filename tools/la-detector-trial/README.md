# LA paired detector runtime candidate

Current update, September 26: the saved three-frame comparison completed on LA
and its results were verified in Home. The isolated runtime is installed. The
earlier preparation instructions below are historical, not an outstanding request
for the owner's testing permission.

The 120-second live worker is now staged, with source hashes in `live-source.json`,
HTTP dependency pins in `http-requirements.lock`, and the exact controller binding
in `live-binding.template.json`. `start_live.py` requires a coordinated epoch and
expiry, performs health checks and starts one resource-bounded process. The first
LA activation loaded successfully and passed authenticated scoped lookup. Jetson
startup refused to load because its user cgroup lacked an enforceable CPU quota,
so no paired images were sent. LA was stopped cleanly with an empty execution
ledger; see `docs/LA-LIVE-DETECTOR-PAIR1-2026-09-26.json` from the repository root.
Its observed RAM peak was 432,119,808 bytes, not a sustained capacity claim.
The coordinating chat owns the required Victoria CPU-controller delegation.
No model loads during staging or module import.

The owner explicitly approved the exact private firewall rule in chat
`01a0dc51-0812-7233-8a3b-b77536ee690a`, turn
`01a0e05d-6078-79a0-99c9-963719cb4eb1`. It was then applied through normal review:
`192.168.1.168 -> 192.168.0.100:9443` on `enp7s0`. The initial rejection remains
historical; the approval is no longer pending. A bounded authenticated TLS probe
verified the direct route, preserved source, CA and IP identity. Its unit exited
successfully without model execution. Worker activation remains coordinated;
template epochs and the controller readiness lease must match before dispatch.

The private CA and scoped token are provisioned on Victoria at
`/home/engineeredlightingllm/perception-lab/private/la-detector-ca.pem` and
`la-detector-token`. Certificates expire after seven days. The listener origin is
`https://192.168.0.100:9443`; these files alone do not establish reachability.

The existing `UltralyticsWorker` remains the model adapter. This directory only
locks its isolated LA dependencies. Do not install them into `hav-vllm` or relax
the Victoria-only guard in `tools/run-lab-saved-detector.py`.

Target inspected live: Linux x86_64, Python 3.12.3, glibc 2.39, NVIDIA driver
595.84 and RTX PRO 6000 Blackwell. Match Jetson's torch 2.10.0, torchvision 0.25.0,
Ultralytics 8.4.0 and application dependency versions. LA uses official CUDA 12.8
wheels; Jetson uses CUDA 12.6 ARM64 wheels and Python 3.10. Report those differences.
Dependency resolution is not proof of GPU/runtime compatibility.

`requirements.lock` was resolved for Linux, including Linux-only NVIDIA and
Triton dependencies, using uv 0.12.19. Hashes restrict artifacts; installation
must additionally use `--only-binary=:all:`. `source.json` binds the Jetson input
lock and local candidate lock. No detector model or image was downloaded by this
resolution step.

After the LA preparation/validation path is explicitly admitted, create a new
private environment under the user's home and install the locked dependencies:

```sh
python3 -m venv /home/marcelo-lima/.venvs/home-lab-yolo26s-trial
/home/marcelo-lima/.venvs/home-lab-yolo26s-trial/bin/python -m pip install \
  --require-hashes --only-binary=:all: -r requirements.lock
```

These commands are preparation instructions, not authorization or an executed
deployment. Check space for wheel cache plus environment first. Keep production
services unchanged. The trial still needs its separate LA runner, enforced
process limits, approved startup/residency allowance, fresh health evidence and
explicit workload authorization. A PyTorch allocator limit is not whole-process
VRAM isolation.

The first run is exactly the three saved Victoria frames supplied by the other
chat, using their encoded-video PTS and hashes. Baseline: pinned `yolo26s.pt`,
FP32, `[640,640]`, `rect=False`, confidence 0.25, IoU 0.7, max detections 128,
batch 1, no augmentation. There is no live camera request, automatic retry,
service restart or continuous workload in this candidate.

## Proposed one-run activation

The separate `run_trial.py` is prepared and its host-independent guard tests pass.
It requires the exact LA hostname, dedicated cgroup limits, exact three input
hashes, a permanent exclusive attempt marker and at least 32 GiB free GPU memory
before imports. It uses the existing adapter and preserves the saved replay
receipt shape. These are exploratory stop limits, not measured production caps.

After explicit approval and fresh preflight, use a new protected trial directory
at `/home/marcelo-lima/home-lab-yolo26-trial`. Copy only the three registered PNGs
and their manifest, the verified checkpoint and required adapter source. Keep
the dependency environment separate from all existing services. Verify hashes
after transfer. Install under a separate bounded preparation unit with a 20-minute
deadline, 4 GiB RAM, zero swap and one CPU. Do not execute a model during install.

Run exactly once (paths refer to the prepared private directory):

```sh
systemd-run --user --unit=lab-la-yolo26-saved-trial \
  --property=MemoryMax=4G --property=MemorySwapMax=0 \
  --property=CPUQuota=100% --property=TasksMax=64 \
  --property=RuntimeMaxSec=120 --property=TimeoutStopSec=5 \
  --property=Restart=no --property=KillMode=control-group --property=UMask=0077 \
  --setenv=YOLO_AUTOINSTALL=false \
  --setenv=LAB_COMMISSIONING_SCOPE=owner-approved-la-three-saved-frames-v1 \
  /home/marcelo-lima/.venvs/home-lab-yolo26s-trial/bin/python \
  /home/marcelo-lima/home-lab-yolo26-trial/run_trial.py \
  --inputs /home/marcelo-lima/home-lab-yolo26-trial/inputs \
  --checkpoint /home/marcelo-lima/home-lab-yolo26-trial/yolo26s.pt \
  --adapter-directory /home/marcelo-lima/home-lab-yolo26-trial/adapter
```

The runner limits the PyTorch allocator to 512 MiB. CUDA context/library memory
is outside that limit; the trial is not independently isolated from other GPU
processes. Record that risk explicitly in the owner approval. Retain fault and
attempt records; do not reset health baselines. Abort for new faults, unsafe
temperatures/storage or unhealthy required containers. The proposed exception
only tolerates the already-reviewed degraded observer state and historical fault.

Return the private output to Victoria, import it as arm B, verify Home replay,
then delete the transferred LA input PNGs and private result copy. If interrupted,
leave the attempt marked incomplete and prioritize cleanup; do not retry inference.
No public publishing of images or captions is involved.

## Lifecycle readiness and unlaunched-lease reconcile

The lifecycle supervisor marks a lease `ready` only when the worker's readiness
lookup returns the exact-profile admission reply
(`409 {"error":"request_unavailable_or_indeterminate","profile_admitted":true}`);
any other 409, 429 or error keeps it `starting`. Once `ready`, the 1 Hz monitor
checks only the systemd unit identity, so it never occupies the worker's single
ingress slot during a session. Known `start_live.preflight` refusals are recorded
as content-free lease reasons (`preflight_memory`, `preflight_vram`,
`preflight_thermal`, `preflight_disk`, ...); unknown ones stay `preflight_rejected`.

Deploy order: the supervisor files and the worker tree ship separately, and an
older worker sends a plain 409 that can never prove ready. Stage a worker release
whose `remote_http.py` declares `READINESS_REPLY` and point `managed-source.json`
at it before, or together with, replacing the supervisor. Before each launch the
supervisor parses the tree `start_live.worker_root` selects (`managed-source.json`,
else `pose-source.json` for pose, else the fixed release) without importing it;
any other declared reply fails the lease as `preflight_worker_readiness` with no
unit started, instead of leaving it `starting` until preparation times out.

An `indeterminate` lease without an invocation (launch never proved a unit) blocks
new starts. After it expires, stop the lifecycle supervisor, then on LA run
`python3 lifecycle_reconcile.py <request_id>` with the supervisor's `PYTHONPATH`.
It records the unit/cgroup evidence and moves the lease to `failed` with reason
`reconciled_worker_absent` only when the fixed worker unit is inactive and
unpopulated; it never starts or stops a unit. The reason states present absence
only: a launch attempt may have run a unit that since exited (`config_present`).

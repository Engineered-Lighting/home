# Predictive path lighting and local model topology

This is the implementation and acceptance record for the owner-approved model
topology. The first delivery is shadow forecasts and virtual floor lighting.
Physical aiming remains behind the existing commissioned geometry, fixture
profile, ownership, Stop, clearance and expiry boundaries.

## Components

| Component | Role |
| --- | --- |
| Den detector, pose and calibrated depth | Current person geometry and association; never wait for optional inference |
| Existing den-track Kalman state | Source of position, velocity and full covariance; no second tracker |
| MotionForecast | Causal CPU prediction with observation-age, registration and timing uncertainty |
| V-JEPA-LM | Isolated, optional semantic intent hypotheses; not room-coordinate measurements |
| Qwen | Existing language/vision engine and policy context, with interactive capacity preserved |
| Coordinator | Reachable target at useful illumination time, independent of optional model availability |
| Vision specialists | Separate qualified whole-body, hand, facial, identity and mask evidence |

The optional model profiles contain immutable artifact references, expected
runtime and preprocessing. Missing archive checksums, preprocessing validation,
resource measurements or applicable weight terms mean **not admitted**, rather
than an inferred model result. V-JEPA-LM's current card limits weights to research
and noncommercial use despite its Apache metadata. Research evaluation does not
establish product-distribution eligibility.

## Evidence and prediction

`TrackEvidence.motion` preserves the tracker's state ordered `[x, y, vx, vy]`,
full covariance, original observation time, source timing bound, frame, track,
association revision, registration and calibration. Three fresh supported
observations spanning at least 0.4 seconds qualify leading. Ambiguity, coasting,
missing covariance and missing timing attestation interrupt qualification.

Forecast horizons are measured after the consumer's current monotonic time.
Propagation includes age since observation exactly once. Samples at 0.2, 0.5,
1 and 2 seconds retain covariance and original evidence expiry. Scalar position
sigma, rounded marker coordinates and body/head-facing arrows are not substitutes
for velocity or full covariance.

Explicit walking `shadow`/`virtual` configuration opts a new filter cohort into
native RGB elapsed time. Ordinary Den startup preserves its existing tracker
clock. The native cohort uses the same Kalman filter and preserves its full P;
arrival jitter does not become filter delta-time. Absolute observation age and
expiry retain separate conservative receipt bounds. In this opt-in mode the
public nominal capture estimate is capped at actual arrival; its independent
timing-error bound is not narrowed. Source discontinuities reset
the cohort immediately, never rebase an old P. Its nominal 1:1 source/receiver
rate remains unverified (`source_rate_assumption: nominal_1to1_unverified`,
`time_qualified: false`). Neither equal P under a replay nor a plausible virtual
target establishes physical clock qualification or useful live coverage.

The walking coordinator predicts to useful illumination time: the remaining
command/transport wait plus the actual candidate-dependent fixture slew and
settle, optionally followed by an explicit floor-leading interval. Its radio
budget reserves Stop/cut capacity. Fictional preview profiles are not hardware
acceptance. A tracking floor bounding box is not an obstacle-aware walkable map.

Motion modes remain separate hypotheses. No weighted mean of routes around
opposite sides of an obstacle may become an aim through it. Fresh measured stops
or turns override older semantic suggestions. Semantic scores are uncalibrated
and must not reduce geometric uncertainty.

## Optional inference

The host owner admits one additional GPU execution at a time while preserving
interactive Qwen and the deployed Den workers. Residency and execution are
separate: a model may remain resident after a completed synchronized call, while
its memory reservation remains charged. Unknown native completion retains the
execution permit until confirmed termination.

The first V-JEPA-LM profiles are the reviewed Qwen3 8B and 4B variants. The model
runs in its own pinned environment, from a complete local offline snapshot,
without changing Qwen's vLLM or Den's runtime. One running and one replaceable
pending request bound the worker. It returns activity/intent evidence with an
explicit unknown option; it never generates fixture commands.

All 32 frames must precede the decision cutoff. A fixed crop covers the entire
clip; per-frame recentering would alter apparent motion. Each frame carries its
actual timestamp and original source/crop transform. Repeated source frames are
reported. A new window is re-encoded; cached evidence can only answer questions
about the identical clip, crop and model revision.

The initial walking clip window is four seconds, with an eight-second
reference window. Forecast horizons are separately evaluated at 0.2, 0.5, 1
and 2 seconds; these are not clip durations. Exact-frame high-resolution crops are required for
fine hand/face evaluation. Enlarging the 640-by-360 preview or matching an RTSP
frame by arrival time does not provide that evidence.

The provisional 8B GPU reservation is 22 GiB. Admission preserves 16 GiB actual
driver-free memory after outstanding reservations, without double-charging
already resident allocations. The first load trial is bounded to four CPU cores,
24 GiB RAM, zero swap, 128 tasks and 30 minutes, with at least 32 GiB available
host RAM before cold loading. Measured startup, resident and request peaks replace
estimates before continuous admission. Optional idle models drain after 120 seconds.

## Voice retirement and rollback

Parakeet, Chatterbox, Kokoro and the Wyoming TTS bridge retire together behind an
explicit opt-in profile. Default startup, supervisor readiness, smoke checks and
Home UI must agree that voice is disabled. Proactive speech and HA announcement
branches are disabled while their independent lighting actions and typed
conversation remain available.

Retirement targets only those containers, never the whole Compose project that
also contains Qwen. Preserve model/reference-voice volumes and backup the exact
deployed configuration. Rollback restores the voice profile, callers and readiness
configuration together. The old V-JEPA observer remains until replacement
acceptance; its later retirement also changes observer recovery targets.

## Acceptance queue

1. Workstation contracts: provenance, future-frame rejection, covariance,
   ambiguity, expiry, queue supersession, process failure and permit retention.
2. Artifact qualification: checksums, graph dimensions, preprocessing equivalence,
   topology mapping, weight terms and offline startup.
3. Paired replay: current-position following, geometric compensation, and
   compensation with recorded semantic hypotheses on identical causal walks.
4. Score beam-arrival coverage, along/cross-path error, stopping overshoot,
   premature/wrong turns, reacquisition, uncertainty coverage and fresh completion.
   Include looking sideways, U-turns, furniture, crossings and occlusion.
5. Bounded shared-load trials after the current producer/GPU lease. Reject new
   Den lease failures, over 10% Den p95 regression, or over 25% interactive Qwen
   p95 first-token regression against matched baseline.
6. Voice-disabled and rollback acceptance, then a separate monitored 24-hour
   canary before continuous model admission. No physical path-lighting promotion
   follows automatically from shadow or replay success.

## Implementation status

The producer exposes bound motion forecasts and an asynchronous exact-frame
intent bridge. Its camera thread replaces one mailbox reference; decoding,
bounded private frame retention, loopback HTTP and reply checks run separately.
The bridge uses already received NPZ frames and makes no extra camera request.
Lost POST responses retain input pins until a correctly bound terminal receipt;
restart with unexplained inputs requires reconciliation. No model is inferred
loaded from configuration. Worker health retains its original observation time.

The walking consumer supports `off`, `shadow` and `virtual`, with source-bound
floor polygons, obstacle/beam checks, fixture arrival timing and independent
Stop/manual/reading precedence. Semantic replies reach the real consumer as
advisory diagnostics; no target influence is admitted. See
[the walking contract](contracts/den/walking-path-lighting.md).

[The paired replay tool](DEN-PATH-REPLAY.md) keeps future truth outside decisions
and reports unavailable metrics explicitly. Its bundled walks, stops, U-turns
and crossings are synthetic contract evidence. New person-state log rows keep
a full-precision availability receipt for importing actual recordings.

Specialist CPU backends and evaluation profiles cover RTMW/Hand5, YuNet/SFace
and FaceLandmarker. Missing artifacts, runtime or reference receipts remain
not admitted. YOLOE-26 is an evaluation profile pending its complete prompt/export
closure; it is not a newly running object-mask service.

The isolated model runtime, source-only bundle and resource wrapper live in
[`tools/den-models`](../tools/den-models/README.md). Admission is a bounded research
trial only, requires complete code/artifact/runtime receipts and actual resource
and backoff observations. Existing Qwen/Den and the legacy labeler do not become
globally serialized just because the new workers share an execution lease.
Unjoined background GPU work must remain deferred during optional model trials.

These are source implementation results. Native model loading, shared-load
peaks, measured fixture delays, held-out lighting outcomes and sustained
admission require their separate acceptance receipts; none is inferred from
the CPU tests or synthetic replay.

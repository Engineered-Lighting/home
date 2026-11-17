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

The initial walking clip window is four seconds, with two- and eight-second
windows as replay controls. Exact-frame high-resolution crops are required for
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

The working branch separates motion evidence, optional model serving, voice
retirement and downstream coordinator/spatial/visual integration. Exact tests,
candidate commits, deployed configuration and runtime measurements are recorded
as they complete. Unmeasured model capacity and unrun acceptance remain explicit;
configured, provisioned, loaded, running, idle and degraded are distinct states.

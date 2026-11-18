# Spatial evidence adapters

These adapters qualify evidence for shadow forecasts and virtual display. They do
not associate people, estimate another velocity, choose a lighting target or grant
physical fixture authority. Models owns motion generation and the asynchronous
specialist bridge. Visuals owns browser projection and rendering.

## Motion from the existing tracker

The pinned `den-track` filter uses room-floor state `[x, y, vx, vy]`. Position is
in metres and velocity in metres per second. Its full 4x4 covariance contains
position variance in m², position/velocity cross-covariance in m²/s, and velocity
variance in m²/s². Do not rebuild that covariance from the rounded public marker
position or its single `sigma_m` field.

The filter's white-acceleration parameter is the square root of continuous noise
spectral density, in m/s^(3/2). Its position process noise is `q * dt³ / 3`, where
`q = white_accel_sigma_mps32²`. This differs from a constant physical acceleration
bound. Observation timestamp error must be qualified across the filter history,
and common registration/timing envelopes must not be treated as independent noise
without a justified correlation model. Missing qualification produces unavailable
evidence. A numerical uncertainty envelope is not a calibrated probability.

The current state explicitly reports `time_status=estimated_source_elapsed` and
`time_qualified=false`. Its history gate supports diagnostic shadow forecasts;
it does not certify a physical clock or calibrated trajectory containment. A
changed source/tracker elapsed-time offset taints that raw track until a new
birth or camera epoch. Report availability rather than treating universal refusal
as a successful operational forecast.

`forecast_spatial.qualify(forecast, snapshot, track, now_mono)` returns a packet or
a fixed refusal reason. It checks the actual current motion state, full covariance,
source observations, confirmed/confident association and exact service, epoch,
frame, track, association revision, transform, scan and calibration bindings. It
preserves the generator's positions and covariance without predicting again.

The canonical forecast fields are `state`, `issued_mono`, `valid_until_mono` and
`samples`. Each sample has `horizon_s`, `at_mono`, `xy_m`, `covariance_m2` and
`sigma_m`. A horizon is relative to issuance; its absolute time stays unchanged
when another consumer reads it later. Observation age is already included by the
generator and must not be added a second time. Head orientation and body facing
are separate anatomical evidence, including when someone walks sideways or looks
behind them.

The qualified packet adds schema `den-spatial-forecast-1`, coordinate space
`room_floor_xy_m`, time domain `producer_monotonic`, covariance order
`[x, y, vx, vy]`, uncertainty kind `conservative_envelope`, `qualified_at_mono`
and `remaining_freshness_s`. Its shadow/virtual scope and false physical, aiming,
occupancy, walkable, beam-footprint and illumination-delay qualification flags are
explicit. These flags do not authorize virtual actuator commands either. The
lighting coordinator still owns eligibility, manual holds, Stop, target selection
and any later qualified walking mode.

The bridge should publish an optional qualified forecast for its current track
and clear it on refusal. Preserve separate semantic path alternatives. Do not
average incompatible routes into a point through an obstacle. New measured motion,
ambiguity, a crossing, coasting or a changed observation generation invalidates
older bindings even if their original capture lease has time left.

## Time at the browser

All `*_mono` fields use the producer's monotonic domain, not the browser's
`performance.now()` origin. `remaining_freshness_s` is measured at qualification.
Starting that duration again on network receipt would extend the evidence lease.
Consumers need the existing validated source-clock/receipt mapping and current
frame binding. Without a usable bound, refuse or clear the overlay. Do not convert
a guessed network delay into a measured freshness guarantee.

Future floor positions are already in registered room XY. Do not apply camera
extrinsics to them again. Display covariance as an uncertainty envelope with its
declared scale, not an invented 95% confidence region. Actual commissioned fixture
timing, beam footprint and qualified walkable geometry remain later physical
acceptance gates.

## Specialist crops and room geometry

`specialist_spatial` keeps specialist landmarks separate from the main body pose.
Its immutable binding includes the complete `FrameRef`, current track and
association revision, parent detection index/ID and observation IDs, profile
SHA256, request ID, worker engine epoch and original capture-based deadline.
The echoed result must match the expected request and trusted crop geometry.

In version 1, `source_size` and `native_size` both name the accepted RGB frame,
currently 640x360. `crop_xyxy` uses that full frame's coordinates.
`source_from_model` is a 2x3 affine mapping model coordinates directly into the
full native frame, including crop translation. There is no implicit addition of
the crop origin or inferred resizing. Points mapping into padding or outside the
declared crop/source remain unsupported. Do not clamp them onto an edge.

Different high-resolution source dimensions are refused until a separate exact
source-frame mapping and its calibration/preprocessing qualification exist. A
larger image is not automatically the same calibrated coordinate system.

For each point, the adapter propagates model pixel covariance with `A Σ Aᵀ`.
It retains a native uncertainty floor of at least four source pixels without
dividing it by a crop enlargement factor. A minimal diagonal shift can enforce
the variance floor while preserving covariance cross terms. The conservative
largest-eigenvalue bound supplies the scalar pixel-noise parameter when invoking
the existing observer's lift; RGB distortion, depth registration and room
transforms remain those of the existing calibrated observer. Depth-supported
points may be labeled measured. A plane fallback retains its inferred/uncertain
status and cannot become measured geometry through the adapter.

Only an explicitly configured, profile-bound COCO17 layout or validated
COCOWholeBody133 first-17 mapping is accepted. FaceLandmarker landmarks have
different semantics and are not interchangeable with COCO facial points. Missing
or low-score joints remain unsupported; one weak ankle does not fabricate or
invalidate unrelated measurements. Parent body observations are never modified
in place. Geometry expires at the minimum of the original parent expiry and the
capture-derived deadline, with the existing 1.5-second spatial ceiling.

## Qualification checks

Local tests cover full-state cross-covariance, exact binding and stale generations,
unaltered absolute sample times, real pinned-tracker inertia at stops and turns,
crossing/occlusion history interruption, and anatomical direction independent of
travel. Crop tests cover translated/nonuniform/skewed affine transforms, propagated
covariance, padding, source bounds, semantic layout mismatch and measured versus
inferred room lifts. These are contract tests, not physical path-lighting or
empirical uncertainty-calibration acceptance.

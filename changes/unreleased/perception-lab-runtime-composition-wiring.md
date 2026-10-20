---
title: Wire Lab spatial localization, caption checks and saved Qwen analysis into the managed runtime
target: backend
type: added
---

The Lab controller now composes three finished subsystems. Each stays inactive
until it is provisioned.

- **Spatial localization.** A new private `spatial_file` live-config key
  enables it. The file holds the calibration record, registration, room map,
  association revision and optional depth-clock commissioning.
  - Only a recipe that needs depth samples the camera's numerical bundles.
    Placement recipes never ingest them.
  - Bundle decoding no longer holds the runtime lock.
  - A recording-owned scheduler takes each localized arm's newest specialist
    output and publishes positions through the existing consumer.
  - A stereo arm uses depth only from the bundle whose own RGB frame is the
    detected frame. Otherwise it abstains and records why. Freshness is never
    extended.
  - When spatial is missing or invalid, the recording continues. The reason is
    recorded as `spatial.unavailable` and shown in live status and
    capabilities.
- **Historical captions.** An enabled recipe that grants captions is now
  checked when the controller starts. Startup fails with a clear error if the
  `caption_history` opt-in, the household caption grant or the caption source
  is missing. Caption readiness is shown as a capability gate.
- **Saved Qwen analysis.** Optional `qwen_profile` and `qwen_transport` keys
  build saved analysis for the managed controller, so saved-session jobs run
  once both keys are provisioned. Without them, saved analysis reports
  `qwen_admission_not_provisioned` as before. The LA consumer is not wired or
  deployed by this change.

The release allowlist adds the reviewed `depth_association`, `depth_clock`,
`spatial_validation` and `qwen_transport` modules. Placement recordings behave
as before.

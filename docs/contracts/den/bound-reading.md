# Bound reading evidence (virtual only)

`activity-lighting-events-2` is the successor event envelope on the existing
authenticated `/api/lighting-brain/v1/homes/{home}/events` route. Its `activity`
and `person_pose` rows retain the v1 fields and require `evidence_version: 2`,
`valid_until` and `binding`. Other event types retain their existing shape.
The state schema remains `activity-lighting-state-1` with additive diagnostics.

## Source and reader binding

The raw paired detector producer creates `den-sole-reader-source-1` before
presentation serialization. It uses exactly one distinct detector person with
one matching raw box. Box IoU at least 0.3 and an observed-frame gap no greater
than two seconds continue the anonymous reader epoch. Multiple/zero people,
invalid, late or expired source, a larger gap, box discontinuity, capture/engine
or model generation change, or a new recording starts a new epoch. Frame-local
instance array order is never reader identity. A failed storage transaction does
not advance the bank. This is correlation, not identity or qualified geometry;
an indistinguishable replacement during unobserved footage cannot be ruled out.

An available binding contains exactly these fields:

- `schema`, `status: "available"`, `reason: null`;
- `session_id`, integer `generation >= 1`, `capture_epoch`, `engine_epoch`,
  `reader_epoch`, `interval_start`;
- frame-local `instance_id`, `source_frame_id`, `source_request_id`, positive
  integer `source_event_id`;
- original `observed_at`, `available_at`, `valid_until` Unix seconds.

The global JEV request freezes the binding of its latest raw visual record and
an input digest. Its camera history and Qwen summary are restricted to that
sole-reader interval. The bounded change omits unbound room facts/captions;
it does not switch to a person-specific provider. The provider's frame-history
ring resets when the binding changes. Missing legacy raw bindings confer no
reading authority. The reply retains this frozen context; a newer detector
count cannot relabel an older answer as belonging to another reader.

The incumbent always-on Qwen perceiver stores receipts outside recording
ledgers. The controller binds its existing trusted `LaQwenReceipts` bank to
the recording's reasoner. A read-only snapshot admits only originals with the
same site/camera/capture and a complete input window inside the recording's
continuous sole-reader interval, ending no later than its latest raw person
observation. Daily perceiver session IDs are not relabeled as person identity.
The latest receipt must remain valid and report one visible person; there is
no fallback to an older good receipt after an invalid/ambiguous latest result.
The runtime's existing recipe/household gate still controls Qwen carriage.
Withheld or missing Qwen on this configured path cannot be reinserted from the
cache and does not qualify reading. Snapshotting does not renew original
expiry, capture media, call a model or open the private fact log.

The activity loop publishes an answer only when it is ready, fresh on arrival,
unexpired, and still matches the current raw reader epoch. A pose must join the
same raw source event/frame/capture and matching instance. A null binding is an
explicit unbound/retirement observation and cannot qualify reading. All source
timestamps remain original; `ttl_s` is capped by both the old type TTL and the
remaining origin claim. The brain validates the whole batch before applying it.

The raw runner supplies the reading bank with `original_frame(request)` expiry,
not the sampling-adjusted viewing output deadline. A presentation can therefore
remain display-current while its reading binding is unavailable. Neither the
sampling hold nor the development dispatch deadline renews lighting authority.

## Expiry, reset and migration

The brain requires fresh bound activity and pose for the same session,
generation, capture, engine and reader epoch, with exactly one person. Invalid,
ambiguous, changed or expired evidence retires reading. The three-second hold
starts again after a reader change or recording roll. A normal non-reading
answer can retain the existing ten-second stop grace only while the same fresh
reader/pose context remains supported; an outage, abstention or expired source
cannot use that grace to extend authority.

Deploy the new brain before the new producer. Legacy v1 events continue to
support occupancy and ambient lighting; they cannot activate a reading spot.
Bound fields in a v1 envelope are refused. An old brain rejects the v2 envelope;
the publisher never retries by stripping binding/expiry or downgrading the
schema. `/healthz` advertises `events_schemas` and
`reading_requires_bound_evidence: true`. A mixed-version rollback may therefore
leave ambient only. It is not qualified reading acceptance.

The v4 pack and development alignment/mount policy are unchanged. This semantic
expiry check does not switch camera alignment to strict mode. In particular,
an actual raw lease of approximately three seconds may be too short for the
current JEV latency/cadence. Report that refusal and measure the bottleneck;
never lengthen an origin lease to make the reading test pass. Four-second
semantic calls do not establish continuous person observations: those must
still arrive inside the separate two-second association-gap rule.

## Diagnostics and operations

State adds `inputs.at_sofa`, `state_revision`, `decision_id`, and
`reading_evidence` containing `bound`, activity/pose bindings and both effective
origin expiries. `decision_id` is stable while the desired mode/fixture outputs
remain unchanged; source refresh alone does not manufacture a new decision.
Source diagnostics are carried on the next normal state update/heartbeat and
do not create extra pushes for otherwise invisible jitter. These are server
state facts; visible Home output needs a correlated renderer receipt.

Authenticated Lab `GET /api/perception-lab/v1/activity-loop` returns
`deep_stack.session_id`, `deep_stack.rolls` and a process-local
`deep_stack.producer_epoch`. Aggregate observed rolls only inside one producer
epoch. Worker/runtime failure retries use exponential delay from 10 seconds,
capped at 160 seconds, through the unchanged controller admission path. An
explicit operator/monitor stop, or an outside stopped recording with an unknown
reason, remains stopped. Retry metadata is visible in `retry_at` and
`retry_failures`; successful recording preparation alone does not reset backoff.

Optional geometry zones with neither polygon nor normal are unmarked and
skipped. Explicit null, partial or malformed marked geometry remains refused.

Offline tests establish serialization, expiry, correlation and recovery logic.
They do not establish live recognition accuracy, latency, book coverage,
sustained resource admission or physical lighting acceptance.

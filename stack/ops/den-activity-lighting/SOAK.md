# Den activity lighting: supervised release admission

Continuous virtual operation requires one passing supervised resource soak and
its explicit owner checklist. A source test, complete-looking JSON, idle room,
or monitor level 0 cannot substitute for the evidence below. Physical output
remains disabled. Resource/service/model changes require a new attempt.

## Commission evidence before sampling

Use the current AGENTS maintenance admission checks immediately before each
operation. Inspect both system and user failed units, current kernel records,
boot identity, storage, temperatures, containers and resource headroom. Preserve
historical incident evidence and baselines. A first monitor pass inspects the
current boot, with documented historical patterns applied; it does not establish
that every matched historical-looking record was previously reviewed. The
operator must reconcile timestamps and incident history before admission.

The monitor now latches at level 3 for missing required sensors, malformed or
unreadable journal/boot/unit/baseline data, unexpected failed units, corrupt
persisted state, or missing/stale required LA monitor status. Evidence failure
is reported separately from hardware faults. Diagnose, then use the existing
owner release command; never delete state, clear system failures, change
thresholds or rewrite a historical baseline to obtain green status.

The commissioned Jetson thermal inventory requires CPU, GPU, SoC0/1/2 and TJ
channels. The example explicitly excludes unsupported CV0/1/2 channels with a
review reason. Reinspect this inventory after hardware/driver changes; do not
exclude a newly failed critical sensor. Every required safety rule must have a
finite reading at every recorded sample. Initial journal collection needs a
valid record/cursor; subsequent successful empty reads are allowed. Commands
are read-only, time bounded, and reject output above 1 MiB.

LA hwmon configuration must list every commissioned required input under `sources.hwmon.required_inputs` with its relative sysfs path, chip name and label. The example paths are synthetic and must be replaced after inspection. An unreadable input, changed chip/label or invalid sentinel latches evidence failure even when an aggregate temperature remains available. Record unsupported optional inputs and their reasons in the private commissioning inventory. Both hosts inspect system and user failed units.

For a missing failed-unit baseline, first run `baseline-units` without `--write`,
diagnose every listed unit and retain that evidence. Creation requires one
`--reviewed-unit system:<unit>` or `--reviewed-unit user:<unit>` for each diagnosed
entry, exactly matching the observed set; it refuses an existing file. Do not
recreate an existing baseline. `validate` returns nonzero on unsafe or incomplete
evidence. Observe complete healthy level-0 checks for ten minutes before a soak.

The loaded hour requires fresh occupancy (at most 90 seconds old), a running activity loop, advancing actual Qwen completions and ten recording rolls. A missing sampling interval resets that occupied span on either host; collection jitter is bounded to 1.5 times the configured interval, capped at 120 seconds. Unknown occupancy is incomplete exposure. These checks do not change resource thresholds.

## Bind the actual deployment

Copy `monitor/config/deployment.example.json` to a private file, fill every
value, and use the identical manifest on both hosts. It names actual component
release IDs; no common Git commit is assumed. Required components are brain,
gateway/Home and ops on LA; bridge, Lab, detector and ops on Jetson. Each needs
actual deployed source/config/dependency artifact hashes. Include all files in
the reviewed release inventory, private settings, geometry and fault/unit
baselines; never put private paths, host identities or credentials in Git.

Each unit entry binds the exact `systemctl show` WorkingDirectory and ExecStart
property values and its system/user scope. LA brain/gateway are system units;
Jetson bridge, managed Lab and detector are user units. All three Jetson
processes are sampled separately. Include finite commissioned MemoryMax limits;
an unlimited/missing memory cap prevents evidence acceptance.

Compute each monitor/soak configuration digest using `den_ops.deployment.digest`
on its loaded JSON, including `_note` when present (canonical sorted compact
JSON SHA-256). File artifact hashes are ordinary SHA-256 over bytes. The manifest
itself is canonically hashed. The sampler verifies local artifact/config identity
at start and end, and actual unit execution identity at every sample. Individual
artifacts are limited to 64 MiB and inventories to 4,096 files; use small source,
config and release-manifest artifacts for giant model packages, and record the
actual model/package identity. This verifies the declared inventory; release
review must establish that inventory completeness.

## Run one immutable attempt per host

Run from the release's `monitor/` directory, as a user able to inspect every
configured scope and read the kernel journal. Both hosts use the same private
manifest and start within 120 seconds. Each runs 11,400 seconds (3 h 10 min),
leaving room for at least three hours of common supervision. Use a fresh output
directory for each attempt; an existing directory is refused. No append, resume
or stitching is supported.

A bounded persistent unit keeps SSH loss from ending the attempt. For example,
under the commissioned monitor user (adjust scope to the approved installation):

```bash
systemd-run --user --unit=den-soak-<unique-attempt> --collect \
  -p MemoryMax=128M -p CPUQuota=20% -p TasksMax=16 -p Nice=10 \
  -p RuntimeMaxSec=12600 -p TimeoutStopSec=15 -p Restart=no \
  --working-directory=<release>/stack/ops/den-activity-lighting/monitor \
  /usr/bin/python3 -E -s -m den_ops --config <private>/monitor.json soak run \
  --soak-config <private>/soak.json --deployment-manifest <private>/deployment.json \
  --out <private-evidence>/<new-attempt>
```

Use one sampler per host. The sampler never starts/stops services. Required
collection failure, fault or reboot ends it with a failed partial receipt.
SIGTERM/interruption also preserves a failed partial receipt when the process
can execute cleanup. A hard kill/power loss may leave samples without a receipt;
that attempt is not admissible. Retain it and begin a new attempt after correction.

After the 30-minute warmup, occupy the Den for a sustained hour within both
samplers' common window, exercising at least ten actual recording rolls. Allow
extra time around that hour so both sampling clocks cover the full common
interval. The Jetson samples authenticated activity-loop `deep_stack.session_id`,
`rolls`, `producer_epoch`, and `qwen_workload`. Commission `loop.qwen_profile`
from the actual model identity (including artifact and engine epoch) before
sealing the shared deployment/configuration digests. Missing/malformed Qwen
telemetry or a differing model fails collection; a recording alone is not
inference exposure. The workload counter advances only for recorded completed
executions with matching model identity, including invalid or expired answers;
it proves execution, not accuracy, freshness or reading authority.

After warmup, Qwen must be running without monitor back-off, with a positive
completion delta between each adjacent 60-second sample (existing maximum gap:
120 seconds). A stall, counter rollback, producer/model change or inactive
recording breaks the span. Ten rolls must occur inside that same span. The
reported common occupied window uses this identical evidence. This is sampled
workload coverage, not a claim that every inference succeeded or a product
latency target. LA must independently observe occupancy during the same hour. Guided
sofa examples may be recorded without changing inference/settings mid-attempt.

## Acceptance and explicit owner checklist

All existing resource/temperature thresholds remain unchanged. Admission checks:

- At least 11,400 seconds per host, 60-second sampling, at least 95% of expected
  samples, positive monotonic progress and no sampling gap over 120 seconds.
  Wall-clock discontinuity over five seconds, reboot, mixed run or mixed manifest
  fails. Both host windows must overlap for at least 10,800 seconds.
- Every required sensor, journal/boot, baseline, unit counter and execution
  identity is readable. No new kernel fault or unexpected failed unit; every
  required unit has finite memory accounting/cap, stays active in at least 99%
  of samples, and never exceeds its configured restart allowance.
- No sample reaches a monitor level-1 threshold. After warmup: memory growth is
  within per-unit config bounds (brain 8, gateway 32, bridge 4, managed Lab and
  detector 48 MiB/h); peak remains below 85% of MemoryMax; host memory and LA
  GPU drift remain within existing bounds.
- Monitor status is fresh in at least 95% of samples, never from the future,
  level 0 throughout. LA brain is online with fresh occupancy/Frigate in at least
  99%; occupied JEV/deep-stack health at least 95%, activity freshness at least
  90%. Empty occupied exposure cannot pass. Both hosts must share an occupied
  hour; Jetson requires at least ten rolls in one epoch. Pose gaps are measured
  at the sampling resolution, not claimed as exact per-frame outage durations.

Run deliberate service-stop interventions **after** the resource window in a
separate supervised owner-checklist window on the same unchanged deployment:

```bash
python3 -m den_ops --config <private>/monitor.json soak checklist \
  --soak-config <private>/soak.json --deployment-manifest <private>/deployment.json \
  --out <private-evidence>/<completed-LA-attempt>
python3 -m den_ops --config <private>/monitor.json soak evaluate \
  --soak-config <private>/soak.json --deployment-manifest <private>/deployment.json \
  --out <private-evidence>/<completed-LA-attempt> --checklist <explicit-checklist.json>
python3 -m den_ops soak combine <LA-receipt.json> <Jetson-receipt.json> \
  --deployment-manifest <private>/deployment.json --out <private-evidence>/<release>
```

No newest-checklist selection occurs. Missing checklist remains incomplete;
partial, duplicate, unknown, invalid, wrong-host/deployment/run or preceding-window
results fail. Only `second_person` may be skipped. The runner checks fresh brain
states and starts timing when Enter begins the action, rather than after the
owner has completed it. Steps: empty/off held 60 s; sofa ambient with whole-room
occupancy plus fresh position and `inputs.at_sofa`; reading spot; reading stop;
optional second person; manual pause; JEV loss/recovery; brain offline/recovery;
leave/off; owner review of why/log. Offline Home must stop automatic spots, hold
ambient and preserve manual pauses. Confirm normal recovery after interventions.
The basic checklist records behavior and elapsed observations; it does not
establish the separate reading latency/accuracy product targets.

Receipts use `den-ops-soak-receipt-4`. Combination requires exactly one LA and
one Jetson receipt, complete valid criteria, matching recomputed verdicts,
matching manifest identities, common windows and actual occupied exposure.
`--release` may repeat the manifest ID; it cannot relabel a mismatch. Legacy
receipts remain historical evidence and cannot establish new admission. Keep
samples, metadata, both host receipts, explicit checklist and combined receipt
with the release. Local/fake-host test PASS establishes software behavior only.

# Den activity lighting: release soak

AGENTS.md admits a release to continuous, virtual-only operation after **one
supervised soak of at least 3 h** passes these written criteria. The soak
receipt is recorded with the release. A change in resource use or service
scope needs a new soak. A settings change (levels, CCTs, timings, cadence)
does not.

## Who runs what

| Host | Command | Samples |
|---|---|---|
| LA AI host | `python3 -m den_ops --config <monitor.json> soak run --soak-config <soak.la.json> --out <dir>` | host signals, `lighting-brain` and `home-web-gateway` units, brain state and `/healthz`, monitor status |
| Victoria Jetson (as the perception-lab user) | `python3 -m den_ops --config <monitor.json> soak run --soak-config <soak.jetson.json> --out <dir>` | host signals, the bridge and `perception-lab-managed` units, monitor status |
| LA, during the soak, with the owner | `python3 -m den_ops --config <monitor.json> soak checklist --soak-config <soak.la.json> --out <dir>` | the scripted MVP checklist, verified against the brain state |

- **Sampling:** each sampler runs from `monitor/`, reads every 60 s for
  `duration_s` (10800 s by default) and appends to `samples-<host>.jsonl` as
  it goes. It writes `soak-<host>.json` and `soak-<host>.md` at the end.
- **Interruptions:** run it under `tmux` or `systemd-run --user --unit=den-soak`
  so a dropped SSH session does not end it. A soak that stops early fails the
  duration criterion. Start a new one; there is no stitching.
- **After the checklist:** rebuild the LA receipt with it:
  `soak evaluate --soak-config <soak.la.json> --out <dir>`. It picks the
  newest `checklist-*.json`.
- **Release verdict:** run
  `soak combine <la receipt> <jetson receipt> --out <dir> --release <id>`.
  It writes `soak-release.{json,md}`, and its verdict is the release's.
- **Read-only:** the sampler uses the monitor's read-only command allow-list.
  It changes nothing on either host.

## Pass criteria

Every criterion below must pass on every host it applies to. Verdicts:

- `pass` when all criteria pass;
- `fail` when any fails;
- `incomplete` while the owner checklist has not been run.

| ID | Criterion | Rule |
|---|---|---|
| `duration` | At least 3 h with steady sampling | `ended − started ≥ max(duration_s, 10800)`, with at least 95 % of the expected samples |
| `faults` | No new kernel, OOM, thermal or hardware faults; no reboot | No kernel journal record in a fault class (MCE, hardware error, panic, lockup, I/O error, thermal, OOM, GPU Xid, segfault) unless it matches the documented fault baseline. A boot-ID change fails too. |
| `temperatures` | Temperatures under the existing limits | No sample reaches any temperature rule's level-1 threshold (the table in README "Limits"): LA GPU < 74 °C, Tctl < 85 °C, Tccd < 84 °C, NVMe < 65 °C; Jetson max zone < 75 °C. The monitor never backs off. |
| `resource_limits` | Memory, swap, GPU memory and disk stay inside the monitor thresholds | No sample reaches a non-temperature rule's level-1 threshold, for example LA GPU free ≥ 16 GiB and RAM available ≥ 12 GiB |
| `memory_flat` | Memory and GPU memory stay flat | See the memory bounds below. |
| `restarts` | Bounded restarts | Each unit's `NRestarts` rises by no more than its `max_restarts` (0 by default), and the unit is `active` in at least 99 % of samples |
| `monitor` | The monitor ran and never backed off | Its `status.json` is at most 180 s old in at least 95 % of samples, and it shows level 0 throughout |
| `brain_inputs` (LA) | Brain online with fresh producer inputs | See the brain bounds below. |
| `mvp_checklist` (LA) | The MVP behaviour checks | Every required step passes; only `second_person` may be skipped |

**Memory bounds (`memory_flat`).** These apply after the 30 min warm-up:

- each unit's `MemoryCurrent` least-squares slope is at most its
  `memory_growth_mib_per_h`:
  - brain: 8;
  - gateway: 32;
  - bridge: 4;
  - managed service: 48 MiB/h;
- each unit's peak is at most 85 % of its `MemoryMax`;
- host `MemAvailable` falls no faster than 256 MiB/h on LA and 128 MiB/h on
  the Jetson;
- LA GPU memory used moves by no more than 1024 MiB from its minimum to its
  maximum.

**Brain bounds (`brain_inputs`, LA).**

- **Whole soak:** in at least 99 % of samples:
  - `brain: "online"` with `/healthz` ok;
  - `occupancy_age_s` ≤ 90;
  - `inputs.health.frigate` true.
- **While the den is occupied** (after the first minute of each occupied
  stretch):
  - JEV and the deep stack healthy in at least 95 % of samples;
  - `activity_age_s` ≤ 30 in at least 90 % of samples.
- **Measured, not judged:** `worst_pose_gap_s` is the longest stale-pose
  stretch while occupied. It is the recording-roll gap AL-3 asked for. A
  value over 10 s means `person_pose` expires at each roll; report it to
  planning.

The bounds are settings in the soak config (`units[]`, `bounds`,
`freshness`). Loosening one is a release-review decision, written into the
receipt's config; it is not done mid-soak.

## The owner checklist (`soak checklist`)

The checklist runs on LA while the soak samples. Each step prints an
instruction. The owner does it and presses Enter, and the runner then polls
the brain state every second until the expected state appears (pass) or the
time runs out (fail). Two steps are the owner's own yes/no.

| Step | Owner does | Expected (from `activity-lighting-state-1`) | Time limit |
|---|---|---|---|
| `empty_off` | Empties the den | `mode: off`, no ambient, no spot, held for 60 s | 300 s |
| `sofa_ambient` | Sits on the sofa | `occupancy.zones.sofa` true and ambient on | 30 s |
| `reading_spot` | Reads a book | a spot on and `mode: reading` (MVP: within about 5 s) | 20 s |
| `reading_stop` | Stops reading | no spot (MVP: about 10 s) | 30 s |
| `second_person` (optional) | Has a second person join | `people_count` ≥ 2, ambient only, held for 20 s | 60 s |
| `manual_pause` | Changes a fixture in Home | that fixture's `paused_until` is set | 15 s |
| `jev_loss` | Reads, then stops the activity loop (and resumes it after) | ambient only, `inputs.health.deep_stack` false | 90 s |
| `brain_offline` | Stops `lighting-brain.service` for about 30 s, then starts it | owner confirms Home showed "brain offline" and held | n/a |
| `leave_off` | Leaves the den | `mode: off` (MVP: about 2 min) | 180 s |
| `why_log` | Reviews Home | owner confirms the why line and log explained each step | n/a |

- **Results file:** each result records its elapsed time and the brain's
  `mode`, `why` and latest decision rule as evidence, and goes to
  `checklist-<host>-<time>.json`.
- **Interventions:** the `jev_loss` and `brain_offline` interventions happen
  during the soak, on purpose. They add expected restarts (`brain_offline`
  restarts the brain once), so:
  - set `max_restarts: 1` for `lighting-brain.service` in the soak config
    used on the checklist day; or
  - run the checklist in its own short window after the 3 h soak.

## Receipt

`soak-<host>.json` (`den-ops-soak-receipt-1`) holds:

- host and release;
- the window;
- the sample count;
- each criterion with its result, detail and measured values;
- the checklist.

The markdown is a one-screen summary for the release PR. Attach both hosts'
receipts and `soak-release.md` to the release, or reference them from it.

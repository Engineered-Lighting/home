# Den activity lighting: always-on operations (AL-5)

Ops files for running the den activity-lighting MVP continuously, in virtual
output only (Engineered-Lighting/home#325, parent #320). They follow the
owner-approved always-on policy in AGENTS.md (draft PR #343):

- one supervised soak of at least 3 h per release, then continuous running;
- a background monitor that backs off in steps and alerts;
- the monitor never clears failures, rewrites incident baselines or restarts
  unrelated services.

Nothing here is deployed by this PR. AL-6 (#326) deploys it with the owner's
approval, following [RUNBOOK-GO-LIVE.md](RUNBOOK-GO-LIVE.md).

## Layout

| Path | What it is |
|---|---|
| `units/la/lighting-brain.service.in` | The brain (AL-1) on the LA host: systemd with a venv, loopback only, 256 MiB / 50 % CPU, private files via `LoadCredential` |
| `units/la/lighting-brain-health.{service.in,timer}`, `lighting-brain-healthcheck.sh` | `GET /healthz` every minute; three misses in a row restart the brain, and only the brain |
| `units/jetson/victoria-ha-bridge.service.in` | The AL-2 bridge on the Victoria Jetson: restart with back-off, 128 MiB / 25 % CPU, `EnvironmentFile` |
| `units/jetson/perception-lab-managed.service.d/50-activity-loop.conf.in` | Drop-in that turns on the AL-3 loop inside the existing `perception-lab-managed.service` (systemd --user) |
| `units/*/den-ops-monitor.{service.in,timer}` | The monitor, one pass a minute on each host |
| `units/values.example.json` | Every placeholder the templates take; copy it privately and fill it in |
| `render_units.py` | Renders the templates from the private values; refuses missing or placeholder values |
| `monitor/den_ops/` | The monitor and the soak runner (stdlib Python, 3.10+) |
| `monitor/config/*.example.json` | Monitor, soak, fault-baseline and gateway-token examples |
| `monitor/tests/` | Offline tests with recorded and synthetic fixtures |
| [`SOAK.md`](SOAK.md) | The soak's pass criteria and how to run it |
| [`RUNBOOK-GO-LIVE.md`](RUNBOOK-GO-LIVE.md) | Draft go-live runbook for AL-6 |

The gateway side lives in `web-gateway/`:

- `lighting-brain-route.mjs` exposes the brain under `/api/lighting-brain/v1/`.
- `den-ops-status.mjs` serves the monitor status and the Home banner under
  `/api/den-ops/v1/`.
- `tools/run-web-gateway-lighting-brain-tests.mjs` tests both.

## Why systemd for the brain, not compose

The LA stack runs most services in `stack/docker-compose.yml`. The brain gets
a systemd unit instead, for two reasons:

- AGENTS.md forbids image builds on `EngineeredLightingServer1`.
- The brain is stdlib Python plus `tzdata`, so a venv is the smaller change.

The health timer stands in for compose's healthcheck. `Restart=always` with
`StartLimitBurst` bounds crash loops, and a unit that hits the limit stays
failed, which the monitor reports.

## Gateway routes

| Route | Auth | Notes |
|---|---|---|
| `GET /api/lighting-brain/v1/homes/{home}/state` | gateway session (cookie or Basic) | no caller credential reaches the brain |
| `GET …/stream` | gateway session | SSE passes through unbuffered. Only the response headers are under `HOME_WEB_PROXY_RESPONSE_TIMEOUT_MS`; the stream itself stays open. |
| `POST …/overrides`, `DELETE …/overrides/{fixture_id}` | session **and** `X-Home-Lighting-Brain-CSRF: 1` | 403 `lighting_brain_csrf_required` without the header |
| `POST …/events` | `Authorization: Bearer <producer token>` only | No gateway session is needed. Only the bearer, the body headers and the user agent are forwarded, and the brain checks the token. |
| anything else under `/api/lighting-brain` | none | 403, never proxied: query strings, other methods, other versions, `%`-encoded paths |
| `GET /api/den-ops/v1/status` | session, or any monitor token | `den-ops-banner-1`: the worst level across hosts, a banner, per-host status, stale hosts |
| `POST /api/den-ops/v1/hosts/{host}/status` | that host's monitor token | `den-ops-status-1`, up to 16 KiB. It is kept in memory, and a host turns stale after 300 s. |

- **Target:** `HOME_WEB_LIGHTING_BRAIN_TARGET`, default
  `http://127.0.0.1:8101/api/lighting-brain`. The gateway and the brain share
  the LA host, so it is loopback.
- **Monitor tokens:** `HOME_WEB_DEN_OPS_TOKENS_FILE` lists them
  (`den-ops-monitor-tokens-1`, SHA-256 digests only, mode 0600). Without it,
  `/api/den-ops` answers 404 `capability_disabled`.
- **Stale timeout:** `HOME_WEB_DEN_OPS_STALE_S` (default 300) sets when a
  silent host turns stale.

## The monitor

`python3 -m den_ops check` runs once a minute from a timer on each host. Each
pass:

1. **Reads the host.** That means:
   - thermal zones and hwmon (k10temp Tctl/Tccd, NVMe Composite);
   - `/proc/meminfo` (memory and swap);
   - `nvidia-smi --query-gpu` on LA, or `tegrastats` on the Jetson;
   - free disk;
   - `systemctl list-units --failed`, compared with a baseline recorded once
     at install;
   - new kernel journal records since the last check, compared with the
     documented fault baseline.
2. **Steps the ladder** (`den_ops/policy.py`):

   | Level | Step | Action |
   |---|---|---|
   | 0 | normal | none |
   | 1 | `cadence_reduced` | `POST …/activity-loop/cadence {"level":"reduced"}`: the JEV interval doubles and the pose rate halves |
   | 2 | `deep_stack_paused` | as 1, plus `POST …/activity-loop/stop {"reason":"monitor_backoff"}`; the brain then holds ambient only |
   | 3 | `loop_stopped` | as 2, and latched until the owner runs `den_ops release --confirm` |

   **Activity-loop calls.** These are AL-3's routes, called with the
   monitor's Lab token, which has the `read` and `record` capabilities.
   - **Every check:** the monitor reads `GET …/activity-loop` (`state`,
     `stopped_reason`, `cadence`) and re-asserts the cadence and the stop,
     because neither survives a service restart.
   - **On recovery:** it sends `POST …/resume {"reason":"monitor_backoff"}` and
     `POST …/cadence {"level":"normal"}`. It sends the cadence call only if the
     monitor reduced the cadence itself.
   - **The owner's stop wins.**
     - The monitor never POSTs a stop over an `operator_stop`, because a
       stop overwrites the reason.
     - It only resumes a `monitor_backoff` stop. The loop refuses anything
       else with 409.
     - When the owner's stop outlives a back-off, the monitor records
       `actuator.owner_stop` ("left stopped: the owner stopped it") every
       check, which shows in the banner and in a Home Assistant notice,
       until the owner resumes the loop.
     - The monitor never resumes the owner's stop.

   - **Escalation:** a threshold must hold for 2 checks before the level
     rises. A new kernel fault record, or another host's level, escalates at
     once.
   - **Hysteresis:** each threshold clears only `clear_margin` below its
     trigger.
   - **Recovery:** one step per 10 quiet minutes. Recovery from level 2
     resumes the loop, but only if the monitor stopped it.
   - **Faults:** a fault record holds its level for 30 min.
3. **Alerts.** It posts the host's status to the gateway every pass; that
   status is the Home banner. It calls Home Assistant `notify` on a level
   change or a new notice, rate-limited to one notice every 15 min.
   - Every URL must be https or loopback http.
   - Home Assistant on a LAN or tailnet-only path may use plain http, but
     only with `"allow_plain_http": true` in its block.
4. **Never touches anything else.** Every command passes a read-only
   allow-list (`hostio.check_readonly`). `systemctl restart`, `reset-failed`,
   `stop`, `journalctl --vacuum`, `nvidia-smi -r` and the like are refused
   before a process is spawned. The baselines are only read. The failed-unit
   baseline is written once by `den_ops baseline-units --write`, which
   refuses to overwrite it.

### Which monitor acts

Only the Jetson monitor calls the activity loop. The LA monitor senses and
alerts, and the Jetson monitor reads the LA level from
`GET /api/den-ops/v1/status`. So LA heat, memory or GPU pressure also backs
off the loop that drives the deep-stack work, and the two monitors never
fight over the loop.

- **Latches propagate.** A latched level 3 on LA latches the Jetson too.
  Release both after checking.
- **Stale hosts drop out.** A host whose status is stale (silent over 300 s)
  no longer holds the other host's level; the banner shows "monitor silent"
  instead.

### Limits (the thresholds in the example configs)

The thresholds come from limits already in the repo; none is new except NVMe.

| Signal | Levels 1 / 2 / 3 | Source |
|---|---|---|
| LA GPU temperature | 74 / 77 / 79 °C | the S1.4 live thermal ladder (`docs/LA-SPEED-OPERATIONS-RUNBOOK.md`), under the 80 °C pilot guard |
| LA CPU Tctl | 85 / 88 / 90 °C | live shed at 88 and abort at 90 (`docs/OCCUPANCY-GATED-MODELS-PLAN.md`) |
| LA CPU Tccd | 84 / 86 / 88 °C | abort at Tccd > 88 |
| LA NVMe Composite | 65 / 70 / 75 °C | **proposed**: the repo records idle below 47 °C and has no limit |
| LA RAM available | 12 / 10 / 8 GiB | the existing ≥ 8 GiB RAM admission check |
| LA GPU memory free | 16 / 12 / 8 GiB | the existing ≥ 16 GiB free VRAM check |
| LA disk free | 100 / 75 / 50 GiB | the existing ≥ 50 GiB check |
| Jetson max thermal zone | 75 / 78 / 80 °C | `jetson_lifecycle.MAX_CELSIUS = 80` |
| Jetson RAM available | 4 / 3.5 / 3 GiB | `jetson_lifecycle` 3 GiB minimum |
| Jetson recording disk free | 30 / 25 / 20 GiB | `jetson_lifecycle` 20 GiB minimum |
| Swap used | LA 25 / 50 / 75 %, Jetson 40 / 60 / 80 % | proposed |

- **Other cadence controls.** The LA host's own thermal ladder still sheds
  V-JEPA and Qwen at 74 and 77 °C. Any other cadence control can be added as
  plain calls in the config's `cadence` block (`reduce` and `restore`).
- **Without `loop.cadence_url`,** level 1 records and alerts only.
- **The durable owner off switch** is still `"enabled": false` in the loop
  config, because a stop does not survive a restart.

## Tests

```bash
cd stack/ops/den-activity-lighting/monitor
python -m pytest tests -q
node tools/run-web-gateway-lighting-brain-tests.mjs   # from the repo root
```

- **Monitor and soak:** offline, with fixtures for recorded and synthetic
  output: sysfs trees, meminfo, `nvidia-smi`, `tegrastats`,
  `systemctl --failed`, kernel journal JSON and a brain state document.
- **Gateway:** runs the real gateway against a fake brain.

# lighting-brain

The den activity-aware lighting brain (AL-1, Engineered-Lighting/home#321).
It is a central, explainable rule service. It takes occupancy, zone, activity,
pose, health and cost events from the edge producers and decides what Home's
four virtual den fixtures do. Every change carries a "why" and lands in a
20-item decision log.

It builds to the den activity-lighting interface contract v1 (schemas
`activity-lighting-events-1`, `activity-lighting-override-1` and
`activity-lighting-state-1`).

- **Runtime:** Python 3.12, standard library only (plus `tzdata` for the
  night window's time zone).
- **Output:** virtual only. The brain publishes state. It never sends a
  physical command.

## Layout

| Path | What it is |
|---|---|
| `lighting_brain/settings.py` | All levels, CCTs and timings (`settings_version`) |
| `lighting_brain/inputs.py` | Event validation, plus the input store with ttl, stale and future handling |
| `lighting_brain/rules.py` | The rules v1 table: a pure `evaluate(inputs, overrides, geometry, settings, now, previous)` |
| `lighting_brain/aim.py` | The over-shoulder aim planner: hand point, fixture choice, head sphere, and the `solveAim` port |
| `lighting_brain/geometry.py` | The private geometry export (`lighting-brain-geometry-1`) |
| `lighting_brain/brain.py` | The stateful shell: ingest, overrides, decision log, state document, SSE frames |
| `lighting_brain/server.py` | The loopback HTTP API and producer-token auth (`http.server`) |
| `config/*.example.json` | A synthetic geometry, placeholder tokens, and default settings |

## Run locally

```bash
cd stack/services/lighting-brain
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

To run the service against the synthetic example geometry:

1. Copy `config/tokens.example.json` to a private path outside the repo, for
   example `~/.config/lighting-brain/tokens.json`.
2. Replace each token with a random value of at least 24 characters, for
   example from `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
3. Start the brain with the variables below (POSIX shell):

```bash
LIGHTING_BRAIN_GEOMETRY=config/geometry.example.json \
LIGHTING_BRAIN_TOKENS=~/.config/lighting-brain/tokens.json \
python -m lighting_brain
```

The brain listens on `127.0.0.1:8101`. To check it:

- `curl -s http://127.0.0.1:8101/healthz`
- `curl -s http://127.0.0.1:8101/api/lighting-brain/v1/homes/victoria/state`
- `curl -N http://127.0.0.1:8101/api/lighting-brain/v1/homes/victoria/stream`

### Tests

All tests run offline with an injectable clock and no sleeps:

| File | What it covers |
|---|---|
| `tests/test_aim.py` | The solver port, hand point, facing, fixture choice, head-sphere rejection |
| `tests/test_inputs.py` | Validation (422s), expired, future and stale events, the reading-run tracking |
| `tests/test_rules.py` | Each rule in the table, the night window, purity, settings |
| `tests/test_timeline.py` | Replays of every MVP "Done" case through a real `Brain` with producers at contract cadences |
| `tests/test_service.py` | The state document shape, SSE framing, config files, and the HTTP API on an ephemeral loopback port |

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `LIGHTING_BRAIN_GEOMETRY` | required | Private geometry export (below). The container default is `/config/geometry.json`. |
| `LIGHTING_BRAIN_TOKENS` | none | Private producer token file (below). |
| `LIGHTING_BRAIN_TOKENS_JSON` | none | The same document inline, for secret stores. It takes precedence over the file. |
| `LIGHTING_BRAIN_SETTINGS` | none | An optional settings file. Omitted fields keep their defaults; unknown fields are refused. |
| `LIGHTING_BRAIN_BIND` | `127.0.0.1` | The brain refuses a non-loopback address unless `LIGHTING_BRAIN_ALLOW_NON_LOOPBACK=1`. Use that only inside a container whose port is published to loopback. |
| `LIGHTING_BRAIN_PORT` | `8101` | |
| `LOG_LEVEL` | `INFO` | Every decision is logged at INFO. |

The brain refuses to start without at least one producer token. It logs a
warning when the geometry is the synthetic example.

### Producer tokens (`lighting-brain-producer-tokens-1`)

```json
{"schema": "lighting-brain-producer-tokens-1",
 "producers": [{"producer": "victoria-ha-bridge", "token": "<random>", "homes": ["victoria"],
                "types": ["occupancy", "zone", "health"]}]}
```

- `homes` and `types` are optional scopes. An event outside its producer's
  scope refuses the whole batch with 403.
- Tokens are compared in constant time.
- Tokens never live in the repo. `.gitignore` here ignores `config/*.json`
  except the `*.example.json` files.

### Geometry export (`lighting-brain-geometry-1`)

This file is exported privately from Home (AL-4). It is never committed.
`config/geometry.example.json` is a synthetic 4.0 × 3.6 m box with fixtures in
its corners, marked `"synthetic": true`.

```json
{"schema": "lighting-brain-geometry-1", "home": "victoria", "room": "den", "frame": "victoria-den",
 "camera_m": [x, y, z],
 "fixtures": [{"fixture_id": "victoria-den-mount-03-virtual-fixture",
               "aim_origin_m": [x, y, z], "horizontal_world_reference": [x, y, 0],
               "limits": {"pan_min_deg": -180, "pan_max_deg": 180, "tilt_min_deg": -90, "tilt_max_deg": 90}}],
 "zones": {"sofa": {"seat_m": [x, y, z], "near_fixtures": ["..."]},
           "desk": {"seat_m": null, "near_fixtures": ["..."]}}}
```

- **Frame:** the registered den room frame: Z-up, metres, floor at z = 0.
- **Fixtures:** for victoria/den, all four contract fixture IDs must be
  present.
- **`aim_origin_m` and `horizontal_world_reference`:** these must be the
  values Home's virtual fixture descriptor uses for `aimAtTarget`
  (`display_origin_m` and `horizontal_world_reference` in
  `app/src/home-3d/equipment-virtual-fixtures.mjs`). The brain's pan/tilt
  check then matches the renderer exactly. `aim_origin_m: null` means the
  fixture can still do ambient, but never the spot.
- **`limits`:** optional; the default is the virtual ±180° pan, ±90° tilt.
- **`zones.<zone>.near_fixtures`:** the fixtures whose ambient that zone
  lights.
- **`zones.sofa.seat_m`:** the sofa mark from Home's selector. A posed person
  more than `pose_seat_max_m` (1.5 m) from it horizontally gets no spot.
- **`camera_m`:** the OAK camera position. It settles the facing direction:
  facing points away from the camera. Without it, the brain uses the
  anatomical shoulder labels.

## API

All routes are under `/api/lighting-brain/v1/homes/{home}/`. An unknown home
returns 404.

| Method and path | Auth | Body and result |
|---|---|---|
| `POST …/events` | `Authorization: Bearer <producer token>` | `{"schema":"activity-lighting-events-1","events":[…]}` returns `{"ok":true,"accepted":n,"ignored":[{"index","type","reason"}]}` |
| `POST …/overrides` | gateway session | `{"schema":"activity-lighting-override-1","fixture_id","reason":"change\|aim\|undo\|redo","at"}` returns `{"ok":true,"fixture_id","paused_until"}` |
| `DELETE …/overrides/{fixture_id}` | gateway session | returns `{"ok":true,"fixture_id","cleared":bool}` |
| `GET …/state` | gateway session | the `activity-lighting-state-1` document |
| `GET …/stream` | gateway session | `text/event-stream`: `retry: 3000`, then the full state on every change and every 5 s |
| `GET /healthz` | none (loopback) | `{ok, home, room, settings_version, synthetic_geometry, uptime_s, events:{accepted, ignored_*}}` |

### Event handling

- **Whole-batch validation.** One bad event refuses the batch with 422, and
  nothing in it is applied. The response names the index:
  `{"ok":false,"error":"unknown_event_type|event_invalid|…","detail","index"}`.
- **Unknown `type`:** refused with `unknown_event_type` (422).
- **Ignored events.** These are accepted HTTP-wise but not applied. They are
  reported in `ignored` and counted in `/healthz`:
  - `expired`: now is past `observed_at + ttl_s`.
  - `future`: `observed_at` is more than 5 s ahead of the brain's clock.
  - `stale`: older than the newest event already applied for the same input
    (the occupancy, zone, activity, pose or health component).
- **Other statuses:**
  - 400: invalid JSON.
  - 401: missing or unknown token.
  - 403: the event is outside the producer's scope.
  - 411: no `Content-Length`.
  - 413: the body is over 256 KiB.

### SSE frames

```text
id: 12
event: state
data: {"schema":"activity-lighting-state-1",…}

```

- The JSON is compact, so each message has exactly one `data:` line.
- A "change" means anything other than `generated_at` and the `*age_s`
  fields.
- When nothing changes, a full state is still pushed every
  `stream_period_s` (5 s).

## Rules v1

`RULES` in `rules.py` is the table. Rules run in this order, each owning its
outputs and its "why".

| Rule | Condition | Output | Settings |
|---|---|---|---|
| `room_off` | No occupancy (room or zone) for 120 s; also at startup. While occupancy is unknown, it needs 30 min with no person evidence instead. | All off, 3 s fade | `room_off_after_s`, `room_off_fade_s`, `unknown_occupancy_off_s` |
| `zone_ambient` | Zone present. It holds for `zone_hold_s` after the zone empties while the room is occupied, and freezes when the room empties so `room_off` ends the scene. | Nearby fixtures' radial and down at 0.30 / 2700 K, 2 s fade. Night (23:00–06:00 local) is 0.10 / 2200 K. | `ambient_*`, `night_*`, `zone_hold_s`, `timezone` |
| `degraded_ambient` | The `jev` or `deep_stack` health event is not ok (including `jev_egress_blocked_indeterminate`), has expired, or has not been received yet. Also: occupancy is unknown (see below). | Ambient only, never a new spot. For unknown occupancy: hold, with the why "Occupancy sensor offline · holding". | (health ttl) |
| `reading_spot_on` | All of the conditions listed below this table | Spot at 0.8 / 3500 K, 1.5 s fade, on the hand point, from the best fixture behind and above the reader with no beam through the head | `reading_hold_on_s`, `spot_*`, `hand_*`, `head_*`, `min_behind_cos`, `max_beam_m`, `pose_seat_max_m` |
| `reading_spot_off` | No `reading` for 10 s, or any precondition lost (which turns the spot off at once) | The spot fades out over 1.5 s | `reading_off_after_s`, `spot_fade_s` |
| `manual_pause` | An override for a fixture | The fixture holds its last output (`paused_until` and `pause_reason` set) until the room has been empty for `room_off_after_s` or `pause_max_s` (30 min) has passed. The spot moves to the next best fixture. | `pause_max_s` |

`reading_spot_on` needs all of these:

- JEV has said `reading` for at least 3 s, measured on `observed_at`.
- The sofa zone is present. If the sofa zone is unknown, JEV's `zone_hint`
  must be `sofa`, or, with no hint, the pose must pass the seat check.
- The best known people count is exactly 1. It is the largest count across
  occupancy, zone, activity and pose.
- The activity's `zone_hint` is not `desk`.
- JEV and the deep stack are healthy.
- A fresh pose has both shoulders.

**Unknown, not absent** (CONTRACT "Producer notes"):

- **Zone states:** a zone is `present`, `absent` or `unknown`. It is unknown
  when it has never been reported (not drawn yet, cold start), or when its
  event expired while Frigate is unhealthy.
- **Unknown zones:** an unknown zone is lit when JEV's `zone_hint` names it.
- **Unknown occupancy:** occupancy is unknown when Frigate reported
  unhealthy (`health` `frigate` not ok or no longer fresh) and no occupancy or
  zone event is fresh. Then the brain:
  - holds the zone lighting as it was when the bridge's last word expired;
  - reports `degraded_ambient` with "Occupancy sensor offline · holding";
  - keeps a spot that was already on while its reading preconditions hold,
    but starts no new spot;
  - turns the room off only after `unknown_occupancy_off_s` (30 min) with no
    person evidence. Evidence is an occupancy or zone `present`, or a fresh
    `activity` or `person_pose` with `person_count` ≥ 1.
- **JEV auto-release:** after `jev` `ok:false` (for example
  `jev_egress_blocked_indeterminate`), `ok:true` lets the spot return once a
  fresh 3 s reading hold passes.

**Hand point.** The planner works from the shoulder midpoint:

1. It goes 0.35 m forward along the facing direction, tilted down by
   `head_pitch_deg`. It assumes positive pitch means looking down, clamped to
   -45°…75°.
2. It then goes 0.25 m down.

Facing is perpendicular to the shoulder line, pointing away from the camera
(the camera sees the reader's back).

**Fixture choice.** The planner takes the following steps:

1. It rejects:
   - a fixture with no position;
   - a pan/tilt the ported `solveAim` can't execute (its limits, or a
     vertical target);
   - a beam longer than 8 m;
   - any beam segment within 0.15 m of `head_m`, or of the shoulder midpoint
     plus 0.25 m when the pose has no head;
   - a fixture in front of the reader.
2. It scores the rest as `behind_cos + 0.5 if above head height (else -0.5) - 0.02·distance`.
3. The fixture already lit gets a +0.15 bonus, so near-ties don't make the
   spot hop between fixtures.
4. The target only moves when the hand point moves more than 0.10 m.

**Solver port.** `aim.solve_aim` ports `solveAim` and `calibratedBasis` from
`app/src/home-apartment-aiming.js`, which `aimAtTarget` in
`app/src/home-3d/equipment-virtual-fixtures.mjs` applies with the same
conventions:

- forward is the horizontal world reference, and right is `(ref.y, -ref.x, 0)`;
- pan is `atan2(Δ·right, Δ·forward)`;
- tilt is `atan2(-Δz, horizontal)`, and positive tilt points down;
- the default limits are pan ±180° and tilt ±90°;
- a straight-down target leaves pan indeterminate and is not executable.

**Decisions.** One entry is made per evaluation that changes something
visible: the mode, a fixture's ambient level or CCT, spot on or off, or a
pause. It is attributed to the most significant rule among the changes, in
this order: `manual_pause`, `room_off`, `degraded_ambient`, `reading_spot_on`,
`reading_spot_off`, `zone_ambient`. Each entry has
`{at, rule, why, inputs, change}`. The log keeps the newest 20, newest first.

## For AL-5/6: gateway route and deployment

Out of scope here. This is exactly what the gateway and deployment lanes
need.

### 1. Gateway route

Add a route to the `routes` table in `web-gateway/server.mjs`:

- **prefix:** `/api/lighting-brain`
- **target:** `http://127.0.0.1:8101/api/lighting-brain`, with an env
  override such as `HOME_WEB_LIGHTING_BRAIN_TARGET`.
- **allow list:**
  - `POST /v1/homes/{home}/events`
  - `POST /v1/homes/{home}/overrides`
  - `DELETE /v1/homes/{home}/overrides/{fixture_id}`
  - `GET /v1/homes/{home}/state`
  - `GET /v1/homes/{home}/stream`
- **ws:** `false`.

### 2. Producer auth through the gateway

Today every proxied route first passes `requireAuth` (the session cookie or
Basic), so an edge producer that only carries its bearer token gets 401 at
the gateway.

- **The change:** let `POST …/events` that carries
  `Authorization: Bearer …` through without the gateway session. This is the
  same pattern as the native-agent bearer ingress. The brain checks the token.
- **Headers to forward:** only `authorization`, `content-type`,
  `content-length` and `user-agent`.
- **Everything else** (overrides, state, stream) keeps the gateway session.
  `cleanHeaders` already strips the cookie and Basic before the brain.

### 3. Server-Sent Events through the gateway

- **No buffering or transform:** the brain sends `X-Accel-Buffering: no` and
  `Cache-Control: no-store`.
- **Response timeout:** `PROXY_RESPONSE_TIMEOUT_MS` only waits for the
  response headers, which the brain sends at once.
- **Idle timeout:** the 120 s socket timeout never fires, because the brain
  pushes every 5 s.
- **Reconnects:** Home's `EventSource` reconnects after `retry: 3000`.

### 4. Same host

The brain binds loopback, so the gateway must run on the brain's host
(hav-ubuntu). If it can't, the deployment needs a reviewed non-loopback bind
plus gateway-to-brain auth, and that is a contract change.

### 5. Service unit

Run `python -m lighting_brain` from this folder with Python 3.12 and
`pip install -r requirements.txt`. Use either a systemd unit (like
`stack/services/supervisor`) or the Dockerfile here.

- **Restart:** `Restart=always`.
- **Health:** `GET /healthz`.
- **Logs:** decisions are logged at INFO.

### 6. Private files on the host

- The geometry export from Home (AL-4).
- The producer tokens: one per producer, for the AL-2 bridge and the AL-3
  Jetson loop. Give each producer its own token.
- Optionally, a settings file.

### 7. Run in a container

```bash
docker build -t lighting-brain .
docker run --network host -v /path/to/private:/config:ro lighting-brain
```

The alternative is to publish the port to loopback:

```bash
docker run -p 127.0.0.1:8101:8101 -e LIGHTING_BRAIN_BIND=0.0.0.0 -e LIGHTING_BRAIN_ALLOW_NON_LOOPBACK=1 -v /path/to/private:/config:ro lighting-brain
```

### 8. Clocks

TTLs compare the producer's `observed_at` with the brain's clock. The Pi,
the Jetson and the LA box need NTP. An event more than 5 s in the future is
ignored as `future`.

## Assumptions to confirm (contract v1 is silent)

- **`head_pitch_deg` sign:** positive means looking down (the solver's tilt
  convention).
- **Facing:** away from the camera, using `camera_m` from the geometry export.
  It falls back to the anatomical shoulder labels.
- **`inputs.health`:** also lists `camera`, the fourth contract component.
  Unknown or expired health reads `false`.
- **Expired activity:** `activity.label` is `null` once the latest answer has
  expired. `age_s` still shows how old it is.
- **`spot.target_m`:** `null` while the spot is off.
- **The pause clock:** a pause runs from when the brain receives the override.
  The client's `at` is recorded but doesn't move the deadline.
- **A pause set while the room is already empty** lasts the full 30 min.
  Only a vacancy that starts after the override ends it early.
- **Unknown occupancy in the state:** `occupancy.present` is `false` and
  `people_count` is `null`, and the why line says the sensor is offline.
- **Room with no zone:** an occupied room with no zone lit stays off
  (`zone_ambient`: "Room occupied · no zone yet").

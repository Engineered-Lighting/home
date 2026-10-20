# Victoria occupancy and zone bridge (AL-2)

This bridge turns what Frigate sees through the Victoria den camera into events
for the lighting brain, as set out in interface contract v1. It covers den
occupancy, desk and sofa zone presence, and Frigate's health. It is
Engineered-Lighting/home#322, part of the den activity-aware lighting MVP (#320).

It **never controls lights**. It only reads from Frigate and posts events. The
existing Home Assistant automation `den_lights_follow_person_occupancy` is left
alone and remains the offline fallback for the real den lights.

## Design: a small Python service on Frigate's MQTT

```
Frigate (Victoria Pi) ──MQTT──▶ bridge ──HTTPS + producer token──▶ web-gateway ▶ lighting brain (LA)
          │                        ▲
          └─ HA Frigate integration ┘  one GET /api/states on (re)connect, optional
```

The bridge is a single-threaded Python service (3.10 or later). It depends only
on `paho-mqtt`; everything else uses the standard library. It subscribes to
four to six exact Frigate topics, keeps one small state table, and posts contract
events.

**Why this design is the simplest one that works:**

- **Frigate's MQTT is the source.** HA's Frigate entities are built from these
  same topics, so reading them directly removes a hop. It needs no HA token for
  normal running, and `frigate/stats` gives a real liveness signal. HA can't
  show a Frigate process that has hung without going offline.
- **Not HA automations with `rest_command`.** That route needs no new process,
  but it can't buffer, back off, detect silence, or be tested offline. Its
  templates would also hold the logic, which would sit on the Pi untested.
- **Not the HA websocket.** It needs a websocket client library and an
  always-on HA token, and it adds nothing over MQTT except the cold-start
  values. Those come from one plain REST read instead (see below).
- **Only one optional HA read.** Frigate publishes person counts only when they
  change and does not retain them. A bridge that restarts while someone sits
  still on the sofa would otherwise not know they are there. On every MQTT
  (re)connect the bridge reads HA's `/api/states` once. HA's Frigate
  integration has been holding those counts, so this fills only the subjects
  the bridge doesn't know yet. MQTT always wins.

### What it reads

| Topic | Use |
|---|---|
| `frigate/available` (retained) | `offline` makes health false and forgets counts. `offline` then `online` means Frigate restarted, so counts reset to 0. Frigate publishes any person it then sees. |
| `frigate/stats` (every 60 s) | Liveness. Health is false when no Frigate message arrives within 150 s, when the camera has `camera_fps` 0, or when it is missing from the stats. |
| `frigate/den_oak/detect/state` (retained) | `OFF` makes health false. |
| `frigate/den_oak/person` | Den people count, which gives `occupancy`. A count of 0 also clears every zone. |
| `frigate/<zone>/person` | Desk or sofa count, which gives `zone`. Only zones listed in the config are used. |

### What it sends (`POST .../homes/victoria/events`)

| Event | When | `ttl_s` |
|---|---|---|
| `occupancy` `{present, people_count}` | on change, plus every 30 s | 90 |
| `zone` `{zone: desk\|sofa, present, people_count}` | on change, plus every 30 s, for each configured zone | 90 |
| `health` `{component: "frigate", ok, detail}` | on change, plus every 60 s | 150 |

- **Unknown state:** a subject is reported only once its state is known.
  "Unknown" is never sent as "empty". Before any zone message arrives, nothing
  is said about that zone.
- **Unhealthy Frigate:** occupancy and zone heartbeats stop, so the brain's
  copies expire after 90 s. The health event says why. The state is sent again
  at once when Frigate recovers.
- **`people_count: null`:** this happens only when the HA seed had the
  occupancy binary sensor but no usable count sensor. It means present, but
  the count is unknown.
- **Delivery:** `sent_at` is set on each attempt, and `observed_at` is when the
  state was last confirmed.
- **Brain unreachable:** the bridge keeps only the newest event per subject.
  That is at most four events: occupancy, desk, sofa and health. It retries
  with backoff of 2, 4, 8 and 16 s, then every 30 s. Anything past
  `observed_at + ttl_s` is dropped, never sent late. A 4xx refusal, such as a
  bad token or a 422, is logged and dropped, not retried.

## Configuration

Copy [`bridge.example.json`](bridge.example.json) to a private location, for
example `/etc/lighting-brain-bridge/bridge.json`. It contains no secrets. The
secrets are read from files named in it (paths relative to the config) or from
environment variables:

| Secret | Config key | Environment variable |
|---|---|---|
| Brain producer bearer token (required) | `brain.token_file` | `LIGHTING_BRAIN_PRODUCER_TOKEN` |
| MQTT password | `mqtt.password_file` | `BRIDGE_MQTT_PASSWORD` |
| HA long-lived token (only if `ha_seed` is set) | `ha_seed.token_file` | `BRIDGE_HA_TOKEN` |

- **`brain.events_url`:** must be `https` and end in `/homes/victoria/events`.
  Plain `http` is accepted only on loopback, for local testing. Set
  `brain.ca_file` if the gateway uses a private CA.
- **`frigate.zones`:** maps contract zones to Frigate zone names. Leave it `{}`
  until the zones exist in Frigate. After the owner has drawn them, set:

  ```json
  "zones": {"desk": "den_desk", "sofa": "den_sofa"}
  ```

- **`ha_seed`:** optional but recommended. Without it, a restart while someone
  sits still reports nothing until they move enough for Frigate to change a
  count. The default entities are `sensor.<name>_person_count`, then
  `binary_sensor.<name>_person_occupancy`. Override them per subject with
  `ha_seed.entities`, for example:

  ```json
  {"occupancy": ["sensor.den_oak_person_count"]}
  ```

- **`timing`:** defaults to the contract cadences. Change it only through the
  planning session.

**Credentials:** use one dedicated, non-admin, local-only HA user, for example
`lighting-bridge`. The Mosquitto add-on accepts HA users, so the same user
gives the MQTT login. A long-lived token created for that user covers the
seed. The bridge only subscribes and makes one GET.

## Running

```bash
pip install -r requirements.txt
python -m victoria_ha_bridge --config /etc/lighting-brain-bridge/bridge.json
```

Use `--dry-run` to print each batch as a JSON line instead of posting it. No
brain token is needed then, which makes it a safe first check against the live
broker once the owner approves that read. Logs go to stderr, at `--log-level`
INFO by default. They record transitions (MQTT, Frigate health, count changes,
brain reachability), not every heartbeat.

## Deploy options (owner approval required; nothing is deployed by this PR)

1. **Victoria Jetson, systemd unit (recommended for the MVP).** The Jetson is
   already an always-on Linux box in the house, and AL-3's activity loop runs
   there as a service. A plain Python venv and a unit file are the least new
   machinery. It reaches Mosquitto on the Pi over the LAN, and the gateway
   over HTTPS. The cost is that if the Jetson is down, occupancy is lost along
   with activity. The real den lights are unaffected, because the HA
   automation still drives them.
2. **Local Home Assistant add-on on the Pi.** This keeps the bridge next to
   Frigate and Mosquitto, and the Supervisor restarts it. It needs an add-on
   wrapper (Dockerfile, `config.yaml`) and a Supervisor-side install. Move here
   if the Jetson proves less available than the Pi.
3. **On the LA box next to the brain: rejected.** It would carry MQTT across
   sites and make the brain's own host the edge producer. The contract treats
   the Victoria HA bridge as an edge producer.

### What AL-5 and AL-6 need for the unit

- **Command:** `<venv>/bin/python -m victoria_ha_bridge --config /etc/lighting-brain-bridge/bridge.json`,
  with the working directory at this folder (or `PYTHONPATH` pointing to it).
- **Run as:** an unprivileged service user with
  `Restart=always`, `RestartSec=5` and `After=network-online.target`. Secrets
  go in files readable only by that user (mode 0600).
- **Shutdown:** SIGTERM stops it cleanly.
- **Footprint:** a few MB of memory and almost no CPU. It needs no GPU and has
  no disk writes beyond logs to journald, so it needs no thermal back-off.
- **Network egress:** MQTT to the Pi (TCP 1883), HTTPS to the web gateway, and
  optionally HA on the Pi (TCP 8123) for the seed.
- **Live checks after deploy:**
  - the brain's `inputs.health.frigate` is true;
  - `occupancy_age_s` stays under about 30 s;
  - walking in and out flips `occupancy.present`;
  - stopping Frigate gives `health.frigate=false` within 150 s.
- **Owner one-time steps:** create the HA user and token, issue the brain
  producer token, then later draw the zones and set `frigate.zones`.

## Frigate desk and sofa zones

[`frigate-zones-den.proposal.yaml`](frigate-zones-den.proposal.yaml) holds the
proposed `den_desk` and `den_sofa` zones for the `den_oak` camera. The
coordinates are placeholders. The file includes the owner's drawing steps, the
config keys (`objects: [person]`, `inertia: 3`, `loitering_time: 0`, no
`required_zones`, `mqtt.stats_interval: 60`) and the MQTT topics and HA
entities that appear afterwards. Applying it is a host change for later, with
the owner's approval.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests run offline with an injectable clock and a fake brain:

- **Timelines:** enter and leave, desk-to-sofa zone changes, two people,
  heartbeat cadence, Frigate going silent, and a Frigate restart, all replayed
  from synthetic Frigate MQTT timelines in
  [`tests/fixtures/mqtt/`](tests/fixtures/mqtt/README.md).
- **Brain down:** buffering, backoff, expiry and rejection.
- **HA seed:** from synthetic `/api/states` fixtures.
- **Plumbing:** the HTTPS transport, the config checks, the zone proposal and
  the runner's paho callbacks.

The fixtures are synthetic. Swap in redacted recordings once the owner approves
a capture.

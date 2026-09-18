# lighting-publisher

Home of the Living Lights belief publisher (plan rev 5). This directory holds
two Python packages and the container that runs them:

- `lighting_publisher/` -- the publisher itself: the two story machines, the
  MQTT and observer inputs, the MQTT publishing and the process (milestone M4);
- `lighting_beliefs/` -- the Jev question set, packet builder, leak guard and
  egress gate (milestone M3). **Nothing in M4 imports it**: the shadow
  publisher sends nothing outside the house.

## `lighting_beliefs` (M3): questions, packet, leak guard, gate

| Module | Purpose |
|---|---|
| `lighting_beliefs/questions.py` | The five Jev questions as data: `tv_attention` (Score, 3 levels), `eating` (Noul), `food_prep`, `settling`, `rest_state` (Score, 3 levels each). `build_questions()`, `questions_as_dicts()` (v1 HTTP shape), `questions_as_sdk()` (needs the `cloud` extra), `QUESTION_IDS`. |
| `lighting_beliefs/reduce.py` | Probability-only reduction: `P(level >= k)` per Score, `P(yes)` per Noul. Vendor `confidence` is stored as metadata and never gates. |
| `lighting_beliefs/packet.py` | Builds and validates the `lighting-beliefs-state/v1` packet from the observer's typed observations. Fail-closed on its own: recursive allow-list with list-vs-mapping strictness, every key and scalar run through the leak guard's roster-free patterns (a hit raises `PacketError` naming the path, never the text; camera, zone and unknown keys appear in error paths as `<key#N>` placeholders, never as the caller's text), 240-character NFKC/ASCII-only text cap with format characters stripped, ages in seconds only (above a day raises; an epoch is a caller bug, not a day-old belief), count caps (16 cameras, 32 zones, 16 media rows, 6 people, 8 claims), name-collision detection after capping, anonymous track labels, no names, relationships, entity ids, modes, light levels or clock. `content_signature()` for change detection with ages bucketed by schema path. |
| `lighting_beliefs/leak_guard.py` | Scans a packet for entity ids (every Home Assistant domain a lighting observer can name, any case, whitespace around the dot tolerated for snake_case object ids), `hav-*` containers, LAN addresses, bare `.local`/`.lan`/`.home`/`.internal` hostnames, JWT prefixes, model names, the OS username (parameter), RTSP and HTTP URLs, digit runs and separated digit groups (phone numbers, thousands separators), emails, ISO timestamps, numeric dates in any order and separator (OSD overlays, two-digit years), month-name dates, times of day (`19:25`, `7pm`, `19h25`, `1925 hours`), Unicode format characters, non-ASCII text, and roster names as whole names and per name part of 3+ characters, where parts of 5+ characters also match inside longer words (bare possessives, plurals) and shorter parts stay whole-word so `Teo` does not block `stereo` (roster file must be mode 0600). Text is NFKC-normalised with format characters removed before matching. Findings name the pattern and key path only; a leaking key is reported as a `<key#N>` placeholder so nothing that journals the finding echoes it. Findings block; there is no strip helper. |
| `lighting_beliefs/egress.py` | `EgressGate`: allows a call only when the egress record (a regular file, no symlink, not group- or other-writable, optionally owner-checked) is enabled and lists the scope, `TYPESAFE_EGRESS=1`, and the HA kill-switch mirror reads `on` with a finite, non-negative age strictly below 300 s (NaN, infinite, negative beyond 5 s skew, future or non-numeric ages deny as `toggle_invalid`); 6 calls per minute, one in flight, an unreleased ticket expires after 120 s and is journaled as `in_flight_expired`; every decision journaled as JSONL. |

## `lighting_publisher` (M4): the shadow publisher

| Module | Purpose |
|---|---|
| `stories.py` | Every timing constant and threshold of stories T and S. Nothing else hard-codes a duration. |
| `beliefs.py` | The belief seam. M4 ships only `NoBeliefs`, so every machine takes its deterministic path. |
| `tv_machine.py` | Story T: TV_OFF, PROVISIONAL, WATCHING, AWAY_HOLD, UNATTENDED, NAPPING. |
| `estimator.py` | Story S: `likely_asleep` / `awake` / `away`, credible people, arrivals and departures. |
| `activity.py` | Per-zone activity and the zone map from `config/zones.json`. |
| `health.py` | Mirror fresh and observer fresh and MQTT up, or the heartbeat stops. |
| `inputs/mirror.py` | The retained Home Assistant mirror and Frigate's person counts, parsed from MQTT messages. |
| `inputs/observer.py` | The observer poll: http(s) only, no proxy, no redirect, one short timeout. |
| `publish/discovery.py` | The entity set, the `_shadow` twins and the Home Assistant discovery payloads. |
| `publish/state.py` | Retained publishing on change or every 60 s, the startup clear, availability. |
| `journal.py` | The decision journal: daily JSONL, 30-day rotation, never raises. |
| `main.py` | The process: connect, subscribe, poll, tick every second, serve `/healthz`. |

### What it publishes

| Entity | State | Attributes |
|---|---|---|
| `binary_sensor.living_lights_tv_watching` | `ON` / `OFF` / `None` | `state_machine`, `p_attention`, `since`, `request_id` |
| `sensor.living_lights_asleep_estimator` | `likely_asleep` / `awake` / `away` / `unknown` | `since`, `reassert`, the evidence dict |
| `sensor.<camera>_<zone>_activity` | `cooking` / `eating` / `idle` / `unknown` | -- |
| `sensor.lighting_publisher_heartbeat` | ISO 8601, every 60 s | -- |

With `PUBLISHER_MODE=shadow` (the default) every object id and unique id gets
a `_shadow` suffix, the heartbeat included, so no generated Home Assistant
template reads it: `binary_sensor.living_lights_publisher_fresh` stays off and
the legacy lighting path keeps the house. `live` drops the suffix. The two
modes share no topic, no availability topic and no last will.

The full topic schema is source-derived in
`docs/qa/home-app-feature-audit.md#lighting-publisher-mqtt-topics`.

### Where its facts come from

No Home Assistant token exists in this process, and there is nowhere to put
one. Home Assistant state arrives only on the retained mirror
(`living_lights/mirror/<domain>/<object_id>`, plus
`living_lights/mirror/heartbeat` once a minute) published by
`ha-config/packages/living_lights_mqtt_mirror.yaml`. Person evidence arrives on
`frigate/<camera>/person` and `frigate/<camera>/<zone>/person`, for the three
cameras in `config/zones.json` only -- somebody on the driveway is not somebody
in the house. Typed presence is polled from `OBSERVER_URL` every 5 s.

### The health gate

`health.py` is a gate on deciding, not only on publishing. When the mirror is
stale (300 s), the observer is stale (15 s) or MQTT is down:

- the heartbeat stops, so `publisher_fresh` goes off 180 s later;
- every belief is published as unknown (`None` for the binary sensor, which is
  how Home Assistant's MQTT platform reads "no state");
- the machines are **not** advanced, because their inputs are untrusted;
- on recovery they are rebuilt -- the estimator keeping its state, the TV
  machine starting from TV_OFF -- so that an hour of silence is not counted as
  an hour of quiet and does not latch the house asleep on the first good tick.

### Known gaps in M4

- **The manual hold is inert.** The estimator honours a manual flip of the
  latch for 45 minutes when `input_text.living_lights_asleep_writer` says a
  person wrote it, but that helper is not in the mirror's entity list. The
  parser reads it the moment it is mirrored; until then no flip is seen as
  manual.
- **A lighting command is read as a brighten.** The per-zone
  `..._last_command_id` helpers carry an opaque id, so the publisher cannot
  tell a brighten from a dim and counts every explicit command as the
  estimator's brighten input. Somebody commanding the lights is somebody
  awake, which is the legacy reading; the shadow report counts how often it
  fires.
- **There is no wake command.** Nothing in the mirror carries one (the
  good-morning energize never reached its helper), so `last_wake` is always
  None and the latch clears on occupancy, an arrival, a departure or a
  command.
- **No beliefs.** `NoBeliefs` means `p_attention` is None, NAPPING is
  unreachable, and every activity sensor reads `idle`. Jev arrives in M6.

### Running it

```bash
cd stack/services/lighting-publisher
python3 -m unittest discover -s tests -t . -v      # 180+ tests, no socket opened

# The container (built and run by the stack; see ../../docker-compose.yml):
#   image  home-ai-voice/lighting-publisher:local
#   name   hav-lighting-publisher
#   health http://127.0.0.1:8105/healthz
```

Environment: `PUBLISHER_MODE`, `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`,
`MQTT_PASSWORD_FILE` (a file under `/run/secrets`, never a value),
`OBSERVER_URL`, `JOURNAL_DIR`, `JOURNAL_RETENTION_DAYS`, `BIND_HOST`,
`PUBLISH_BIND_ADDR`, `PORT`, `TZ`, `ZONES_PATH`. The process exits **78** if
either bind address would make `/healthz` reachable off loopback, the same
refusal the video labeler makes.

Deploy, verify, score and roll back: `docs/RUNBOOK.md`, "Living Lights belief
publisher (shadow)". Score the shadow nights with `tools/shadow-report.py`,
which reads the journal and raw Frigate rows -- never the estimator's own
rule.

## No egress exists yet

Nothing in `lighting_beliefs` opens a socket, imports an HTTP client, or reads an API
key, and nothing in `lighting_publisher` imports `lighting_beliefs` (a test
enforces it). The publisher's only outbound connections are the house's own
broker and the observer on the LAN. The TypeSafe SDK is only an optional extra (`pip install
'lighting_beliefs[cloud]'`, pinned to `typesafe-sdk==0.6.0`) and is imported
lazily by `questions_as_sdk()`. The container, the MQTT mirror, the observer
adapter and the actual call site arrive in M4 and later, and every call must
pass `leak_guard.assert_clean()` and an allowed `EgressGate.request()` first.

## Install and test

```bash
cd stack/services/lighting-publisher
python3 -m pip install -e .          # stdlib only (lighting_beliefs)
python3 -m unittest discover -s tests -t . -v
```

From the repository root:

```bash
python3 -m unittest discover -s stack/services/lighting-publisher/tests -t stack/services/lighting-publisher -v
```

Tests are pure logic: no network, no Home Assistant, no observer. Golden
packets for a TV evening and a quiet night live in `tests/fixtures/`.

## Egress record

`egress-record.example.json` is a redacted example of the record the gate
reads. The real record is signed by the owner, lives outside the repo, and is
enabled only for the scopes it lists. It must be a regular file (not a
symlink) with mode 0600 or 0644 (no group or other write bit); the gate
refuses it as `record_insecure` otherwise, so a machine with umask 0002 needs
an explicit `chmod`. Disabling the record is the first rollback rung; the HA
kill switch and `TYPESAFE_EGRESS` are the other two.

See `docs/ARCHITECTURE_DECISIONS.md`, ADR-005, for the outbound-traffic
amendment this package requires.

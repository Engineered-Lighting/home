# Den activity-aware lighting: interface contract v1

All lanes build to this. Change it only through the planning session, so no
lane drifts. Schemas are JSON. Times are Unix seconds (float). Coordinates are
the registered den room frame in metres (`victoria-den`, the same frame as the
saved desk target and the equipment registration).

## Service and transport
- **Service:** `stack/services/lighting-brain/`, Python 3.12 with no heavy
  dependencies. It runs on the LA box (hav-ubuntu) as an always-on service.
- **Routes:** it listens on loopback, and the existing `web-gateway` exposes it
  under `/api/lighting-brain/v1/` with the gateway's existing auth.
- **Edge producers** (Jetson perception-lab, Victoria HA bridge) authenticate
  with a per-producer bearer token from private config, never in the repo.
- **Home:** reads state through the gateway with its existing session.

## Events into the brain: `POST /api/lighting-brain/v1/homes/{home}/events`
The body is `{"schema":"activity-lighting-events-1","events":[...]}`. Every
event has these fields:
`{type, home, room, source, observed_at, sent_at, ttl_s}`

The brain ignores an event after `observed_at + ttl_s`, and records that it did.
Unknown `type` values are refused (HTTP 422).

| type | extra fields | producer | typical cadence / ttl |
|---|---|---|---|
| `occupancy` | `present` (bool), `people_count` (int or null) | HA/Frigate bridge (lane AL-2) | on change, plus a 30 s heartbeat; ttl 90 s |
| `zone` | `zone` (`desk` or `sofa`), `present` (bool), `people_count` (int or null) | HA/Frigate bridge (AL-2) | on change, plus a 30 s heartbeat; ttl 90 s |
| `activity` | `label` (one of `reading`, `computer_work`, `phone_use`, `resting`, `transition`, `other`, `insufficient_evidence`); `confidence` (0–1 or null); `person_count` (int or null); `model` (e.g. `jev-1.13.0`); `facts_key` (str); `zone_hint` (`desk`, `sofa` or null) | Jetson activity loop (AL-3) | each JEV answer, every 2–10 s while occupied; ttl 12 s |
| `person_pose` | `people`: list of `{track_id, shoulders_m:{left,right}` (each `[x,y,z]` or null), `head_m` (`[x,y,z]` or null), `head_pitch_deg` (number or null), `approximate:true}`; `person_count` (int) | Jetson activity loop (AL-3) | each placed frame, about 0.5–1 Hz; ttl 10 s |
| `health` | `component` (`jev`, `deep_stack`, `frigate` or `camera`), `ok` (bool), `detail` (str or null) | each producer | on change, plus a 60 s heartbeat; ttl 150 s |
| `cost` | `component:'jev'`, `calls` (int, since the last event), `est_usd` (number or null) | AL-3 | each minute; ttl 300 s |

## Manual override: `POST /api/lighting-brain/v1/homes/{home}/overrides`
- **Set:** `{"schema":"activity-lighting-override-1","fixture_id":"...","reason":"change|aim|undo|redo","at":<s>}`.
- **Effect:** the brain pauses automation for that fixture until the room
  empties or 30 min pass.
- **Clear:** `DELETE .../overrides/{fixture_id}` hands the fixture back to Auto.
- **Source:** Home calls this from the W2 hand-change seam
  (`releaseFixtureForHandChange`).

## State out
**Routes:** `GET /api/lighting-brain/v1/homes/{home}/state`, plus
Server-Sent Events at `.../stream`, which pushes the full state on every change
and every 5 s.

```json
{
  "schema": "activity-lighting-state-1",
  "home": "victoria", "room": "den", "generated_at": 0.0, "brain": "online",
  "mode": "off | ambient | night_ambient | reading | degraded_ambient",
  "why": "Reading at sofa · spotlight on",
  "occupancy": {"present": true, "people_count": 1, "zones": {"sofa": true, "desk": false}},
  "activity": {"label": "reading", "confidence": 0.8, "age_s": 1.2, "source": "jev"},
  "fixtures": [
    {"fixture_id": "victoria-den-mount-03-virtual-fixture",
     "ambient": {"radial_level": 0.3, "down_level": 0.3, "cct_k": 2700, "fade_s": 2.0},
     "spot": {"on": true, "level": 0.8, "cct_k": 3500, "target_m": [0, 0, 0], "fade_s": 1.5},
     "paused_until": null, "pause_reason": null}
  ],
  "decisions": [{"at": 0.0, "rule": "reading_spot_on", "why": "...", "inputs": {"activity": "reading", "zone": "sofa"}, "change": "..."}],
  "cost": {"jev_calls_last_hour": 0, "est_usd_last_hour": null},
  "inputs": {"occupancy_age_s": 0.0, "activity_age_s": 0.0, "pose_age_s": 0.0, "health": {"jev": true, "deep_stack": true, "frigate": true}},
  "settings_version": "v1"
}
```

- **`decisions`:** the last 20 decisions, newest first.
- **`fixtures`:** always lists all four registered fixture IDs:
  - `victoria-den-proposed-fixture-01` (mount 1, walls A/D)
  - `victoria-den-proposed-fixture-02` (mount 2, walls B/D)
  - `victoria-den-mount-03-virtual-fixture` (socket 3, walls B/C)
  - `victoria-den-mount-04-virtual-fixture` (socket 4, walls A/C)
- **Fixtures off:** `ambient` levels are 0 and `spot.on` is false.
- **Spot target:** `spot.target_m` is a room-frame point. Home applies it with
  the existing `aimAtTarget(id, point)`, and the renderer solves pan/tilt. The
  brain also checks feasibility with the same solver math.
- **Brain unreachable:** Home shows "brain offline" and leaves the fixtures in
  their last state. No new automatic changes.

## Rules v1 (adjustable settings, `settings_version`)

| Rule | Condition | Output |
|---|---|---|
| `room_off` | no occupancy for 120 s | all off (fade 3 s) |
| `zone_ambient` | zone present | nearby fixtures' ambient at 0.30 / 2700 K (night 23:00–06:00: 0.10 / 2200 K), fade 2 s |
| `reading_spot_on` | `activity=reading` held ≥ 3 s, person in the sofa zone, exactly 1 person, deep stack healthy, pose fresh | spot on the hand-region point: about 3500 K, level 0.8, from the best behind/above fixture with no beam through the head |
| `reading_spot_off` | no `reading` for 10 s, or any precondition lost | spot fades out over 1.5 s |
| `degraded_ambient` | JEV or deep stack unhealthy or stale | ambient only, no spot |
| `manual_pause` | an override for a fixture | that fixture is untouched until the room is empty or 30 min pass |

**Hand-region point:**
- Start from the shoulder midpoint.
- Move about 0.35 m forward along the facing direction. Facing is the
  perpendicular to the shoulder line, on the side away from the camera-facing
  back, adjusted by head pitch.
- Move about 0.25 m down.

These are approximate on purpose. The fixture choice prefers fixtures behind
the person (opposite the facing direction) and above head height, and it
rejects any beam segment within 0.15 m of `head_m`.

## Sofa target
The sofa zone and seat come from the owner's one-time mark in Home's selector
(lane AL-4). They are saved alongside the desk target in private equipment
state. The brain reads them from a private config export, never from the
browser.

## Producer notes (v1 clarifications, 2026-10-19)
These came out of AL-3 and change no schema.
- **`person_pose.track_id`:** opaque. AL-3 emits a per-frame `slot-N`; there is
  no cross-frame tracking until AL-P1.
- **`person_pose.people`:** holds at most one entry today, because only
  instance 0 is projected.
- **`person_count`:** comes from the merged detector person boxes. The brain
  uses `person_count` (and the occupancy `people_count`) for the "exactly one
  person" rule, not `len(people)`.
- **`activity.zone_hint`:** null unless the private sofa/desk zone export
  (AL-4's mark) is present. With the export, AL-3 tests the projected shoulder
  midpoint against it.
- **`activity.confidence`:** JEV `probabilities[label]` when present, otherwise
  its distribution confidence.
- **`health` with `component:'jev'`, `ok:false`,
  `detail:'jev_egress_blocked_indeterminate'`:** JEV egress is frozen after an
  indeterminate request. The brain treats it as degraded, giving ambient only.
- **Frigate unhealthy** (`health` with `component:'frigate'`, `ok:false`, from
  AL-2 #331): the bridge stops asserting occupancy and zones, so they expire.
  The brain treats an expiry that comes with Frigate unhealthy as **unknown,
  not absent**:
  - It holds the current lighting state, with mode `degraded_ambient` and the
    why line "Occupancy sensor offline · holding".
  - It still turns the spot off if the reading preconditions fail.
  - It applies `room_off` only after 30 min with no person evidence from any
    source (fresh `person_pose` / `activity` `person_count` ≥ 1 counts as
    evidence). The 30 min is a setting.
- **Missing zone:** a zone with no event at all, such as one not drawn yet or a
  cold start, is unknown, not absent.
- **`source`:** a free string identifying the producer, e.g.
  `victoria-ha-bridge`.
- **`people_count: null` with `present: true`:** a valid combination. For the
  "exactly one person" rule, the brain uses AL-3's `person_count` when the
  occupancy count is null.
- **`component:'camera'`:** belongs to AL-3, which owns the OAK camera. A
  Frigate-side feed loss is reported by AL-2 as `component:'frigate'` with a
  detail string.

## Home ↔ brain notes (from AL-4 and AL-1, 2026-10-19)
- **One private file for the brain: `lighting-brain-geometry-1`** (format in the
  AL-1 README, PR #332).
  - AL-4's export tool (`tools/den-room/brain-targets.mjs`) writes this file
    directly:
    - the four fixtures' `aim_origin_m` and `horizontal_world_reference`,
      taken from Home's virtual fixture descriptors (the values `aimAtTarget`
      uses);
    - `camera_m`, when the registration has it;
    - `zones.sofa.seat_m` and `zones.desk` from the owner's marks;
    - `near_fixtures`, with documented defaults the owner can edit.
  - It keeps the `binding` / `equipment_ref` identity, so the brain can refuse
    a mismatched registration.
  - The separate `lighting-brain-targets-1` file is folded into it.
- **Override auth:** Home sends the session cookie plus
  `X-Home-Lighting-Brain-CSRF: 1` on POST/DELETE. The gateway route (AL-5/6)
  must accept it.
- **`brain` field:** Home follows only `brain:"online"`. Any other value is
  treated as offline: Home holds the last state.
- **Pauses:**
  - A non-null `paused_until` is the brain's acknowledgement; a return to null
    means cleared.
  - An unacknowledged pause expires in Home after 30 min.
  - A late override (sent after a reconnect, carrying the original `at`) is
    accepted. The brain runs the pause from when it receives it.
- **Seat point:** the centre of the marked seat polygon (`seat_method:
  zone_centre`) is enough for the MVP.
- **Spot normal:** the brain sends no target normal. Home aims with `[0,0,1]`,
  which is fine for "roughly right".
- **Placing the private geometry file on the LA box:** a host operation, part
  of AL-6, with the owner's approval.
- **`person_pose.head_pitch_deg` sign:** positive means looking **down**
  (towards the lap or a book), and 0 is level. AL-1 already assumes this. AL-3
  flips its sign to match (decided 2026-10-19).

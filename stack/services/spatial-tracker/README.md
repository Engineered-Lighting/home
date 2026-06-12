# spatial-tracker

Person localization service for the 3D apartment dashboard. Consumes Frigate
NVR person detections over MQTT, back-projects them into the apartment's
metric world frame, runs a per-person Kalman filter, labels activities from
Home Assistant media/light state, and broadcasts tracks at 10 Hz over a
websocket. Also hosts the camera-calibration scaffolding and a daily
recording/replay facility.

## Coordinate convention

* **World frame**: right-handed, **Z-up**, meters, floor at `z=0`.
* **Camera frame**: OpenCV convention — `+Z` forward (optical axis),
  `+X` right, `+Y` down.
* **Extrinsics**: `q_wxyz` is the **world→camera** rotation `R` as a unit
  quaternion `[w,x,y,z]`; `t = -R @ C`; `C` is the camera center in world
  coordinates. A pixel back-projects as
  `d_world = R^T @ [x', y', 1]` (with `(x', y')` from `cv2.undistortPoints`)
  and hits the floor at `X = C + s·d`, `s = -C_z / d_z`.
* **K scaling**: Frigate detections arrive in **detect resolution** pixels
  (e.g. 1280×720@10). Intrinsics `K`/`dist` are valid at
  `intrinsics.image_size`. The tracker scales detection pixels
  `detect_res → image_size` (per-axis linear scale) before undistorting. If
  you calibrate on the main stream, set `image_size` to the main resolution
  and everything else follows.

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate    # or .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8098
```

Tests (pure logic, no network):

```bash
pip install pytest
python -m pytest tests/ -q
```

## Environment

| var | default | notes |
|---|---|---|
| `MQTT_HOST` | `192.168.0.125` | Frigate's MQTT broker |
| `MQTT_PORT` | `1883` | |
| `MQTT_USER` / `MQTT_PASS` | empty | empty user → anonymous connect |
| `FRIGATE_URL` | `http://192.168.0.125:5000` | snapshots for calibration |
| `HA_URL` | `http://192.168.0.125:8123` | apartment model + websocket states |
| `HA_TOKEN` | empty | long-lived token; without it the model store just uses the on-disk fallback |
| `DATA_DIR` | `/data` | model cache, floor.json, recordings |
| `PORT` | `8098` | |
| `REPLAY_MODE` | `0` | `1` = no MQTT/HA connections (replay-only service) |
| `RECORD_DEDUP_EMPTY` | `1` | `0` = strictly record every 10 Hz frame even when empty |
| `LOG_LEVEL` | `INFO` | |

Data files (all optional):

* `{DATA_DIR}/model/apartment_model.json` — seed/fallback apartment model;
  rewritten with the last good HA fetch.
* `{DATA_DIR}/model/floor.json` —
  `{walkable_polygon: [[x,y],...], holes: [...]}`; floor hits outside this
  polygon buffered +0.5 m are rejected. Missing file → check skipped.
* `{DATA_DIR}/model/collision_edges.npz` — `Nx2x3` array of 3D segments for
  the calibration overlay.
* `{DATA_DIR}/recordings/YYYYMMDD.ndjson` — one JSON line per broadcast
  frame, rotated daily, deleted after 14 days.

## Endpoints

| | |
|---|---|
| `GET /healthz` | `{ok, mqtt_connected, ha_model_revision, tracks_active, uptime_s}` |
| `GET /model` | current apartment model (read-through cache) |
| `GET /tracks` | latest broadcast frame as REST |
| `WS /ws/tracks` | live frames at 10 Hz |
| `WS /ws/tracks?replay=YYYYMMDD` | recorded day, real-time paced, looped |
| `GET /replay/sessions` | list recording files |
| `GET /calib/{cam}/snapshot` | fresh frame proxied from Frigate |
| `POST /calib/{cam}/extrinsics` | `{pairs:[{px:[u,v],xyz:[x,y,z]}...], image_size:[w,h]}` → PnP pose (`q_wxyz`, `t`, `C`, `rms_px`, per-point errors, floor coverage polygon). Does **not** persist — merge into the model and POST to HA yourself. |
| `POST /calib/{cam}/intrinsics/solve` | 501 — ChArUco capture session not wired yet |
| `POST /calib/{cam}/intrinsics/from_images` | multipart ChArUco (DICT_5X5_100) images + `board_cols`, `board_rows`, `square_mm` → `{K, dist, rms_px, n_frames_used, image_size}` (CALIB_RATIONAL_MODEL) |
| `GET /calib/{cam}/overlay.jpg` | collision edges projected onto the latest snapshot (404 with reason if uncalibrated / no edges file) |

### Track wire format

```json
{"type": "tracks", "ts": 1718000000.0, "tracks": [{
  "id": "t1", "person": "marcelo", "state": "active",
  "pos": [2.5, 1.0, 0.0], "vel": [0.3, 0.0],
  "cov": [[0.02, 0.0], [0.0, 0.02]],
  "room": "living_room", "zone": null,
  "source_cams": ["living_room"],
  "conf": 0.9, "conf_reason": "good",
  "activity": "watching_tv", "activity_conf": 0.8, "activity_source": "rules"
}]}
```

`state`: `active` | `coasting` | `stationary_held` | `room_only`.
`conf_reason`: `good` | `coasting` | `multi_ambiguous` | `room_only`.

## Behavior summary

* **Stage A** (always): camera→room mapping, room-level tracks keyed by
  Frigate object id.
* **Stage B** (calibrated cameras): foot-point ray casting with
  cropped-feet (z=0.9 plane, σ×√3) and seated (z=0.45 plane, σ×√3, when bbox
  h/w > 1.6 and stationary and bottom not at the frame edge and the foot
  ray-cast is implausible) fallbacks; per-track KF
  (σ_acc=1.5 m/s², R from `max(0.15, 0.04·range)·(0.5/score)`),
  3σ Mahalanobis gating, 3-hit confirmation, 0.8 m cross-camera merge.
* **Coast control**: covariance saturates at σ=1.5 m (prediction freezes);
  >5 s without a measurement → demoted to room-level (pos null, room kept);
  Frigate `stationary` → position held, covariance frozen; >30 s without any
  signal → retired; re-detection within 1 m of a track retired <30 s ago
  re-binds its id.
* **Rooms**: polygon hysteresis (1.5 s or 3 consecutive measurements);
  boundary keeps the previous room.
* **Activities** (rules.py): watching_tv 10/20, listening_music 15/30,
  cooking 60/120, working 120/60, moving 2/5, idle 30/–
  (enter-debounce / exit-hysteresis seconds). HA websocket silence >30 s
  suppresses the media-dependent rules.

## Deviations from spec (and why)

* `python-multipart` added to requirements — FastAPI needs it to parse the
  multipart upload in `/calib/{cam}/intrinsics/from_images`.
* Recording dedups **consecutive empty** frames by default
  (`RECORD_DEDUP_EMPTY=0` restores strict every-frame recording): 10 Hz of
  empty frames is ~860k lines/day of zero information.
* Seated heuristic is a *fallback*, not a primary path: `h/w > 1.6` AND
  stationary AND bbox bottom not at the frame edge AND the foot-point
  ray-cast implausible (no floor hit, or hit outside the walkable hull) →
  bbox-center ray ∩ z=0.45 seat plane, σ×√3. A stationary *standing* person
  (plausible foot ray) keeps the normal foot-point fix. If your cameras show
  seated people as squat boxes, change `SEATED_ASPECT_GT` in tracker.py to
  an upper bound instead.
* Unconfirmed (≤2 hit) candidate tracks are broadcast as `room_only` rather
  than hidden, so stage-B rooms degrade gracefully to stage-A behavior
  during confirmation.
* The dwell conditions written in the rule table (`cooking dwell>60s`,
  `working dwell>120s`) are evaluated *in addition to* the enter-debounce,
  since the spec lists both.

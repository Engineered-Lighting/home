# Setup stored fields: proposal for the owner (APT-U02, APT-U06, APT-U07)

**Status: proposed, pending the owner's approval.** Nothing in this note is an
accepted schema. The code that writes these fields is behind
`?setupProposal=1` (or `localStorage["home.apartment.setupProposal"] = "1"`),
labelled "Proposed fields · pending approval" wherever it shows, and off by
default. With the flag off, Setup writes exactly what it wrote before.

Three Setup tools need fields the Echo `apartment_model` does not have yet:

- Names: a short in-room label.
- Cameras and sensors: a hand-placed pose, kept apart from calibration.
- mmWave: a presence-sensor record.

All three are additive and optional in `apartment_model` v1. Older readers
ignore them. The Home Assistant endpoint stores the document whole, with
revision CAS and backups, so no server change is needed.

Victoria's equipment registration is a closed record owned with Den. This note
proposes nothing that the UI writes there. It lists the matching registration
fields as **Den's decision**, and Victoria keeps showing these tools as
unavailable until Den agrees a successor.

## 1. Names: `devices[].label` (APT-U02)

| Field | Type | Rules |
|---|---|---|
| `devices[].name` | string, 1–48 | **Existing.** The Apartment display name used in lists, the inspector and messages. Unchanged. |
| `devices[].label` | string, 1–10, optional | **New.** The short tag over the light in the room. When absent, the room tag keeps today's behaviour. |

- **Home Assistant is never written.** Renames and labels stay in Apartment; Home Assistant keeps its own names and `ha_entity_id`. The tests spy on the HA client and assert that no entity-registry call is made.
- **Presentation only, nothing stored:**
  - "Was …" with Reset compares against the value when Setup opened.
  - Clearing a field restores its original value on blur.
  - Duplicate names are flagged but allowed.
- **Victoria (Den's decision):** an `equipment[].short_label` (1–10) next to the existing `label`. It is not built.

## 2. Hand-placed camera pose: `devices[].placement` (APT-U06)

Calibrated pose stays where it is: `devices[].camera.intrinsics` and `.extrinsics`, written only by calibration. The hand-placed pose is a separate object:

```json
"placement": {
  "method": "hand",
  "mount": "wall" | "ceiling" | "shelf",
  "pitch_deg": -90..30,
  "hfov_deg": 30..150,
  "vfov_deg": 20..120,
  "placed_at": "ISO-8601",
  "calibration_ref": "<extrinsics digest at placement time> | null"
}
```

- **Existing fields keep their meaning.**
  - `pos` is the camera body position: on the wall at its height, on the ceiling above the clicked floor point, or on the clicked top. It is not the optical pivot.
  - `yaw_rad` is pan.
  - Both are already written by today's device editor.
- **Placing never writes `camera.intrinsics` or `camera.extrinsics`.** The test asserts both are byte-identical after every placement edit and save.
- **Status is derived, not stored:**

  | Status | When |
  |---|---|
  | Placed by hand · not calibrated | There are no extrinsics. |
  | Moved since calibration · recalibrate | The camera was placed by hand while today's extrinsics already existed (`calibration_ref` equals their digest). |
  | Calibrated | Extrinsics exist and were solved after the last hand placement (`calibration_ref` is null or differs). |

- **Covers** is a presentation of named aims, fixtures and zones inside the field and range, with "Line of sight isn't checked yet". It stores nothing.
- **Victoria (Den's decision):** a hand-placement successor alongside the OAK-D record, whose intrinsics and extrinsics stay null by schema. It is not built. Today's tape-measured placement remains the Victoria path.

## 3. Presence sensor record: `devices[].type = "presence_sensor"` (APT-U07)

```json
{
  "id": "sensor-<slug>",
  "type": "presence_sensor",
  "name": "Study mmWave",
  "pos": [x, y, z],
  "yaw_rad": 0,
  "room_id": "<zone id> | null",
  "placement": { "method": "hand", "mount": "wall" | "ceiling" | "shelf",
                 "pitch_deg": -90..30, "hfov_deg": 30..150, "vfov_deg": 20..120,
                 "range_m": 1..12, "placed_at": "ISO-8601" },
  "ordered": true,
  "ha_entity_ids": [],
  "source": "manual", "confidence": 1, "updated_at": "ISO-8601"
}
```

- **Status is derived only from Home Assistant:**
  - **Ordered · not connected** while `ha_entity_ids` is empty, or while none of them exist in Home Assistant's states.
  - **Paired** only when every listed entity exists.
- The sensor never reads "connected" from Apartment data alone, and a dark or unsensed room is never shown as empty.
- **No lighting use.** Presence feeds no automation. A qualified presence producer is Den's, and it is parked in the Den plan.
- **Pairing** (filling `ha_entity_ids`) is an owner action after the device arrives. It is not part of this proposal's build.
- **Existing readers were checked:**
  - `markers.js` draws an unknown type as "other".
  - `buildPalette` reads Home Assistant, not model types.
  - The tracker's `devices_in_room(type)` ignores types it does not ask for.
- **Victoria (Den's decision):** none proposed.

## Questions for the owner

1. Approve `devices[].label` (≤ 10 characters) for Echo?
2. Approve `devices[].placement` for Echo cameras, separate from calibrated extrinsics, with the derived status above?
3. Approve the `presence_sensor` device record for Echo, with status derived only from Home Assistant entities?
4. Ask Den for the matching Victoria registration fields, or keep Victoria's Names and camera placement unavailable for now?

Until each answer is yes, the matching build stays behind `setupProposal` and saves nothing new in normal use.

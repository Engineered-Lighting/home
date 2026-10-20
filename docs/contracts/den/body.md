# Body: pose joints and body profile

**Status:** proposed contract, 2026-10-20. It covers APT-V03 (#276), V04 (#277)
and P05.

## Room-frame pose that Den emits: `person_pose`

The activity loop (AL-3) sends this to the lighting brain. It is the only
room-frame body Den produces for the MVP. Example:
[fixtures/person-pose-event.json](fixtures/person-pose-event.json).

```json
{"type": "person_pose", "home": "victoria", "room": "den", "source": "perception-lab/activity-loop",
 "observed_at": 0.0, "sent_at": 0.0, "ttl_s": 10, "person_count": 1,
 "people": [{"track_id": "slot-0",
             "shoulders_m": {"left": [0,0,0], "right": [0,0,0]},
             "head_m": [0,0,0],
             "head_pitch_deg": 12.0,
             "approximate": true}]}
```

| Field | Meaning | Flags and limits |
|---|---|---|
| `approximate` | Always `true`. Placement comes from the reviewed approximate camera-to-room alignment. | It is never qualified: there is no error bound, and it grants no geometry, Follow or lighting authority on its own. |
| `track_id` | A frame-local slot (`slot-0`), not an identity or an anonymous track. | There is one person per event today. Two-person tracking is AL-P1. |
| `shoulders_m.left` / `.right` | room-frame `[x,y,z]` metres, or `null` | **Missing stays `null`.** A joint is present only when the model supplied it with a room position. |
| `head_m` | nose, else the midpoint of the ears, else the midpoint of the eyes; or `null` | The fallback order is fixed. Which joint was used is not reported. |
| `head_pitch_deg` | degrees, **positive looks down**, 0 is level, negative looks up. Clamped to −30…+60. | It comes from the 2D nose/shoulder measure (the `head3d.prior_forward` calibration). It is `null` without that measure. |
| `person_count` | people seen in the frame after duplicate merging, or `null` | It is separate from `people`: one person can be counted without being placed. |

**Per-joint confidence and inferred flags: not supplied.**
- `person_pose` has no per-joint `confidence`, `inferred` or `observed` flag.
- Den never infers a missing joint. Every joint it sends was supplied by the
  model, and a joint it doesn't have is `null`. So "inferred limb" marking
  (V03 `done[0]`) has nothing to mark yet.
- A UI must not draw limbs between missing joints, and must not present
  `approximate: true` as measured.

## 2D joints in Home's perception view

These are shown in the den perception panel, not sent to the brain. They come
from the perception view's `pose2d.persons[].joints[]`.

- **`status`:** `supported`, or another status with a `reason`. Only
  `supported` joints are drawn.
- **`score`:** a raw model response. It is **not a calibrated probability**,
  and Home labels it that way.
- **Frame-local instance:** each person is a frame-local instance
  (`instance_id`). Anonymous association (`track_ref`, `track_epoch`) is
  "viewing only".

## Body volume in use today

The only body volume any Den component uses is the lighting brain's **head
clearance sphere**:
- a radius of `head_clearance_m = 0.15` m (an adjustable setting) around
  `head_m`;
- an aim is rejected when its beam passes within that radius
  (`beam_through_head`).

It is a planning check, not a measured volume.

## Nominal body profile: **not yet supplied**

V03 capsules and V04 occlusion need an agreed nominal profile. Den does not
produce one today. The proposed shape below is for agreement only: no producer
emits it, and no consumer should depend on it until it is listed as supplied.

```json
{"schema": "home-body-profile-1", "status": "proposed_not_supplied",
 "profile": "nominal_adult_v1",
 "parts": [
   {"part": "head", "shape": "sphere", "joints": ["head"], "radius_m": 0.11},
   {"part": "torso", "shape": "capsule", "joints": ["shoulder_mid", "hip_mid"], "radius_m": 0.16},
   {"part": "upper_arm_left", "shape": "capsule", "joints": ["shoulder_left", "elbow_left"], "radius_m": 0.05}
 ],
 "measured": false, "applies_to": "approximate"}
```

**Rules once supplied**
- **Supplied joints only:** a capsule is drawn only between two supplied
  joints. A part missing a joint is omitted, never guessed.
- **Not measured:** `measured: false` means the UI says "not measured", and no
  clearance or safety claim may rest on it (#210).
- **One body:** Lit shadows (V03) and aim occlusion (V04) must use the same
  profile instance.

**Gaps blocking V03 and V04 today**
- **Joints:** `person_pose` carries only shoulders and head, without elbows,
  hips or wrists. The fuller 2D joint set exists only in the perception view.
- **Inferred flags:** no inferred/observed flag exists, because Den emits none.
- **Owner:** the profile radii need an owner. Den proposes the values above
  as placeholders only.

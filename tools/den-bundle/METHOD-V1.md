# den-bundle METHOD-V1: how a person bundle is assembled

Rung 5 of `docs/architecture/live-v1/ladder.json`. This file holds every
number the assembler uses, in the one fenced block below. `method.py` reads the
block (CRLF or LF alike) and stamps its sha256, taken over the block's canonical
JSON, into every router request record.

A slot the contracts leave open is `{"open": true, "proposed": ..., "why": ...}`.
Practice runs on synthetic fixtures use the proposed value; `den_bundle.py gate
--scored` refuses while any slot here or in `ROUTER-V0.md` is open. Closing a
slot means writing the number in place of the object and bumping `version`.

```json den-bundle-method
{
  "method": "den-bundle-method",
  "version": 1,
  "window_s": {"open": true, "proposed": 3.0, "why": "PersonBundle.interval is 'typically 2-4 s'. Proposed 3.0: R3's live request is 2-4 stills over about 3 s, and N2's request spans 3 s (frames at r, r-1.5, r-3). Set on development, before any scored run."},
  "max_gap_s": {"open": true, "proposed": 1.5, "why": "The contracts set no staleness bound. A source is flagged in `missing` when its newest evidence is more than this before the window's end, its first more than this after the start, or two of its entries more than this apart. Proposed 1.5: N2's frames are 1.5 s apart, and R5's capture-to-completion was about 1.46 s."},
  "depth_skew_s": 0.067,
  "pad_fraction": {"open": true, "proposed": 0.2, "why": "The crop pad. The ladder (N1) says only 'padded so the hands and the worked object stay in the crop'. Proposed 0.2 of the box's width on each side and of its height top and bottom: N1's starting pad, which may only grow."},
  "pad_min_px": {"open": true, "proposed": 16, "why": "The least pad on each side, in pixels. Proposed 16: N1's starting value."},
  "frame_px": {"den-oak": [1920, 1080]}
}
```

## Where each number comes from

| Key | Value | Source |
|---|---|---|
| `window_s` | open, proposed 3.0 | `schemas.json` PersonBundle `interval`: typically 2-4 s |
| `max_gap_s` | open, proposed 1.5 | none in the contracts; see the slot's `why` |
| `depth_skew_s` | 0.067 | `schemas.json` Observation `depth_ts`: more than one stereo frame (67 ms) from `capture_ts` and the depth is not used |
| `pad_fraction`, `pad_min_px` | open, proposed 0.2 and 16 | the ladder's N1 text names a pad but no number; N1's plan starts at 20% and 16 px |
| `frame_px.den-oak` | 1920 x 1080 | note R2: the den OAK-D Pro's RGB is 1920 x 1080; `schemas.json` frames: camera pixels are the camera's native resolution |

## The rules the numbers feed

- **Window.** A bundle requested at time `t1` covers `[t1 - window_s, t1]` on
  the capture clock. Only records known by `t1` are read: observations captured
  by then and track lines dated by then.
- **Track line dates.** The Track record has no time of its own. A track line is
  dated by the newest `capture_ts` among the observations in its
  `last_obs_ids`, and never earlier than the line before it for the same track
  (so a line with no known observation, a coasting step, takes the date of the
  line before it).
- **Association-gated observation**, for track T and interval `[t0, t1]`: kind
  `keypoints`, not rejected, `capture_ts` inside the interval, and listed in T's
  `last_obs_ids` only by lines whose `association.status` is `confident`.
  `keypoint_series` holds every such observation, and nothing else, copied
  verbatim: `obs_id`, `ts` (its `capture_ts`), `camera_id` (its
  `source.sensor_id`), `transform_id`, `keypoints`, `joints_room`.
- **Frames** are T's own image observations (`person_box`, `keypoints`; a
  `person_box` wins a tie) in the interval, one per camera and capture time,
  whatever the association status: `association_safe` speaks for the whole
  bundle.
- **`association_safe`** is false when any track line in effect over the
  interval (the last one dated before `t0`, and every one inside) is not
  `confident`, when a `confident` line's `since_ts` falls after `t0` while the
  track already existed before it, or when no line is in effect.
- **`missing`** lists, in this order, each source that is absent or stale:
  `track` (positions), `person_box` (boxed frames), `keypoints`, `depth`,
  `object_box`. A source is stale when its evidence leaves a hole longer than
  `max_gap_s` anywhere in the window, edges included. `depth` is flagged when
  there are no keypoints, or any keypoint entry's `depth_ts` is absent or more
  than `depth_skew_s` from its `capture_ts`, or any joint was lifted on a plane.
  `object_box` is flagged when a linked object has no positioned object box in
  the window. Nothing waits: the bundle is built from what is there at `t1`.
- **Crop request** (`frames[].crop_ref`), as N2's plan crops: per camera, the
  box of that camera's latest boxed frame, padded by
  `max(pad_fraction x size, pad_min_px)` on each side, clipped to `frame_px`,
  then widened outward to even pixels (NV12 chroma is 2 x 2), applied to every
  frame of that camera. `crop_ref` is `{"box": [x0, y0, x1, y1], "from_ts": ts}`.

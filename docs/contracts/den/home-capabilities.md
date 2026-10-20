# Per-home capability and state fields that Den supplies

**Status:** proposed contract, 2026-10-20. It covers APT-F05 (#224) and F06
(#225), starting from the #281 crosswalk
(`docs/design/apartment-unified/FIRST-WAVE-CAPABILITY-STATE-CONTRACT.md`).

No capability object exists in the registry or the session today. This
document fixes the **field names and values that Den producers can fill**, so
that the registry/session owner and the UI can agree on one key set. Den
supplies only the Victoria den. Echo Park's values come from its own producers.
For Den's purposes they are always "not applicable" in Victoria, and the
reverse is true too.

Example: [fixtures/home-den-capabilities-victoria.json](fixtures/home-den-capabilities-victoria.json).

## Shape

```text
home-den-capabilities-1 (proposed)
{ schema, site_id, generated_at,
  ha, engineered, perception, body, assets, bench, lighting_brain, den_ops }
```

Every section has the same three questions, kept apart:

- **`supported`** (bool): the app has this feature for this home.
- **`available`** (bool or null): it works in this home now. `null` means
  unknown.
- **`permitted`**: always absent here. Permission stays with the existing
  authority at its own boundary, such as dispatch, the commissioning journal or
  the session owner. A UI must never derive it from these fields.

Each section also carries `reason`: a code from
[reason-vocabulary.json](reason-vocabulary.json), or `null` when available.
Times are Unix seconds, from the producer. Home's receipt time is a separate
`received_at` field and never replaces a producer time.

## Fields

| Section | Field | Values | Den producer, and source today |
|---|---|---|---|
| **ha** | `connection` | `connected`, `offline`, `unknown` | Not Den: Home's HA connection for the site. Den reads it only for the den occupancy fallback. |
| | `last_report_at` | seconds or `null` | HA state `last_updated` when supplied; otherwise `null` (not invented) |
| **engineered** | `value` | `none`, `simulated`, `modeled`, `commissioned` | Victoria: `modeled`. The four virtual fixtures come from the private equipment registration (`equipment-virtual-fixtures.mjs`, `truth_role: virtual_preview`). The others: `simulated` is the authored and simulation scene; `commissioned` is **not yet supplied** (needs the M4 journal); `none` means the home has no engineered fixtures. |
| | `fixtures[]` | `{fixture_id, value, physical_output:false}` | The four contract fixture IDs. `physical_output` is always `false` in the MVP. |
| | `driver` | `lighting_brain`, `manual`, `none` | `lighting_brain` while Home follows the brain; per fixture, see `lighting_brain.fixtures[].auto`. |
| **perception** | `supported` | bool | `true` for Victoria (the den OAK camera and perception-lab) |
| | `camera` | `available`, `unavailable`, `unknown` + `reason` | perception-lab capabilities; the AL-3 health `camera` detail (`camera_no_viewing_source`, `camera_no_frames_yet`) |
| | `analysis` | `idle`, `requested`, `starting`, `recording`, `stopping`, `stopped`, `failed`, `interrupted`, `blocked`, `indeterminate` | perception source `analysisState()` / session state. Always-on mode adds the AL-3 `deep_stack` detail. |
| | `jev` | `{ok, detail}` | AL-3 `health` component `jev` |
| | `frame` | see **Frame timing** | the current analyzed frame, if one is shown |
| **body** | `supported` | bool | `true` for Victoria |
| | `placement` | `registered`, `diagnostic`, `approximate`, `none` | perception view `roomPose`, `diagnosticRoomPose`, `approximateRoomPose`. Today Victoria reaches only `approximate`. |
| | `renderable` | bool | the view's `renderable` for that placement |
| | `reason` | `perception.placement` code | for example, `approximate_mount_signature_changed` |
| | `frame` | see **Frame timing** | the pose's original times |
| | `profile` | `null` | A nominal body profile is **not yet supplied** ([body.md](body.md)). |
| **assets** | `scene` | `ready`, `loading`, `unavailable`, `error`, `invalidated` | `HomeApartmentSiteSession` phase |
| | `content_revision`, `registration_version` | string, int | `HomeSpatialViewport` / content pack. These are identity, not private values. |
| | `equipment` | `{revision, status: saved, unavailable}` | private equipment registration response (revision only; no digest in the UI) |
| **bench** | `supported` | `false` | Not supplied for Victoria. There is no bench rig on the den sockets. |
| **lighting_brain** | `status` | `brain.status` code | Home brain client readback |
| | `mode`, `why`, `generated_at` | from the state | `activity-lighting-state-1` |
| | `fixtures[]` | `{fixture_id, auto, paused, pause_reason, paused_until}` | Home follower readback |
| **den_ops** | `level`, `step`, `banner` | `den_ops.level` | `den-ops-banner-1` (AL-5), polled about every 30 s. `null` if it can't be read. |

## Frame timing

The same block applies to any displayed observation: an analyzed frame, a pose
or a model result.

```json
{"captured_at": 0.0, "observed_at": 0.0, "available_at": 0.0, "valid_until": 0.0,
 "age": {"status": "current|stale|unavailable|invalid", "seconds": 0.0, "uncertainty_s": null, "reason": null}}
```

- **Producer times:** every time comes from the producer, and each field is
  `null` when not supplied.
  - `captured_at` is the frame's capture time.
  - `observed_at` is the observation's own time.
  - `available_at` is when the result became available.
  - `valid_until` is the producer's expiry.
- **`age`:** computed by the perception view's `age()`. It is `current` only
  when every required time is present, in order, not in the future, and not
  expired within a known clock uncertainty. Otherwise `reason` is a
  `perception.age` code. `uncertainty_s: null` means age unavailable
  (`clock_unavailable`), which is today's normal state for the current
  source's clock. Never show a number in that case.
- **Expiry:** `stale` with `expired` means the producer's `valid_until` has
  passed. Polling, redraws or a new receipt never extend it, and expiry never
  implies an empty room.
- **Brain and monitor documents:**
  - The brain's `generated_at` and the monitor's `generated_at` are producer
    times.
  - The brain counts as **offline** when the client gets no valid state for
    15 s (`heartbeat_missed`). That is a liveness rule, not an age band.
  - A monitor host turns **stale** after 300 s without a report. The gateway
    marks this; Home just shows the banner.

## What Den does not supply (yet)

- **`commissioned` fixtures:** these need the M4 journal and the owner's
  go-ahead.
- **A fitted or nominal body profile,** and inferred-joint flags (see
  [body.md](body.md)).
- **A clock-qualified current-source age:** the source clock's `uncertainty_s`
  is `null`.
- **A bench capability for Victoria.**
- **Per-home persistence of selection, tab, proposals or camera choice:** that
  is UI and session state (F05). Den stores nothing for it.

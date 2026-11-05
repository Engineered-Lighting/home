# den-track METHOD: Tracks from geometry, and E2's score

Rung 3 of `docs/architecture/live-v1/ladder.json`, E2's geometry-only arm. Field
names are the `observation` and `track` records of `schemas.json`; this file says
how each value is made. Every number the tool uses is in the one block below.

A slot the contracts leave open is `{"open": true, "proposed": ..., "why": ...}`.
Practice runs use the proposed value and say so. `den_track.py score --scored`
refuses while any slot is open. To close a slot, write the value in place of the
object. The block's fingerprint (sha256 over its canonical JSON) and the code's
(sha256 over the tool's source, without the tests) are stamped on every run file and
score, so a score names the exact method and code it ran under.

The tracker's plain numbers were tuned on synthetic scenes, as recorded below.
**They are frozen before any real crossing is scored**: closing
`freeze.frozen_on` is the freeze. A take that informs a change to this block or the
code moves to development, and the score then needs fresh takes (the ladder's rule).
The tool holds to it: see "A take is scored once", below.

## Input

Per frame, meaning one `capture_ts` on one `source.sensor_id`:

- **People**: `kind` `person_box` or `keypoints`, with `validity` `valid`.
  Everything else is left out and counted. `depth_point`, `radar_point`,
  `face_match` and `appearance_vec` have no writer today, and the last two wait
  for the owner's decision on learning permissions.
- **One Observation, one detection**: rung 2 writes one per detector box, `keypoints`
  when the box got a pose, else `person_box`. So two in one frame are two detections,
  however much their `image_box`es overlap: often one person behind another. Nothing
  is merged, and a track line lists one Observation.
- **Measurement**: the floor projection of `room_position`: x, y and the top-left
  2x2 of `cov`, times `obs_cov_scale`. For keypoints that is the torso reference
  point, as `schemas.json` says. An Observation with no usable position is left out.
- **The clock**: any Observation makes a frame, even an object box. A frame with
  no Observation at all does not exist, so being unseen is measured in seconds.

The tool sorts a file by `capture_ts`, so replay is causal on the capture clock:
a line never depends on anything captured after it.

## Motion and occlusion

A constant-velocity Kalman filter on the floor plane, state `[x, y, vx, vy]`, with
process noise `accel_sigma`. A track unseen since before this frame slows down
with time constant `coast_velocity_tau_s`, its covariance still growing, so its
gate still covers a person who kept walking. Once unseen for more than
`unseen_after_s`, it stops at the floor's edge (`room.floor_m` plus
`floor_margin_m`): people leave by doors, not through walls.

A person behind the armchair is a track coasting for up to `max_coast_s`. The
tracker has no map of occluders; it knows only that nobody has seen them.

## Association

1. Confirmed tracks first. Inside the gate (`gate_chi2`, on the Mahalanobis
   distance), every joint hypothesis is weighed: a link by `p_detect` times the
   Gaussian, a miss by `1 - p_detect`, a free detection by the density of false
   people and newcomers (`clutter_density` + `birth_density`, plus
   `exit_birth_weight` in a Gaussian of `exit_radius_m` around each exit). A track
   unseen for more than `unseen_after_s` uses `p_detect_unseen`. The best hypothesis is used.
2. Tentative tracks then get what is left, so a phantom never casts doubt on a
   person. A detection still free starts a tentative track.
3. A link more than `robust_d2` out pulls the track less, for depth outliers.

## Confidence and status

`schemas.json` has one place for this: `association`, "confident, or ambiguous with
named hypotheses and their probabilities". There is no number at the top of the
record, and `identity.confidence` belongs to the identity layer.

- **Inside**, a track's confidence starts at 1. At each link, the share of weight
  held by hypotheses that would move an identity (the risk) is moved out of it, if
  it is at least `risk_floor`, to named rivals: another track, or a newcomer.
- **`association.status`** is `ambiguous` while confidence is below
  `ambiguous_below`, or another confirmed track is within reach. Reach is
  `crossing_radius_m`, plus `hidden_reach_mps` times the seconds either has been
  unseen. Geometry can be sure of a crossing and the status is still ambiguous:
  rung 5's gate wants `association_safe` false through every annotated crossing.
- **A new track** reads `ambiguous` from its first line, whoever is near, until it has been
  seen with no doubt for `reconfirm_s`, and the line that confirms it claims nothing whatever
  that slot says. It has no history, and two sightings can be two people: when two stand close
  as a take starts and each is missed on one of the first frames, the one track takes both. So
  someone new gets no narrow beam, no teaching and no `heading_as_of` for that long. A
  tentative track within reach of a confirmed one is still not someone within reach of that
  confirmed track: a phantom casts no doubt on a person.
- **`association.hypotheses`**, while ambiguous: `{"hypothesis": "kept", "p": ...}`,
  then `swapped:<track_id>` per rival and `newcomer`. They sum to 1. Empty while
  confident, as in the schema's example.
- **Back to `confident`**, once the track has been seen with no doubt for
  `reconfirm_s`. Confidence resets to 1 and `since_ts` moves. This restarts the
  claim: the track is one person from `since_ts` on. Whether that is the person
  from before the ambiguity, geometry cannot say. `since_ts` is what says it.

## State

`tentative` until `confirm_hits` links in `confirm_window` frames, and it ends if it
falls short or is unseen for `tentative_max_gap_s`. `confirmed` while recently seen.
`coasting` once unseen for `coasting_after_s`. `ended` on one last line, with empty
`last_obs_ids`, once unseen for `max_coast_s`, or for `exit_coast_s` within
`exit_radius_m` of an exit. A track whose time ran out during a gap with no frames
ends before that frame's links, so it cannot take a newcomer. `track_id` is
`trk-<n>`, never reused in a run. The end of a file writes no line.

## Heading

Body facing from `joints_room`, as `schemas.json` says, not from velocity. A pair
counts when both its joints are `supported` (stereo, not a plane); the pairs are
shoulders (5, 6) and hips (11, 12). Facing is the mean left-to-right line turned 90
degrees counterclockwise, as a bearing clockwise from +y. `sd_deg` is the line's
variance across its own length, in degrees.

`heading_as_of` is that Observation's `capture_ts`. It moves only when the track took
the detection, its status is confident and `sd_deg` is at most `heading_max_sd_deg`:
never on a prediction, an emission or evidence whose owner is in doubt.

## Time on a track line

`schemas.json` gives Track no time, and rung 3's scorer needs none: a link takes its time
from the Observation it lists. With `record.time_field` null, lines hold only the schema's
fields. With a name, every line also carries its frame's capture time under it, the
coasting and ended lines too. A line exists only at a frame: the tool invents no frames
for a gap, since the tracker measures being unseen in seconds and its results were tuned so.

## What rung 3 leaves empty

`head_pose` is null, `posture` is `{"value": "unknown", "score": null}` and
`linked_objects` is `[]`: PersonState's fields from K7 are the authoritative ones.
`appearance` is absent: it needs the owner's permission. `identity` is
`{"binding": "unbound", "person_id": null, "method": null, "confidence": null, "as_of": null}`.
Each line also carries `"version": "track/v1"`, as the contracts' README asks.

## The score (E2)

Per take, truth (`den-track-truth/1`, see `truth.py`) gives each observation its
person. A link joins an observation's person to the track that listed it in
`last_obs_ids` and that line's status. Rejected observations are not scored.

- **Silent swap**: a track moves from one labelled person to another on a line whose
  status is `confident`. On an `ambiguous` line it is a flagged switch. A false
  person on a track is not a switch, and neither is a person moving to a new track.
- **Cannot tell**: an observation labelled `?` is left out of the score and counted. It
  names nobody, so a move across such labels happened somewhere in the span, and every
  line of that track from the first `?` to the new person's link is read: all `confident`
  is silent, all `ambiguous` is flagged, and a mix is unresolved. `unresolved_switch` says
  how an unresolved one counts at the gate, and the report gives their number.
- **Per crossing**: each crossing is the owner's marked `interval`, padded by
  `window_pad_s` on each side so the window holds frames before and after. Switches
  are found over the whole take and counted in the window they fall in.
- **IDF1**: with each window's own best one-to-one mapping (Ristani et al. 2016, at
  observation level). A person is one instance per frame, so a second box of one person
  that a track took is a false positive and not also a miss. The gate's number sums the
  windows' counts. Whole-take IDF1,
  one mapping per take, is reported beside it and is harsher: a flagged swap costs
  everything after it.
- **Which crossings count**: a marked crossing is a trial only if it shows what its kind
  claims, by the numbers in `trial_rule`. A sighting is a frame with a scored observation
  of that person: valid, with a position and a person's letter. "Not a person", rejected
  and `?` observations are not sightings, and two boxes of one person in a frame are one.
  A person is **followed through** the mark if at least `min_scored` frames see them in the
  window, and as many before the mark and after it. Under `sides` `take` those before and
  after may come from anywhere in the take; under `window`, only from the window. Under
  either, they must not go unseen across the mark for longer than `tracker.max_coast_s`: past
  that their track has ended and they come back on a new one, so there was nothing to swap.
  - `crossing`, `crossing_occluded`, `near_pass`: at least `min_people_per_crossing` people
    followed through, who came within `contact_reach_m` of each other inside the window. A
    person hidden in that time is read in a straight line between their sightings.
  - `occlusion`: the same people followed through, with no need to be near, and one of
    them unseen for at least `unseen_s` during the mark.
  - `door`: inside the window, one person seen in `min_scored` frames before the mark and
    none after it, and another the other way round, with no longer than `tracker.max_coast_s`
    between the last sighting of the one and the first of the other. Nobody is on both sides
    of a door swap.

  A window that holds a silent swap is always a trial. Anything else is listed as excluded,
  with why, and left out of n, the bound and IDF1. So a crossing with one person scored, a
  person lost at the contact until their track has ended, one sighting of A before and one of
  B after, a take cut short, a mistyped time, or a mark typed while the two stood across the
  room shows nothing about identity and does not count.

  From the observations alone, a person hidden behind the other after the contact looks
  the same as a detector stalling on them. Under `take` both count if the person is seen
  again within `tracker.max_coast_s`: the track is still coasting, and the tracker has to give
  it back to the right person, which is a real test. So `take` and `window` differ only for
  someone back after the window closes and within the coast.
  Near a door a track ends sooner, after `exit_coast_s`, and the rule does not read that. What
  the rule cannot judge is the owner's intent: a mark typed where the two stand a metre apart
  and do nothing passes it. The marks stay the owner's.
- **The bound**: one-sided Clopper-Pearson at `alpha` on the admitted crossings with a
  silent swap. With 0 of n it is `1 - alpha ** (1 / n)`: 13.9% at 20, 9.5% at 30.
- **The gate**: no silent swap anywhere in the takes, inside a crossing or not, and
  IDF1 at least `idf1_gate`, on at least `min_crossings` admitted crossings.
- **Reported beside the gate, with no limit in the contracts**: flagged switches; the
  ambiguous share of tracked observations inside each crossing, inside its mark, and
  outside every crossing, so a tracker cannot pass by doubting everything; a stand-in
  for E2's "how often a face is even visible".

A scored run also refuses tracks made under another method or another version of the
code, changed files, unlabelled observations, one observation in two takes, fewer than
`min_crossings` admitted crossings, admitted crossings of any kind short of
`crossings_plan`, or crossings that overlap once padded. More than the plan is allowed,
and the report shows the counts.
It also refuses a truth file that does not name a labeller, say the labels were made
blind to system output (`blind`, self-declared, so this catches only an honest no), and
name the observations it labels (`observations_sha256`), and takes with more than one
labeller. The report says what the truth files say.

**A take is scored once.** Any `score`, practice or scored, shows results, so it uses the
takes it looked at. It notes them, together, as hashes and capture times: the method's and the
code's fingerprints and, per take, a `take_id` over its frames (sensor and `capture_ts`), its
`span` (first and last `capture_ts`) and the hashes of its observations, tracks and truth. The
note goes beside each take's observations (`observations.score-runs.jsonl`), which travels with
the take's folder, and in this Windows user's record
(`%LOCALAPPDATA%\den-track\score-runs.jsonl`), which stays when files are copied, moved or deleted.

Two takes are the same footage if they have the same `take_id` or their spans overlap, on any
sensor: a capture time never comes round again, so an overlap is one recording, whichever frames
each kept. That covers rung 2 run again with other settings, which can drop a frame, since a
frame with nobody seen is no frame; a take cut in two; and a file re-saved with its times rounded.

A scored run holding footage in any note must be that same look: the same takes, with nothing
changed. So a retune, of the numbers or the code, cannot be tried on takes that have been looked
at, new labels cannot be tried, a take cannot be dropped or added once results are known, and the
same footage cannot come back with a frame less. Two takes in one scored run may not be the same
footage. Replaying a scored run is allowed, and gives the same numbers. Each report goes
to a new folder, so an earlier one stays, and a scored run on fresh takes lists the earlier
scored runs the record holds and whether they passed (`earlier_scored_runs`), so a pass after
a failure says so. A refused run shows no result and uses no take.

Three things get past it. Deleting both notes by hand, which nothing can stop. A take looked
at alone and then left out of the session: nothing ties it to the others, so it cannot be
refused, the plan catches it only if a kind falls short, and the report counts it among
`other_takes_looked_at`, beside any pilot takes. So run `score` only on a complete session. And
footage whose capture times have moved, by hand or by a clock corrected between the two runs: the
tool has only those times to know it by. Frames of a recording that no look has reached are fresh.

## The block

```json den-track-method
{
  "method": "den-track-method",
  "version": 1,
  "freeze": {
    "frozen_on": {"open": true, "proposed": "the date of the freeze", "why": "Closing this is the freeze: the owner writes the date once the other slots are closed and any pilot take is done. After that, any change to this block changes its fingerprint, and a scored run refuses tracks made under another one."}
  },
  "record": {
    "time_field": {"open": true, "proposed": null, "why": "schemas.json gives Track no time, so a coasting or ended line lists no observation to date it, and rung 5 dates it at the line before. null keeps to the schema. A name (\"as_of\" is rung 5's suggestion) puts the frame's capture time on every line under it, and rung 5 and K7 must then read the same name. Decide before the freeze: this changes the block's fingerprint, and tracks of a take already scored cannot be made again under another one."}
  },
  "room": {
    "floor_m": {"open": true, "proposed": [0.0, 0.0, 4.572, 3.759], "why": "[x0, y0, x1, y1] of the floor in the room frame. RoomModel's surfaces have no agreed geometry form yet. Proposed from note R10's tape measures and the frame's definition (origin at the minimum corner, x along the long axis). The owner checks it against the frame rung 1 registers to."},
    "exits_m": {"open": true, "proposed": [], "why": "[[x, y], ...] where people can leave. No contract gives the door's position. Proposed: none, which is known to be wrong: a person who leaves keeps coasting for max_coast_s, and someone coming in by the door in that time can take their track. The owner gives the door's position in the room frame before a scored run."}
  },
  "tracker": {
    "accel_sigma": 0.65,
    "p_detect": 0.9,
    "p_detect_unseen": 0.5,
    "unseen_after_s": 0.35,
    "tentative_max_gap_s": 1.0,
    "gate_chi2": 13.8,
    "clutter_density": 0.002,
    "birth_density": 0.001,
    "hidden_reach_mps": 0.5,
    "exit_radius_m": 0.6,
    "exit_birth_weight": 0.5,
    "exit_coast_s": 1.0,
    "coast_velocity_tau_s": 1.0,
    "new_track_speed_sigma": 1.5,
    "confirm_hits": 2,
    "confirm_window": 3,
    "coasting_after_s": 0.6,
    "floor_margin_m": 0.4,
    "robust_d2": 9.0,
    "risk_floor": 0.02,
    "ambiguous_below": 0.98,
    "heading_max_sd_deg": 30.0,
    "obs_cov_scale": 1.0,
    "max_hypotheses": 20000,
    "max_coast_s": {"open": true, "proposed": 6.0, "why": "How long an unseen person keeps their track. Synthetic scenes barely move between 3 and 12 s, and longer only adds ambiguous time, so the den decides: how long can someone stay hidden behind the armchair and come back as themselves? A pilot take can show it."},
    "crossing_radius_m": {"open": true, "proposed": 1.2, "why": "How close counts as a crossing; the contracts name no distance. Rung 5's gate wants association_safe false through every annotated crossing, so status must be ambiguous before the owner's mark starts. The mark is arm's reach, about 1 m, so 1.2 leaves a margin. Larger costs narrow-beam and teaching time for people near each other."},
    "reconfirm_s": {"open": true, "proposed": 1.5, "why": "How long a track is seen with no doubt before status returns to confident, and before a new track first reads confident. No contract sets it. While ambiguous a person gets no narrow beam and no teaching, so longer costs that; 1.0 let a silent swap through on noisy depth in tuning. Proposed 1.5 also leaves room for the owner's mark ending late."}
  },
  "scoring": {
    "idf1_gate": 0.9,
    "alpha": 0.05,
    "min_crossings": 20,
    "min_people_per_crossing": 2,
    "window_pad_s": {"open": true, "proposed": 2.0, "why": "Padding on each side of the owner's mark, so a window holds frames before and after and a swap shows in its own IDF1. The contracts name none. Proposed 2.0: about what walkers need to come together and part. Staged crossings must be further apart than twice this."},
    "face_visible_score_min": {"open": true, "proposed": 0.4, "why": "E2 asks how often a face is even visible. Without reading frames, the stand-in is nose and both eyes above this keypoint score. Proposed 0.4, rung 2's proposed keypoint_score_min."},
    "trial_rule": {"open": true, "proposed": {"sides": "take", "min_scored": 2, "contact_reach_m": 2.0, "unseen_s": 0.6}, "why": "What a marked crossing must show to count as a trial; see 'Which crossings count'. sides, take or window: where a person's sightings before and after the mark may come from. Under both, nobody may go unseen across the mark for longer than tracker.max_coast_s, since their track has ended by then and nothing is tested. take: anywhere in the take, so a far person hidden behind the near one past the padding counts if they are seen again within that time, while their track can still be given back. window: only inside the padded window, which is stricter and needs along-the-view crossings staged so both are seen again within window_pad_s (on synthetic close crossings it admits about half). min_scored: how many frames must see each person in the window and on each side; proposed 2 because 3 starts to refuse synthetic crossings behind the armchair, about 1 in 10. contact_reach_m: how near the two must come inside the window. It is a check against a mistyped mark, not a definition of a crossing, so it is wider than arm's reach: depth error adds to what is read, and synthetic crossings marked at 1.6 read up to 1.7. unseen_s: how long someone must be unseen during an occlusion's mark; proposed from tracker.coasting_after_s."},
    "unresolved_switch": {"open": true, "proposed": "flagged", "why": "A track changes person across cannot-tell labels, with the tracker confident for part of that span: the labels cannot say whether the change came while it was sure. flagged: it does not fail the gate, and its number is reported. A swap made while confident, then ambiguous before the labels resume, would pass; the review saw that only on constructed records, never on tracker output. silent: it fails the gate. A labeller who stays unsure about a second past the parting then fails a swap the tracker did report; the review saw 1 flip in 80 hard takes. flagged or silent."},
    "crossings_plan": {"open": true, "proposed": {"crossing": 12, "crossing_occluded": 5, "occlusion": 3, "near_pass": 3, "door": 2}, "why": "E2 says 20 to 30 staged crossings and occlusions, not how many of each. Fixed before recording, so the sample is set in advance: a scored run refuses when the crossings that count fall short of it for any kind, so the hard ones cannot quietly go missing. Proposed: the README's script, 25 crossings weighted toward what geometry finds hard (bound 11.3% at 0 failures). 30 would give 9.5%."}
  }
}
```

## Where each number comes from

| Numbers | Source |
|---|---|
| `idf1_gate`, `min_crossings`, `alpha` | ladder rung 3 and the brief: IDF1 at least 0.9, 20-30 crossings, a one-sided 95% bound |
| `min_people_per_crossing` | a swap takes two people |
| `gate_chi2` | chi-square, 2 degrees of freedom, 99.9% |
| `accel_sigma`, `ambiguous_below`, `risk_floor`, `p_detect_unseen`, `hidden_reach_mps`, `clutter_density`, `birth_density`, `confirm_hits`, `confirm_window` | tuned below: these are the values that moved the results |
| `p_detect`, `exit_coast_s`, `coast_velocity_tau_s`, `new_track_speed_sigma`, `robust_d2` | swept below, and the results barely moved |
| `unseen_after_s` | about 1.5 frames at R6's 4.6-4.9 Hz |
| `coasting_after_s`, `tentative_max_gap_s`, `floor_margin_m`, `exit_radius_m`, `exit_birth_weight`, `heading_max_sd_deg`, `max_hypotheses` | judgement, not tuned; `obs_cov_scale` stays 1 until a pilot says otherwise |

## How the values were tuned

Synthetic scenes only (`synth.py`): a made-up den, two people, nine scenes, three
noise profiles, at 4 frames a second. One value at a time, first for no silent swaps,
then for the least ambiguity outside crossings, then for IDF1. Each line runs
`python -B tools\den-track\den_track.py practice --first-seed <s> --seeds <n> --noise <p>`
with `--set tracker.<name>=<value>` to try a value.

- **Seeds 0-59** tuned the numbers.
- **Seeds 1000-1099** were looked at to diagnose silent swaps, which led to the reach
  rule and the door's radius, so they count as tuning too.
- **Seeds 2000-2199** were run once, after the numbers stopped moving (1,800 runs at
  each noise). They and the conditions below were run again after four later changes, with
  no value changed, and these are the results of that run: the reader stopped merging
  overlapping boxes, a tentative track beside a person reads `ambiguous`, a crossing along the
  view ends with the near person stepping aside, so both are seen again, and a new track reads
  `ambiguous` until it has been seen for `reconfirm_s`. The last took silent swaps at noisy
  from 7 to 3. The ninth scene, `close_at_start`, came with it: synthetic scenes had all started
  with the two apart, so they never showed what it fixed. On seeds 2000-2049 of the first eight
  scenes it raised the ambiguous share outside crossings at typical noise from 0.22 to 0.36:
  these takes last 5 to 14 s, and for someone who stays it is only their first `reconfirm_s`. IDF1
  is over the crossings that count: all but 3 at noisy, and all but 56 of the 900 at 2 frames a
  second, where `min_scored` is a lot to ask.

| Noise | Silent swaps | Flagged switches | IDF1 by scene, lowest to highest | Lowest scene |
|---|---|---|---|---|
| clean | 0 | 181 | 0.954 to 1.000 | `near_pass` |
| typical | 1 | 426 | 0.914 to 0.987 | `crossing_behind_armchair` |
| noisy | 3 | 585 | 0.858 to 0.977 | `near_pass` |

**Other conditions**, seeds 3000-3099 at typical noise, 900 runs each:

| Condition | Silent swaps | Flagged switches | IDF1 | Lowest scene |
|---|---|---|---|---|
| 2 frames a second | 2 | 367 | 0.915 | `near_pass` 0.658 |
| 4.75 frames a second (R6) | 0 | 160 | 0.972 | `touching_crossing` 0.937 |
| 8 frames a second | 0 | 198 | 0.972 | `crossing_behind_armchair` 0.938 |
| 15 frames a second | 0 | 246 | 0.969 | `crossing_behind_armchair` 0.942 |
| Observations' covariance half too small | 0 | 229 | 0.937 | `near_pass` 0.879 |
| Observations' covariance twice too big | 1 | 182 | 0.965 | `crossing_behind_armchair` 0.900 |

**Two people close when a take starts**, 200 takes at each distance (0.2 to 1.5 m) and noise,
standing 2 s and then parting: no silent swap at any. Before a new track doubted itself, 14 of
200 takes at 0.45 m and typical noise had one, mostly on a track's second line.

None of this is a score. It says the method works on the scenes it was written for.

## What this method is weak at

- **Frame rate.** Below about 4 frames a second, two people who stop at arm's length
  swap often, because constant velocity carries both tracks through each other. It is
  flagged, not silent, but IDF1 falls. Trying a "they may have stopped" prediction in
  the association fixed that and broke crossings, so it is not here.
- **A person the tracker never confirmed.** Someone already hidden when the take starts,
  stepping out where a seen person just vanished, gives no rival to doubt. `hidden_reach_mps`
  covers people it has tracked, not people it has not.
- **A duplicate box of one person.** If the detector's own overlap filter lets one
  through, it is a second detection and starts a tentative track, which reads `ambiguous`
  on every line while a confirmed track is in reach. It casts no doubt on the person beside
  it. Before it fades it can take the other person's next detection and be confirmed on it,
  so that person is listed on a third track for a while: a flagged switch and lost IDF1. It
  can still read as a silent swap, rarely: a box placed well off its person can land by someone
  else, whose settled track takes it with nobody within reach to doubt it. Measured on seeds
  4000-4099, 900 runs a row, with a box-only duplicate on a share of observations, each one or a
  burst of frames, and its position off by a Gaussian of the size given:

  | Noise | Duplicates | Off by | Silent swaps | Flagged switches | IDF1 |
  |---|---|---|---|---|---|
  | typical | none | | 0 | 226 | 0.955 |
  | typical | 5% | 4 cm | 0 | 426 | 0.919 |
  | typical | 20% | 4 cm | 0 | 1077 | 0.813 |
  | typical | 5%, bursts of 4 | 30 cm | 0 | 955 | 0.839 |
  | typical | 5%, bursts of 4 | 60 cm | 1 | 1105 | 0.850 |
  | typical | 20%, bursts of 4 | 60 cm | 1 | 3621 | 0.695 |
  | noisy | none | | 2 | 348 | 0.930 |
  | noisy | 5% | 4 cm | 1 | 565 | 0.895 |
  | noisy | 20% | 4 cm | 1 | 1286 | 0.793 |
  | clean | 20%, bursts of 4 | 60 cm | 0 | 3610 | 0.727 |

  So the cost is IDF1, which misses the gate at 20%, more than swaps. How often the real
  detector does this is unknown: the report counts `second_boxes`, so a pilot take shows it, and
  the place to act is the detector's own overlap filter in rung 2. The tool does not merge two
  boxes that overlap and agree on the floor: rung 2 places a box-only person at the median depth in
  their box, which for someone behind another person is often the front person's depth, so
  those two would agree too.
- **Noisy depth along the line of sight.** Stereo error grows with the square of the
  range, and a crossing along the camera's view leans on it. In the noisy profile,
  `near_pass`, `touching_crossing` and `crossing_behind_armchair` fall below 0.9.
  If the real den looks like that, the ladder's answer is that session appearance
  becomes its own rung, after the owner's decision on learning permissions.

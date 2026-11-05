# N2 plan: the offline need test on one person

Status: plan only. Nothing here is built or run. Written 2026-11-05.

## Context

- N2 tests the riskiest premise first: do the den camera's pixels show what
  light one person needs? The perception rungs wait for its verdict.
- It scores a declared variant, not production: one anonymous person (N1's id
  `p1`), N1's held-out sessions, and `focused_engagement` and `visual_demand`
  with their confidences, from frozen, versioned stand-ins. The verdict
  (`feasible`, `not_feasible` or `insufficient`) goes to an EvalRecord.
  Nothing is promoted or changed in the loop.
- Inputs: R's retained staged takes (CHECK-V1), N1's frozen `fixture-v1` and
  `labels-v1` (`claude/plan-n1`), qwen_scene prompt-1, and den-core
  (`claude/den-core`) for the estimator and its byte-for-byte replay.
- It follows the N1 plan as revised on 2026-11-05: the phone's band is a
  PROTOCOL-V1 setting (under `none` the phone is a no-need shortcut span and
  each round gains a `tidying` block); mail and wiping are at the counter; and
  every task starts after at least 30 s with no task, with one 90 s quiet block
  per round whose tail lies past τ_off.
- Numbers come only from the architecture's N2 text and the owner's answers.
  Anything else is an empty named slot in `GATE-V1.md`, filled on the
  development split before the held-out set opens.

## 1. Two checks: can it pass by construction?

### (a) Onset headroom

- Requests run every P = 4 s on R's `t_s`. A touch lands at a random phase, so
  the wait w to the next request is uniform on [0, 4) s, median 2 s.
- The first `starting` comes at d_on = w + S + 4 s × m: S is the measured
  service time, and m counts replies after the touch that don't yet show it.
- With m = 0, median d_on ≈ 2 s + median S. The 3 s target leaves about 1 s
  for S, and nothing for a missed first reply: if more than half the onsets
  need m ≥ 1, the median is about 6 s plus S.
- The whole-room stills receipt (1.25 s median, 1.97 s p95) times a different
  workload and doesn't qualify per-person crops. If crops were no faster,
  median d_on would be about 3.25 s: a fail by construction.
- Options:
  1. **Pacing.** Offline the schedule is virtual; physical sends stay at least
     4 s apart. A smaller P leaves 3 s − P/2 for S, costs 4/P times the
     requests, and scores a cadence the live loop doesn't run.
  2. **Gate on a bound.** Keep P = 4 s and gate d_on − w (from the first
     request that could see the touch) against 3 s − P/2 = 1 s; report raw
     d_on. This removes the schedule's phase from the gate, not S.
  3. **Report only.** Onset gates nothing and is marked untested.
- **What this plan does.** P stays 4 s and development measures S. If
  2 s + median S_dev ≤ 3 s, onset is gated as written. If not, GATE-V1 sets
  `pacing_s` and `onset_mode` before the held-out set opens. Recommended
  fallback: pacing, with P chosen so P/2 + median S_dev leaves room under 3 s,
  because it keeps the owner's 3 s target gated. If no P fits, onset is
  report-only, so a not_feasible verdict never rests on cadence and service
  time, which are not the need premise.

### (b) Vocabulary

- prompt-1's `held_object` (qwen_scene.py line 40) is book, phone, cup, none,
  other, unclear. `activity_hypothesis` (line 45) is reading, phone_use,
  computer_work, resting, transition, other, unclear. Nothing names mail,
  paper, a cloth, wiping or tidying.
- So sorting mail (fine), wiping (casual) and `tidying` (casual, under the
  `none` phone band) probably all read `other` / `other`. Cases with the same
  reply get the same band, so one side is wrong by construction:
  - **`other` → fine.** Every scored wiping second is false-fine. One 25 to
    30 s wiping block has 19 to 24 scored seconds after the 6 s grace, so under
    the 0.033 cap its round needs about 580 to 730 s of non-fine scored time
    (19 / 0.033 to 24 / 0.033). A whole N1 round is 290 s, and `tidying`
    doubles the excess.
  - **`other` → casual.** Every mail second is a fine miss. Mail is about half
    of each round's 60 fine seconds, so fine recall is at most about 0.5 to
    0.6, and `sorting_mail` fails against any floor above that.
  - **`other` holds.** Both abstain or keep an older level.
- `visible_basis` or `gaze_target` might separate them; prompt-1 isn't built to.
- Options:
  1. **Keep prompt-1.** The case criterion probably fails on a vocabulary gap,
     which says nothing about the pixels.
  2. **Prompt-2:** prompt-1 plus closed values for what the staged cases handle
     (paper or mail, a cloth; handling papers, wiping or tidying), as N2's own
     module with its own `PROMPT_VERSION`. qwen_scene and the live loop stay
     prompt-1.
     - The module brings its own reply parser and receipt builder, because
       qwen_scene's can't take a prompt-2 reply: `build_observation` raises
       "unsupported prompt version" for anything but prompt-1 (line 265), and
       `parse_reply` rejects a new value as `value_out_of_vocabulary` (line
       135).
     - Its receipt keeps the `qwen-observation-1` shape with its own
       `result_schema` and `prompt_version`, so the adapter, need v1 and
       score.py read it unchanged. It reuses qwen_scene's version-free helpers
       (`canonical`, `digest`, `frame_descriptors`, `first_receipt`).
  3. Dropping mail and wiping isn't an option: A13 names them shortcut cases.
- **What this plan does.** Development runs prompt-1 first and prints a case ×
  reply-row table (counts only). If `sorting_mail` and `wiping` share their
  most common row, prompt-2 is written then, on development only, and
  development is asked and fitted again. GATE-V1 records `prompt`; the
  EvalRecord says a prompt-2 was written with the staged cases in view.

## 2. Scope: the owner's one choice

| | Option 1: the ladder's stand-in | Option 2: Qwen only (recommended) |
|---|---|---|
| Inputs | N1's boxes and id, the surface map, a fixed 2D pose and object detector, the Qwen crop request | N1's boxes and id, the Qwen crop request. The surface map is recorded, unused. |
| Evidence row | the reply, plus two detector facts: a hand keypoint inside a surface polygon, the object class at the hand | the reply only |
| New decisions | which detector and version, and its install on LA | none |
| Extra work | a detector pass on each request's latest crop on LA, in an isolated environment: about 500 lines, 1.5 agent-days | none |
| feasible means | the premise holds, given a detector at least as good as the stand-in | the premise holds on a crop and a box alone; a detector isn't needed for it |
| not_feasible means | the closest stand-in to the planned stack failed: the strongest "no" this camera can give, after the diagnosis | only the cheapest variant failed. It can't say whether a detector would pass. The perception rungs still wait, and the diagnosis says whether option 1 is worth a fresh held-out set. |

- **Recommendation: option 2.** It front-loads no decision, installs nothing,
  is the smallest build, and sends only crops to LA. Leaving the surface map out
  also keeps place, a shortcut cue, out of the estimator. Its cost: a
  not_feasible delays rather than settles the question, and uses up the
  held-out set (a fresh one is about 4 more N1 sessions). The owner chooses at
  plan review; GATE-V1 records `scope`.

## 3. The variant N2 scores

| Stand-in | Fixed as |
|---|---|
| Boxes and id | `fixture-v1`: padded per-frame boxes, `person_id: "p1"` |
| Surface map | `fixture-v1`'s `surface-map.json`, used under option 1 only |
| Interpreter request | qwen_scene `request_body` (prompt-1, or prompt-2): 3 frames over 3 s (qwen_live's `spread` defaults), each cropped to the latest frame's padded box; context: placement, a local time label, prior null |
| Model | qwen_scene's `MODEL` as served on LA; each reply's engine epoch recorded |
| Field assignment | both fields read one row per reply: (`activity_hypothesis`, `held_object`), or a hold row (section 5) |
| Engagement rule | need v1's A7 preset at A7's starting values, unless changed on development |
| Pacing and replay | P = 4 s on `t_s`, sends at least 4 s apart, S measured; den-core's runner and 2.0 s tick, one log per usable segment |

## 4. Definitions

### 4.1 What is scored

- **Grid.** Second k is [k, k+1) of `t_s`, sampled at k + 0.5. It is scored
  only if it lies wholly in one usable segment and outside every `uncertain`
  span. Uncertain seconds are counted, never scored.
- **Gold.** Engaged inside an interval. The band is the interval's, and `none`
  outside intervals. Quiet spans, and the phone under the `none` band, are gold
  none for both fields. score.py reads the phone band from the PROTOCOL-V1
  block the labels name. Under `casual`, the EvalRecord notes the departure
  from A13, which scores the phone at the counter as no need.
- **System.** The latest `person_state` record at or before the second, in that
  segment's log; before the first record, `none` with no level.
  - **On** is `starting` or `engaged`; **off** is `none` or `ending`.
  - The band is the level's band at N1's cut points: fine ≥ 0.6, casual from
    0.4 to under 0.6, none under 0.4.
  - A level is **fresh** while the reply that set it is inside its
    `valid_until` (observed time plus qwen_scene's 10 s), else **stale**.
- **Units** (what "per episode" counts): each gold interval, and each quiet or
  no-need span with a scored second. A unit's engagement label is whichever of
  on or off held for more of its scored seconds (a tie is off). Its band is
  the band of its median fresh level; with no fresh level it is **abstained**.
  A unit is **right** when its label and band both equal the gold's. An
  abstained unit is wrong.
- **Windows**, left out of the measures named: [onset, onset + τ_on) with
  τ_on = 3 s, engagement measures; [end, end + τ_off) with τ_off = 60 s, every
  measure (N1's rule that a quiet second counts only past τ_off; seconds in a
  later interval belong to it); [onset, onset + 6 s), demand measures, the 6 s
  grace for one person.
- **Unsure marks.** A `sure` flag set false removes the interval from the
  measures that rest on it, nothing else. `onset`: not timed. `offset`: its
  seconds leave early_off. `band`: its seconds leave every demand measure, and
  it is not a unit.
- **Carried ends.** The estimator restarts at each segment start. A piece with
  a `carried` end is not a unit and is never timed, but its seconds count. A
  carried onset takes its windows from the segment start; a carried offset has
  no τ_off window, as a cut follows it.
- **Low light, reported only.** Each metric is also reported on `low_light`
  data against the rest. Gated metrics pool both, as the room really is.

### 4.2 Onset and re-entry

- **Timed onsets** are the sure `touch` onsets of units: N1 protocol items 2
  and 5, the first frame of the hand touching the worked object, timed to the
  first `starting`. `no_touch` onsets are reported apart.
- For a timed onset at t_on, take the state in force at t_on. If it is on,
  its **on-run** is the unbroken run of on records that holds it; it began at
  the run's first record.
  - **On, and the run began after the previous labelled offset** (the latest
    interval offset before t_on in the same segment; with none, any run
    counts): d_on = 0, tagged `entered_on`. The estimator turned on in the
    gap, ahead of the touch.
  - **On, and the run began at or before that offset**: the onset is
    **missed**, tagged `stayed_on`. The estimator never left the earlier task,
    so it has no time for this one.
  - **Off**: d_on runs to the first later record in `starting` or `engaged`
    before the interval's offset. From `none` that is a new `starting`.
  - **Re-entry**: if the state is still `ending` from an earlier task, the
    onset output is need v1's `ending → engaged` record, tagged
    `entered_from_ending`.
  - With no such record before the offset, the onset is **missed**.
  - A missed onset is d_on = +∞ in the median, and counted with its tag.
- **Why `stayed_on` is a miss, not a share gate.** 15 of every 20 N1 onsets
  follow a 30 s gap that lies wholly inside the previous task's τ_off, where
  false_on scores nothing. Taking d_on = 0 for any onset already on would let
  an estimator that stays engaged across the counter tasks score 0 on those 15
  and pass. A cap on the `entered_on` share would need a new owner number and
  would still pass zeros up to the cap. A miss needs no new number and scores
  the case as what it is: this task's start went unnoticed. The cost falls on
  an estimator that holds `engaged` through a hidden-hands gap (hold rows
  pause its exit); the diagnosis reads that from the `stayed_on` count.
- The onset score is max(d_on, 0) (A13); by this rule d_on is never negative.
  The dwell, from the onset output to the first `engaged`, is reported only.
- With 30 s free of tasks before each task, every tag should be rare; many
  `entered_from_ending` or `stayed_on` point to a slow exit.

### 4.3 Demand: grace and fall

- The 6 s grace holds a 4 s cycle plus up to 2 s of S.
- After a fine task ends, the level falls over about 10 s (A7), inside
  [end, end + τ_off), which nothing scores. A fine level still in force past
  τ_off is false-fine.
- Fine recall counts a hit only on a fresh fine level: an abstention or a stale
  level is a miss. False-fine counts the level in force, fresh or stale. Both
  are the conservative reading.

### 4.4 Metrics

| Metric | Unit; counted per | Better | Threshold | Bound |
|---|---|---|---|---|
| onset | s; median of max(d_on, 0) over timed onsets | lower | ≤ 3 s, or per `onset_mode` | bootstrap |
| early_off | fraction of scored gold-engaged seconds with the system off, counted from the interval's first on output (its onset, if already on), and every scored second of a missed interval; per second | lower | ≤ `cap.early_off` (target ≈ 0) | bootstrap |
| false_on | fraction of scored gold-none seconds (quiet, and the phone under `none`) with the system on; per second | lower | ≤ `cap.false_on` | bootstrap |
| band competence, fine and casual | fraction of the band's units that are right; per unit | higher | ≥ `floor.competence.<band>` | exact binomial |
| fine recall | fraction of scored gold-fine seconds with a fresh fine level; per second | higher | ≥ `floor.fine_recall` | bootstrap |
| false-fine | fraction of scored gold none and casual seconds with a fine level in force; per second | lower | ≤ 0.033 | bootstrap |
| coverage | fraction of a unit's scored seconds with a fresh level, averaged over interval units | higher | ≥ `floor.coverage` | bootstrap |
| ECE, each field | Σ_b (n_b/N)·\|acc_b − conf_b\|, top label, per unit, 10 equal-width bins on [0, 1] | lower | ≤ 0.1 | bootstrap |
| F1, dwell, S | F1 per second (positive = on, windows left out); dwell median; S median and p95 per request | — | reported | bootstrap (F1, dwell) |

- early_off starts at the interval's first on output: a late start is onset's
  error, and counting it twice would fail early_off by check (a)'s arithmetic.
- A **missed interval** has no on output from its onset to its offset, so it
  has no start to count from. All of its scored seconds go to early_off, as
  A13's "gold engaged, system off" counts them, and it is a missed onset: d_on
  = +∞ in the median when its onset is timed, and in the miss count either
  way. Otherwise an estimator that never turns on would add nothing to
  early_off.
- false_on is the quiet criterion at band level, per second. N1 gives about 5
  scored quiet units per take (7 with the phone as `none`), about 20 to 28 per
  held-out set: too few for a 30-unit gate.
- ECE: a unit's top label is its engagement label or its band; its confidence
  is that field's mean confidence over the seconds holding the label; acc is 1
  when the label equals the gold. Abstained units leave the demand ECE.

### 4.5 Sample gates and case criteria

- Sample gates, on held-out data after every exclusion (the owner's per-band
  numbers, also applied to onset):
  - fine and casual: at least 30 units each from at least 3 sessions over at
    least 2 days; onset: at least 30 timed onsets, same rule;
  - every per-second metric and every case: data in at least 3 held-out
    sessions over at least 2 days.
- An unmet gate means insufficient: record more sessions, never relax a
  threshold.
- **Cases** (N1 protocol item 8). Shortcut: `sorting_mail` and `still_reader`
  (fine), `wiping` (casual), and `phone_at_counter` (casual, or a no-need span
  under `none`). Quiet: `idle_seated`, `idle_standing`. `tidying` counts in the
  casual band; N1 doesn't name it a shortcut case.
- **Case criterion**, for shortcut and quiet cases alike: the case's rate of
  right units, with its exact binomial interval, against its gold band's
  competence floor (`floor.competence.none` for no-need cases). A case
  **fails** when the whole interval lies below that floor. It is a veto, not a
  proof: the band metrics carry the burden of proof, and a small count widens
  a case's interval, so it fails only on clear evidence.

### 4.6 Bounds and tests

- Intervals are two-sided 95%. N2 has no comparator, so no per-metric δ.
- **Exact binomial** (Clopper–Pearson) for per-unit proportions (band
  competence, case criteria), with units pooled across held-out sessions and
  treated as independent, since each task starts from none.
- **Session bootstrap** for the rest: 10,000 rounds, percentile interval,
  seeded from the labels hash. With 4 held-out sessions there are only 35
  distinct resamples, so these intervals are coarse and likely too narrow; the
  EvalRecord says so.
- A floor is **met** when the lower bound is at or above it, **failed** when the
  upper bound is below it, and **straddled** otherwise; a cap is the mirror.

### 4.7 Verdict precedence

The first rule that applies decides:

1. **Refused, no verdict.** A stand-in's hash differs from GATE-V1's, a replay
   log fails den-core's `check.py`, an ask line predates the labels freeze, or
   a GATE-V1 slot is empty. score.py writes nothing.
2. **insufficient**: a sample gate is unmet. Failures are still reported.
3. **not_feasible**: a gated metric failed, or a case failed.
4. **insufficient**: a gated metric straddled.
5. **feasible**: every gated metric was met.

### 4.8 The EvalRecord

- Canonical JSON (N1's rule) in `C:\den-takes\n2\`, outside git and OneDrive.
  Git gets one line per scored run in `tools/den-n2/RESULTS.md`: verdict and
  the EvalRecord, GATE-V1 and labels hashes, with counts. Fields:
  - `verdict`; `reasons` (the deciding rule, each failed, straddled or unmet
    item); `gate` (GATE-V1.md's sha256 and commit);
  - `variant`: scope, phone band, prompt and its sha256, request settings,
    served model and engine epochs, need version with code and config sha256s,
    adapter and ask-step sha256s, `pacing_s`, `tick_s`, `onset_mode`, detector;
  - `gold_set.n1 = {fixture: {version, hash}, labels: {version, hash}}`, with
    `n_intervals`, `label_version`, `hash` and `refreshed` from the freeze line;
  - as the N1 plan sets out: `gold_set.regions: null`, `gold_set.regions_status:
    "no worked-patch marks (N1/N2 make no working-region claim)"`, `R_eval: []`,
    `tau_R: null`, `tau_R_status: "pending (reference-geometry)"`. The
    `gold_set` schema gains `regions_status` and a nullable `regions` then;
  - `split`; `metrics` (unit, counted-per, orientation, value, interval, bound
    method, threshold, status, low-light split); `sample_gates`; `cases`;
  - `service` (S median and p95; re-send, invalid and failed counts);
    `excluded` (uncertain seconds, unsure marks, carried pieces, onset tags,
    misses); `replay` (each log's sha256 and `check.py` result);
  - `promoted: false`; no working-region, room-position, two-person or
    routed-timing claim; `diagnosis`, null unless not_feasible.

## 5. The estimator: den-core need v1

N2 adds no estimator module. It extends den-core's `need.py` with an A7 preset.

- **Prerequisite.** The owner's local den-core is ahead of `origin`, and must
  be pushed before any build extends `need.py`.
- Without the preset, `make_policy()` stays need v0 and its outputs don't
  change. The edit changes `need.py`'s sha256, so `check.py` refuses older logs
  (`code changed: need`); those are synthetic plumbing runs only.
- **The preset** (`need-v1-a7`) sits wholly in the policy config, so `check.py`
  rebuilds it from the log header: `theta_on` 0.6, `theta_off` 0.4,
  `entry_dwell_s` 5, `exit_negative_s` 20, `demand_rise_s` 1, `demand_fall_s`
  10 (A7), and GATE-V1's `facts_map`, `confidence_table`, `field_assignment`.
- **Rows.** Each `facts` event gives one row: (`activity_hypothesis`,
  `held_object`) of a valid reply with `people_visible` one; or `no_box` (the
  person is out of view); or a hold row: `unclear` (a key field unclear, or
  people other than one), `invalid`, `failed`, or `stale` (past `valid_until`
  on arrival).
- **Facts map**, fitted on development. A seen row's evidence is the share of
  its development seconds that are gold-engaged. Its level is A7's example for
  its majority development band: fine 0.6 (small print), none 0.2 (a self-lit
  screen), casual 0.4 (the cut; A7 gives no casual example). Only bands are
  scored, so a level matters through its band and its fall. `no_box` has
  evidence 0 and the none level. Hold rows and unseen rows move nothing.
- **States**, over s, the evidence of the latest non-hold row:
  - none → starting when s > θ_on; starting → engaged when s has stayed above
    θ_on for 5 s; starting → none when a non-hold row brings s to θ_on or below;
  - engaged → ending when s < θ_off; ending → engaged when s > θ_on (re-entry);
  - ending → none after 20 s of observed negative evidence. The exit clock runs
    while s < θ_off and the latest row is not a hold row, pauses on a hold row
    (hidden hands hold the state), and resets when s > θ_on.
- **Demand.** The target is the latest non-hold row's level. The level rises to
  a higher target at once (A7: within about 1 s), and falls to a lower one
  linearly over 10 s. It is null until the segment's first non-hold row.
- **Confidence** (prompt-1 gives none): `confidence_table[field][label][row]`,
  the development accuracy of that label at seconds with that row. A row unseen
  on development takes the label's pooled development accuracy.
- **Output.** person_state/v1 as v0 writes it, with the two confidences and
  `visual_demand.level` filled (`interpreter.version` `need-v1-a7`), on any
  change, at a `facts` event or den-core's 2.0 s tick. Under option 1 the row
  gains the detector's two facts.
- **Adapter.** den-core `sources.n2_asks(path)`, per its README's "Adding a
  source adapter": one function, a name in `events.ADAPTER_SOURCES`, `run.py
  --n2-asks FILE --need-config FILE`, a test. One `facts` event per slot, at
  t = the take's `started_unix` in whole seconds + r + S (S = 0 if nothing was
  sent). Its data is a la-fact-log-1-shaped line (12 keys) holding the slot's
  receipt or null, with `source_id` `den_n2_offline`, never the live one's.
- **Replay.** One den-core run per usable segment, `--policies need`, so state
  resets at cuts. Every log must pass `check.py` before score.py reads it.

## 6. The ask path

A local look-test tool, not in git, copies JPEG stills to the LA host's RAM
disk and asks the local vLLM through `qwen_scene` unchanged: one request at a
time, at least 4 s apart, a 20 s timeout, a GPU temperature and free-memory
gate, one re-send. N2's ask step reuses that pattern; this names its interface.

- **Refusals** (PC): any session until the labels-v1 freeze line is in
  `tools/den-label/FREEZES.md` and matches the labels on disk; held-out
  sessions while any GATE-V1 slot is empty.
- **Schedule**, from R's `t_s`, reset at every cut. In each usable segment
  [a, b), requests go at r = a + kP for k = 1, 2, … while r < b. Each takes the
  latest frame at or before r and the frames nearest r − 1.5 s and r − 3 s in
  the same segment. An empty box in the latest frame logs `no_box`; fewer than
  2 frames logs `failed`. Neither sends anything.
- **Crop**, on the PC (standard library only): one rectangle per request, the
  latest frame's padded box widened outward to even pixels (NV12's chroma is
  2 × 2), applied to every frame of the request. Only these NV12 crops and a
  manifest leave the PC.
- **On LA.** JPEG encoding happens here, because R's frames are NV12 and the PC
  side is standard library only. It uses the look-test tool's settings, which
  GATE-V1 records. Then `qwen_scene.model_context` (placement, a local time
  label from the latest frame's `recv_pc_unix`, prior null), `request_body` or
  prompt-2's, the gate checks, the send, the timeout and one re-send.
- **Replies.** qwen_scene's `parse_reply` and `build_observation` under
  prompt-1, prompt-2's own under prompt-2 (section 1(b)), and `first_receipt`
  under either: an invalid reply stays an invalid receipt with its reason,
  never repaired. A request that fails twice is logged `failed`, with no
  receipt. A gate refusal waits and retries the same slot; the virtual
  schedule doesn't move. S is LA's monotonic time from the first send to the
  reply used.
- **Out:** `asks.jsonl` per session, one line per slot (slot, segment, r,
  frames, crop, outcome, S, attempts, receipt: words and digests only). The
  crops leave the RAM disk when the run ends or stops. Agents never open
  frames or crops.
- N1's revised takes have 1,500 to 1,610 s of activity: about 375 to 400
  requests per take at P = 4 s, and at least 25 to 27 minutes of LA time.

## 7. Order

1. **Labels-v1 freeze** (N1): both freeze lines on `origin`. No estimator output
   exists before this; the ask step refuses.
2. **Development step**, development sessions only: ask with prompt-1, replay,
   and run `score.py --split development` (every metric with its interval, S,
   the case × row table); then fill GATE-V1's slots. A prompt-2 is allowed here
   and only here; if written, development is asked and fitted again under it.
3. **GATE-V1.md committed** with every slot filled, and pushed. Its commit time,
   before the first held-out ask line, shows the order.
4. **The held-out set, opened once:** ask, replay, `check.py`, `score.py
   --split held_out`, the EvalRecord, the RESULTS.md line.
5. **After the verdict:**
   - **feasible**: the perception rungs may start; their bar is at least N1's
     box and constant id.
   - **insufficient**: more sessions under N1's rule, in a new labels version;
     the enlarged held-out set is scored once more, GATE-V1 unchanged.
   - **not_feasible**: keep the current lighting component, and don't start or
     carry further the tracker, bundles or per-person optimiser on the strength
     of the need premise. Diagnose by field and case first. Viewpoint-limited
     (units heavy in `unclear`) points to camera placement; a still-reader
     failure to the rule's weights and θ_on, revisited on development;
     demand-only (engagement right, band wrong) to the interpreter input.
     Under option 2, also ask whether option 1 is worth running. Then use a
     fresh held-out set: the held-out half of new sessions under N1's rule.

## 8. GATE-V1.md and its empty slots

- Committed first with its slots empty, like R's CHECK-V1: prose plus one
  fenced `gate-v1` block that score.py reads. `--split held_out` refuses while
  any slot is `null`. Copied in now, never slots: τ_on, τ_off, the 6 s grace,
  the cut points, false-fine ≤ 0.033, onset ≤ 3 s, ECE ≤ 0.1 over 10 bins, the
  sample gates, the interval rules, A7's starting values.

| Slot | Filled by, before held-out |
|---|---|
| `scope` | the owner, at plan review |
| `prompt` (prompt-1 or prompt-2, with its sha256) | development step |
| `pacing_s`, `onset_mode` | development step, from S_dev |
| `facts_map`, `confidence_table` | fitted on development |
| rule values changed from A7, if any | development step |
| `floor.competence.fine`, `.casual`, `.none` | the owner, on development |
| `floor.fine_recall`, `floor.coverage` | the owner, on development |
| `cap.early_off`, `cap.false_on` | the owner, on development |
| `detector` (option 1 only) | development step |
| stand-in versions and hashes, S_dev, the look-test tool's JPEG and gate settings | mechanical |

## 9. Build order and sizes

| Step | Pieces | Size | Agent time |
|---|---|---|---|
| 1 | `tools/den-n2/score.py`, `GATE-V1.md` (slots empty), `test_den_n2.py` on synthetic N1-format labels and need logs, `README.md`, `RESULTS.md`, release note | about 1,300 lines | 1.5 days |
| 2 | need v1 preset and `test_need.py` additions; `sources.n2_asks`, the `run.py` flags and their test | about 550 lines | 1 day |
| 3 | the ask step: PC `ask.py` (schedule, crop, copy, refusals), the LA step (look-test pattern), tests with a fake LA | about 750 lines | 1 day |
| at development, if needed | prompt-2: its request body, reply parser and receipt builder, and their tests | about 260 lines | 0.5 day |
| option 1 only | detector pass, its facts, tests | about 500 lines | 1.5 days |
| **Total, option 2** | | **about 2,900 lines** | **about 4 agent-days** |

Tests are all synthetic. score.py: every window, mark, carried piece, onset
tag, bound, verdict rule and refusal, and development mode never opening
held-out labels. need v1: v0 unchanged, each transition, the exit clock, rise
and fall, `check.py` OK on a v1 log. Ask step: the schedule and its resets,
exact NV12 crops, the freeze refusal, no image or reply text in any log, and
re-sends, timeouts and gate waits against a fake LA and clock. prompt-2: its
own values pass its parser, and its receipts go through the adapter and need
v1 unchanged.

## 10. Approvals

- **Now:** this plan, and the scope choice.
- **At run time**, a go-ahead each: LA GPU time, and den-footage crops sent to
  LA's RAM disk. The earlier figure was about 270 requests per 21-minute take;
  N1's revised takes give about 375 to 400, so about 1,500 to 1,600 for 4
  development sessions (twice with prompt-2) and the same for 4 held-out. A
  smaller `pacing_s` multiplies this by 4/P; option 1 adds the detector's
  install and GPU time. Building, synthetic tests, replays and scoring need
  no go-ahead.

## 11. Risks

| Risk | What N2 does |
|---|---|
| S depends on what else uses LA's vLLM (the always-on den loop) | S measured as it comes; development and held-out run under the same loop state, noted in the EvalRecord |
| 4 held-out sessions make the bootstrap coarse | Exact binomial for per-unit proportions; the limitation is written down |
| Floors set after seeing development | Set by the owner, committed before held-out, never changed after |
| A prompt-2 written for the staged cases flatters held-out | The EvalRecord names the prompt and where it was written |
| N1 changes again before it is built | score.py reads only the N1 fields named here; reconcile at build time |

## 12. Deliberately left out

- Any change to production, the live loop, qwen_scene, CHECK-V1, or N1's
  protocol and tools; any working-region, room-position, two-person or
  routed-timing claim; a new estimator module, tracker, registration, bundles,
  comparator or δ; images or reply text in any log.
- `qwen_person_scene` (main's whole-frame request with a supplied box). It
  shares prompt-1's enums, so check (b) applies to it too.

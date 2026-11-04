# N1 plan: the single-person reference

Status: plan only. Nothing here is built or run. Written 2026-11-04.

## Context

- N1 is the ladder rung "A single-person reference, as two artifacts". It
  comes after R and before N2. The owner chose to run R, N1 and N2 before any
  perception rung (need-test-order: first).
- R (`tools/den-take`, branch `claude/rung-r`) records single-person den takes.
  A **retained session** is a take whose newest `check-N.json` says PASS and
  which is not a commissioning take. N1 uses retained sessions only.
- N1 makes two things and keeps them apart:
  - **The input fixture**: per-frame person boxes, one constant id, and a
    static surface map. It is a fixed input for N2. It is not gold.
  - **The labels**: engagement intervals, demand bands, and the shortcut and
    quiet cases. The owner makes them in a separate blind pass.
- N1's gate:
  - a written annotation protocol, fixed before labelling;
  - a whole-session split into development and held-out, fixed before any
    tuning;
  - the fixture and the labels versioned and hashed separately before any
    estimator output is opened.
- N1 unblocks N2's scores. N2 estimates `focused_engagement` and
  `visual_demand` offline from frozen stand-ins, and its sample gate decides
  feasible, not feasible or insufficient.
- N1 takes its protocol from the owner's answers of 2026-11-02: any den footage
  may be kept on household computers; the owner labels alone, blind, with a
  delayed self re-mark; onset is contact-starting; R, N1 and N2 go first; the
  seed demand gates. The decision register still lists these as pending on
  purpose. N1 does not change that.
- The owner wants this simple. N1 adds one command, one page and two spoken
  scripts. It uses no GPU, no detector, no network, and no camera beyond R's
  own takes.

## Approach

- New folder `tools/den-label/`. Standard-library Python 3.12, run with
  `python -B`, plus one local HTML page. Nothing to install.
- It runs on the owner's PC only, next to the takes. A small server on
  127.0.0.1 serves one take's frames to the page in the owner's browser.
  Frames never leave the PC.
- It reuses R's bundle reader (`check.read_bundle`) at run time and R's
  synthetic take helpers in tests.
- From a take it reads only `frames.csv`, the newest `check-N.json`, and two
  fields of `take.json` (`script`, `started_unix`). It never reads cue times.
- **Boxes are drawn by the owner**: a box on keyframes, straight lines in
  between, then an audit playback. This is simpler than a fixed detector. A
  detector needs LA GPU use (a go-ahead) with frames copied off the PC, or a
  new local install, and the owner would still have to audit it.
  Owner-drawn keyframes need no go-ahead, install nothing, and keep frames on
  the PC.
- **Hard cases are staged inside R's takes** by two new spoken scripts that
  R's `take.py` plays (`--script hard-a`, `--script hard-b`). This small
  `take.py` change is N1's only change outside `tools/den-label/`.
- All N1 output lives on the PC in `C:\den-takes\n1\`, outside git and
  OneDrive. Git gets the code, the protocol, and one line per freeze with
  versions, hashes and counts. Boxes and labels never go to git or the cloud.
- During N1, agents never open frames or the page. They read only
  `label.py status` output and freeze lines: counts and hashes.
- **No den-core events.** den-core's `den-event-1` lines are what policies read
  during replay. Gold labels there could leak into a policy. The fixture is
  per-frame geometry, not an event stream. N2 can add an adapter later if it
  needs one.

## Files

| File | Runs on | Does |
|---|---|---|
| `tools/den-label/PROTOCOL-V1.md` | repo | The annotation protocol (the gate). Committed first, before any label. Its sha256 goes into every draft, both manifests and every freeze line. |
| `tools/den-label/label.py` | owner's PC | The one command: `sessions`, `surfaces`, `boxes`, `labels`, `remark`, `status`, `freeze`, `verify`. Runs the 127.0.0.1 page server. |
| `tools/den-label/n1.py` | PC (imported) | Formats, the split rule, box interpolation and padding, label checks, regions, the re-mark sample and agreement, canonical hashing, freezing. |
| `tools/den-label/page.html` | owner's browser | One page with three modes: surfaces, boxes, labels (the re-mark uses label mode). Draws NV12 frames on a canvas. Keyboard-driven. Autosaves to the server. |
| `tools/den-label/README.md` | repo | Owner steps, in PowerShell 5.1. |
| `tools/den-label/FREEZES.md` | repo | Append-only log of freeze lines: versions, hashes, counts. No content. |
| `tools/den-label/test_den_label.py` | PC or cloud | Unit tests on synthetic takes only. |
| `tools/den-label/test-page-browser.mjs` | cloud | One browser smoke test of the page on a synthetic take. |
| `tools/den-take/take.py` (small change) | owner's PC | `--script` also takes a name from `tools/den-take/scripts/`, then speaks and logs its cues. |
| `tools/den-take/scripts/hard-a.json`, `hard-b.json` | repo | The two staged scripts. |
| `tools/den-take/test_den_take.py` (additions) | PC or cloud | Tests for staged scripts. |
| `changes/unreleased/den-label.md` | repo | The release note, added at build time. |

## What N1 produces

On the PC:

```text
C:\den-takes\n1\
  split.json            append-only: session, script, order, split
  work\                 drafts: surface draft, keyframes, label drafts (never an N2 input)
  fixture-v1\           the frozen input fixture
    fixture.json
    surface-map.json
    boxes\<take>.csv
  labels-v1\            the frozen labels
    labels.json
    sessions\<take>.json
    remark.json
```

### The input fixture (`den-n1-fixture-1`)

- `surface-map.json`: one map for every session, because the camera does not
  move. Coordinates are pixels of the 640x360 RGB frame. Each surface has:
  - `id` and `type`: `desk`, `counter`, `shelf`, `table`, `seat`, `lap` or
    `handheld`;
  - `height_cm`, declared by the owner, measured from the floor;
  - `polygon` in image pixels.
- `lap` is the region over the desk chair's seat where a seated lap is.
  `handheld` has no polygon. It means "held in the hands, wherever the person
  is", with declared heights for seated and standing. The map also names its
  reference frame (take, frame number, frame sha256).
- `boxes\<take>.csv`: one row per frame in the take's usable segments:
  `n,t_s,x0,y0,x1,y1`. Whole pixels, already padded and clipped to the image.
  Empty box fields mean no part of the person is in view.
- `fixture.json`, the manifest, holds:
  - schema, version, freeze time, and the protocol and tool sha256s;
  - `person_id: "p1"`, the one constant id;
  - the padding rule and the surface map's sha256;
  - per session: take id, check file name and sha256, `frames.csv` sha256, the
    usable segments, the frame count, and the boxes file's sha256.
- Never in the fixture: hands, contacts, working regions, keyframes, cue
  times, labels, or anything else time-varying beyond the box.
- **Fixture hash** = sha256 of `fixture.json`. The manifest holds every file's
  sha256, so one hash covers the whole fixture.

### The labels (`den-n1-labels-1`)

- `sessions\<take>.json`, one per session:
  - `take`, `check_sha256`, `split`, `protocol_sha256`.
  - `actor`: `labeller: "owner"`, `pass: "label"`, start and save times, tool
    sha256s.
  - `intervals`. Each one has `onset_n`, `onset_t_s`, `onset_kind`
    (`touch`, `no_touch` or `carried`), `offset_n`, `offset_t_s`,
    `offset_kind` (`release`, `no_touch` or `carried`), `band` (`fine` or
    `casual`), `case`, `sure` (`onset`, `offset`, `band`: true or false) and
    `by: "owner"`.
  - `spans`. Each one has `kind` (`quiet`, `uncertain` or `low_light`), a
    `case` for quiet spans (`idle_seated` or `idle_standing`), `start_n`,
    `end_n` and `by: "owner"`.
  - `reviewed`: the frame ranges the owner played through in the label pass.
- `remark.json`: the re-mark sample (seed and windows), the owner's second
  marks in the same format with `pass: "remark"`, and the agreement figures.
- `labels.json`, the manifest, holds: schema, version, freeze time, protocol
  sha256, `split.json` sha256, per session (take, split, file sha256, interval
  count, regions), the re-mark's sha256, and totals per split and band.
- **Labels hash** = sha256 of `labels.json`.

### Versions, hashes and the EvalRecord

- Every manifest is canonical JSON: sorted keys, no spaces, ASCII, one final
  newline. This is the same rule as den-core's `dumps`.
- A frozen folder is never edited. Any change makes `fixture-v2` or
  `labels-v2`. Sessions added later also make a new version, which keeps the
  old sessions unchanged.
- Each `freeze` prints one line. The owner pastes it to an agent, who appends
  it to `FREEZES.md` and pushes. The commit time shows the freeze came before
  any estimator run.
- The labels freeze also prints the EvalRecord fields N1 fills:
  - `gold_set.n1 = {fixture: {version, hash}, labels: {version, hash}}`;
  - `n_intervals`, `label_version`, `hash` (the labels hash), `refreshed` (the
    freeze date);
  - `regions {marked, uncertain, missing}`, in seconds.
- N2 writes the EvalRecord. `R_eval` and `tau_R` are not N1's.
- Regions: `marked` is reviewed usable time outside uncertain spans.
  `uncertain` is time inside uncertain spans. `missing` is usable time the
  label pass never reviewed. Cut time is not usable and counts nowhere.
- Every interval is owner-made. No model ever makes a label.

## Session staging

- Hard cases are staged inside ordinary R takes. R's slates, check and cuts do
  not change.
- **The `take.py` change.** `--script` takes `commission`, `free`, or the name
  of a file in `tools/den-take/scripts/`. A script file (`den-take-script-1`)
  lists blocks, each with `at_s` (seconds after "Begin"), `case` and `say`.
  take.py speaks each block at its time with R's speech pattern and logs it in
  `take.json` as a cue named `stage`. After the last block it goes to the end
  slate. Enter still ends a take early. `take.json` records the script file's
  sha256. check.py needs no change, because it finds `begin` and
  `hold_phone_end` by name.
- **Two scripts, `hard-a` and `hard-b`.** They have the same cases in a
  different order with different lengths, so no case sits at a fixed time
  after "Begin".
- One round of `hard-a` (about 3.5 minutes):

| Spoken cue | Case | Block |
|---|---|---|
| "Sit at the desk. Hands still." | `idle_seated` (quiet) | 15 s |
| "Sort the mail." | `sorting_mail` (fine) | 40 s |
| "Stop. Hands still." | `idle_seated` (quiet) | 15 s |
| "Read the book. Keep still." | `still_reader` (fine) | 45 s |
| "Stop. Stand up and wait." | `idle_standing` (quiet) | 15 s |
| "Wipe the desk." | `wiping` (casual) | 30 s |
| "Stop. Go to the counter." | walking | 15 s |
| "Use your phone at the counter." | `phone_at_counter` (casual) | 35 s |

- A take plays 5 rounds. After round 3, the cue "Turn the den lights down low
  now." leaves 20 s, and rounds 4 and 5 run in low light. The activity part is
  about 18 minutes. The whole take is about 21 minutes, inside R's 28-minute
  cap.
- Each take gives about 20 engagement episodes (about 10 fine and 10 casual)
  and about 15 quiet spans. Every case appears in normal light and in low
  light.
- **Props**, on the desk before "Begin": a stack of mail, a book, a cloth. The
  phone is the owner's own. After the last phone block, open the slate page
  again for the end slate. R's slate retries cover a slow switch.
- **The cues are prompts, not labels.** Labels come from the video. If the
  owner does something else, the labels say what really happened.
- Cue times are used nowhere in N1. Neither the fixture nor the labels read
  them. `status` checks case coverage from the labels' case tags only.
- **How many.** 8 staged takes, four `hard-a` and four `hard-b`, alternating,
  on at least 2 days. Two a day is fine. Each split then gets 4 sessions and
  about 40 episodes per band. N2's sample gate needs at least 30 episodes per
  band from at least 3 sessions over at least 2 days. With the scripts
  alternating, seven takes is the fewest that gives 3 held-out sessions under
  the split rule.
- **Free takes are optional.** A retained free take is either labelled in full
  or left out as a whole when it is registered, before any pass, with a reason
  recorded.
- If the counter is not in the camera's view, the phone case moves to another
  in-view spot away from the desk. The owner names it when okaying the cue
  text.
- Lights down: the owner dims the den lights by hand, the usual way. If they
  come back up, the owner marks `low_light` only where the room is dim.

## The labelling passes

### Order, per session

1. `label.py sessions` registers new retained sessions and gives each its
   split, before any pass.
2. The surface map, once for all sessions.
3. The fixture pass (boxes), then its audit.
4. The label pass. It opens only after that session's audit is complete. Once
   it starts, that session's boxes are locked.
5. The re-mark of a sample, at least 3 days after labelling.
6. Freeze the fixture, freeze the labels, and commit the two lines. Only then
   may N2 run.

### The surface map (once, about 15 minutes)

- `label.py surfaces` opens the page on any real frame. A commissioning-take
  frame is fine.
- The owner steps to a clear view and clicks the corners of each surface in
  view. They pick its type and type its height in cm, measured once with a
  tape. Lap and handheld start with default heights the owner can change.

### Fixture pass: boxes (about 10 to 15 minutes per staged take)

- `label.py boxes <take>` opens the page on the take's usable frames only. No
  slates, no cut frames.
- **The box rule** (in PROTOCOL-V1): the owner draws the tight box around the
  whole visible person, including arms, hands and anything held. The tool adds
  a fixed pad and clips to the image.
- The pad is one setting for the whole fixture. It starts at 20% of the box's
  width on each side and 20% of its height top and bottom, and at least 16 px.
  It may only grow, and only before the fixture freeze. Growing it needs no
  redrawing.
- The owner never draws a box around an object, a hand or a work area, and
  never adds a keyframe because an activity starts. Keyframes go where the
  tight box stops fitting the body.
- **Workflow.** Play at 2x or 4x with the current box shown. Pause when it no
  longer fits, then drag it to fit. That frame becomes a keyframe, and straight
  lines fill in between. One key marks "person not in view" from a frame on.
- **Audit.** Play the whole usable part at up to 4x with the padded box. If the
  hands or the worked object leave it, fix the keyframe there. If that happens
  often, raise the pad for every session. The page records the audited range.
  The freeze refuses a session that was not audited end to end.
- Keyframes stay in `work\`. Only the padded per-frame boxes enter the fixture.
- If the owner sees a second person that R's check missed, they re-run R's
  `check.py` and type the span. The newest check counts. label.py notices the
  new check and drops any boxes and labels now inside a cut.

### The protocol (PROTOCOL-V1, the gate)

It is committed before the first label, and the owner okays it first.

1. **Engagement.** The person works with an object or surface they need to
   see. An interval runs from onset to offset.
2. **Onset (contact-starting).** The first frame showing the hand touching the
   worked object (`touch`). If a task starts with no touch, for example
   reading a page left on the desk, the onset is the first frame the person is
   settled facing it at working distance (`no_touch`). An interval already
   running when a usable segment starts, or split by a band change, has a
   `carried` onset.
3. **Offset.** The last frame of contact before contact with that object stops
   for more than 6 s (`release`). For a `no_touch` task, it is the frame the
   person turns away or leaves working distance. `carried` at a segment end or
   a band split. An interval never crosses a cut or a segment edge.
4. **Pauses and switches.** A pause of 6 s or less with the same object does
   not end the interval. Switching to a different task starts a new one.
5. **What onset delay is timed to.** From the labelled `touch` onset to the
   first estimator output for that interval whose `focused_engagement` state
   leaves `none` (first notice, `starting`). Only onsets marked sure count. The
   5 s dwell, from `starting` to `engaged`, is kept and reported separately. It
   is not part of onset delay. The target is a median of 3 s or less (A13).
   N2 does the timing.
6. **Frame time.** Every mark is a frame. Its time is that frame's `t_s`. At
   about 4 frames a second, marks are quantised to 0.2 to 0.27 s. The true
   touch lies between the previous frame and the marked one.
7. **Demand bands and cut points.** One band per interval, set by the task:
   - `fine`: needs small detail at hand distance. Reading print, mail,
     writing, fine handwork.
   - `casual`: needs to see, but not small detail. Wiping, tidying, handling
     things without reading them, using a phone (its screen lights itself).
   - `none`: every reviewed second outside intervals.

   On N2's `visual_demand` scale the cut points are the seed demand gates:
   fine at 0.6 or more, casual from 0.4 to under 0.6, none under 0.4. The owner
   labels bands, never numbers. If the band clearly changes for 30 s or more,
   the interval is split there, with both sides `carried`.
8. **Cases.** Each interval has one case: `sorting_mail`, `still_reader`,
   `wiping`, `phone_at_counter`, or `other`. The first four are the shortcut
   cases, where a cheap cue (motion, posture or place) points the wrong way.
   Quiet spans have `idle_seated` or `idle_standing`: a person is present and
   not engaged, and the right answer is no engagement. `low_light` spans mark
   time with the room dim.
9. **Uncertainty.** Never guess. If unsure of a boundary or a band, set that
   `sure` flag to false; it is left out of that measure only. If unsure
   whether the person is engaged at all, mark an `uncertain` span. It is left
   out of all scoring and counted in `regions.uncertain`.
10. **Who labels.** The owner, alone. Every item records `by: "owner"`, and
    every file records its pass and times. Results are reported as
    single-labeller results with a delayed self re-mark.
11. **Blind.** Labels are made and frozen before any estimator output exists
    for those sessions. The label page shows only frames: no boxes, no cue
    times, no estimator output. N2 may open its output only after the labels
    freeze line is on `origin`.
12. **Split.** The rule in the Split section, fixed now.

### Label pass (about 20 to 30 minutes per staged take)

- `label.py labels <take>` opens the page in label mode. It shows the usable
  frames only, with no boxes.
- Play at 1x to 4x. At each engagement:
  - step frame by frame to the first touch and press the onset key;
  - pick the band and the case, one key each;
  - step to the last contact and press the offset key.
- Quiet, low-light and uncertain spans get a start key and an end key. They
  need not be frame-exact.
- The page tracks what has been played through. Unreviewed time is `missing`.
- The server checks every save: each interval inside one usable segment, onset
  before offset, no overlaps, bands and cases from the fixed lists.

### Re-mark (once, about 25 minutes in all)

- `label.py remark` draws a sample of 3-minute windows covering 10% of the
  labelled minutes, at least 6 windows. Windows come only from sessions
  labelled at least 3 days earlier. The seed comes from `split.json`'s hash,
  so nobody picks the windows.
- The owner labels each window from scratch. The first labels are hidden.
- n1.py reports intervals matched (overlap at least half), onset and offset
  differences (median and 90th percentile, sure `touch` and `release` marks
  only), band agreement with Cohen's kappa, and case agreement.
- The first-pass labels stay the gold. The re-mark only reports consistency.

## Split

- By whole session. It is assigned by rule when a session is registered,
  before any pass and long before any tuning.
- **Rule:** within each script name (`hard-a`, `hard-b`, `free`), sessions
  alternate development, held-out, development, held-out, in recording order
  (`take.json`'s `started_unix`). The first of each script is development.
- Why per script: the owner alternates `hard-a` and `hard-b`, so one
  alternation over all takes would put every `hard-a` in one split.
- `split.json` is append-only. A session's split never changes. An excluded
  free take gets no split and takes no slot.
- Sessions added later continue the same alternation and arrive in a new
  labels version.
- N2 tunes (its recall and coverage floors and anything else) on development
  only, and reads held-out labels only in its scoring run. `status` shows
  held-out counts, never held-out contents.
- `split.json`'s hash is in the labels manifest and in every labels freeze
  line.

## Tests

All tests use synthetic takes made with R's own test helpers (`make_take`,
`bundle`, `nv12`). No real frame, no camera, and no network beyond 127.0.0.1.

`test_den_label.py`:

- **Sessions.** Only the newest check counts. FAIL and commissioning takes are
  not retained. A later PASS check makes a session retained.
- **No cue data.** The `take.json` reader returns only `script` and
  `started_unix`.
- **Split.** Per-script alternation in recording order. Stable when sessions
  are added. Never reassigns. Refuses an edited `split.json`. Exclusions take
  no slot.
- **Frame server.** Serves only usable-segment frames of the named take
  (slate and cut frames get 404). Returns raw NV12 of 345,600 bytes. Binds
  127.0.0.1 only. Refuses a wrong token or Host header. Sends no CORS header.
  Refuses paths outside the take.
- **Boxes.** Interpolation is linear in `t_s`. Absent runs stay empty. The pad
  and the clip are exact. The pad can only grow.
- **Fixture.** Exactly one row per usable frame. One id. Only whitelisted
  fields, so no hand, contact or region field can enter. Refuses an unaudited
  session. Refuses box edits once the label pass has started.
- **Labels.** Every protocol check: bounds, order, overlaps, segment edges,
  fixed lists, the 6 s pause rule, band splits. `by` on every item. Regions
  add up to the usable seconds.
- **Label mode sends no boxes.** Its endpoints carry no box data.
- **Re-mark.** Same windows for the same seed. Windows lie inside usable
  segments and come only from sessions labelled 3 or more days earlier. First
  labels are hidden. Agreement figures are right on hand-made cases.
- **Freeze.** Canonical bytes give fixed hashes. Frozen folders refuse writes
  and overwrites. Versions go up. `verify` catches one changed byte. Drafts
  made under another protocol sha256 are refused. The two hashes are
  independent: changing a label leaves the fixture hash alone, and the
  reverse.
- **Status.** Counts per split and band, and days per split. No times.
- **Folders.** Refuses an N1 folder inside a git checkout or OneDrive.

`test_den_take.py` additions:

- A script file parses. Bad files are refused: unknown keys, times not
  increasing, or longer than 28 minutes.
- With R's fake camera, a staged take speaks each cue at its time, logs it as a
  `stage` cue with its case, records the script's sha256, and ends with the
  end slate.
- check.py gives the same usable part and verdict with and without stage cues.
- Enter ends a staged take early.

`test-page-browser.mjs` (cloud only, with the preinstalled Chromium): opens the
page on a synthetic take, draws a box, interpolates, audits, marks one
interval, and checks the drafts the server saved.

## Deliberately left out

- Any detector or model, GPU use, the LA host, the Jetson. No frame leaves the
  PC.
- Hand, contact and working-region marks. Keyframes in the fixture.
- A second labeller and inter-labeller figures. Self re-mark only, as the owner
  chose.
- Demand numbers. The owner labels bands only.
- Cue markers, "jump to cue" and pre-filled tags in the label page. They would
  anchor the labels on the script.
- den-core events for labels or the fixture.
- Any estimator, scoring, EvalRecord writing, or recall and coverage floors.
  Those are N2's.
- Any camera move or reframing. The sofa stays out of view and out of scope.
- Face blurring, export or sharing. N1 output stays on the PC. Only hashes
  reach git.
- Any change to CHECK-V1 or check.py.
- Second-person handling beyond R's cuts.
- Video export, and labelling on a phone or tablet.
- Any change to the architecture's decision register.

## Approvals needed

Steps marked **[go-ahead]** need the owner's yes. Nothing else does.

1. **[go-ahead] Okay PROTOCOL-V1** before the first label (about 10 minutes):
   the definitions, the phone-is-casual default, the starting pad, and the
   spoken cue text (including where the phone case happens if the counter is
   not in view). This is the gate's "fixed before labelling".
2. **[go-ahead] The staged batch:** "8 staged takes, `hard-a` and `hard-b`,
   four each". R asks for a go-ahead per take or per named batch, because a
   camera fault during polling could briefly drop Frigate's den feed, which
   the den lights follow.
3. **[go-ahead] Merging the `take.py` change.** It is built after R lands on
   `main`, so the two sessions never edit `take.py` at once. It changes
   `take.py`'s hash, not CHECK-V1.
4. No go-ahead needed: running `label.py` (PC only, 127.0.0.1, no camera),
   the surface map, the passes, the re-mark, the freezes.
5. An owner action, not a go-ahead: paste the two freeze lines to an agent.

## Risks

| Risk | Effect | What N1 does |
|---|---|---|
| Owner time | About 9 hours in all | Short staged takes, a keyboard page, autosave, stop any time. Label the first two takes fully before recording the rest. |
| Small or hidden contacts (phone at the counter, hands behind the body) at 640x360 | Unsure onsets | `sure` flags and uncertain spans, counted and reported. Never guessed. |
| About 4 frames a second | Onsets quantised to 0.2 to 0.27 s | Stated in the protocol. Small against the 3 s target. |
| A box that encodes the onset | The fixture leaks the answer | Body-only tight box, fixed pad, no activity keyframes, field whitelist, boxes locked before labels. |
| Staged behaviour is not natural | Scores flatter real use | Two orders. Free takes welcome. N2 can report staged and free sessions apart. |
| Script timing | An estimator could learn time from "Begin" | Cue times never enter N1. Two scripts with different orders. |
| Too few episodes after uncertainty | N2 says insufficient | `status` shows counts per split and band. Record more under the same rule. |
| One labeller | No inter-labeller figure | Reported as single-labeller, with the self re-mark. |
| R's format changes before its first real take | The tool breaks | N1 reads only the fields listed here. Tests use R's own helpers. |
| Dimming mid-take | A few seconds of exposure change | The `low_light` span starts once the room is dim. |
| Counter not in view | The phone case is lost | Moved to another in-view spot, named by the owner. |
| Disk: C: is 97% full | 8 staged takes need about 40 to 70 GB | R's 35 GB floor refuses a take first. N1's own files are small. |
| N1 output only on the PC | Lost with the disk | README suggests a copy to another household computer or a USB drive after each freeze. |

## Owner time per take

| Step | Staged take (about 21 min) | Free take (up to 28 min, optional) |
|---|---|---|
| Record (R) | 21 min | up to 30 min |
| R's check and second-person scan | 3 min | 3 min |
| Boxes and audit | 10 to 15 min | 10 to 20 min |
| Labels | 20 to 30 min | 15 to 35 min |
| **Total** | **about 1 hour** | **about 1 to 1.5 hours** |

One-time: read and okay the protocol (10 min), the surface map (15 min), the
re-mark (25 min), and the freezes (5 min).

All of N1: 8 staged takes at about 1 hour each, plus about 1 hour one-time.
That is about 9 hours, over 1 to 2 weeks of evenings.

## Effort

| Piece | Size | Agent time |
|---|---|---|
| PROTOCOL-V1.md | about 150 lines | 0.5 day |
| `take.py` staged scripts, two script files, tests | about 60 lines plus 150 test lines | 0.5 day |
| `n1.py` | about 450 lines | 1 day |
| `label.py` | about 300 lines | 0.5 day |
| `page.html` | about 600 lines | 1.5 days |
| Tests and the browser smoke test | about 800 lines | 1 day |
| README, FREEZES.md, release note | about 150 lines | 0.25 day |
| **Total** | **about 2,500 lines** | **about 5 agent-days, in 2 or 3 sessions** |

That is close to R's size (about 2,200 lines).

## What can be built before R's first real take, and what waits for it

Before R's first real take (no camera, no real frames):

- PROTOCOL-V1.md, committed first, then the owner's okay.
- `n1.py`, `label.py`, `page.html`, and all tests, on R's synthetic takes.
- The staged scripts and the `take.py` change, tested with R's fake camera.
  This waits only for R's branch to land on `main`, not for a real take.
- The README owner steps.
- A full synthetic dry run in the cloud: register, boxes, labels, re-mark,
  freeze, verify.

Waits for R's first real take (the commissioning take, once it passes):

- A practice run of the page on the commissioning take. Nothing from it is
  ever frozen, because it is not retained. This also checks page speed on real
  bundles.
- The surface map, which needs a real frame, and the check that the counter is
  in view.
- A first look at the pad on real frames.

Waits for retained staged takes (R's commissioning PASS, then go-ahead 2):

- The fixture and label passes themselves.
- The real owner-time numbers. They are checked after the first `hard-a` and
  `hard-b` are fully labelled. Script changes after that become new script
  names, which alternate on their own.
- The re-mark (3 days after labelling) and the two freezes.

# N1 plan: the single-person reference

Status: plan only. Nothing here is built or run. Written 2026-11-04, revised
2026-11-05 (phone band, desk view, quiet spacing).

## Context

- N1 is the ladder rung "A single-person reference, as two artifacts". It
  comes after R and before N2. The owner chose to run R, N1 and N2 before any
  perception rung (need-test-order: first).
- R (`tools/den-take`, branch `claude/rung-r`) records single-person den takes.
  A **retained session** is a take whose newest `check-N.json` says PASS and
  which is not a commissioning take. N1 uses only retained **staged** takes:
  their script is a file under `tools/den-take/scripts/`, not `free`.
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
- From a take it reads only `frames.csv`, the bundle files `frames.csv` lists
  (`frames/*.npz`, which the frame server opens through `check.read_bundle` for
  each frame's NV12 image), its `check-N.json` files, and two fields of
  `take.json` (`script`, `started_unix`). It never reads cue times.
- **Boxes are drawn by the owner**: a box on keyframes, straight lines in
  between, then an audit playback. This is simpler than a fixed detector. A
  detector needs LA GPU use (a go-ahead) with frames copied off the PC, or a
  new local install, and the owner would still have to audit it.
  Owner-drawn keyframes need no go-ahead, install nothing, and keep frames on
  the PC.
- **Hard cases are staged inside R's takes** by two new spoken scripts that
  R's `take.py` plays (`--script hard-a`, `--script hard-b`). Outside
  `tools/den-label/`, N1 changes only `tools/den-take/` (take.py's `--script`,
  two new files under `scripts/`, and `make_take`'s `started_unix` and
  `script` parameters plus staged-script tests in `test_den_take.py`) and adds
  the release note.
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
| `tools/den-label/PROTOCOL-V1.md` | repo | The annotation protocol (the gate). Committed first, before any label. Its sha256 goes into every draft, both manifests and every freeze line. Its one fenced block holds the few settings n1.py reads: the phone band, the pause and the re-mark wait. |
| `tools/den-label/label.py` | owner's PC | The one command: `sessions`, `surfaces`, `boxes`, `labels`, `remark`, `status`, `freeze`, `verify`. Runs the 127.0.0.1 page server. |
| `tools/den-label/n1.py` | PC (imported) | Formats, the split rule, box interpolation and padding, label checks, re-mark agreement, canonical hashing, freezing. |
| `tools/den-label/page.html` | owner's browser | One page with three modes: surfaces, boxes, labels (the re-mark uses label mode). Draws NV12 frames on a canvas. Keyboard-driven. Autosaves to the server. |
| `tools/den-label/README.md` | repo | Owner steps, in PowerShell 5.1. |
| `tools/den-label/FREEZES.md` | repo | Append-only log of freeze lines: versions, hashes, counts. No content. |
| `tools/den-label/test_den_label.py` | PC or cloud | Unit tests on synthetic takes only. |
| `tools/den-label/test-page-browser.mjs` | cloud | One browser smoke test of the page on a synthetic take. |
| `tools/den-take/take.py` (small change) | owner's PC | `--script` also takes a name from `tools/den-take/scripts/`, then speaks and logs its cues. |
| `tools/den-take/scripts/hard-a.json`, `hard-b.json` | repo | The two staged scripts. |
| `tools/den-take/test_den_take.py` (additions) | PC or cloud | Tests for staged scripts. R's `make_take` writes no `started_unix` and only `commission` or `free` as `script`, so it gains both as keyword parameters: `started_unix` defaults to a fixed base plus an offset per take, and `script` takes staged names. |
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
  - `take`, `check` (the check file's name), `check_sha256`, `split`,
    `protocol_sha256`.
  - `actor`: `labeller: "owner"`, `pass: "label"`, start and save times, the
    `end_to_end` time (null until the owner marks it), tool sha256s.
  - `intervals`. Each one has `onset_n`, `onset_t_s`, `onset_kind`
    (`touch`, `no_touch` or `carried`), `offset_n`, `offset_t_s`,
    `offset_kind` (`release`, `no_touch` or `carried`), `band` (`fine` or
    `casual`), `case`, `sure` (`onset`, `offset`, `band`: true or false) and
    `by: "owner"`.
  - `spans`. Each one has `kind` (`quiet`, `uncertain` or `low_light`), a
    `case` for quiet spans (`idle_seated` or `idle_standing`, and
    `phone_at_counter` if the owner sets the phone band to `none`),
    `start_n`, `end_n` and `by: "owner"`.
- `remark.json`: the re-marked take, its check file name and check sha256, the
  second marks in the same format with `pass: "remark"`, and the agreement
  figures.
- `labels.json`, the manifest, holds: schema, version, freeze time, protocol
  sha256, `split.json` and re-mark sha256s, per session (take, split, check
  file name, `check_sha256`, file sha256), and interval counts by band and
  `uncertain_s` per session and split.
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
    freeze date).
- N2 writes the EvalRecord. It writes `gold_set.regions: null`, `R_eval: []`
  and `tau_R: null`, and keeps `tau_R_status` as "pending
  (reference-geometry)". A new field, `gold_set.regions_status`, carries "no
  worked-patch marks (N1/N2 make no working-region claim)". The `gold_set`
  schema gains `regions_status` and a nullable `regions` when N2 is built.
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
- **Spacing.** Two architecture numbers set the gaps. A7's estimator leaves an
  episode only after 20 s of observed negative. A13's late-off band, τ_off,
  is the 60 s after a labelled offset, and it isn't counted. An earlier draft
  started each 15 s quiet block within 15 to 30 s of a task's end, so no quiet
  second fell outside τ_off and none could test "no engagement". Its
  `still_reader` also started while the mail episode was still `ending`, so
  that onset did not begin from none. The scripts now follow two rules:
  - every task starts after at least 30 s with no task (A7's 20 s plus a 10 s
    margin for a slow stop or start), so its onset begins from none;
  - each round has one long quiet block of 90 s. When it follows a task, its
    first 60 s are the late-off band and its last 30 s or so are scored
    quiet.
- **How quiet seconds are scored given τ_off** (in PROTOCOL-V1, so N2 follows
  it): a quiet second tests "no engagement" only if it is more than τ_off
  (60 s) after the latest labelled offset before it. Quiet seconds inside the
  band are labelled the same way but are not scored as quiet. The 30 s gaps
  therefore serve only the next onset. The scored quiet is the tail of each
  long block, plus any block before a take's first task.
- **Desk view (known risk).** A posed check on 2026-11-02 found that with the
  chair pulled in to the desk, the hands and a book lying on the desk or held
  low are below the camera's frame. As an earlier draft staged them, "Sort the
  mail", "Wipe the desk" and "Sit at the desk. Hands still" probably had the
  hands out of view. So:
  - the desk cues use the posture that check found in view: chair pushed back
    from the desk, book held at chest height;
  - mail sorting and wiping move to a surface the commissioning take's frame
    shows in view: the counter if it is in view, which is where A13 stages
    both cases. The owner names the spot at approval 1, the same way as the
    counter fallback;
  - the camera does not move.
- One round of `hard-a` (290 s):

| Spoken cue | Case | Block |
|---|---|---|
| "Go to the desk. Sit with the chair pushed back. Hands still." | `idle_seated` (quiet) | 90 s in rounds 1, 3 and 5; 30 s in rounds 2 and 4 |
| "Hold the book up at chest height and read it. Keep still." | `still_reader` (fine) | 30 s |
| "Stop. Put the book down. Go to the counter and wait." | `idle_standing` (quiet) | 90 s in rounds 2 and 4; 30 s in rounds 1, 3 and 5 |
| "Sort the mail on the counter." | `sorting_mail` (fine) | 30 s |
| "Stop. Hands still." | `idle_standing` (quiet) | 30 s |
| "Wipe the counter." | `wiping` (casual) | 25 s |
| "Stop. Hands still." | `idle_standing` (quiet) | 30 s |
| "Use your phone at the counter." | `phone_at_counter` (casual) | 25 s |

- One round of `hard-b` (290 s):

| Spoken cue | Case | Block |
|---|---|---|
| "Go to the counter. Stand and wait. Hands still." | `idle_standing` (quiet) | 90 s in rounds 2 and 4; 30 s in rounds 1, 3 and 5 |
| "Wipe the counter." | `wiping` (casual) | 30 s |
| "Stop. Hands still." | `idle_standing` (quiet) | 30 s |
| "Use your phone at the counter." | `phone_at_counter` (casual) | 20 s |
| "Stop. Put the phone away. Hands still." | `idle_standing` (quiet) | 30 s |
| "Sort the mail on the counter." | `sorting_mail` (fine) | 25 s |
| "Stop. Go to the desk. Sit with the chair pushed back. Hands still." | `idle_seated` (quiet) | 90 s in rounds 1, 3 and 5; 30 s in rounds 2 and 4 |
| "Hold the book up at chest height and read it. Keep still." | `still_reader` (fine) | 35 s |

- "Counter" in the cues means the spot named at approval 1.
- A take plays 5 rounds. After round 3, the cue "Stop. Turn the den lights
  down low now." leaves 20 s, and rounds 4 and 5 run in low light. After
  round 5, "Stop. Hands still." leaves 30 s, so the last task ends on a
  release inside the usable part. The activity part is 5 x 290 + 20 + 30 =
  1,500 s, 25 minutes. With the slates and phone handling (about 1 to 2
  minutes) the whole take is about 27 minutes. That is inside R's 28-minute
  cap on the activity part and CHECK-V1's 30-minute slate separation, even
  with two end-slate retries.
- Each take gives 20 engagement episodes (10 fine and 10 casual) and about 21
  quiet spans, 5 of them long. That is about 3 minutes of scored quiet per
  take: the tail of each long block, and a whole first block. Every case
  appears in normal light and in low light: `idle_seated` is long in rounds 1,
  3 and 5, and `idle_standing` in rounds 2 and 4.
- **If the phone band is `none`** (approval 1): `phone_at_counter` becomes a
  no-need shortcut case, a quiet-span case like `idle_seated`. Casual would
  then be wiping only: about 5 per take and about 20 per split, under N2's
  30-per-band gate. The remedy is one more casual block per round, not more
  takes, settled at that same okay before any take:
  - each round's phone block becomes `tidying` (casual), "Tidy the things on
    the counter.", with the same slot and length;
  - the phone block moves to right after the long `idle_standing` block in
    rounds 2 and 4, as "Use your phone at the counter." (25 s) and then
    "Stop. Put the phone away. Hands still." (30 s). Its seconds then lie
    past τ_off, and the next onset still begins from none. In `hard-b` the
    cue after the old phone slot becomes "Stop. Hands still.";
  - each take grows by 110 s, to 1,610 s (about 27 minutes) of activity, or
    about 28 to 29 minutes in all. That is still inside both limits. Casual
    is again 10 per take (wiping and tidying), and the phone case appears in
    normal and low light.
- **Props**, before "Begin": the book on the desk, the stack of mail and a
  cloth on the counter (or the named spot). The phone is the owner's own; keep
  it in a pocket between its blocks. At the end slate, open the slate page
  again. R's slate retries cover a slow switch.
- **The commissioning take's 3 minutes.** R's commissioning take has 180 s of
  unscripted activity (`take.py`'s `commission_activity_s`). N1 relies on it
  for the counter check, the surface map and the desk-view check. The
  den-label README tells the owner to spend them like this, with nothing
  spoken and R's files unchanged:
  - about 60 s sitting at the desk with the chair pushed back, holding the
    mail and then the book at chest height;
  - about 60 s wiping the counter (or, if the owner already knows the counter
    is out of view, the surface they would use instead);
  - about 60 s standing at the counter using the phone.

  One look at the commissioning take's contact sheet (check.py's
  `review\index.html`) then answers every in-view question: the counter, the
  wiping surface, and the seated posture with the mail and the book.
- **The cues are prompts, not labels.** Labels come from the video. If the
  owner does something else, the labels say what really happened.
- `status` checks case coverage from the labels' case tags, never cue times.
- **How many.** 8 staged takes, four `hard-a` and four `hard-b`, alternating,
  at most two a day, so the held-out sessions fall on at least 2 days. Each
  split then gets 4 sessions and about 40 episodes per band. N2's sample gate
  needs at least 30 episodes per band from at least 3 sessions over at least 2
  days. With the scripts alternating, seven takes is the fewest that gives 3
  held-out sessions under the split rule.
- If the counter is not in the camera's view, the mail, wiping and phone
  cases move to another in-view spot away from the desk. The owner names it
  when okaying the cue text, from the commissioning take's contact sheet.
- Lights down: the owner dims the den lights by hand, the usual way. If they
  come back up, the owner marks `low_light` only where the room is dim.

## The labelling passes

### Order, per session

1. `label.py sessions` registers new retained staged takes and gives each its
   split, before any pass.
2. The surface map, once for all sessions.
3. The fixture pass (boxes), then its audit.
4. The label pass. It opens only after that session's audit is complete. Once
   it starts, that session's boxes are locked, except for label.py's own
   cut-driven drops (see "A second person R's check missed").
5. The re-mark of one session, at least 3 days after its label pass.
6. Freeze the fixture, freeze the labels, and commit the two lines. Only then
   may N2 run.

### The surface map (once, about 15 minutes)

- `label.py surfaces` opens the page on any real frame. A commissioning-take
  frame is fine.
- The owner steps to a clear view and clicks the corners of each surface in
  view. They pick its type and type its height in cm, measured once with a
  tape. Lap and handheld start with default heights the owner can change.

### Fixture pass: boxes (about 20 to 30 minutes per staged take)

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

### A second person R's check missed

- If the owner sees a second person in any pass, they end the pass with the
  page's second-person key. `label.py` prints the union of the second-person
  spans typed in every earlier `check-N.json` of that take, as `mm:ss-mm:ss`
  spans (the typed spans merged, without the pad), or `(no earlier spans)`. It
  never prints the raw word `none`.
- The owner re-runs check.py on the take. At its prompt they type those spans
  plus the new ones. If there were none, they type only the new spans.
  check.py's span parser takes a bare `none` but not `none` next to a span.
- **The union test.** check.py never deletes a `check-N.json`, and the newest
  one counts, so a check label.py refuses stays the newest. label.py therefore
  compares a newer check with every earlier one, not only the one before it.
  It accepts a newer check only if it says PASS and its `second_person` cuts
  cover the union of the `second_person` cuts in every earlier `check-N.json`
  of that take. So frames only ever enter cuts.
- **Blocked.** While the newest check fails this test, the session is blocked.
  label.py refuses its passes, saves and freeze, and prints the union again,
  until a newer check covers it. `status` shows the session as blocked, with
  no spans.
- **When a newer check is accepted**, label.py:
  - clips every interval and span in that session's drafts (the re-mark's
    too) to the new usable segments. It drops the parts inside cuts and splits
    a straddling interval into one piece per remaining segment, each cut-side
    end set to `carried`. Spans split the same way. It drops empty pieces,
    and any interval piece too short to keep its onset before its offset.
    Ends are frames. For example, take an interval from the frame at about
    65 s to the frame at about 75 s, and a new cut [69, 71). The cut is
    half-open in `t_s`, as check.py's `usable_segments` makes it, so the
    segment before it holds frames with `t_s` < 69 and the one after it
    starts at the first frame with `t_s` >= 71. The interval becomes two
    pieces. The first keeps its onset, and its offset (`carried`) is the
    earlier segment's `last_n`, the last frame with `t_s` < 69. The second
    piece's onset (`carried`) is the next segment's `first_n`, and it keeps
    its offset;
  - drops the boxes inside the new cuts: cut frames get no fixture row. The
    keyframes stay as they are in `work\`, so no box outside a cut changes and
    the audit still holds. These cut-driven drops are the one change to boxes
    allowed after the label pass starts;
  - prints each change, and clears the session's `end_to_end` mark so the
    owner looks again and re-confirms it;
  - rewrites `check_sha256` (and the check file name) in that session's
    drafts to the accepted check's, so both manifests the next freeze writes
    name it. A frozen folder is never edited, so after a freeze this means a
    new fixture version and a new labels version. `freeze` refuses any draft
    or session still tied to a superseded check.

### The protocol (PROTOCOL-V1, the gate)

It is committed before the first label, and the owner okays it first.

1. **Engagement.** The person works with an object or surface they need to
   see. An interval runs from onset to offset.
2. **Onset (contact-starting).** The first frame showing the hand touching the
   worked object (`touch`). If a task starts with no touch, for example
   reading a page left on the desk, the onset is the first frame the person is
   settled facing it at working distance (`no_touch`).
3. **Offset.** The last frame of contact before contact with that object stops
   for more than 6 s (`release`). For a `no_touch` task, it is the frame the
   person turns away or leaves working distance. An interval never crosses a
   cut or a segment edge. One running at a segment edge stops there, and
   label.py marks that end `carried`.
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
     things without reading them, and by default using a phone (its screen
     lights itself).
   - `none`: every usable second outside intervals and uncertain spans.

   The phone's band is the one setting the okay chooses (`casual` or `none`,
   see approval 1). It sits in the protocol's fenced block. Under `none`, phone
   use is not an interval: it is a quiet span with case `phone_at_counter`.

   On N2's `visual_demand` scale the cut points are the seed demand gates:
   fine at 0.6 or more, casual from 0.4 to under 0.6, none under 0.4. The owner
   labels bands, never numbers. If demand clearly changes within one task, set
   `sure.band` false.
8. **Cases.** Each interval has one case: `sorting_mail`, `still_reader`,
   `wiping`, `phone_at_counter` (under the `casual` phone band), `tidying`, or
   `other`. The first four are the shortcut cases, where a cheap cue (motion,
   posture or place) points the wrong way. Quiet spans have `idle_seated` or
   `idle_standing`, and under the `none` phone band `phone_at_counter`, which
   stays a shortcut case as a no-need span: a person is present and not
   engaged, and the right answer is no engagement.
   `low_light` spans mark time with the room dim. A quiet second tests "no
   engagement" only if it is more than τ_off (60 s, A13's late-off band) after
   the latest labelled offset before it. Earlier quiet seconds are labelled
   the same way, and N2 does not score them as quiet.
9. **Uncertainty.** Never guess. If unsure of a boundary or a band, set that
   `sure` flag to false; it is left out of that measure only. If unsure
   whether the person is engaged at all, mark an `uncertain` span. It is left
   out of all scoring and counted in the manifest's `uncertain_s`.
10. **Who labels.** The owner, alone. Every item records `by: "owner"`, and
    every file records its pass and times. Results are reported as
    single-labeller results with a delayed self re-mark.
11. **Blind.** Labels are made and frozen before any estimator output exists
    for those sessions. The label page shows only frames: no boxes, no cue
    times, no estimator output. N2 may open its output only after the labels
    freeze line is on `origin`.
12. **Split and re-mark.** The rules in those two sections, fixed now.

### Label pass (about 25 to 35 minutes per staged take)

- `label.py labels <take>` opens the page in label mode. It shows the usable
  frames only, with no boxes.
- Play at 1x to 4x. At each engagement:
  - step frame by frame to the first touch and press the onset key;
  - pick the band and the case, one key each;
  - step to the last contact and press the offset key.
- Quiet, low-light and uncertain spans get a start key and an end key. They
  need not be frame-exact.
- When done, the owner marks the session labelled end to end. `freeze` refuses
  a session without that mark, as it refuses an unaudited one. An accepted
  newer check clears the mark (see "A second person R's check missed").
- The server checks every save: each interval inside one usable segment, onset
  before offset, no overlaps, bands and cases from the fixed lists.

### Re-mark (once, one session, about 30 minutes)

- The re-mark is the second development session in recording order (normally
  the first `hard-b`). The owner re-marks it from scratch in label mode at
  least 3 days after its label pass, with the first labels hidden. `label.py
  remark` refuses before 3 days.
- n1.py reports interval matches (overlap of at least half), and band and case
  agreement. It reports onset and offset differences (median and p90) only
  over ends that both passes marked sure and placed by hand: onsets `touch` or
  `no_touch`, offsets `release` or `no_touch`. Carried ends are left out of
  the differences and counted separately.
- The first-pass labels stay the gold. The re-mark only reports consistency.

## Split

- By whole session. It is assigned by rule when a session is registered,
  before any pass and long before any tuning.
- **Rule:** within each staged script name (`hard-a`, `hard-b`), sessions
  alternate development, held-out, development, held-out, in recording order
  (`take.json`'s `started_unix`). The first of each script is development.
- Why per script: the owner alternates `hard-a` and `hard-b`, so one
  alternation over all takes would put every `hard-a` in one split.
- `split.json` is append-only. A session's split never changes.
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

- **Sessions.** Only the newest check counts. FAIL, commissioning and free
  takes are not registered. A later PASS check makes a session retained.
- **No cue data.** The `take.json` reader returns only `script` and
  `started_unix`. A `take.json` from `take.run` with R's `FakeCamera` and a
  staged script feeds this reader, so a rename in take.py fails a test. Until
  the `take.py` change lands (approval 3), that test runs `take.run` with
  `free`, and den-label's own test helpers build any staged-script fixture
  (a staged `script` name and a `started_unix`) without touching
  `tools/den-take/`.
- **Split.** Per-script alternation in recording order. Stable when sessions
  are added. Never reassigns. Refuses an edited `split.json`.
- **Second person: the union test.** With 60 s of activity, check-1 typed
  `00:40-00:45`. A check-2 typed `01:10` is refused and blocks the session:
  passes, saves and freeze are refused, and `status` shows it blocked. A
  check-3 typed `01:10, 01:30` is refused too: it covers check-2 but not
  check-1, and label.py printed `00:40-00:45, 01:10-01:10` as the union. A
  check-4 typed `00:40-00:45, 01:10, 01:30` is accepted and unblocks it.
- **Second person: no earlier spans.** With check-1 typed `none`, label.py
  prints `(no earlier spans)`, never `none`, and a check-2 typed `01:10` is
  accepted.
- **Second person: clipping.** An interval from the frame at about 65 s to the
  frame at about 75 s, labelled under a check without a cut there, is split
  when a check with a new [69, 71) cut (typed `01:10`) is accepted. The first
  piece keeps its onset and ends at the earlier segment's `last_n`, the last
  frame with `t_s` < 69 (offset `carried`). The second starts at the next
  segment's `first_n`, the first frame with `t_s` >= 71 (onset `carried`),
  and keeps its offset. No piece holds a frame with 69 <= `t_s` < 71. A span
  is split the same way, and a piece too short to keep an onset before its
  offset is dropped. Each change is printed. Boxes outside the cut are
  unchanged, and no fixture row is written for a cut frame. The `end_to_end`
  mark is cleared. Every draft of the session then names the accepted check's
  file and sha256, and `freeze` refuses a draft still naming a superseded
  check.
- **Second person: the page key.** In each pass, the page's second-person key
  ends the pass: the server saves the draft, stops serving frames, and
  label.py prints the union of the earlier cuts as typed spans (for example
  `00:40-00:45`), or `(no earlier spans)` when every earlier check typed
  `none`.
- **Frame server.** Serves only usable-segment frames of the named take
  (slate and cut frames get 404). Returns raw NV12 of 345,600 bytes. Binds
  127.0.0.1 only. Refuses a wrong token or Host header. Sends no CORS header.
  Refuses paths outside the take.
- **Boxes.** Interpolation is linear in `t_s`. Absent runs stay empty. The pad
  and the clip are exact. The pad can only grow.
- **Fixture.** Exactly one row per usable frame. One id. Only whitelisted
  fields, so no hand, contact or region field can enter. Refuses an unaudited
  session. Refuses box edits once the label pass has started; label.py's own
  cut-driven drops after an accepted newer check are the one exception.
- **Labels.** Every protocol check: bounds, order, overlaps, segment edges and
  `carried`, fixed lists, the 6 s pause rule (never applied across a cut).
  `by` on every item. Refuses a session not marked labelled end to end.
- **Label mode sends no boxes.** Its endpoints carry no box data.
- **Re-mark.** Picks the second development session. Refuses before 3 days.
  Hides the first labels. `remark.json` names the session and its check
  sha256. Agreement figures are right on hand-made cases. A matched interval
  with a `carried` end contributes no difference for that end and is counted
  as carried; an end either pass marked unsure contributes none either.
- **Freeze.** Canonical bytes give fixed hashes. Frozen folders refuse writes
  and overwrites. Versions go up. `verify` catches one changed byte. Drafts
  made under another protocol sha256 are refused. The two hashes are
  independent: changing a label leaves the fixture hash alone, and the
  reverse.
- **Status.** Counts per split and band, quiet spans per case with their
  seconds past τ_off, and days per split. No times.
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

1. **[go-ahead] Okay PROTOCOL-V1** before the first label and before any
   staged take (about 10 minutes), after the look at the commissioning take's
   contact sheet. This is the gate's "fixed before labelling". It covers:
   - the definitions and the starting pad;
   - **the phone-is-casual default.** It differs from the architecture: A13
     scores "on a phone at the counter" as no need, and A7's example gives a
     self-lit screen a visual demand of 0.2, under the 0.4 casual cut. If the
     owner picks `none`, `phone_at_counter` becomes a no-need shortcut case,
     a span case like quiet. Casual is then wiping only: about 5 per take and
     about 20 per split, under the 30-per-band gate. The remedy is one more
     casual block per round (`tidying`, see "Session staging"), settled at
     this same okay before any take, not more takes. `label.py status`
     already shows counts per split and band, so it gets no band flag;
   - the spoken cue text, and the spot for mail sorting, wiping and the
     phone: the counter if the contact sheet shows it in view, else another
     in-view surface away from the desk that the owner names.
2. **[go-ahead] The staged batch:** "8 staged takes, `hard-a` and `hard-b`,
   four each". R asks for a go-ahead per take or per named batch, because a
   camera fault during polling could briefly drop Frigate's den feed, which
   the den lights follow.
3. **[go-ahead] Merging the `tools/den-take/` change**: take.py's `--script`,
   the two script files, and `make_take`'s parameters and the staged-script
   tests. It is built after R lands on `main`, so the two sessions never edit
   `take.py` at once. It changes `take.py`'s hash, not CHECK-V1 or check.py.
4. No go-ahead needed: running `label.py` (PC only, 127.0.0.1, no camera),
   the practice run, the surface map, the passes, re-running check.py for a
   missed second person, the re-mark, the freezes.
5. An owner action, not a go-ahead: paste the two freeze lines to an agent.

## Risks

| Risk | Effect | What N1 does |
|---|---|---|
| Owner time | About 11.5 to 14.5 hours in all | Short staged takes, a keyboard page, autosave, stop any time. Label the first two takes fully before recording the rest. Stop rule: if boxes and audit on the first `hard-a` and `hard-b` each take more than about 40 min (planned 20 to 30), simplify the box workflow before recording the other six, for example by holding the box until the next keyframe instead of straight lines, or by folding the audit into the drawing pass. |
| Small or hidden contacts (phone at the counter, hands behind the body) at 640x360 | Unsure onsets | `sure` flags and uncertain spans, counted and reported. Never guessed. |
| About 4 frames a second | Onsets quantised to 0.2 to 0.27 s | Stated in the protocol. Small against the 3 s target. |
| A box that encodes the onset | The fixture leaks the answer | Body-only tight box, fixed pad, no activity keyframes, field whitelist, boxes locked before labels. |
| Staged behaviour is not natural | Scores flatter real use | Two orders. Free sessions can be added later as a new labels version if N2 needs them. |
| Script timing | An estimator could learn time from "Begin" | Cue times never enter N1. Two scripts with different orders. |
| Too few episodes after uncertainty | N2 says insufficient | `status` shows counts per split and band. Record more under the same rule. |
| One labeller | No inter-labeller figure | Reported as single-labeller, with the self re-mark. |
| R's format changes before its first real take | The tool breaks | N1 reads only the fields listed here. Tests use R's own helpers, and one reads a `take.json` written by `take.run`. |
| Dimming mid-take | A few seconds of exposure change | The `low_light` span starts once the room is dim. |
| Counter not in view | The mail, wiping and phone cases are lost | Moved to another in-view spot away from the desk, named by the owner at approval 1 from the commissioning contact sheet. |
| Desk view: a posed check on 2026-11-02 found that with the chair pulled in, the hands and a book on the desk or held low are below the frame | Desk-top contacts unseen, onsets unlabellable | Desk cues use the posture found in view (chair pushed back, book at chest height). Mail and wiping move to the counter or the named in-view spot. The commissioning take's 3 minutes and one contact-sheet look check it before any staged take. No camera move. |
| Phone band set to `none` at approval 1 | Casual falls to about 20 per split, under the gate | One `tidying` block per round replaces the phone block, and the phone moves past τ_off in rounds 2 and 4. Settled at the okay, before any take. |
| Quiet blocks too close to tasks | No quiet second outside τ_off; onsets not from none | At least 30 s with no task before every task, and one 90 s quiet block per round whose tail is scored. The τ_off rule is in PROTOCOL-V1. |
| Disk: C: is 97% full | 8 staged takes of about 27 minutes need about 45 to 85 GB | R's 35 GB floor refuses a take first. N1's own files are small. |
| N1 output only on the PC | Lost with the disk | README suggests a copy to another household computer or a USB drive after each freeze. |

## Owner time per take

| Step | Staged take (about 27 min) |
|---|---|
| Record (R) | 27 min |
| R's check and second-person scan (about 1,600 thumbnails) | 5 to 6 min |
| Boxes and audit | 20 to 30 min |
| Labels | 25 to 35 min |
| **Total** | **about 77 to 98 min (1.3 to 1.6 hours)** |

One-time, about 80 min: the in-view look at the commissioning take's contact
sheet (5 min), read and okay the protocol (10 min), the practice run of the
page on the commissioning take (15 min), the surface map (15 min), the re-mark
of one session (30 min), and the freezes (5 min).

All of N1: 8 staged takes at about 77 to 98 min each is 10.3 to 13.1 hours.
With the one-time 80 min, that is about 11.5 to 14.5 hours, over about 2 to 3
weeks of evenings. A `none` phone band adds about 2 minutes per take.

## Effort

| Piece | Size | Agent time |
|---|---|---|
| PROTOCOL-V1.md | about 150 lines | 0.5 day |
| `take.py` staged scripts, two script files, `make_take` parameters, tests | about 160 lines plus 180 test lines | 0.5 day |
| `n1.py` (adds the protocol block, the phone band, check names in the manifests, frame-exact clipping) | about 600 lines | 1.25 days |
| `label.py` (adds the second-person key's stop and the union print, quiet past τ_off in `status`) | about 450 lines | 1 day |
| `page.html` | about 550 lines | 1.5 days |
| Tests and the browser smoke test (adds the page-key, phone-band and frame-exact clipping tests) | about 1,200 lines | 1.5 days |
| README, FREEZES.md, release note | about 150 lines | 0.25 day |
| **Total** | **about 3,440 lines** | **about 6.5 agent-days, in about 3 sessions** |

That is about 1.3 times R's size (about 2,700 lines).

## What can be built before R's first real take, and what waits for it

Before R's first real take (no camera, no real frames):

- PROTOCOL-V1.md, committed first. The owner's okay comes after the
  commissioning take's contact-sheet look (below), and before any staged take.
- `n1.py`, `label.py`, `page.html`, and all tests, on R's synthetic takes.
- The staged scripts and the `take.py` change, tested with R's fake camera.
  This waits only for R's branch to land on `main`, not for a real take.
- The README owner steps.
- A full synthetic dry run in the cloud: register, boxes, labels, re-mark,
  freeze, verify.

Waits for R's first real take (the commissioning take, once it passes):

- The commissioning take itself, with its 3 unscripted minutes spent as
  "Session staging" says: seated at the desk pushed back with the mail and
  the book, wiping the counter, and the phone at the counter.
- One look at its contact sheet: the check that the counter is in view, and
  next to it the in-view check for the desk posture (chair pushed back, book
  at chest height) and for the surface used for mail and wiping. The owner
  names the spot at approval 1.
- A practice run of the page on the commissioning take (about 15 min of owner
  time, counted in the one-time owner time). Nothing from it is ever frozen,
  because it is not retained. This also checks page speed on real bundles.
- The surface map, which needs a real frame.
- A first look at the pad on real frames.

Waits for retained staged takes (R's commissioning PASS, then go-ahead 2):

- The fixture and label passes themselves.
- The real owner-time numbers and the stop rule (under Risks), after the first
  `hard-a` and `hard-b` are fully labelled. Script changes after that become
  new script names, which alternate on their own.
- The re-mark (3 days after its label pass) and the two freezes.

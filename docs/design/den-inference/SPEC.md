# Den live inference in Home: spec

How Home shows what the den's perception and Qwen believe in real time, and what the light does about it. It's
built for the owner's live view on a phone and a laptop, in both themes. It is drawn only from the streams Home
already receives, plus a short list of fields den-live would need to add (below).

**Recommendation:** direction A, the **live caption**, as the everyday view. It takes three things from the other
directions: the **signal strip** (B) as a drawer in the status chip, the **because line and refusal tags** (C) in
the tap card, and the **two-minute strip** (D) in the tap card. The prototypes, the design check and the reasoning
are in [`prototypes/`](prototypes/) and the den program's design note.

## 1. What Home receives, and what this design asks for

| Stream | Today (den-live `6f9a7410`, Home `178a6df5`) | Proposed, read when present |
|---|---|---|
| `den-live-people-1`, about 3 a second | `tracks[]`: `track_id`, `state`, `xy_m`, `sigma_m`, `heading_deg` (null when not fresh), `near`, `service_run_id`, `camera_epoch`, `association_revision`. `activity {word, close_up, at}` on the track Qwen answered for. `health`: `timings_ms` p50/p95 per stage, `fps_in`, `lag_s_median`, `transit_s_median`, `dropped_per_min`, `qwen {state, why, last_reply_at, reply_s_median, asks_per_min}`. `mode`, `camera {status, why}`, `perception_status`. | `activity.valid_for_s` (asked for on 2026-11-13). `activity.facts {held_object, book_state, gaze_target, subject_posture}` and `activity.basis` (`visible_basis`), from the den-live-scene-2 reply. `activity.capture_ts` (the newest still) and `activity.request_id`. `track.seated` and `track.posture` (contract 2, planned). `track.identity {kind, label, score, at, source}` (face recognition, being chosen). `health.qwen.last_outcome {kind, at}` and `health.qwen.inflight_since`. |
| `den-virtual-spots-1`, 10 Hz | Per fixture `on`, `level`, `cct_k`, `pan_deg`, `tilt_deg`, `target_m`, `reason`, `motion_phase`. Top level `subject {track_id, camera_epoch, association_revision, request_id}`, `stopped`, `valid_until_mono`. | Nothing. |
| Coordinator state, polled every 2 s | `decision {decision_id, subject, proposal {kind, fixture, refused, target_basis}}`, `settings`. | Nothing. |
| `den-live-body-2` | The measured body, behind `HOME_WEB_DEN_BODY_V2` (off). | Nothing. |

The facts carry **no confidence**, and none is invented. Qwen's `visible_basis` stands in for one: it's what the
model says it saw ("book in hands · pages open · head down").

## 2. Components

### 2.1 Live caption (one per tracked person)

Where: beside the person, on the room canvas. It anchors to the measured body's shoulders when the body layer holds
this person (matched by track, or within 0.6 m), else to the floor marker. A hairline leader runs from the anchor to the
caption.

| Line | Shows | Detail step |
|---|---|---|
| Name | `Sam` (household, strong match), `Sam?` with a dashed underline (weak), `Guest` (a face that matched nobody), `Someone` (no face, or no identity field). Never a face image. | Always |
| Provenance | `face 0.93 · 12 s ago`, `face seen · not household`, `no face seen yet` | Close |
| Qwen's word | Life ring, word, age (`Reading` ◔ `3 s`). With no current answer: what Qwen is doing (see states). A person Qwen isn't asked about (not seated) gets their posture instead and no ring. | Always |
| Object | A solid accent pill: `BOOK`, `PHONE`, `CUP`, `CRAFT` | Room and close (always on a phone) |
| Qwen saw | `Qwen saw book in hands · pages open · head down` | Close |
| House | `Spot 3 · reading light 70% · 2700 K`; `Spot 3 aimed, dark`; `Stop is holding the spots` | Room and close (always on a phone) |

Detail steps come from the camera's distance to the anchor (`zoomRatio`, the System labels' 0.35 and 0.8 cut-offs).
On a narrow view (under 520 px) the caption uses its compact sizes.

### 2.2 Life ring

A 16 px ring before Qwen's word. It's full when an answer arrives and drains linearly to empty at its expiry: 10 s
after the answer, or sooner if `valid_for_s` says so. When it's empty, the word goes in the same frame. There's no
number to over-read, and no debounce on expiry.

### 2.3 Status chip (exists)

It stays in the top-left corner: `Den · live`, `1 person · 0.57 s behind`, and the banners Home already has
(`Stream stopped`, `Too dark`, camera problems). With the caption on, its Qwen line moves into the caption.
**Graft from B:** tapping the chip opens the **signal strip**: Camera → Detector → Tracker → Qwen → Coordinator →
Beam. It shows den-live's own p50/p95 for each stage, a tick per real arrival, and the still → light ruler against
the 2 s goal. It's a drawer, not the main view.

### 2.4 Tap card (glass)

Tapping a caption opens it (on a phone, a bottom sheet). The rows are:
- **Doing:** the answer's age and how long it stays good, with a bar; the still's age.
- **Holding** and **looking at.**
- **Qwen saw.**
- **The light (graft from C):** the "because" sentence, built only from bound data, and each refused spot with its
  reason.
- **The track:** its id, where it is (±σ), its facing, and its epoch and revision.
- **The last two minutes (graft from D):** lanes for presence, Qwen's answers with their expiry, the light's level,
  and seeing. Tap a moment to read it.

### 2.5 Spot tags and refusal rings (from C)

These appear in the Lighting view, or while the card is open. Each spot gets a tag at its can:
- **Lit:** level and CCT.
- **Pre-aim:** `aimed, dark, ready`.
- **Refused:** the reason in `--apt-blocked`, with a dashed ring at the can.

A tether line runs from the lit spot to its subject's ring.

## 3. States

| State | Caption | Chip / elsewhere |
|---|---|---|
| Connecting | none | `Connecting…` |
| Answer fresh | word in ice, ring full, `0 s` | |
| Answer ageing | ring draining, `6 s` | |
| Answer expired | word gone at once; the line says what Qwen is doing | |
| Asking (`inflight_since`) | `Asking Qwen…`, a dot orbits the empty ring | |
| Gate open, nothing in flight | `Waiting for Qwen · replied 3 s ago` | |
| Two people in view (`last_outcome` unbound) | `Can't tell whose answer: two people in view`, for up to 10 s | |
| Qwen unavailable | `Qwen unavailable · Qwen isn't answering` | |
| Qwen off | `Qwen off · no one seated` (den-live's `why`) | |
| Not seated / walking | `Standing`, `Walking`, or `Not seated`; no ring | |
| Leaving (coasting) | caption at 60%, dashed ring | |
| Identity weak / guest / unknown | `Sam?` / `Guest` / `Someone` | |
| Light lit / pre-aim / fading / stopped | warm house line / grey `aimed, dark` / `fading out` / `Stop is holding the spots` | |
| Spot snapshot stale (past `valid_until_mono`) | house line gone | spot tags gone |
| Stream stopped (3 s) | captions desaturate, line `Stream stopped`, then fade with the markers | `Stream stopped` |
| Too dark | captions and markers go (no tracks) | `Too dark to see · infrared is not on yet` |
| New camera epoch | the caption restarts as `Someone` until identity binds again; the house line drops until the spot's subject names the new track | |

## 4. Tokens

The canvas stays dark in both themes, so the caption uses Home's dark values, scoped to its layer as
`--den-cap-*`. Panels around the canvas (the card on a phone, the strip, the timeline) follow the theme through
`--hg-*` and `--apt-*`.

| Token | Value | Use |
|---|---|---|
| `--den-cap-fg` | `#ffffff` (`--hg-fg-0` dark) | name |
| `--den-cap-fg-1` / `-2` / `-3` | `#ededed` / `#a1a1a1` / `#8f8f8f` | text, status line, mono detail |
| `--den-cap-ice` | `#b8d8ff` (`--hg-ice` dark) | Qwen's word, life ring |
| `--den-cap-accent` | `#4d9cff` (`--hg-blue` dark) | held-object pill, black mono text |
| `--den-cap-warm` | `#ffddb0` (`--apt-warm` dark) | house line |
| `--apt-blocked`, `--apt-attn`, `--apt-ok` | Home's | refusals, attention, live |

Type: Geist 600 20 px name (17 compact), Geist 500 15 px word (14 compact), Geist Mono 11.5 px detail, Geist 500
13.5 px house line. No text under 11.5 px. A soft dark radial scrim, not a card, sits behind each caption.

## 5. Motion

- **Position:** follows the anchor each frame through a 0.24 s exponential smoother. The caption keeps its side until
  the anchor passes 42% or 66% of the width, and stays 8 px inside the view.
- **New answer:** the word flashes white with an ice glow (0.9 s), and a soft pulse runs down the leader from the
  person to the caption (0.7 s). This fires only when a new answer arrives (`activity.at` changes).
- **Ring:** drains linearly. It's set as `stroke-dasharray`, and only written when it changes by 0.1%.
- **Arrive and leave:** the caption fades in and out over about 0.33 s, following the marker's own fade.
- **Reduced motion:** no flash, no pulse, no smoothing (jumps), and the ring is still drawn.

## 6. Bindings and rules (implemented in `app/src/home-den-inference-model.mjs`)

- **Age** = event `emitted_ts` − `activity.at` + seconds since this page got the event, which is den-live's clock
  plus local elapsed time. The device clock never matters.
- **Shown while:** `age < 10 s` and `seconds since the event < valid_for_s` (when sent). Home never extends an
  expiry.
- **Qwen is asked about:** `track.seated` when sent. Otherwise, the track with an answer, or the only track.
- **Name:** `identity.kind === 'household'` and `score ≥ IDENTITY_STRONG`. The 0.8 is a placeholder until the face
  model's threshold is chosen.
- **House line:** only when the applied spot snapshot's `subject.track_id`, `camera_epoch` and
  `association_revision` all match this track. A lit fixture wins, then one aimed by `pre_aim` or `task`.
- **Fixture names:** come from the virtual fixtures' readback labels.
- **Text:** every string goes in with `textContent`.

## 7. Performance

- The DOM text changes only when its value does, at most at the event rate (about 3 a second) plus once a second for
  ages.
- Each frame does one projection and one transform per caption, and draws the leaders on a single 2D canvas.
- The spot snapshot (10 Hz) only swaps a reference.
- Nothing allocates per frame beyond the leader gradient during a pulse.
- **Known cost:** each caption reads its own size every frame after a text write. That's fine for the den's one or
  two people. Batch the reads if it ever labels a crowd.

## 8. Privacy

- Faces are never shown. Identity is a name tag; the provenance line says `face 0.93`, never a crop.
- No frames, positions or identities leave the house. The prototypes are synthetic: an invented room and a
  placeholder name.
- Names show to everyone who opens the live view (the owner's decision of 2026-11-11). The design has no per-device
  differences.

## 9. What the prototype branch has

`claude/den-inference-view`, cut from the Home candidate `178a6df5`, not pushed or deployed:
- `app/src/home-den-inference-model.mjs`: the rules, pure, with `tools/test-den-inference-model.mjs`.
- `app/src/home-den-inference-caption.mjs`: the caption layer (2.1, 2.2 and the motion above).
- `app/src/home-den-live-view.mjs`: mounts the caption when `HomeDenLive.caption`. It feeds it the people events and
  the applied spot snapshot, and hands the chip's Qwen line and the body's labels over to it.
- `web-gateway`: `HOME_WEB_DEN_CAPTION` adds `caption: true` to `HomeDenLive`. It's off by default.
- `tools/test-den-live-serve-browser.mjs`: the caption's real-browser case on a desktop and a phone, with the
  proposed fields on a synthetic stream.

Not in the branch yet: the tap card (2.4), the signal strip drawer (2.3), the spot tags (2.5), and the asking
orbit.

# V-JEPA 2.1 den tuning plan (AL-T, #327)

October 19, 2026. Offline-first. V-JEPA stays an optional JEV input and does
not block the MVP ([MVP-SPEC](MVP-SPEC-20261019.md), [TICKETS](TICKETS.md)).
The host steps are in
[VJEPA-TUNING-HOST-RUN-PROPOSAL.md](VJEPA-TUNING-HOST-RUN-PROPOSAL.md). None
of them has run.

## 1. Audit: what exists today

**Where the code lives**
- **Training and eval code:** on LA in `~/vjepa-probe-0` (commit
  `67853d2f`), which is not in this repo. A partial code-only copy is in
  `.tmp/observer-gating/src/probe0/`. It has `home_v2/models.py`, `data.py`
  and `evaluation.py`, but not `tools/v3_finetune.py`,
  `tools/v4_home_runtime.py` or `pooling_common.py`.
- **Serving:** `tools/la-vjepa-worker/` (`vjepa_worker.py`, `vjepa_pins.py`),
  which runs as `lab-vjepa-lifecycle` / `lab-vjepa-worker` on LA.
- **Victoria side:**
  - `stack/services/perception-lab/vjepa_live.py` (the clip pump);
  - `vjepa_scene.py` (validation, buckets, the JEV projection);
  - `jev_wire.py` (the `vjepa` block);
  - `bakeoff_vjepa.py` (arm C, rule-based, no fit).

**How the probes were trained**
- **Encoder:** frozen V-JEPA 2.1 ViT-B/16 (distilled from ViT-G), 384 px, EMA
  encoder. Eight frames give a 4 x 24 x 24 = 2304-token grid of 768 dims.
- **Heads:** `probe0.home_v2.models.Readout`. The kinds are `linear` (mean of
  pooled tokens), `lightweight` (one cross-attention query) and `attentive`
  (three transformer blocks, then a query, then an MLP; millions of
  parameters).
  - The pinned heads (`runs/home-v2/expanded-optimization-20260908/fits/*`)
    are a three-seed ensemble per task.
  - Their architecture is in each checkpoint's `architecture` field, which
    nobody has read yet (step H0 reads it).
- **Data:** posture and activity were trained on the LA **household**
  manifest:
  - six original recordings, human-verified clip labels;
  - 64-frame clips, cached at 8 frames over the whole clip span;
  - crop producer `household-track-union-pad25/v1`;
  - **activity:** multi-label sigmoid over 11 classes (`mixed_standard`);
  - **posture:** softmax over 6 classes.
- **Zero-shot readout:** the 23-class NTU head
  (`/srv/data/probe-0/v3/finetune-unfreeze-0.pt`, frozen encoder, epoch 13)
  gives the three den family sums: typing → computer_work, phone →
  phone_use, reading/writing → reading.
- **No head was trained on den data.**

**Train/serve mismatch.** The manifest itself says
`production_equivalent: false`. Training clips span a whole 64-frame clip
and use pad-25 track crops. Serving uses:
- 8 den previews at about 2 Hz, nearest-slot to a 0.5 s grid over 3.5 s;
- crop `tracked-union-box-square-1.5`;
- 640 x 360 JPEG input.

**Class balance and label quality**
- **LA household:** six recordings. Reading and phone are rare, and
  `on_computer` has effectively no training support ("computer untrained").
- **Den labels:** about 18 to 21 minutes over five sessions. One person, one
  desk chair, a lit room.
  - Owner-reviewed gold is **59 s, all computer_work**.
  - The rest is:
    - whole-session `owner_statement` (unreviewed);
    - bookmarks;
    - `qwen_proposed` intervals. These are circular: JEV consumed Qwen.
  - Every annotation actor is the gateway token, so the evaluation plan's
    gold rule gives 0 s.
  - Per class (doc estimate):

    | Class | Labelled time | Sessions |
    | --- | --- | --- |
    | reading | 97 s | 2 |
    | phone_use | 82 s | 2 |
    | computer_work | about 886 s | 5 |
    | resting | 46 s | 1 |
    | transition | 40 s or less | 1 |
- **There is no sofa data at all**, and sofa reading is the MVP behaviour.
- **Den V-JEPA observations:** only 78 clips in total: `30b6f5e5` (2),
  `006752dc` (23) and `25220ef6` (53). The ledger stores probabilities, never
  embeddings.

**Window length and stride**
- **Training:** 8 frames over the original clip span.
- **Serving:** 8 frames over 3.5 s. One clip is requested every 2 s (40 per
  minute cap), so consecutive clips overlap by 1.5 s.
- No temporal aggregation runs beyond the 60 s bucket summary in
  `vjepa_scene.summarize`.

**Frozen or attentive.** The encoder is always frozen. The heads are small
readouts of one of the three kinds above; which kind the pinned fits use is
unverified. The NTU head is a frozen-encoder `Head`.

**Eval protocol**
- **LA:** grouped CV. `home_v2.data.grouped_folds` defaults to 5 outer folds
  grouped by `video|track`, with a 2 s purge. The receipt calls this
  leave-one-recording-out; with `mode=track`, other tracks from the same
  recording can still train the fold.
  - Thresholds use inner out-of-fold precision ≥ 0.90 and are marked
    `enabled: false`.
  - Results: posture acc 0.81 / balanced 0.62, reading F1 0.40–0.56,
    phone about 0.05, computer untrained.
- **Den:** `lab_eval.py` has macro-F1, Wilson bounds, ECE/Brier and
  risk-coverage, but there is no frozen split and no held-out session.
  V-JEPA has **never been scored on the den**.
  - The Victoria buckets (`READING_SIGNAL` 0.6 / 0.3 and
    `ZERO_SHOT_SIGNAL` 0.5 / 0.2) were picked before any den replay.

**Why reading F1 is 0.40–0.56.** The causes, most important first:
1. **Too little and too narrow data.** Six recordings, few reading clips,
   and the range is fold and seed variance on a tiny positive class.
2. **Reading is low-motion and object-defined.** The book is a small part of
   a 1.5x person-union crop. V-JEPA's strength is motion, and mean pooling
   dilutes small regions; the literature puts the pooling cost at 16–17
   points on K400.
3. **Single 3.5 s clip, no temporal context.** Multi-clip context adds about
   7 points in the V-JEPA paper.
4. **Multi-label sigmoid over 11 mostly irrelevant classes.** No class
   weighting toward reading, and the threshold is uncalibrated.
5. **The mismatch above, for den use:** crop, span and frame rate. Plus a new
   camera: 0.5–0.7 m away, face half out of frame.
6. **The LA metric is optimistic** if `mode=track` folds share recordings.

**What exists for computer_work**
- **Den:** about 886 s of labels. Most of it is unreviewed whole-session
  owner statements; 59 s is owner-reviewed.
- **HASS.Agent PC input recency** (`derived.computer_input_recency`) is
  recorded at 1 Hz in every session. It is a **camera-independent** weak
  label.
- **Public:** NTU `typing`, through the zero-shot head. JEV scored
  computer_work 0.07/0.00 before `desk-seat-1`, for geometry reasons.
- **LA:** the video-labeler (23 classes) and Intelligence (13 classes, mostly
  Qwen-labelled) stores are on the LA host, with sizes unknown.
- **Nothing local:** no ledger copy, clips or embeddings exist on this
  workstation. A read-only search found only test fixtures.

## 2. Ranked plan

Expected gains are educated estimates, not measurements. Each one has to
show up in leave-one-session-out den F1 before it is adopted. The rank
reflects MVP value (sofa reading) per unit of cost.

| # | Lever | What | Expected gain | Cost |
| --- | --- | --- | --- | --- |
| 1 | **Den data: guided sofa sessions plus weak labels** | The owner records three days x about 25 min, at least one in the evening (step-by-step script below). Quick tags, then a review pass. Weak labels from `vjepa_tune_labels.py` add volume. The 18 min of desk labels are re-scored with the owner-review tier. | Turns "never measured" into a number. It is a precondition for every lever below. | About 75 min of owner recording plus about 60 min of review. Host: H0–H2 (about 2 h of operator time, under 15 min of GPU). |
| 2 | **Den-trained linear probe on frozen tokens** | Softmax over the five den classes on mean (+motion) pooled 4 x 12 x 12 tokens, class-weighted, three seeds. It replaces the household reading head for den use. | The biggest expected step, from domain match: reading F1 from today's unknown (0.40–0.56 on LA) to about **0.60–0.75** held out. Phone is unclear. | CPU minutes on LA (H3). |
| 3 | **Hand-region crop from pose** | A second crop around the wrists and elbows (`hands`), compared alone and next to the union crop. | **+5–10 F1 points** on reading and phone (object in the hands); about 0 on resting. | A second encode per clip, then later a worker input-profile change. |
| 4 | **Temporal smoothing and aggregation** | Causal EMA, last-k window or a sticky HMM over 3–8 clips (6–16 s), with a gap reset. | **+3–8 F1 points** and far less flicker, which matters for the 3–5 s on and 10 s off spotlight rule. | About zero. Victoria-side; it fits in the projection summary. |
| 5 | **Calibration and thresholds** | One temperature per fold on held-back sessions. "high" means LOSO precision ≥ 0.9 and "medium" ≥ 0.7, judged on the Wilson bound with one trial per 12 s. A class with no reachable threshold stays `not_calibrated`. | F1 unchanged. It makes "high" mean something and stops false spotlights. | Trivial. |
| 6 | **Efficient attentive probe** | H = 2 heads with one query each, a shared low-rank key projection, and values = the tokens (about 32 K parameters). It starts at mean pooling. | **+0–8 points** if the data supports it; at under an hour of labels it may overfit. Adopt only when it beats linear by ≥ 0.03 macro-F1 across sessions. | CPU about 20–40 min per sweep on LA. |
| 7 | **Clip length** | 7 s at 1 s steps against today's 3.5 s at 0.5 s steps. The frame rate is capped by 2 Hz previews either way. | **+0–4 points** for still activities. | A second export variant and its encodes. |
| 8 | **Output schema v2 for JEV** | See section 4. Per-class den-calibrated words, plus where the labels came from. | Lets JEV weigh V-JEPA correctly, including its correlation with Qwen. | An AL-3 change (projection, egress allowlist, tests). It needs an owner-approved egress update. |
| 9 | **computer_work** | Train with PC-input weak labels, which are independent of the camera. Use it only as a confirmer: post-MVP desk lighting uses HASS.Agent directly. | Unknown. The camera geometry is the limit. | Comes free with 1–5. |
| 10 | Retire the NTU zero-shot | Drop it once den heads beat it on the same clips (the cache stores it as the baseline). | Simpler, cleaner evidence. | Small. |
| — | Not recommended now | Unfreezing encoder blocks, the released 4-block probe, new encoders. | Overfits at this label volume. | — |

**Guided sofa recording script.** One person, owner-tagged, 300 s recording
chunks:
1. Reading an open book on the sofa.
2. Holding a closed book.
3. Phone on the sofa.
4. Resting with eyes closed.
5. Standing up and sitting down (transition).
6. The same at the desk: reading, phone, typing.
7. An empty room.

At least one day after dark with the room lit. Quick tags at each change,
then an owner review pass that sets `reviewed: true`, so these become gold.

**Decision rules**
- **Sample gate:** a class is reported only with ≥ 3 held-out sessions over
  ≥ 2 days and ≥ 480 labelled seconds (the `lab_eval` gate).
- **Adoption:** a lever is adopted only on that gate.
- **What reaches JEV:** a den head goes to JEV only when its `high` bucket
  reaches precision ≥ 0.9 held out.

## 3. Offline tooling (this ticket; new files, no host)

**Files**

| File | Role |
| --- | --- |
| `stack/services/perception-lab/vjepa_tune_probe.py` | numpy probes (linear, efficient attentive with exact backprop), seed ensembles, temperature, causal smoothing (EMA, window, HMM), Wilson-gated buckets, and `den_facts`, which builds the proposed JEV facts as enum words only. |
| `stack/services/perception-lab/vjepa_tune_eval.py` | Leave-one-session (or one-day) CV. Weak labels train but never score. Calibration on held-back sessions, the smoothing sweep, per-class P/R/F1, macro-F1, reading AP, ECE, Brier, coverage at precision 0.9, per-session bands, and the pinned-readout baseline on the same clips. `--final` saves a head. |
| `stack/services/perception-lab/vjepa_tune_labels.py` | Tiered clip labels from a read-only ledger: gold, owner, and weak (Qwen+JEV agreement, PC-input-backed computer_work). Circularity flags are recorded. |
| `stack/services/perception-lab/vjepa_tune_export.py` | Jetson, read-only. Exports clips from stored previews. The 3.5 s selection equals the live `ClipFrames._search` (tested); boxes come from the live `PersonBoxes.union`; it also exports pose joints for the hand crop. |
| `tools/la-vjepa-worker/vjepa_den_encode.py` | LA, inside a bounded unit. Encodes the clips with the worker's own pinned loader (`verify_artifacts`, `gpu_admission`, `ProbeModel`), pools to 4 x 12 x 12, and runs the pinned heads for the baseline. |
| `stack/services/perception-lab/vjepa_tune_synth.py` | Synthetic caches for tests and dry runs. **Not den evidence.** |
| `stack/services/perception-lab/vjepa_tune_config.json` | One config for export, encode, weak labels, protocol, probes, smoothing and buckets. |
| `stack/services/perception-lab/test_vjepa_tune.py` and `tools/la-vjepa-worker/test_vjepa_den_encode.py` | 18 + 5 tests: weak-label rules, ledger reads, live-selection parity, causal export, a finite-difference gradient check, smoothing causality, buckets, no fold leakage, the report round trip, and the encoder cache through a fake model. |

**What ran locally**
- Both new test files pass.
- A synthetic end-to-end run (`vjepa_tune_eval.py --synthetic`, 720 scored
  clips, about 14 s) exercised every probe and smoothing combination and the
  head export. On that synthetic data:
  - attentive scored 0.62–0.64 macro-F1, against 0.47–0.56 for the linear
    probes;
  - EMA smoothing helped the attentive probe.

  This only shows the plumbing works. **It says nothing about the den.**
- No real data exists locally, so no real offline run was possible.
- The existing `test_vjepa_worker.py` had one flaky keep-alive socket test
  that passed on rerun; it does not import these files.

## 4. What JEV should get (proposed `vjepa-den-projection-2`)

The current shape is `posture`, one `reading_signal`, three NTU
`zero_shot` sums, `motion` and `scene_change`, with `calibration:
unvalidated`. The proposed shape keeps the fields that work and replaces the
uncalibrated readouts.

```json
{"family": "temporal_encoder", "schema": "vjepa-den-projection-2", "status": "supplied",
 "latest": {"state": "current", "age": "fresh_under_3s",
            "facts": {"activity": {"reading": "high", "phone_use": "low", "computer_work": "not_calibrated",
                                    "resting": "low", "transition": "low"},
                      "top_high": "reading", "posture": {"top": "sitting", "confidence": "high"},
                      "motion": "still", "crop": "union_and_hands"}},
 "summary_last_60s": {"activity_high": {"reading": "most", "...": "none"}, "stability": "stable"},
 "calibration": "den_cv_v1", "label_basis": "owner_plus_qwen_weak",
 "correlated_with": "same_camera_visual", "independent_confirmation": false}
```

**Rules**
- **Enum words only.** No number, embedding, box or identifier leaves
  (`vjepa_tune_probe.facts_tokens()` is the allowlist).
- **Per-class status:** a class reads `not_calibrated` until it passes the
  sample gate and its LOSO bucket target.
- **`label_basis`:** tells JEV that weak labels came from Qwen and JEV
  agreement, so V-JEPA is not independent confirmation of Qwen. PC-input-based
  computer_work is the exception.
- **Smoothing and summary** run on Victoria, causally.

## 5. Risks
- **One person:** the den data is one person, so held-out sessions are not
  new people. Guests stay ambient-only, per the MVP.
- **Weak labels inherit Qwen's errors.** They train but never score, and
  `label_basis` discloses them.
- **Previews are kept only as long as the Lab store keeps them.** H0 checks
  what is still there.
- **The tokens are biometric.** They stay on LA under 0700, and the exported
  JPEGs are deleted after encoding (see the proposal).

# Live pipeline review, 2026-11-05

This is an independent, adversarial check of every rung built so far against the live-v1 contracts. No real hardware or data was used: fakes bound to 127.0.0.1, and people are A and B.

**Overall.** Each tool is careful, offline and well tested on its own synthetic data, and every key formula recomputes by hand. No tool writes a light or touches the pinned camera script outside the owner's steps, with one exception: rung 8's probe restore can rewrite a bulb a person has just set.

The pieces do not yet make a pipeline:
- the first seam (registration → observations) refuses every real registration;
- neither of the light model's two consumers can read it;
- several gates pass on empty or wrong evidence;
- with two people, router v0 cannot meet the 3 s onset target at all.

No rung should be run for a verdict until its critical and high findings are fixed.

## What was reviewed

| Rung | Branch (claude/…) | Commit reviewed | Finished? |
|---|---|---|---|
| R | rung-r · den-take-script | 8b1c945c · 23cdde50 | yes · yes |
| N1 | plan-n1 · den-label | b1f1a9d2 · a28130a4 | **no release note** · yes |
| N2 | plan-n2 · den-n2 | 764b213e · a39f02f5 | **no release note** · yes |
| 0 | den-core · correction-capture | 022aa9d5 · 37e47b20 | yes · yes |
| 0, 9 | den-core-live | 64bd23ca | yes |
| 1, 2, 3, 5 | rung-1-register, rung-2-observe, rung-3-track, rung-5-bundle | ce55b0a9, ab491a88, 49ff481d, cf659ba8 | yes |
| 7 | rung-7-photometry | 1931c831, plus a delta review to 9a5023e3 | yes |
| 8 | plan-rung-8 · rung-8-stage0 · rung-8-stage1 | 7949678a · 58aa1742 · 028981bc | **no release note** · yes · yes |
| 10 | rung-10-verify | b8ac320e | yes |

The spec was live-records at 6772ed39.

Seven branches appeared after the 05:07 UTC start: den-take-script, den-n2, den-core-live, rung-5-bundle, rung-7-photometry, rung-8-stage1 and rung-10-verify. den-core-live and rung-3-track changed substantially after a first review began, so both were reviewed again at their final commits. rung-7-photometry got a delta review, and two of its findings are fixed at the final commit.

**The three plan branches are unfinished by the brief's rule:** they have no release note, although none has changed for over 90 minutes. Every other branch has a note and at least 35 quiet minutes. A final fetch at 07:33 showed every head still at the commit above.

## Verdict per rung

**R, recording: sound with fixes.** Two reviewers rebuilt the clock check independently and matched it exactly: a 0.13 s drift gives a bound of 0.193 s and passes, and 0.14 s gives 0.203 s and fails. The recorder only reads from the camera and never writes the token. The defects are test gaps: the end-of-take session check, the rising-drift limit and the row count can each be removed with every test still green.

**N1, one-person reference: sound with fixes.** Freeze hashes recompute exactly, tampering is caught, and the session split is fixed by rule before any labelling. The held-out margin is thin, about 40 units per band against N2's 30. A session whose frames are all cut blocks both freezes, and `status` prints held-out label positions where agents read them.

**N2, need test (plan and scorer): not yet sound.** Every statistic matched independent code, including the 10,000-round bootstrap. But the onset rule credits any "on" run begun in the unscored gap between tasks, so an estimator that ignores touches still passes. A held-out run accepts an edited, uncommitted gate file: one edit turned "not feasible" into "feasible". The none band has no 30-episode gate.

**0, record and replay: not yet sound.** The replay is safe and byte-identical, and correction capture's intervals match. But only half of the gate exists: there is no applied scene or step log, live den_lighting is never compared, and both checkers still print OK. Replays never see a game. A gap between exports counts as occupied hours (12 h became 294 h).

**1, registration: not yet sound.** The fit matches an independent solver to 1e-13, and the 5 cm rule flips exactly at 5 cm. But the mount check says "unchanged" after a 5° turn or 8 cm slide that moves room positions 5-40 cm. Checks placed beside fit marks pass while room points sit 7-12 cm off.

**2, observations: not yet sound.** Plane lift, the torso point and the 67 ms depth rule match independent code. But it cannot read rung 1's room model at all. It ignores rung 1's "invalidated" mark and lifts old takes with the newest registration. Its freshness gate passes with half the detector results expired.

**3, tracks: not yet sound.** The final commit ran end to end, the tracker is causal, and the one-sided bounds recompute exactly (0.139 at 20 crossings, 0.095 at 30). But a window with nothing labelled counts as a passed trial: the tool printed PASSED for 1 real crossing plus 19 empty windows. The freeze covers only METHOD.md, and the reader merges two people's detections.

**5, bundles and router: not yet sound.** Copies are byte-identical, crossings mark bundles unsafe, and the schedule matches hand values. But the gate passes with nothing checked, even when scored. Bundles count evidence by capture time, not arrival. The 30 s back-off reacts to replies the live service treats as skips.

**7, measure light: not yet sound.** The `k_L` round trip, exposure invariance and the held-out lux check match, and the empty tolerance slot blocks scoring. But under the pending per-light-class option, coverage pools classes, so an unchecked class passes as measured and anchored. Nothing freezes the data between scores. The owner steps run captures with den_lighting active.

**8, light model (plan, Stage 0, Stage 1): not yet sound.** Stage 0 talks only to the camera and never touches the pinned script, and Stage 1's maths matches an independent rebuild to 1e-12. But Stage 1's restore retry can rewrite a bulb a person set by hand. After some Home Assistant errors it says "changed nothing" while den_lighting stays paused. Edited frozen blocks can turn FAIL into PASS.

**9, coordinator v0: not yet sound.** It is offline, replays byte-identically, and pins held bulbs at read-back. But a scored run cannot happen on real inputs: it rejects rung 8's model, and at that model's size its decisions overflow the log line limit. Its scorer reads each track's last state, and 9 of 11 gate mutants survive.

**10, verify: not yet sound.** The ±15% boundary and V11's −2.99% are right. But the held-bulb rule drops misses and keeps hits, so any hold can lift a true 50% hit rate to "pass". With the proposed background, perfect off and dim scenes score as misses. It cannot read rung 8's model or rung 9's decisions.

## Seams: one synthetic evening through the real tools

`probes/seam/run_seam.py` runs one 3.5-minute evening (A at the counter, B in the armchair, one crossing) through the real tools, each tool's output feeding the next. 24 links read cleanly, from take through clock check, registration, observations, tracks and bundles (every `obs_id` resolves) to need and decide. These links break:

- **Registration → observations.** den-observe refuses den-register's camera entry: the forms differ and the RGB geometry is missing. It also cannot read `room-model.jsonl` once that file holds two revisions. Rung 2 therefore runs only on its own synthetic camera, and every downstream position rests on an unregistered pose.
- **Light model → coordinator and verify.** Both refuse Stage 1's real `light-model.json`. den-verify also crashes on den-core's `log.jsonl`, and it reports "verified" with zero points on den-core-live's decisions.
- **Tracks → bundles.** Track time sits in a non-canon `capture_ts` that den-bundle never reads, so coasting and ended lines are misdated. An empty or mismatched tracks file routes nothing, and the gate still passes.
- **PowerShell 5.1 `>` output** is UTF-16, and all four consumers refuse it. den-observe exits 1, which its README defines as "gate failed".
- **Not exercised:** photometry → LightState (needs a camera day), and labels → N2's scorer. den-n2 rightly refuses without N1's freeze line.

Of the fields outside `schemas.json`, only those a consumer misreads are kept as findings: Track's time field, Observation's missing version, and the Decision dialect, where a schema reader sees every lit channel at level 0. Verification refuted one harness result: need's room-level merge of A and B came from the harness's own stand-in interpreter, which the spec allows until rung 6.

## Ranked findings

Independent refuters dropped 63 of 355 reports. After merging duplicates, **194 defects** remain. 193 of them are **confirmed**, reproduced by an independent verifier: 4 critical, 10 high, 103 medium, 76 low. The remaining one is plausible, upheld on file:line evidence. Ranking puts a wrong verdict first, then lost data, then hardware touched, then a blocked owner. `findings.json` gives each finding's reproduction and a suggested fix.

1. **Critical**, rung-3-track `scorer_core.py:99`: Empty crossing windows count as passed trials: a scored run PASSED on 1 real crossing plus 19 empty windows.
2. **Critical**, rung-7-photometry `card.py:416`: Under the per-light-class option, coverage is pooled, so a class scale with no independent check passes as measured and anchored.
3. **Critical**, rung-10-verify `score.py:303`: Held-bulb exclusion drops misses but keeps hits; at a 5% light share any hold turns a failing session into a pass.
4. **Critical**, rung-8-stage1 `probe.py:583`: The probe restore retry rewrites a bulb a person set by hand during the restore.
5. **High**, rung-10-verify `score.py:274`: With the proposed background, perfectly executed offs and deep dims score as misses.
6. **High**, rung-5-bundle `gate.py:170`: The gate passes, scored runs included, when it checked nothing (empty inputs, unmatched or missing crossings).
7. **High**, rung-3-track `pipeline.py:85`: Held-out takes can be re-scored after a tracker retune under the same fingerprint; the old report is overwritten.
8. **High**, correction-capture `capture.py:315`: A gap between two exports counts as occupied hours (12 h became 294 h) and flips the verdict.
9. **High**, rung-1-register `scene.py:233`: The mount check says "unchanged" after turns or slides that move room points 5-40 cm.
10. **High**, rung-3-track `records.py:238`: The reader merges two different people's detections, so one track lists the other person's observation.
11. **High**, rung-8-stage1 `probe.py:87`: After a Home Assistant error the probe prints "changed nothing" while den_lighting stays paused and bulbs stay lit.
12. **High**, den-core-live `decide.py:339`: At rung 8's model size, decisions exceed the 16 KB log line, so a scored run aborts with no log.
13. **High**, rung-2-observe `roommodel.py:90`: Rung 2 refuses rung 1's room model: a different camera-entry form, and JSON Lines read as one document.
14. **High**, rung-5-bundle `records.py:94`: Replayed bundles count evidence by capture time, not arrival, so they hold results not yet received.

Most of the 103 medium findings are gate logic that no test pins, record dialects a consumer misreads, and failure paths that print the wrong thing.


## What is missing for a live system

- **Nothing runs live.** Every tool is an offline command over JSON Lines files. No process feeds detectors into rung 2, streams observations to the tracker, or assembles bundles as evidence arrives. Rung 6 does not exist, so no bundle ever becomes a PersonState. No GPU-host endpoint accepts a person crop: both accept only whole 640×360 frames, while den-bundle clips crops to 1920×1080.
- **Pacing.** Router v0's round-robin at one request per 4 s serves each of two people every 8 s. The median wait alone is then 4 s, against the 3 s onset median, and the median onset on den-bundle's own router is 4.5-6.8 s. Router v0 also backs off 30 s on any 429, which the GPU host sends while voice or the whole-room pump is busy. The live pump backs off only on 503. Under that load each person is refreshed every 18-40 s.
- **Replays read the future.** Bundles count evidence by capture time, but detector results arrive about 1.5 s later, so replay scores will flatter live. Rung 9's scorer also reads each track's last state.
- **No live clock.** The capture clock is mapped offline from R's slate.
- **No actuation path.** There is no step log, no readback, and no coordinator write path through den_lighting (rung 11). Stage 1's model is relative and per camera block, so it cannot serve the 300/200 lx floors until rung 7 anchors `k_L` and blocks map to surfaces.
- **No budgets.** No contract sets the GPU memory or latency for per-person requests beside voice. Rung 9's 100 ms per decision costs 81-87 ms of CPU on a full-size model.

**Premise.** Under router v0, two people cannot reach the 3 s onset target by construction. N2 could also pass on a request path the live loop cannot reproduce. Settle both before spending held-out sessions.

## How it was checked

- **Suites:** every suite was run twice on a clean checkout. All pass except den-core-live's rung 9 timing test, which is red under load: 0.14-0.21 s against 0.1 s, and 0.102 s once at load 5. One den_lighting test errors because of the Home Assistant version installed.
- **Mutants:** 161 of 396 targeted mutants of gate logic survived. The weakest tools were den-n2 and the rung 9 scorer.
- **Merge:** all 18 branches merge with no conflict and no hybrid file. Once merged, den-photometry's tests import den-light's `synthetic.py`, and 57 of its 59 tests are lost.
- **Hand checks:** these match: `k_L` (0.100 and 174.533 lx back), the bounds (0.139 at 20 crossings, 0.095 at 30), Clopper-Pearson, the session bootstrap, false-fine, onset, the ±15% boundary, and corrections per hour.
- **Mismatches:** den-track's pooled IDF1 (0.933 against 0.600); den-verify's held-bulb hit rate (1.0 against 0.50); den-n2's ECE at a bin edge, and its fine recall counted per second where the ladder says per episode; φ when a session has zero hours.

The detail is in `probes/RESULTS.md`.

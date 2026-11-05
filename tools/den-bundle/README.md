# den-bundle: person bundles and router v0 (rung 5)

Rung 5 of the live architecture (`docs/architecture/live-v1/ladder.json`).

- **Reads:** `track` and `observation` records.
- **Writes:** `person_bundle` records: one track's evidence over a short
  window, aligned in time, for the Qwen interpreter.
- **Router v0:** decides which track gets the next interpreter request, and
  when, within today's caps.

**Status: built, not run.** Everything here is tested on synthetic fixtures
with a fake clock and a fake sender. The tool has no real sender:

- it never calls LA, the camera, the Jetson, Home Assistant or any other host;
- it records nothing and drives no light.

Running the router against LA is a later step, and it needs the owner's
go-ahead at run time.

## Files

| File | What it is |
|---|---|
| `METHOD-V1.md` | The assembly numbers, with their open slots |
| `ROUTER-V0.md` | The router rule: caps, back-off, order, timeout |
| `method.py` | Reads both blocks (CRLF or LF) and hashes them over canonical JSON |
| `records.py` | JSON Lines in and out; dates track lines |
| `assemble.py` | `track` + `observation` -> `person_bundle` |
| `router.py` | Router v0, and `run()` to drive it on a fake clock |
| `fakes.py` | `FakeClock`, `FakeSender` |
| `gate.py` | Rung 5's gate, on bundles and request logs |
| `synth.py` | The five synthetic fixtures |
| `den_bundle.py` | The command line |
| `test_*.py` | The tests (standard library `unittest`) |

## Commands (PowerShell 5.1, from the repo root)

Run the tests:

```powershell
python -B -m unittest discover -s tools\den-bundle -v
```

Write the fixtures to a folder outside the repo, then replay router v0 on one
of them and check the gate:

```powershell
$Out = "$env:TEMP\den-bundle"
```

```powershell
python -B tools\den-bundle\den_bundle.py fixtures --out $Out
```

```powershell
python -B tools\den-bundle\den_bundle.py route --observations $Out\crossing\observations.jsonl --tracks $Out\crossing\tracks.jsonl --from 2.5 --to 13 --requests $Out\requests.jsonl --bundles $Out\bundles.jsonl
```

```powershell
python -B tools\den-bundle\den_bundle.py gate --observations $Out\crossing\observations.jsonl --tracks $Out\crossing\tracks.jsonl --bundles $Out\bundles.jsonl --requests $Out\requests.jsonl --crossings $Out\crossing\crossings.jsonl
```

Build one bundle:

```powershell
python -B tools\den-bundle\den_bundle.py assemble --observations $Out\stale_observation\observations.jsonl --tracks $Out\stale_observation\tracks.jsonl --track A --at 7 --out $Out\one.jsonl
```

How the command line behaves:

- **Times.** A time is Unix seconds. A value below 1,000,000,000 means
  seconds after the first observation's capture.
- **Output files.** The tool writes them itself, as UTF-8 with LF, so no `>`
  redirect is needed. PowerShell 5.1's `>` would write UTF-16.
- **Exit codes.** 0 means done or pass, 1 means the gate failed, 2 means
  refused or bad input.
- **Practice and scored runs.** Today every run is practice, and says which
  slots are still open. `gate --scored` refuses, with exit 2, until every slot
  in both files is closed.

## Records

All records are JSON Lines: one record per line, UTF-8, LF. CRLF reads the same.

- **`person_bundle/v1`.** The field names are exactly those of `schemas.json`
  PersonBundle, plus `version`:
  - `bundle_id`, `track_id`, `interval`, `frames`, `keypoint_series`,
    `positions`, `objects`, `others`, `missing`, `association_safe`;
  - `frames[].crop_ref` is the crop request, `{"box": [x0, y0, x1, y1],
    "from_ts": t}`. It is the padded box from that camera's latest boxed frame,
    widened to even pixels, as N2's plan crops (METHOD-V1.md).
- **`router_request/v0`.** Rung 5's own record, one per start or `no_box`:
  - `request_id`, `track_id`, `sent_ts`, `bundle_id`, `interval`, `missing`,
    `association_safe`;
  - `tracks` (eligible at the start), `per_person_period_s`;
  - `rule` and `method`, each `{version, sha256, open}`;
  - `outcome` (`ok`, `refused`, `error`, `timeout`, `no_box`, or null while in
    flight), `done_ts`, `backoff_until`, and `detail` on an error.
- **Annotated crossings.** E2 names no format, so rung 5 reads lines of
  `{"crossing_id", "track_ids", "interval": [t0, t1]}`.
- **Inputs.** `observation` and `track` lines with the schema's fields. A
  `version` key on input is ignored.

## The gate (ladder rung 5), as `gate.py` checks it

| Clause | Checked |
|---|---|
| Each bundle covers its window | `window`: the interval is `window_s` long, ending at the request |
| ... with its sources aligned | `aligned`: every frame, keypoint entry and position is inside the interval; every keypoint entry has its frame; the other tracks' boxes line up with the frames; each camera's crop is even, inside the frame, and holds its latest box |
| `association_safe` false through every annotated crossing | `crossing`: every bundle of a crossing track whose interval meets the crossing |
| Missing sources flagged, not waited for | `missing`: every hole longer than `max_gap_s`, and every late or plane-lifted depth, is in `missing`; `no_wait`: each routed bundle ends at its send time |
| `keypoint_series` copies association-gated Observations verbatim | `verbatim`: every association-gated keypoints Observation in the interval is there, nothing else is, and `obs_id`, `ts`, `camera_id`, `transform_id`, keypoints and `joints_room` equal the record |
| Router v0 | `in_flight`, `interval` (4 s), `per_minute` (15), `backoff` (30 s), `round_robin`, `stamped` (rule version and sha256) |

No latency threshold is set for bundles, as the ladder says.

## The fixtures (`synth.py`)

| Fixture | What it shows |
|---|---|
| `crossing` | A and B walk past each other. The crossing is annotated over 5.0-7.0 s; the tracker is ambiguous 4.5-7.0 s. Every bundle meeting the crossing has `association_safe` false, and ambiguous observations never enter `keypoint_series`. |
| `missing_depth` | A at the counter. Depth is 67 ms off at 2.0 s (still used), 0.2 s late from 4.0 s (plane fallback), and gone from 6.0 s. `depth` is flagged. |
| `stale_observation` | The detector stalls after 5.0 s, and a result captured at 3.2 s arrives late. A request at 7.0 s flags `track`, `person_box` and `keypoints` and leaves the late result out of the window. |
| `two_tracks` | A and B for 70 s: they alternate, 8 s each. |
| `track_leaves` | B walks out at 20.0 s. B's last bundle is flagged and sent; then A gets every slot, 4 s each. |

## Open slots

The contracts leave these numbers open. Each has a proposed value that
practice runs use:

| Slot | File | Proposed | Why that value |
|---|---|---|---|
| `window_s` | METHOD-V1.md | 3.0 s | PersonBundle's interval is "typically 2-4 s"; R3 and N2 use about 3 s |
| `max_gap_s` | METHOD-V1.md | 1.5 s | No staleness bound in the contracts; N2's frames are 1.5 s apart |
| `pad_fraction` | METHOD-V1.md | 0.2 | The crop pad; N1's starting pad |
| `pad_min_px` | METHOD-V1.md | 16 px | N1's starting floor |
| `timeout_s` | ROUTER-V0.md | 20 s | Today's caps name no timeout; N2's look-test pattern uses 20 s |

The router's caps are closed at the ladder's values: 4 s, 15 a minute, one in
flight, a 30 s back-off. Also closed are the 67 ms depth skew (`schemas.json`)
and den-oak's 1920 x 1080 frame (note R2).

## Owner questions

1. **Track has no time of its own.** Rung 5 dates each track line by the
   newest `capture_ts` among its `last_obs_ids`, and never earlier than the
   track's previous line. So a coasting or ended line with no observation
   takes effect at the previous line's time. Should rung 3's Track gain a
   capture-time field (an `as_of`), so those lines are placed when they
   happen?
2. **Back-off scope.** After a refusal, error or timeout, router v0 pauses
   every track for 30 s, not just the one refused. LA's 429 is about LA's
   load (note R19), not one person. Is that the intent of "the 30 s back-off"?
3. **No-box requests.** A bundle with no person box has nothing to crop, so
   router v0 logs it `no_box`, does not send it, uses no cap, and passes the
   turn. Should it send such bundles anyway, with their flags?
4. **Crossing annotations.** E2 names no file format, so `gate.py` reads
   `{crossing_id, track_ids, interval}` lines. Should rung 4's gold set adopt
   this format, or name its own?

## What is deliberately not here

- A real sender: no LA call, no JPEG encoding, no frame or crop file.
- The interpreter's choice of stills from a bundle's frames, which is rung 6's.
- Anything learned from bundles. Bundles with `association_safe` false are
  never for teaching, and none are kept here.

# Den live probes

Read-only diagnostics for the live Den body. They observe Home and den-live and change nothing. They are not part
of a den-live release: `tools/den-live/release.py` packs `tools/den-live`, and this directory sits beside it.

The body continuity probe is the first step for the body flicker (goal 12, backlog V2). It times every hidden
interval while someone is tracked and attributes each one to a cause. The cause with 60% or more of the hidden time
picks the fix.

Household media rule: a receipt holds timestamps, statuses, counts, percentiles, 32-bit hashes of ids and distance
bands only. It never holds a point, a position, an image or camera data, and every file stays on this workstation.

## The three tools

| File | Runs on | What it does |
|---|---|---|
| `body-continuity-probe.mjs` | the workstation, in Chromium | Opens Home's Den view, waits for one fresh confirmed track, and measures a window (120 s by default). Writes one receipt |
| `body_continuity_tap.py` | the den-live host, from stdin | Two loopback GET subscriptions (body and people) for a bounded window. Prints one JSON document |
| `body-continuity-join.mjs` | the workstation | Joins a receipt and a tap by wall time and states the decision |

`body-continuity-core.mjs` holds the logic the three share: the in-page collector, the attribution, the decision
rule and the join.

## The browser probe

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$env:LOCALAPPDATA\ms-playwright"
node tools/den-live-probes/body-continuity-probe.mjs --out-dir <dir> --gpu
```

| Option | Default | Meaning |
|---|---|---|
| `--out-dir` | required | Where the receipt goes. Each run writes a new `body-continuity-<utc>-<label>-<random>.json` and never overwrites one |
| `--seconds` | 120 | The measured window |
| `--min-occupied-s` | 60 | Seconds of fresh single-confirmed-track time the window needs |
| `--max-extend-s` | 120 | How long the window may be extended to reach that |
| `--wait-occupied-s` | 120 | How long to wait for a confirmed track before the window opens |
| `--gpu` | off | Chromium with `--use-angle=d3d11`, so a real GPU draws. Otherwise SwiftShader |
| `--headed` | off | A visible window. Its input never reaches Home |
| `--phone` | off | A 390x844 touch viewport, labelled `phone` |
| `--dpr` | 1 | The device pixel ratio |
| `--long-tab-min` | 0 | Keep the tab open this many minutes before the window, to measure a long-lived connection |
| `--no-latency-trace` | off | Leave out `&den-latency=1`. With it, `HomeDenLatency` supplies the GPU-completion witness |
| `--origin`, `--site`, `--label` | Home's tailnet origin, `victoria`, `desktop` | |
| `--playwright` | `playwright` | A path to Playwright's `index.mjs`. `HOME_PLAYWRIGHT_MODULE` does the same |

Exit status: 0 when the occupancy gate was met, 3 when it was not (`unoccupied` or `insufficient_occupancy`; the
receipt is still written, says `decisive: false`, and its decision is `not_decisive` with the ranking kept as a
description), 1 when the probe failed, 2 for bad arguments. The window opens only on fresh single-confirmed time,
never on a person held through a perception stall.

**Dependency.** It needs Playwright 1.61.1, the version in the root `package.json`, and its Chromium. Install the
repo's dev dependencies with `npm ci`, or point `--playwright` or `HOME_PLAYWRIGHT_MODULE` at an existing
Playwright 1.61.1 `index.mjs`. Nothing is installed by the probe.

**Read-only.** Only GET requests to the Home origin leave the page. Any other method, a cross-origin request, a
WebSocket, a media request or a camera path (Frigate and HA camera proxies, `latest.jpg` snapshots, HLS playlists
and segments, go2rtc-style frame and stream paths) is aborted and counted under `read_only.blocked`.
`getUserMedia` refuses. The probe never clicks, types or taps, so Stop, Resume, holds and analysis controls are
never touched, and a headed window swallows the operator's input. The browser context is fresh and kept nowhere.

**What it measures.** An `onAfterRender` witness on the body's `body-recording` points (WebGL) times each drawn
frame. A rAF loop marks frame boundaries, and the renderer's frame counter tells a rendered frame from a skipped one.
The probe's `EventSource` subclass times SSE opens, errors and closes. It reads `body_clock` and people events, and
wraps Home's own body listener. That reads `HomeDenLiveView.status().body` before and after each packet, so it knows
whether the packet was accepted. It uses the `accepted` and per-reason reject counters when the page has them
(`rejected_by_reason` or `rejected`, `reopens` as a number or by reason, `accepted_not_drawn` or `unapplied`), and
otherwise `frames` plus a reject reason inferred from the same lease clock. The client continuity branch's reject
keys map to the probe's classes (`past_lease` to `stale_on_arrival`, `before_capture` to `pre_capture`, `scope` to
`scope_mismatch`, `pre_clock` to `no_clock`); a packet no counter moved for is `uncounted`. The receipt names the
source under `acceptance.counter_source`. The wrapper's own time per packet (a second JSON parse and two `status()`
reads, outside the app's listener) is under `probe_cost.body_wrapper_ms`, so its perturbation is visible.

People events keep the person tracked through a perception stall: after fresh tracked time, a `stale`, `degraded`
or `starting` people event, or silence on an open people stream, holds the last fresh track for up to 3 s
(perceive.py's `EPOCH_GAP_S`). A fresh event with no tracks ends tracking at once, and so does a stall past 3 s, so
`entry_wait` only follows someone appearing after either of those.

The receipt gives:
- hidden-while-tracked intervals, each with its start, end, duration, cause, every producer clear inside it and the
  people context (track count, coasting, ambiguous, no current observation);
- blank ms by cause, p50, p95 and max, per-minute counts and the drawn fractions;
- `attribution_coverage`: the share of hidden-while-tracked ms that evidence explains (everything but
  `unattributed`, `producer_other` and `no_body_yet`), against G12-C's 0.95 criterion;
- per-packet acceptance, accept-to-draw latency and accepted packets that were never drawn;
- exit latency, and false persistence: frames drawn after a processed clear, or after the producer's expiry;
- rAF fps, the GPU name and the GPU-completion witness;
- connection ages, reopens and timestamped SSE opens and errors.

| Cause | Meaning | Fix it points to |
|---|---|---|
| `producer_scope_changed` | A producer `scope_changed` clear | G12-A: same-track rebind |
| `producer_ambiguous` | A producer `ambiguous` clear | G12-B: ambiguity gating, after the tap shows 5 or more episodes |
| `lease_elapsed` | The shown frame's lease ran out before the next ready | G12-D: cadence and lease margin |
| `perception_stall` | No fresh frame while someone was tracked (a stale, degraded or starting people event, or silence), with the hidden tail until the next figure. It absorbs the `stale` or `unavailable` idle clear and the lease that ran out just before | G12-D: missing camera frames and frame cadence |
| `producer_pose_unavailable`, `producer_insufficient_depth` | Hold reasons | G12-D: hold-reason runs |
| `arrived_past_lease` | A ready arrived already past its lease | G12-E: client lease clock |
| `client_reopen` | Home closed the body stream itself | G12-E: destructive clock refresh |
| `sse_reconnect`, `body_stream_not_open` | The body SSE failed or was not open | G12-F |
| `accepted_not_drawn`, `client_reject_*`, `not_mounted` | Client-side loss: an accepted frame not yet drawn, a client reject, or no body layer | G12-G |
| `unattributed` | A drawn frame hidden with no clear, reject, lease end or connection event seen | none; extend the probe first |
| `producer_empty`, `producer_unavailable`, ... | Truthful hides | none; left out of the decision |
| `entry_wait`, `no_body_yet` | Waiting for a first figure | none; left out of the decision |

When a perception stall begins inside an interval another cause opened (a `scope_changed` or `ambiguous` clear, say),
the part before the stall keeps that cause and the rest is `perception_stall`. A connection cause (`sse_reconnect`,
`client_reopen`, `body_stream_not_open`, `not_mounted`) holds for the whole interval.

## The host tap

```bash
timeout 150 ssh -o BatchMode=yes -o ConnectTimeout=15 home-app python3 -I - --seconds 120 \
  < tools/den-live-probes/body_continuity_tap.py > <dir>/tap-<utc>.json
```

Python reads the script from stdin, so nothing is copied to the host. `-I` keeps it isolated, and it writes no file
there. It needs the standard library only. Track ids become 32-bit hashes, the same hash the browser receipt uses.
Positions become distance bands (`<0.6`, `0.6-1.0`, `1.0-1.5`, `>=1.5` m) as each event is read.

It reports:
- **coverage gaps**: tracked time with no ready under its lease, by cause. A `lease_elapsed` gap is `body_skip`
  when processed frames arrived without a ready, and `frame_gap` when none did. With den-live's `health.body`
  counters, it names the skip instead: `stills_precedence`, `hold_reason`, `rate_skip` or `busy_skip`. The
  browser's perception-stall rule applies here too: a stall keeps the person tracked for up to 3 s, and its gap is
  `perception_stall` with the first non-fresh people status (`stale`, `degraded`, `starting`, ...) or `silent`.
  `coverage.stall_ms` is the stalled time;
- **frame_vs_ready**: ready inter-arrival against processed-frame inter-arrival. A p95 ratio of 1.2 or less means
  the frame rate binds the cadence. It also reports Qwen ask overlap;
- **ambiguous episodes**: each `ambiguous` clear until the next ready, with the extra track's state, current
  observation and nearest distance band. The G12-B rule needs 5 or more episodes. Run longer passively (10 to 15
  occupied minutes) to collect them;
- scope clears, same-track revision changes, clear reasons, and the body and people timelines for the join.

## The join

```powershell
node tools/den-live-probes/body-continuity-join.mjs --browser <receipt.json> --tap <tap.json> --out-dir <dir> --tolerance-ms 250
```

Body packets seen by both sides are matched on run and seq. The median browser-minus-host receive time becomes the
offset; it includes transit and any clock difference. With no match, the clocks are assumed synchronized and the
result says so. Host evidence counts for an interval within the tolerance (250 ms by default) after that offset.
`lease_elapsed` then splits into `body_skip`, `frame_gap` or a named skip. It is `host_still_covered` when the host
still had a valid lease, which points at the client's clock, and `not_delivered` when a ready never reached the
browser. `perception_stall` takes the host's people status as its sub-cause. The decision is the same 60% rule, and
within `lease_elapsed` the host sub-cause above half sets the G12-D target.

The join picks a fix only when the browser receipt met its occupancy gate (`decisive: true`) and the host tap
overlaps its window. Otherwise `decisive` is false and the verdict is `not_decisive`: the ranking stays, with a note
naming the browser status, and no fix. The summary line prints `decisive` and `browser_status`. Exit status: 0
decisive, 3 not decisive (the file is still written), 2 for bad arguments or inputs that are not a
`den-body-continuity-probe-2` receipt and a `den-body-continuity-tap-2` tap.

## Paired baseline

V2 asks for one seated single-person interval with three concurrent measurements:
1. the probe with `--gpu`, in one fresh tab and one tab kept open 45 minutes or more (`--long-tab-min 45`);
2. the host tap over the same minutes;
3. the join.

Repeat with `--phone` for the phone view. An after-fix window counts only with the same probe, the same length,
and 3 or more same-track revision changes (`scope_changed.same_track_revision_changes`).

The only live smoke so far (an empty Den) ran the `-1` schemas. The first occupied baseline is the first live run of
the `-2` probe and tap, so check its people capture and counter source before trusting its decision.

## Tests

```powershell
node --test tools/den-live-probes/test-body-continuity-core.mjs
python -B -m unittest discover -s tools/den-live-probes -p "test_*.py"
```

Both run synthetic event streams on this workstation, with no network.

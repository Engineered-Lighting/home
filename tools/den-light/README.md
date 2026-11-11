# den-light: rung 8's light model

Stage 0 checks, in about one minute of camera time, whether the den's OAK
camera can meter light at all with exposure, gain and white balance held. The
owner decides go or no-go before any of Stage 1 runs. Stage 1 (below) fits the
per-bulb model from one probe session and scores it on held-out scenes. The plan is
[`docs/plans/rung-8-light-model/PLAN.md`](../../docs/plans/rung-8-light-model/PLAN.md);
the method, with every number, is [METHOD-V1.md](METHOD-V1.md).

| File | Runs on | Does |
|---|---|---|
| `METHOD-V1.md` | repo | meter-norm/v1, the sweep, the void reasons, the fit and the check. |
| `oak-capture-meter.py` | Jetson | The installed camera server plus the metering hold, and nothing else. |
| `meter.py` | PC | Reads bundles, decodes NV12, fits the inverse response, runs the check. |
| `sweep.py` | PC | The install lines (`--prepare`, `--install-record`, `--rollback-check`), the sweep (`--run`) and `--recheck`. |
| `test_den_light.py` | anywhere | Stage 0's tests on synthetic data and fakes on 127.0.0.1 only. |
| `guard.py` | PC | Stage 1's probe safety rules, pure: start checks, own or foreign, aborts, the restore order. |
| `probe.py` | PC | Stage 1's probe session and `--recover`. |
| `model.py` | PC | Stage 1's schedule, reduction, fit and score; writes `light-model.json` and `score.json`. |
| `predict.py` | anywhere | `predict(scene, model)` over `light-model.json`: pure, for rungs 9 and 10. |
| `synthetic.py` | anywhere | Tests only: a synthetic den, a fake camera and a fake HA running den_lighting 1.0.4. |
| `test_stage1.py` | anywhere | Stage 1's tests, the same rules as Stage 0's. |

It needs Python 3.12 and the standard library only. `sweep.py` talks only to
the camera, never to Home Assistant, and writes no lights.

## Privacy rules

- Everything lives on this PC under `C:\den-light\stage0-<date>\`. The tools
  refuse a folder inside a git checkout or OneDrive, a Stage 0 folder that
  already holds a sweep, and less than 1 GB free.
- A sweep keeps only the RGB plane and metadata of the frames it uses (about
  0.35 MB each, about 36 of them), never depth, left or right. A void sweep's frames
  are deleted. The rest stay on this PC until you delete them, as your
  2026-11-02 answer allows for den footage on household computers. They never
  go to git, OneDrive or the cloud.
- Agents read only `install.json`, `rollback.json`, and each `response.json`
  and `recheck-<n>.json`. They never open `frames\`.
- The camera token stays in den-take's `C:\den-takes\private\camera-token.txt`.
  No code path prints, logs or writes it. If that file does not exist yet,
  make it with den-take's README step 2 (branch `claude/rung-r`).

## Before approval 1: the open numbers

METHOD-V1 marks each number the plan left open with `open`. `sweep.py --run`
refuses to start while any mark is there. Tell the agent to keep each
proposed value or change it; the agent then removes the marks in one commit,
before any sweep frame.

## Step 1, once: a folder for this branch

Your checkout at `C:\claude\home` stays as it is. In PowerShell 5.1:

```powershell
git -C C:\claude\home fetch origin claude/rung-8-stage0
```

```powershell
git -C C:\claude\home worktree add C:\claude\home\.claude\worktrees\rung-8-stage0 claude/rung-8-stage0
```

```powershell
Set-Location C:\claude\home\.claude\worktrees\rung-8-stage0
```

Run every command below in PowerShell 5.1 from that folder (the one that
holds `tools\`). If `python` is not found, use `py -3.12`. If the agent says
the branch changed, run `git pull` there.

## Approval 1: install the metering server and run the sweep

Approval 1 covers steps 2 to 6: reading the unit, copying the meter server
into a new sibling directory next to the pinned one, a systemd drop-in that
points `oak-den.service` at it with its flags unchanged (the pinned
`capture.py` is never touched), one restart of `oak-den.service` in an empty,
dark den (Frigate's den feed drops briefly, and the den lights follow that
feed), then exposure, gain, white balance and focus held for about a minute
while you are out of view. Frigate sees the held settings meanwhile. Nothing
is paused and no light is written. It also covers up to 3 void reruns the
same evening.

### Step 2: names for this window, and the fixture check

Put your Jetson ssh login (as in den-take's README) in place of
`<jetson-user>@<jetson-address>`, and the Jetson's address in `$Cam`:

```powershell
$J = '<jetson-user>@<jetson-address>'
```

```powershell
$Cam = 'http://<jetson-address>:8090'
```

```powershell
$D = Get-Date -Format yyyyMMdd
```

```powershell
$S = "C:\den-light\stage0-$D"
```

If you open a new window later, set the same four lines again, with
`$D = '<the date in your stage0 folder name>'` instead of `Get-Date`.

```powershell
New-Item -ItemType Directory -Force C:\den-light | Out-Null
```

```powershell
New-Item -ItemType Directory $S | Out-Null
```

This only reads the unit and hashes the pinned script; it changes nothing:

```powershell
ssh $J "systemctl show oak-den.service -p FragmentPath -p DropInPaths -p ExecStart -p ActiveState; sha256sum ~/victoria-camera-pilot/pilot-20260926/capture.py" | Set-Content -Encoding utf8 "$S\before.txt"
```

```powershell
python -B tools\den-light\sweep.py --prepare $S --date $D
```

It checks that `oak-den.service` runs exactly the pinned
`victoria-camera-pilot/pilot-20260926/capture.py` and that it hashes the
fixture's `d052fc21…`, then writes `$S\zz-den-light-meter.conf`: the same
ExecStart with only the script path changed. If it says **Refused**, stop:
change nothing and tell the agent.

### Step 3: install, then restart in an empty, dark den

```powershell
ssh $J "mkdir ~/victoria-camera-pilot/pilot-$D-meter"
```

```powershell
scp tools\den-light\oak-capture-meter.py "$S\zz-den-light-meter.conf" "${J}:victoria-camera-pilot/pilot-$D-meter/"
```

```powershell
ssh -t $J "sudo install -D -m 644 ~/victoria-camera-pilot/pilot-$D-meter/zz-den-light-meter.conf /etc/systemd/system/oak-den.service.d/zz-den-light-meter.conf && sudo systemctl daemon-reload"
```

`mkdir` fails if the directory already exists; then stop and tell the agent.
`sudo` asks for your Jetson password. With the den empty and dark:

```powershell
ssh -t $J "sudo systemctl restart oak-den.service"
```

### Step 4: the after-check

Wait about 15 s, then:

```powershell
ssh $J "systemctl show oak-den.service -p FragmentPath -p DropInPaths -p ExecStart -p ActiveState; sha256sum ~/victoria-camera-pilot/pilot-20260926/capture.py ~/victoria-camera-pilot/pilot-$D-meter/oak-capture-meter.py" | Set-Content -Encoding utf8 "$S\after.txt"
```

```powershell
python -B tools\den-light\sweep.py --install-record $S --camera $Cam
```

It checks that ExecStart is the before line with only the script changed,
that the drop-in is the only new one, that the meter file hashes the
committed `oak-capture-meter.py`, that the pinned script still hashes
`d052fc21…`, and that the camera answers unheld with the new keys. Then it
writes `$S\install.json`. If it says **Refused**, roll back (below), with
one exception:

- **"cannot reach the camera"** is a network problem, not a broken camera:
  port 8090 is not reachable from this PC. Open a second PowerShell window,
  set `$J` there and run the tunnel below, then in the first window set
  `$Cam = 'http://127.0.0.1:18090'` and run the `--install-record` line
  again. Close the tunnel window when done.
- **"the camera answers but is unhealthy"** names the status it got (503 is
  the usual one) and says to run the rollback now. The meter server is
  running but not serving fresh frames: do not try the tunnel; run the
  rollback (below) straight away and tell the agent.

```powershell
ssh -N -L 18090:127.0.0.1:8090 $J
```

### Step 5: light the room

Den_lighting's arrival scene, at least one bulb on. If none is on, turn one
on in the HA app; den_lighting keeps that as its normal scene until it is
changed again, so set it back by hand afterwards if you want.

**How much light a sweep needs.** The sweep steps up to twice the camera's
own auto exposure, and the camera stops at 33 ms and ISO 1600. So the room
must be bright enough that auto exposure sits at or below ISO 800 at 33 ms
(or a shorter time at a higher ISO, as long as time × ISO is no more than
that). As a rough guide only (the usual exposure arithmetic, not measured in
the den): about 30 lux or more on the room's surfaces, which is more than a
single bulb at a low level. `sweep.py` reads the camera's auto exposure
before it holds anything. If the room is dimmer, it stops with **"too dark:
…"**, holds nothing and writes nothing: turn on one more den bulb (front
left, front right, rear left or rear right) at full in the HA app and run
the same line again. A too-dark stop is not a void and does not count as a
rerun.

### Step 6: the sweep (about a minute)

```powershell
python -B tools\den-light\sweep.py --run $S --camera $Cam
```

When it says "Starting in 15 s", step out of the camera's view and stay out
until it prints "Released". It waits for a still scene (up to 20 s), checks
the light (step 5), holds the camera, steps through 11 exposures and repeats
the first, then releases, all inside about 100 s, so the lights stay on
(den_lighting keeps them on for 120 s after Frigate stops seeing you). It
then fits for up to a minute. Ctrl+C before the release voids the sweep (it
still releases the camera); from the release on, Ctrl+C is ignored until
`response.json` is written, so pressing it again cannot lose the record.

It prints PASS, FAIL, VOID or ERROR and writes `$S\sweep-<n>\response.json`.
A void (the scene moved, no still scene, a 503, the camera unreachable, a new
camera session, the run too long, or Ctrl+C or an error before the repeat) is
neither a pass nor a fail and deletes its frames; if it says it could not
delete some, delete the ones it names by hand. Run the same line again, up to
3 times the same evening. A later rerun needs approval 1 again. ERROR means
the data was complete but the fit stopped on an error: the frames are kept,
and once the agent has fixed it, this recomputes the verdict from the saved
folder without the camera (put the sweep's number in place of `<n>`):

```powershell
python -B tools\den-light\sweep.py --recheck "$S\sweep-<n>"
```

It writes `recheck-<n>.json` beside `response.json` and never changes
`response.json`. It works on any pass or fail sweep too.

Then tell the agent the folder name. It reads `install.json` and
`response.json` only. On PASS the meter server may stay installed for Stage 1;
unheld, it behaves as the installed server. On FAIL you choose RAW output (a
bigger camera change, with its own approval) or stopping rung 8.

Exit codes: 0 = pass (or a step that succeeded), 1 = fail, void or error,
2 = refused (this command changed nothing; "too dark" is a refusal).

## Rollback

On a no-go, on an unhealthy camera, before any pilot command (below), or
whenever you want the installed server back:

```powershell
ssh -t $J "sudo rm /etc/systemd/system/oak-den.service.d/zz-den-light-meter.conf && sudo systemctl daemon-reload && sudo systemctl restart oak-den.service"
```

Wait about 15 s, then:

```powershell
ssh $J "systemctl show oak-den.service -p FragmentPath -p DropInPaths -p ExecStart -p ActiveState; sha256sum ~/victoria-camera-pilot/pilot-20260926/capture.py" | Set-Content -Encoding utf8 "$S\rollback.txt"
```

```powershell
python -B tools\den-light\sweep.py --rollback-check $S --camera $Cam
```

It is the install's after-check, now expecting the before state. It checks
that `oak-den.service` runs the pinned script with its before ExecStart and
drop-ins and that the script still hashes `d052fc21…`. Then it asks the
camera: `/health` must answer healthy, still from the same camera session
about 30 s later (a unit that keeps crashing and restarting can still read
`active` right after a restart), and a bundle must come without the meter
keys. Only then does it write `$S\rollback.json`. It takes about a minute.
If it says **Refused**, tell the agent. The meter directory on the Jetson is
then unused; you may delete it.

**Pilot commands need the rollback first.** The camera pilot's own tools do
not work while the den-light drop-in is installed: the local pilot tool's
check, activate and rollback all refuse while `oak-den.service.d` holds
anything besides the pilot's own `90-` file, and the pilot-release helper
(`switch-pilot-release.sh`) would be overridden by the drop-in. So run the
den-light rollback above, and see `--rollback-check` pass, before any pilot
command.

# Stage 1: the light model (only after Stage 0 passes)

Stage 1 fits each bulb's contribution to every block of the camera's view
from one probe session in the empty den after dark, and scores it on 16
held-out mixed scenes. **Gate: median 8% and p90 19%** relative error against
the camera's own readings. It needs the meter server from Stage 0 still
installed, and den_lighting 1.0.4 (the tool refuses another store version or
sensor attribute set). The light model is relative (`basis_unit: relative`):
absolute lux waits on photometry-commissioning, which is still pending.

## predict(): what rungs 9 and 10 call

`predict.py` is standard library only, does no I/O and runs nothing on
import:

```python
import json, predict
model = json.load(open(r"C:\den-light\<session-id>\light-model.json", encoding="utf-8"))
out = predict.predict({"den_front_left.white": {"level": 0.5, "cct_k": 2700}}, model)
```

`predict(scene, model, daylight=None)` returns
`{"surfaces": {point_id: value or None, ..., "unit": "relative"}, "version": "lm-den-<session-id>", "daylight": "excluded" | "included"}`.

- `scene` is per channel, keyed `<fixture_id>.<channel_id>` as
  `light-model.json`'s `channels` list them (`den_front_left.white`,
  `front_right.white`, `den_rear_left.white`, `den_rear_right.white`), each
  `{"level": 0..1, "cct_k": kelvin}` as in Decision.requested_scene. `level`
  is the command as a share of full (Hue's 254). A channel left out, or at
  level 0, is off; a channel that is on needs `cct_k`; an unknown channel
  raises ValueError. `predict.scene_from_ha({entity_id: {on, brightness,
  kelvin}}, model)` converts a Home Assistant scene.
- `surfaces` holds every sample point (one per block, ids `r<row>c<col>`, in
  `light-model.json`'s order) plus the `unit` key, the shape of
  Decision.predicted.surfaces. A point the fit excluded is None.
  That holds in every scene, an all-off one included: None means nothing is
  known there, 0 means no electric light.
- The value is the electric light Ê = Σ g_c(u_c)·B_c(p, k_c). Pass
  `daylight={point_id: D}` to add daylight; rung 8 does not model it.
- `predict.prepare(model)` reads the file once; pass its result instead of
  the dict when calling many times. One call takes a few milliseconds.

## The probe log: send and settle times

Each clean step adds one `step` line to `probes.jsonl`. Times are seconds:

| Field | Meaning |
|---|---|
| `sent_pc` | PC wall clock (Unix time) just before the step's first write |
| `send_s` | from then until HA had answered the step's last write |
| `settle_s` | from then until the first received frame that already agrees with the step's settled frames: when the camera saw the light land, within one frame |
| `capture_s` | from then until the step's last frame was in |

The same line holds `index`, `step_kind`, `scene`, and `label`, `ratio`, `bulb`,
`level` and `kelvin` where they apply. A step that changes no light (the first
basis step after the confirm captures), or changes it within Stage 0's noise
tolerance (the lowest dimming levels), reads as landing on its first frame, so
size a settle margin from the steps that change a lot, such as `basis`.

## Before the session: the open slots

METHOD-V1's Stage 1 block marks each number the plan or the contracts leave
open as `open`, with a proposed value. `probe.py` refuses to start and
`model.py` refuses to score while any slot is open. Tell the agent to keep
or change each one; the slots close in one commit before any probe:

| Slot | Proposed | What it sets |
|---|---|---|
| `block_px` | 20 | block size: 32 x 18 blocks |
| `reduce_pixel_step` | 2 | every 2nd pixel inside a block |
| `min_unmasked_share` | 0.5 | a block with less unmasked area is excluded |
| `exposure_search_steps` | 8 | steps of √2 down from auto's pick for the held exposure |
| `confirm_ratios` | [0.25, 0.5, 1] | exposures for the Stage 0 curve confirmation |
| `step_estimate_s` | 2.5 | one step's time, for the dark-run rule |
| `bracket_every` | 6 | an all-off capture after at most this many lit steps |
| `near_full_share` | 0.75 | what counts as near full combined output |
| `schedule_seed` | 20261110 | the hold-outs' random seed |
| `frames_per_capture` | 3 | settled frames per step |
| `step_dwell_s` | 1 | frames count only from this long after a step's send; HA shows a Hue bulb's target before it lands |
| `settle_limit_s` | 5 | how long a step may take to settle, from its send |
| `bracket_tolerance_multiple` | 5 | brackets that disagree by more flag a block |
| `hold_check_wait_s` | 5 | wait before the restored-hold check |
| `resume_check_s` | 30 | bulbs must read off this long after the resume |
| `ha_timeout_s` | 10 | an HA request with no reply in this time counts as HA lost |

## Approval 2, once: a Home Assistant token

In the HA app: your profile, Security, Long-lived access tokens, Create
token, name it `den-light`, and copy it. Then, in PowerShell 5.1:

```powershell
New-Item -ItemType Directory -Force C:\den-light\private | Out-Null
```

```powershell
notepad C:\den-light\private\ha-token.txt
```

Paste the token into Notepad, save and close. It is never typed on a command
line, printed or written anywhere else.

## Approval 3, per session: run probe session 1

The session holds the camera for about 12 minutes, makes about 150 light
changes over about 10 minutes in the empty den after dark, and pauses
den_lighting, then restores its saved normal scene and resumes it. In
PowerShell 5.1, from the branch folder (Step 1 above, with branch
`claude/rung-8-stage1`), set the names for this window. `$Cam` is as in Stage
0; `$S0` is the Stage 0 sweep folder that passed; `$H` is your HA ssh login
(the SSH add-on), if you copy the store file that way:

```powershell
$HA = 'http://<ha-host>:8123'
```

```powershell
$Cam = 'http://<jetson-address>:8090'
```

```powershell
$S0 = 'C:\den-light\stage0-<date>\sweep-<n>'
```

```powershell
$P = "C:\den-light\s1-$(Get-Date -Format yyyyMMdd)"
```

```powershell
$H = '<ha-ssh-user>@<ha-host>'
```

1. **In the den, copy den_lighting's store file.** It holds the normal
   scene the session writes back. If no bulb has ever been set by hand, set
   each one once in the HA app first, so every bulb has a level and a
   temperature.

   ```powershell
   scp "${H}:/config/.storage/den_lighting.state" C:\den-light\private\den_lighting.state
   ```

   On a newer HA OS the folder may be `/homeassistant/.storage/` instead of
   `/config/.storage/`; a copy over the Samba share works too.

2. **Start the session, then leave when it says so.**

   ```powershell
   python -B tools\den-light\probe.py --session $P --ha $HA --camera $Cam --stage0 $S0
   ```

   It refuses, changing nothing, unless the copy has automatic on, no game,
   no holds and a full normal scene; den_lighting reports automatic, no
   error and no game; the incumbent automation is off; all four bulbs are
   available; and the camera is healthy. Then it says "Leave the den now".
   It waits up to 10 minutes for the den to read empty for 2 minutes with
   all four bulbs off, pauses den_lighting, and steps the bulbs. Keep the den
   empty until it prints its outcome (about 12 minutes). Ctrl+C before the
   pause stops it; after the pause, Ctrl+C aborts and restores. A session
   that stopped before the pause used up its folder name, so set `$P` to a new
   name (add `-2`) before you start again.

3. **Read the outcome.** "complete" means the data is complete and the bulbs
   were restored and den_lighting resumed; any line above it names what was
   not. "aborted" names the reason (someone came in, a bulb changed that the
   tool did not change, the camera, HA lost or refusing a write, Ctrl+C). A bulb
   someone changed is never written again; the tool prints, for example,
   "light.X was not restored: its normal now carries the probe's level and
   temperature. Set it once by hand in the HA app." If occupancy reads
   unknown at the end, it asks you to walk through the den once and checks
   the bulbs go off about 2 minutes later. "den_lighting did not read back as
   resumed" means tell the agent.

4. **If it stopped part-way** ("stopped", or an abort that says so: HA lost,
   the window closed, a game started), finish it once HA answers (after the
   game, for a game). It is safe to run again if it stops again:

   ```powershell
   python -B tools\den-light\probe.py --recover $P --ha $HA --camera $Cam
   ```

5. **Fit and score** (no camera, no HA):

   ```powershell
   python -B tools\den-light\model.py --session $P --stage0 $S0
   ```

   It prints PASS or FAIL with the median and p90, and writes
   `readings.json`, `light-model.json` and `score.json` in `$P`. Tell the
   agent the folder name. It refuses if METHOD-V1's Stage 1 block has changed
   since the session: changing a number after the probes would be tuning on
   its hold-outs, which takes a new session.

Exit codes as in Stage 0: 0 = complete or pass, 1 = aborted, stopped or fail,
2 = refused (nothing was changed).

## Stage 1 privacy rules

- A session writes only to its own folder, `C:\den-light\<session-id>\`. The
  tools refuse a folder inside a git checkout or OneDrive, one that already
  holds a session, one holding a token, and less than 1 GB free.
- `frames\` keeps the RGB plane and metadata of the frames used (about
  0.35 MB each). An abort deletes the frames after the last clean step. The
  rest stay on this PC until you delete them, as your 2026-11-02 answer
  allows; once `readings.json` exists nothing needs them. They never go to
  git, OneDrive or the cloud.
- Agents read `session.json`, `probes.jsonl`, `fixtures.jsonl`,
  `readings.json`, `light-model.json` and `score.json`, never `frames\`. The
  store copy and the token stay in `C:\den-light\private\`.

# Tests

```powershell
python -B -m unittest discover -s tools/den-light -v
```

They use synthetic data and fakes on 127.0.0.1 only: no Jetson, no camera,
no Home Assistant, no real token. Stage 1's fake HA runs den_lighting
1.0.4's own `policy.py` (a byte-identical copy in
`tools/fixtures/den_lighting-1.0.4/`, pinned by sha256). They take about four
minutes. Synthetic runs prove plumbing; they are never evidence for the gate.

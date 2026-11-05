# den-light: rung 8's light model, Stage 0

Stage 0 checks, in about one minute of camera time, whether the den's OAK
camera can meter light at all with exposure, gain and white balance held. The
owner decides go or no-go before any of Stage 1 is built. The plan is
[`docs/plans/rung-8-light-model/PLAN.md`](../../docs/plans/rung-8-light-model/PLAN.md);
the method, with every number, is [METHOD-V1.md](METHOD-V1.md).

| File | Runs on | Does |
|---|---|---|
| `METHOD-V1.md` | repo | meter-norm/v1, the sweep, the void reasons, the fit and the check. |
| `oak-capture-meter.py` | Jetson | The installed camera server plus the metering hold, and nothing else. |
| `meter.py` | PC | Reads bundles, decodes NV12, fits the inverse response, runs the check. |
| `sweep.py` | PC | The install lines (`--prepare`, `--install-record`, `--rollback-check`) and the sweep (`--run`). |
| `test_den_light.py` | anywhere | Tests on synthetic data and fakes on 127.0.0.1 only. |

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
- Agents read only `install.json` and each `response.json`. They never open
  `frames\`.
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
writes `$S\install.json`. If it says **Refused**, roll back (below).

If it says "cannot reach a healthy camera", port 8090 is not reachable from
this PC. Open a second PowerShell window, set `$J` there and run the tunnel,
then in the first window set `$Cam = 'http://127.0.0.1:18090'` and run the
`--install-record` line again. Close the tunnel window when done.

```powershell
ssh -N -L 18090:127.0.0.1:8090 $J
```

### Step 5: light the room

Den_lighting's arrival scene, at least one bulb on. If none is on, turn one
on in the HA app; den_lighting keeps that as its normal scene until it is
changed again, so set it back by hand afterwards if you want.

### Step 6: the sweep (about a minute)

```powershell
python -B tools\den-light\sweep.py --run $S --camera $Cam
```

When it says "Starting in 15 s", step out of the camera's view and stay out
until it prints "Released". It waits for a still scene (up to 20 s), holds
the camera, steps through 11 exposures and repeats the first, then releases,
all inside about 100 s, so the lights stay on (den_lighting keeps them on for
120 s after Frigate stops seeing you). It then fits for up to a minute;
Ctrl+C is ignored until `response.json` is written.

It prints PASS, FAIL or VOID and writes `$S\sweep-<n>\response.json`. A void
(the scene moved, no still scene, a 503, the camera unreachable, a new camera
session, the run too long, or Ctrl+C or an error before the repeat) is neither
a pass nor a fail and deletes its frames; run the same line again, up to 3
times the same evening. A later rerun needs approval 1 again.

Then tell the agent the folder name. It reads `install.json` and
`response.json` only. On PASS the meter server may stay installed for Stage 1;
unheld, it behaves as the installed server. On FAIL you choose RAW output (a
bigger camera change, with its own approval) or stopping rung 8.

Exit codes: 0 = pass (or a step that succeeded), 1 = fail or void,
2 = refused (nothing was started or changed).

## Rollback

On a no-go, or whenever you want the installed server back:

```powershell
ssh -t $J "sudo rm /etc/systemd/system/oak-den.service.d/zz-den-light-meter.conf && sudo systemctl daemon-reload && sudo systemctl restart oak-den.service"
```

```powershell
ssh $J "systemctl show oak-den.service -p FragmentPath -p DropInPaths -p ExecStart -p ActiveState; sha256sum ~/victoria-camera-pilot/pilot-20260926/capture.py" | Set-Content -Encoding utf8 "$S\rollback.txt"
```

```powershell
python -B tools\den-light\sweep.py --rollback-check $S
```

It checks that `oak-den.service` runs the pinned script with its before
ExecStart and drop-ins, and that the script still hashes `d052fc21…`, then
writes `$S\rollback.json`. The meter directory on the Jetson is then unused;
you may delete it.

The pilot-release helper (`switch-pilot-release.sh`) is not part of this. While
the drop-in exists it would override whatever release the helper switches to,
so roll back first if you need it.

## Tests

```powershell
python -B -m unittest discover -s tools/den-light -v
```

They use synthetic data and a fake camera on 127.0.0.1 only: no Jetson, no
camera, no Home Assistant. They take about 30 seconds.

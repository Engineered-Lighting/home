# den-take: the rung R recorder

den-take records single-person den sessions in which every frame carries its
own capture time. It checks each take with the method in
[CHECK-V1.md](CHECK-V1.md), which was written before any camera read.

| File | Runs on | Does |
|---|---|---|
| `take.py` | your PC | Records one take from the camera's `/frame.npz` bundles. |
| `check.py` | your PC | Checks a take and writes `check-N.json`. |
| `slate.html` | your phone | Flashes the CHECK-V1 pattern on the phone's own clock. |
| `CHECK-V1.md` | repo | The method, with every tolerance in one block. |
| `scripts/hard-a.json`, `scripts/hard-b.json` | repo | N1's two staged scripts: spoken cues and their times. |
| `test_den_take.py` | PC | Tests on synthetic data only. |

It needs Python 3.12 and the standard library only. Nothing is installed or
changed on the Jetson. `take.py` only reads `/health` and `/frame.npz` from the
camera server already running there (oak-den, port 8090), with its token.

## Privacy rules

- Takes live only on this PC, under `C:\den-takes\<id>\`. They never go into
  git, OneDrive or the cloud. `take.py` refuses a folder inside a git checkout
  or OneDrive, a folder that already holds a take, and less than 35 GB free.
- Anyone may appear. Frames are raw, not blurred, and kept until you delete
  them (`choices.md` item 1).
- Frames cross the LAN once, from the Jetson to the PC. They travel over the
  camera's plain HTTP (its documented design), or inside the ssh tunnel below.
- Agents read only `take.json` and `check-N.json`. They never open `frames\`,
  `review\` or any thumbnail. Anything shown to others has faces blurred, which
  is outside R.
- The token stays in `C:\den-takes\private\camera-token.txt`. No code path
  prints, logs or writes it.

## Step 1, once: a folder for this branch

Your checkout at `C:\claude\home` is on another branch with changes of its own.
Leave it as it is: these lines add a second folder, a git worktree, that holds
`claude/rung-r`. In PowerShell 5.1:

```powershell
git -C C:\claude\home fetch origin claude/rung-r
git -C C:\claude\home worktree add C:\claude\home\.claude\worktrees\rung-r claude/rung-r
Set-Location C:\claude\home\.claude\worktrees\rung-r
```

Run every command below in PowerShell 5.1, from
`C:\claude\home\.claude\worktrees\rung-r` (the folder that holds `tools\`). If
`python` is not found, use `py -3.12` instead. If the agent says the branch
has changed, run `git pull` in that folder. Once the branch merges, skip this
step and run everything from a checkout of `main` instead.

## Step 2, once: the camera token

```powershell
New-Item -ItemType Directory -Force C:\den-takes\private | Out-Null
ssh engineeredlightingllm@192.168.1.168 "sed -n 's/^VICTORIA_CAMERA_TOKEN=//p' victoria-camera/private/camera.env" | Set-Content -Encoding ascii C:\den-takes\private\camera-token.txt
(Get-Content C:\den-takes\private\camera-token.txt).Length
```

The last line prints only the token's length, which should be 24 or more. Do
not open the file or paste the token anywhere.

## Step 3, once per phone: the slate page

First choice: copy `tools\den-take\slate.html` to the phone by cable or any
file share, and open it in the phone's browser.

Fallback: serve it from this PC for a minute. Run `ipconfig` and note this PC's
IPv4 address, then put it in place of `<PC address>`:

```powershell
python -m http.server 8765 --bind <PC address> --directory tools\den-take
```

On the phone, open `http://<PC address>:8765/slate.html`, then stop the server
with Ctrl+C. The page needs no network once it has loaded. If Windows asks
about the firewall, Allow adds a lasting rule for python.exe. To remove it
afterwards, go to Control Panel > Windows Defender Firewall > Allow an app
through Windows Firewall > Change settings, select the Python entries and
click Remove.

On the phone, tap the page once for full screen, turn the brightness up, and
keep the screen awake.

## Step 4, needs a go-ahead: the commissioning take (about 4 minutes)

```powershell
python -B tools\den-take\take.py --script commission --out C:\den-takes\c1
```

If take.py says "cannot reach the camera", use the tunnel below: open a second
PowerShell window, run it there, and add `--camera http://127.0.0.1:18090` to
the take.py command.
Nothing changes on the host. Close the tunnel window afterwards.

```powershell
ssh -N -L 18090:127.0.0.1:8090 engineeredlightingllm@192.168.1.168
```

Go-ahead text: if polling ever made the camera restart, Frigate's den feed
would drop briefly, and the den lights follow that feed
(`den_lights_follow_person_occupancy`). take.py stops at the first sign of
trouble: a 503, a new camera session, or 15 s with no new frame.

## Steps 5 and 7, PC only: check each take

```powershell
python -B tools\den-take\check.py C:\den-takes\c1
```

If the A and B rules pass, check.py opens `review\index.html` in your browser:
one thumbnail per second, 10 to a row. Scan it. At the prompt, type `none`, or
type each span that shows any part of a second real person (a hand or the edge
of a body counts; if in doubt, cut), as the first and last thumbnail times,
for example `01:10-01:20, 03:02-03:05`. The prompt works only in an
interactive console.

check.py prints PASS or FAIL, the frames per second, MB/s, each slate
attempt's offset window and the elapsed bound, then writes `check-1.json`. A
re-run writes `check-2.json` and never changes an earlier file; the newest one
counts. Then tell the agent the folder name. It reads only `take.json` and
`check-N.json`, and reports how many 30-minute takes fit on the disk. If a
readability setting must change, the agent commits CHECK-V2 and step 4 repeats
under a new go-ahead.

## Step 6, a go-ahead per take or per named batch: free takes

```powershell
python -B tools\den-take\take.py --script free --out C:\den-takes\2026-11-10-1
```

You will hear: "Hold the phone to the camera." / "Slate." (20 s) / then
"Slate OK. Put the phone away.", or "Unreadable, again." (at most two retries).
Then, 10 s later, "Begin." / the activity part, at most 28 min; press Enter to
end it / "Hold the phone to the camera." and the end slate, the same way /
"Done." If three start-slate attempts are unreadable, the take stops. The cues
are also printed in the window. A single Ctrl+C stops a take; it is then kept
with `complete: false` and fails the check. After any take, take.py asks the
camera's `/health` once more, up to about 25 s; wait for the "Saved" line. A
second Ctrl+C there skips that wait, and the take is still saved. A take whose
`take.json` was never finished (take.py closed or killed, or a power cut) is
refused by check.py.

## Step 8, a go-ahead per named batch: N1's staged takes

N1 (`tools/den-label`) uses staged takes: a take.py run that speaks a script
from `scripts\`. Before "Begin.", set out the props: the book in its place
(the in-view surface you named when you okayed the protocol, never the desk
top), and the stack of mail and a cloth on the counter (or the spot you
named). Keep your phone in a pocket between its blocks.

```powershell
python -B tools\den-take\take.py --script hard-a --out C:\den-takes\2026-11-12-1
```

The slates are as in Step 6. After "Begin.", take.py speaks and prints each cue
of `scripts\hard-a.json` at its time, the first one 3 s after "Begin.", and
after 25 minutes (the script's `end_s`) it starts the end slate by itself.
Dim the den lights by hand when it says so. Enter still ends the take early,
and the take is still saved. take.json records the script's name and its
file's sha256; check.py checks the take as usual (Steps 5 and 7). Alternate
`hard-a` and `hard-b`, at most two a day. take.py refuses an unknown or
malformed script before it reads the camera.

Exit codes: take.py 0 = complete, 1 = stopped, 2 = refused (nothing
recorded). check.py 0 = PASS, 1 = FAIL, 2 = refused (no check file written).

## Notes and risks

- **Disk.** C: is the PC's only drive, with 135 GB free on 2026-11-04 (97%
  full). The real bundle size is not measured yet: at most about 7 MB/s (12 GB
  per 30 minutes), probably 6 to 7 GB. With the 35 GB floor, about 8 to 15
  thirty-minute takes fit. The commissioning take measures the real size.
- **The slate may not read** (small phone, glare, low light). The peek retries
  on the spot.
- **Port 8090** has never been reached from the PC. The ssh tunnel is the fallback.
- **Load on the camera.** The camera ends its session if its RTSP writer falls
  about 2 s behind, or its 15 s watchdog fires. take.py stops polling at the
  first 503 or session change.
- **Resolution.** Only 640x360 at about 4 frames per second, so onset labels
  are quantised to about 0.2 to 0.27 s.
- **Camera build.** The installed build is not certain from files. take.py
  depends only on metadata schema 1, refuses anything else, and reads
  `/health` first.

## Tests

```powershell
python -B -m unittest discover -s tools/den-take -v
```

They use synthetic data and a fake camera on 127.0.0.1 only. There is no
camera, no ssh and no speech. They take about 2 minutes.

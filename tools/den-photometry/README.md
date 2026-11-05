# den-photometry: rung 7, measure light (E8)

Rung 7 gives the den camera an absolute light scale. A grey card of known
reflectance and one lux-meter reading at the card fix the camera scale `k_L`
on day 1; the other meter points (desk and sofa at least) and later days,
one of them lit by window daylight, are held back as checks in lux. The same
captures measure the reflectance of the surfaces the coordinator will target
(counter-1 at least) from the card beside them. The method, with every
number, is [METHOD-V1.md](METHOD-V1.md). The contracts are
[`docs/architecture/live-v1/`](../../docs/architecture/live-v1/README.md).

| File | Does |
|---|---|
| `METHOD-V1.md` | Every number, the open slots, the math, which captures count, the verdicts and the records. |
| `card.py` | The math and the records: k_L, the round trip, exposure invariance, held-out lux checks, reflectance, uncertainties. No network. |
| `photometry.py` | Your commands: `--self-check`, `--new-day`, `--capture`, `--score`. |
| `synthetic.py`, `test_*.py` | Tests on synthetic frames and a fake camera on 127.0.0.1 only. |

It needs Python 3.12 and the standard library only. It imports rung 8's
`tools\den-light\meter.py` and `sweep.py` for the metering hold and
meter-norm/v1. **It never writes to any light** and never talks to Home
Assistant: it talks only to the camera, and each capture runs under whatever
light is on, which you log with it.

## What it needs first

- **Rung 8's Stage 0 passed**, with the meter build still installed
  (`tools/den-light/README.md`, steps 2 to 6, on branch
  `claude/rung-8-stage0`). This tool reads that sweep's `response.json` for
  meter-norm/v1 and holds the camera through the same `POST /meter`. If you
  have rolled the meter build back, captures refuse ("not the meter build").
  Pilot commands still need rung 8's rollback first.
- **Your go-ahead to run.** Each capture holds the camera's exposure, gain and
  white balance for about 10 s while you are out of view, as Stage 0 did;
  Frigate sees the held settings meanwhile. Rung 8's approval 1 covered Stage
  0 only, so rung 7's captures need your own go-ahead.
- **The camera token** in `C:\den-takes\private\camera-token.txt`, as for
  Stage 0. No code path prints, logs or writes it.
- **A grey card** of known reflectance (its stated value and tolerance go into
  METHOD-V1) and **a lux meter**.
- **The den's RoomModel** as a JSON or JSON Lines file, for `--score` (ask the
  agent which file).

## Privacy rules

- Everything lives on this PC under `C:\den-photometry\`. The tool refuses a
  folder inside a git checkout or OneDrive, one that holds the token, and less
  than 1 GB free.
- A capture keeps the RGB plane and metadata of its frames (rung 8's frames
  per exposure, three exposures: 9 frames of about 0.35 MB) and
  `preview.bmp`, never depth, left or right. A void
  capture's frames are deleted. The rest stay on this PC until you delete
  them. They never go to git, OneDrive or the cloud.
- Agents read only `day.json`, each `capture.json`, `worksheet.csv` and the
  `score-<n>\` files. They never open `frames\` or `preview.bmp`.
- Never type a person's name into `--light` or the worksheet's `note`. People
  are A and B.

## Before day 1: the open slots

METHOD-V1 marks every value you have not confirmed with `open`. While any mark
is there, `--new-day`, `--capture` and `--score` refuse; `--self-check` still
runs. Three slots are empty because they are your decision
(photometry-commissioning, not answered yet):

- `meter_tolerance_lx`: how far, in lux, a held-out reading may be from the
  camera's estimate. It is fixed before day 1 and never changes after.
- `k_L_scope`: one camera scale (`single`) or one per light type
  (`per-illuminant-class`).
- `meter_class`: your meter's make, model and class, as one word.

The others have proposals (the card's 0.18 ± 0.01, the meter's 5 %, the fit
at counter-1, exposure invariance within 3 %, and so on). Tell the agent to
keep or change each one; the agent removes the marks in one commit, before
day 1. After day 1's first capture the block never changes.

## Step 1, once: a folder for this branch

Your checkout at `C:\claude\home` stays as it is. In PowerShell 5.1:

```powershell
git -C C:\claude\home fetch origin claude/rung-7-photometry
```

```powershell
git -C C:\claude\home worktree add C:\claude\home\.claude\worktrees\rung-7-photometry claude/rung-7-photometry
```

```powershell
Set-Location C:\claude\home\.claude\worktrees\rung-7-photometry
```

Run every command below in PowerShell 5.1 from that folder (the one that
holds `tools\`). If `python` is not found, use `py -3.12`. If the agent says
the branch changed, run `git pull` there.

## Step 2: names for this window

Put the Jetson's address in `$Cam` (or `http://127.0.0.1:18090` through rung
8's ssh tunnel, README step 4 there), and your passing Stage 0 sweep folder in
`$S0`:

```powershell
$Cam = 'http://<jetson-address>:8090'
```

```powershell
$S0 = 'C:\den-light\stage0-<date>\sweep-<n>'
```

```powershell
$P = 'C:\den-photometry'
```

```powershell
New-Item -ItemType Directory -Force $P | Out-Null
```

## Step 3: the self-check (no camera)

```powershell
python -B tools\den-photometry\photometry.py --self-check
```

It runs the known-card round trip through the same code the score uses:
100 nc, ρ 0.18 and 174.533 lx must give k_L 0.100 (cd/m²)/nc, L 10.0 cd/m²
and 174.533 lx back. It prints `ROUND TRIP PASS` and lists any open slots.

## Step 4: each day, a day folder

```powershell
python -B tools\den-photometry\photometry.py --new-day $P --stage0-sweep $S0
```

```powershell
$Day = "$P\day-$(Get-Date -Format yyyyMMdd)"
```

If you open a new window later the same day, set `$Cam`, `$P` and `$Day`
again. **Day 1 is the first day with any capture**, and its first good
capture at the fit point (counter-1, as proposed) fits k_L, so start day 1
there.

## Step 5: the light

Leave on whatever light you are measuring. This tool changes nothing:
den_lighting keeps running as usual and keeps the lights on for 120 s after
Frigate stops seeing you; a capture takes well under a minute. Log what is
on with each capture:

- `--illuminant bulbs`, `daylight` or `mixed`;
- `--blinds open`, `closed` or `unknown`;
- `--light "<what is on, in your words>"`, for example
  `"front left and front right at full"` or `"daylight only, no bulbs"`.

A capture needs auto exposure inside the camera's hold range. If the den is
too bright it stops with **"too bright"** before holding anything: dim a
little and run the same line again.

## Step 6: one capture at one point

1. Lay the grey card flat on the surface, facing up, where the camera sees
   it. Put the meter's sensor beside the card, in the card's plane, facing up,
   not covering it. At counter-1, leave a plain, matte part of the counter in
   view beside the card too (the reflectance pass).
2. Read the meter without shadowing it, and remember the number: that is
   `E_before_lx`.
3. Run (with the point's name; `desk`, `sofa` and `counter-1` are the ones
   the method needs):

```powershell
python -B tools\den-photometry\photometry.py --capture $Day --point counter-1 --illuminant bulbs --blinds closed --light "front left and front right at full" --camera $Cam
```

4. When it says "Starting in 15 s", step out of the camera's view and stay
   out until it prints "Released". It waits for a still scene, holds auto's
   exposure, then half of it, then auto's again, and releases. Ctrl+C before
   the release voids the capture (it still releases the camera).
5. Come back and read the meter again, the same way: that is `E_after_lx`.

It prints **KEPT**, with the capture number and its `preview.bmp`, or
**VOID** (the light or the scene changed, no still scene, the camera
unreachable or answering 503, or Ctrl+C): a void deletes its frames; run the
same line again. A **WARNING** that frames were not at the held settings
means that capture will not count: run it again.

Exit codes: 0 = kept (or a step that succeeded), 1 = void, error, or a score
that did not pass, 2 = refused (this command changed nothing).

## Step 7: type the readings into the worksheet

Open the worksheet in Notepad (Excel locks the file while it is open, and the
next capture could not add its row):

```powershell
notepad "$Day\worksheet.csv"
```

Each capture has a row starting with its number. Do not change the first six
columns. Type, between the commas:

- `E_before_lx` and `E_after_lx`: your two readings, in lux. Both or neither.
- `card_box`: the card's box in the preview, `x0 y0 x1 y1`, inside the card's
  edges (a few pixels in from each edge). Open the preview in Paint and hover
  over the card's top-left and bottom-right corners; Paint shows the pixel
  position at the bottom left of its window:

```powershell
mspaint "$Day\capture-1\preview.bmp"
```

- `surface_box`: at counter-1, a box on the plain part of the counter beside
  the card, the same way. Leave it empty elsewhere.
- `note`: anything (no names).

A finished row looks like this:

```text
1,counter-1,bulbs,closed,front left and front right at full,2026-11-10 19:03:12,174.5,175.1,305 155 334 184,365 155 414 184,
```

Save the file (File, Save). A row with nothing typed is simply not used.

## Step 8: more days, and the daylight day

Day 1: counter-1 first (it fits k_L), then desk and sofa. On at least one
more day, capture desk, sofa and counter-1 again. One of the days must be lit
by window daylight (`--illuminant daylight`, blinds as they are): for E8's
daylight day, take captures through that day, logging the blinds each time.
Each day starts with step 4.

## Step 9: the score

```powershell
python -B tools\den-photometry\photometry.py --score $P --room-model <path to the den RoomModel>
```

To add the sun's position to each LightState, add
`--latitude <deg> --longitude <deg>` (east positive). The location is used
only to compute the sun and is never stored.

It pairs every typed reading and box with its capture's frames and prints
the round trip, exposure invariance, k_L with its uncertainty, every held-out
point (meter, camera and error in lux, with its uncertainty), the measured
reflectances, every capture it did not use and why, and finally **RUNG 7
GATE: PASS**, **FAIL** or **INCOMPLETE** (with what is missing). It writes
`$P\score-<n>\` with `calibration_record.jsonl`, `room_model.jsonl`,
`light_state.jsonl` and `report.json`, and changes nothing else. Run it again
after more captures; each score is a new folder whose records supersede the
last. Then tell the agent the score folder's name.

## Tests

```powershell
python -B -m unittest discover -s tools/den-photometry -v
```

They use synthetic frames and a fake camera on 127.0.0.1 only: no Jetson, no
camera, no Home Assistant, no light. They take under a minute.

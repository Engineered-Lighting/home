# den-register: rung 1, the camera in the room frame

den-register places the den camera in the room frame from control points you
measure with a tape, see in one frame of an R take, and click. It fits a rigid
camera-to-room transform, checks it against two points it never saw, and on a
pass writes the RoomModel's camera entry and a CalibrationRecord. Every later
Observation's `transform_id` (`den-oak@<version>`) resolves to that entry. The
method, with every number, is [REGISTER-V1.md](REGISTER-V1.md); the records
follow `docs/architecture/live-v1/schemas.json`.

| File | Does |
|---|---|
| `register.py` | The commands: `worksheet`, `click`, `fit`, `check-mount`, `resolve`. |
| `page.html`, `serve.py` | The click page, served on 127.0.0.1 only. |
| `rigid.py` | The rigid fit, its residuals and the leave-one-out bound. |
| `scene.py` | Reads one R take frame: left image, depth plane, intrinsics. |
| `rig.py` | The camera's own geometry for the entry, from a frame and the camera's `/calibration` reply. |
| `worksheet.py`, `records.py` | Your worksheet; the RoomModel and CalibrationRecord files. |
| `REGISTER-V1.md` | The method and its numbers, some still open. |
| `test_den_register.py`, `synthetic.py` | Tests on synthetic scenes only. |

It needs Python 3.12 and the standard library only. It never opens the camera
or contacts the Jetson, LA or Home Assistant: it reads takes that R's
`take.py` already saved on this PC, and one camera reply you save in step 4b.

## Privacy rules

- Your measurements, clicks and results live in one folder on this PC,
  `C:\den-register`. They never go into git, OneDrive or the cloud;
  `register.py` refuses a folder inside a git checkout or OneDrive.
- The click page shows one frame of a take, from this PC only (127.0.0.1, with
  a one-time token). Frames never leave the PC.
- Tell the agent only the verdict line (`REGISTERED ...` or `REFUSED`) and the
  reason names under it. Do not paste the worksheet, clicks or fit files.

## Step 1, once: a folder for this branch

As for R, keep your checkout at `C:\claude\home` as it is and add a worktree
that holds `claude/rung-1-register`. In PowerShell 5.1:

```powershell
git -C C:\claude\home fetch origin claude/rung-1-register
git -C C:\claude\home worktree add C:\claude\home\.claude\worktrees\rung-1 claude/rung-1-register
Set-Location C:\claude\home\.claude\worktrees\rung-1
```

Run every command below from `C:\claude\home\.claude\worktrees\rung-1`. If
`python` is not found, use `py -3.12` instead. If the agent says the branch has
changed, run `git pull` there. This branch includes R's `tools\den-take`.

## Step 2: the worksheet

```powershell
python -B tools\den-register\register.py worksheet C:\den-register
notepad C:\den-register\worksheet.json
```

The room frame is the scan's: metres, `z` up from the floor, `x` along the
room's long axis (4.572 m), origin at the scan's minimum corner. Write which
wall is `x = 0` and which is `y = 0` under `references`.

## Step 3: mark and measure

1. Put six small tape crosses on flat surfaces the camera sees, spread across
   the room: some on the floor, one or two on a table or counter top, one on a
   wall, and at least one at the far end from the camera. Not all on one line:
   the mount check below cannot confirm crosses that stay close to one line
   (`mount_min_spread_m`, proposed 40 cm), and `fit` says so. Not all near the
   camera: it cannot confirm them unless the farthest is `mount_min_reach_m`
   (proposed 3 m) from the camera, and `fit` says that too. Not on edges or corners,
   where depth jumps. Leave them in place after the fit: the mount check clicks them
   again.
2. Measure each cross's centre: `x` from the `x = 0` wall, `y` from the
   `y = 0` wall, `z` from the floor, with a tape or a laser. Write it as
   `"room_xyz_m": [x, y, z]` in metres, for example `[2.31, 1.07, 0.0]`.
3. Keep four as `"role": "fit"` and two as `"role": "check"`. The checks are
   never used by the fit, so pick two that are not next to a fit point.
4. Optional: if you can find a cross in the scan viewer, write the position it
   shows as `"scan_xyz_m"`. It must agree with your tape within 5 cm.
5. `measured_by` is `A` or `B`. Save the file as UTF-8 (Notepad's default).

## Step 4, R's go-ahead: a short take

Under R's own go-ahead for a free take, record a short one with nobody in view
during its activity part. Leave the crosses in place.

```powershell
python -B tools\den-take\take.py --script free --out C:\den-takes\reg-1
```

Do the start slate as usual, then step out of the camera's view. About 20
seconds after "Begin.", press Enter, then do the end slate. Use a new folder
name for each take.

## Step 4b, once, R's go-ahead: the camera's own geometry

Rung 2 needs the camera's stereo baseline and the offset between its depth and
colour cameras. They are in the camera's factory calibration, which no take
saves. Save the camera's `/calibration` reply in the working folder, with R's
token. It is a single read:

```powershell
$token = (Get-Content -Raw C:\den-takes\private\camera-token.txt).Trim()
Invoke-WebRequest -UseBasicParsing -Headers @{ Authorization = "Bearer $token" } -Uri http://192.168.1.168:8090/calibration -OutFile C:\den-register\camera-calibration.json
```

If the camera cannot be reached directly, use R's tunnel and
`http://127.0.0.1:18090/calibration` instead. A 503 means the camera server is
not ready yet; try again. Save it again only if the camera itself is replaced.

## Step 5: click each point

```powershell
python -B tools\den-register\register.py click C:\den-register --take C:\den-takes\reg-1
```

Your browser opens the page. Pick a point in the list, then click its cross on
the left camera image. Each click shows the depth found there; "depth_missing"
means too little depth around it, so click a little to the side. If that does
not help, you can leave the point unclicked, but keep a cross at the far end: a
turn shows most there, and a dark end of the room with no cross on it is hard to
confirm later, because the mount check has to count it as far away. If no cross
you clicked is `mount_min_reach_m` (proposed 3 m) from the camera, `fit` says so
and the mount check will not confirm that registration. More light helps. The
Brightness button helps in a dark room; the Colour view is for
finding your way only. Every click is saved at once in
`C:\den-register\clicks-reg-1.json`. Press Done when finished. Running the
command again reopens the same frame with your clicks; add `--frame 40` to use
another frame of the take (and click again).

## Step 6: close the open slots

`fit` refuses while any number in REGISTER-V1.md is `open`. Before your first
fit, tell the agent which proposed values to accept (or other values). The
owner questions below say what they cost. The agent commits them; then run
`git pull`. Do this before you see any fit result.

If a line of the block does not read, or the file is gone, `click`, `fit` and
`check-mount` stop with `REFUSED method_invalid`, naming what is wrong, and exit 2.
That says nothing about the camera. Put the line right, or
`git checkout -- tools\den-register\REGISTER-V1.md` to start again from what is
committed.

## Step 7: fit and read the verdict

```powershell
python -B tools\den-register\register.py fit C:\den-register --take C:\den-takes\reg-1
```

It prints `REGISTERED den-oak@<version>`, or `REFUSED` with each reason, then
each point's residual or check error and where it puts the camera. Every run
writes `fit-N.json`; a pass also adds a line to `room-model.jsonl` and to
`calibration-records.jsonl`. The reasons:

| Reason | What to do |
|---|---|
| `slots_open` | Step 6. |
| `method_invalid` | Step 6: REGISTER-V1.md does not read, and the line says what is wrong. |
| `camera_calibration_missing`, `camera_calibration_invalid` | Step 4b. |
| `intrinsics_missing` | Use another take; if it stays, tell the agent the reason's text. |
| `too_few_pairs`, `too_few_checks` | Click at least 3 fit points and 2 checks. |
| `collinear` | Add a fit point away from the line the others are on. |
| `depth_missing` | Click that point again, a little to the side, or clear it. |
| `frame_depth_different_times` | Use another frame (`--frame`). |
| `fit_residual_over_gate`, `check_over_gate` | Re-measure the named point, and check its click. |
| `leave_one_out_over_gate` | Re-measure the named point and the others. A fit point away from the rest helps too: the fit is leaning on this one. |
| `scan_disagrees` | The tape references and the scan disagree: check which walls are `x = 0` and `y = 0`. |
| `camera_below_floor` | `x`, `y` or `z` runs the wrong way, or two are swapped. |
| `clicks_stale` | Click again on this take. |
| `mount_changed` | The camera moved after this take: record a new one. |
| `already_registered` | Nothing new to register. |

To look up a registration later:

```powershell
python -B tools\den-register\register.py resolve C:\den-register den-oak@2026-11-10.1
```

## Later: has the camera moved?

A registration holds only while the camera stays put. After the takes you want
to score, or after anything may have knocked the camera, record a new take with
the room clear and the crosses in place (step 4), then:

```powershell
python -B tools\den-register\register.py check-mount C:\den-register --take C:\den-takes\2026-11-12-1
python -B tools\den-register\register.py click C:\den-register --take C:\den-takes\2026-11-12-1
python -B tools\den-register\register.py check-mount C:\den-register --take C:\den-takes\2026-11-12-1
```

The first check compares depth only, and can only catch a large move. If it
finds none, it says `MOUNT NOT CONFIRMED`: depth cannot see a small turn or
slide, even one that puts room positions 40 cm off. So click every cross the
registration used on the new take, and run the check again. A turn shows most at
the far crosses, so leaving one out confirms nothing, and the line names it. The
check works out how the crosses moved together since the registration, carries
each click through the registration to compare it with your tape position, and
works out how far that movement would carry anything else in view:

| Line | Means | What to do |
|---|---|---|
| `MOUNT CONFIRMED` | Every cross is within 5 cm, and so is everything in view, at the deeper of what the registration frame and this frame saw there. Any part of the view the registration frame had no depth for counts as `mount_view_reach_m` away (proposed 6 m). The registration holds. | Nothing. |
| `MOUNT NOT CONFIRMED` | A cross is not clicked, or the crosses look moved together on this one take, or one cross does not move with the others, or one is more than 5 cm from your tape position, or the crosses show a movement that puts something else in view further off, or they are too close to one line to show a turn about it, or all too close to the camera to show one. Nothing changed. | Do what the line says: click the crosses it names, on a take with a clearer view if something is in the way, or record a new take and click them all again. If it says the same: a cross on furniture that moved, measure it again; otherwise treat the camera as moved. Then steps 4, 5 and 7. |
| `MOUNT CHANGED` | The depth moved a lot, or two separately clicked takes agree that the crosses moved together by more than 5 cm. The registration is now invalidated, for good, and takes it had vouched for can no longer be replayed. | The line lists what to re-check. Then steps 4, 5 and 7. |
| `mount_take_not_later` | That take is not newer than the registration's own. | Record a new take. |
| `mount_take_in_future` | That take ended later than this PC's clock says it is now, so it could vouch for takes not recorded yet. | Check the PC's clock, then record a new take. |

One cross off on its own, because you misclicked it or it moved, gives `MOUNT NOT
CONFIRMED` and names it. But a far cross can pull the movement the crosses show
together along with it, so one slipped click there can make them all look moved
on a camera that never moved. An invalidation cannot be undone, so it takes two
separately clicked takes that agree:

1. The first take says `MOUNT NOT CONFIRMED`, that the crosses look moved. It
   confirms nothing, invalidates nothing and asks for a new take. A scored replay
   of a take after the last confirmation stays refused, as it was.
2. A clean re-click clears it. If you know a click slipped, click it again on the
   same take and run the check again. Otherwise record a new take (step 4), click
   every cross, and run the check: if that take does not show the movement, the
   first is forgotten.
3. A second take, recorded later than the first, that shows the same movement
   (from one take's crosses to the other's, none moves more than 5 cm) gives
   `MOUNT CHANGED`. Clicking the same take again never counts as the second.

A real knock ends invalidated after these two takes. So does a far cross on
furniture that moved and stays moved, though the camera stayed: both takes see the
same thing, and the tool cannot tell the two apart. If you know a cross moved,
measure it again and register again instead of recording the second take. The first
take's crosses are read back from its `mount-N.json`, so leave those files in the
folder; without them it only asks for one more take.

Noisy clicks change those two takes, as far as synthetic noise can say. These are
for one cross at 5.2 m and five at 2 to 2.5 m, 300 trials each:

- An unmoved camera. With every click at 1 px and depth noise of 0.5% or 1%, no first
  take looked moved. With the far cross clicked at 3 px and stereo depth noise of
  0.05 px (about 4 cm at that cross), 10.0% looked moved and 0.7% ended invalidated on
  the second take. At 4 px and 0.02 to 0.05 px, 21 to 29% looked moved and 1.3 to 2.3%
  ended invalidated on the second take, about 7% if you keep going to six takes.
- A knock. At 1 px and 0.5% depth a 5° turn was invalidated on the second take every
  time, and a 1° turn, 9.5 cm off across the view, 46% of the time and 76% within six
  takes. Two noisy takes can differ by more than 5 cm and then do not agree: with the
  far cross at 3 px, a 5° turn was invalidated on the second take 50% of the time and
  96% within six.
- So do not keep recording takes until one confirms. Each take gives noise another
  chance: over six takes, with the far cross at 3 px, a 1° turn got one confirmation
  in 6.0% of trials (0.3% at 1 px, 10% at 4 px and 0.02 px). If two or three takes say
  the crosses look moved, treat the camera as moved and register again.

A person standing close to the camera during the check take can invalidate on one
take, because no click is involved: filling over half of the frame moves the depth
enough to give `MOUNT CHANGED`. This is why the room stays clear for that take.

`MOUNT CONFIRMED` is saved in `room-model.jsonl`. It vouches for the takes
recorded since the registration, up to the one you just checked, so a scored
replay (rung 2) refuses any take after that until you check again. `resolve`
prints which take last confirmed the registration.

Exit codes: 0 = registered, confirmed or valid; 1 = refused, changed, not
confirmed or invalidated, with the reason printed and saved in `fit-N.json` or
`mount-N.json`; 2 = refused before anything was saved, with the reason
printed.

In PowerShell 5.1, don't make a file this tool reads with `>`: that writes
UTF-16, which is refused with exit 2, and the tool writes its own output. To
repair one where it lies:
`(Get-Content worksheet.json) | Set-Content -Encoding utf8 worksheet.json`.

## Owner questions

1. **How often a correct registration gets through.** Real click and depth noise is
   not measured yet, so these rates are on synthetic noise: each click 1 px, tape
   5 mm, a camera that never moved, 2000 trials a row.

   | Crosses | Depth noise | `fit` passes | Of those, a later check confirms | Both |
   |---|---|---|---|---|
   | Six, 3.1 to 4.2 m from the camera | 0.5% | 57.2% | 44.4% | about 25% |
   | Six, 3.1 to 4.2 m from the camera | 1% | 13.2% | 6.7% | about 1% |
   | One at 5.2 m, five at 2 to 2.5 m (step 3) | 0.5% | 39.7% | 80.9% | about 32% |
   | One at 5.2 m, five at 2 to 2.5 m (step 3) | 1% | 8.8% | 33.5% | about 3% |

   So `REFUSED` or `MOUNT NOT CONFIRMED` on a correct registration is usual, and a
   re-click or a new take is the normal answer. In the layout step 3 asks for, the
   leave-one-out gate alone refuses 58.5% of correct registrations at 0.5% depth
   (29.2% in the other layout). It stays because rung 2 refuses a bound over the
   gate. Do you accept the proposed values on these rates, or do you want a real
   take's noise measured first?
2. **How far the farthest cross must be.** `mount_min_reach_m` is proposed at 3 m.
   Under it the mount check confirms nothing. The same noise at 0.5% depth, among
   fits that pass (200 for the first row, 400 for the others): how often a turn is
   confirmed that puts something in view about 7 cm off (a 0.75° pitch or turn).

   | Crosses | 0.75° pitch | 0.75° turn |
   |---|---|---|
   | All within 2 m, without the slot | 25% | 7.5% |
   | All within 2 m, with the slot | 0% | 0% |
   | One at 3.1 m, five within 2 m | 13.2% | 6.5% |
   | One at 5.2 m, five within 2 m | 4.0% | 3.0% |
   | One at 5.2 m, five at 2 to 2.5 m (step 3) | 2.5% | 3.5% |
   | Six, 3.1 to 4.2 m from the camera | 0.2% | 0.8% |

   The slot stops the first row. It does not cure the rows after it: one far cross
   with the rest near still leaves this, and a larger value would only refuse more
   layouts. Is 3 m what you want?

## Tests

```powershell
python -B -m unittest discover -s tools/den-register -v
```

They use synthetic scenes and a page on 127.0.0.1 only, with no camera and no
network. They take about 30 seconds.

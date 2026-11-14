# RGB clock and head mask at the Den sofa

Use the owner’s Windows workstation in room light with the current OAK-D position, the existing detector venv and the den-take token. The capture reads only `/calibration` and `/frame.npz`. The annotation page and scoring run locally.

## Simple sofa check

Use `--camera-relative` to score the head and upper-neck mask in the camera image using its own calibration. This mode uses fixed trial settings from the generated RGB body test and compares reported RGB/depth timestamps against the existing 67 ms pairing limit. Room registration and optical clock qualification are outside the scope of this check.

Paste this block in PowerShell. The ten-second countdown gives you time to sit with your book. Follow the spoken cues for reading, turning left and back, turning right and back, reclining and reaching. Keep doing each motion until the next cue. Capture takes about 50 seconds.

```powershell
$Py = Join-Path $env:USERPROFILE 'ml\den-detector\Scripts\python.exe'
$Tool = 'C:\Claude\home\.worktrees\den-measured-body\tools\den-live\sofa_qualify.py'
$Local = Join-Path $env:LOCALAPPDATA 'DenSofaQualification'
$Stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$Session = Join-Path $Local "sofa-$Stamp"
& {
    & $Py -B $Tool capture $Session --camera-relative
    if ($LASTEXITCODE -ne 0) { throw 'Capture stopped. Check the first error above.' }
    & $Py -B $Tool prepare $Session
    if ($LASTEXITCODE -ne 0) { throw 'Preparation stopped. Check the first error above.' }
    Write-Host "Save mask-annotations.json in $Session"
    Start-Process -FilePath (Join-Path $Session 'mark-head-and-neck.html')
}
```

For each image, draw a box around the complete head including hair, ears and chin. Draw another around the full upper neck from chin to shoulder line. Choose **Cannot bound** when you cannot see either region well enough. Download `mask-annotations.json` and save or move it into the printed session folder. Then paste this block in the same PowerShell window.

```powershell
& {
    & $Py -B $Tool score $Session --annotations (Join-Path $Session 'mask-annotations.json')
    if ($LASTEXITCODE -notin @(0, 1)) { throw 'Scoring stopped. Check the first error above.' }
    Get-Content -LiteralPath (Join-Path $Session 'qualification.json')
}
```

`camera_trial_pass` means the sampled mask and timestamp pairing both passed. `camera_trial_open` means at least one check did not pass. The report lists covered, withheld and undercovered samples per cue. The optical clock remains unmeasured in either case.

## Capture with room registration and a candidate profile

Use the existing detector venv, its pinned models and the existing camera token. Set up the candidate profile before capture. If one is already prepared, point `$Candidate` at it and skip the `profile-template` and `notepad` lines. Otherwise fill the template with the measured/proposed bounds for the installed camera and the person being tested. Use the planned witness ID as `clock_evidence_id`. The template gets the registration and scan IDs from the current local room model.

```powershell
$Py = Join-Path $env:USERPROFILE 'ml\den-detector\Scripts\python.exe'
$Tool = 'C:\Claude\home\.worktrees\den-measured-body\tools\den-live\sofa_qualify.py'
$Local = Join-Path $env:LOCALAPPDATA 'DenSofaQualification'
$Stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$Session = Join-Path $Local "sofa-$Stamp"
$Candidate = Join-Path $Local "candidate-$Stamp.json"
& $Py -B $Tool profile-template $Candidate
notepad.exe $Candidate
```

The `head` fields take metres, pixels, metres per second and seconds as named. Set the head radius around the mean of the five face landmarks and the neck radius around the segment to the shoulder midpoint. Supply the maximum supported face-to-shoulder distance, residual alignment and localization margins, head speed bound, maximum full-exposure separation and a valid depth fraction. Keep these settings fixed for the session.

Paste the next block. There is a ten-second countdown. Follow the spoken cues for reading, turning both ways, reclining and reaching. Capture saves three distinct bundles per cue in about 50 seconds. For the optional combined clock-and-mask score, start the timing setup described below before capture.

```powershell
& $Py -B $Tool capture $Session --profile $Candidate
if ($LASTEXITCODE -ne 0) { throw 'Capture incomplete. Use a new session directory.' }
& $Py -B $Tool prepare $Session
if ($LASTEXITCODE -ne 0) { throw 'Preparation failed.' }
Start-Process -FilePath (Join-Path $Session 'mark-head-and-neck.html')
```

## Mark and score

Draw the complete head, including hair, ears and chin, then the full upper neck from chin to shoulder line on each left image. Choose **Cannot bound** when cropped, occluded, overlapping another person or uncertain. Save the downloaded file as `mask-annotations.json` inside `$Session`.

Score the session. This uses a completed witness file when one exists.

```powershell
$ScoreArgs = @('score', $Session, '--annotations', (Join-Path $Session 'mask-annotations.json'))
$Witness = Join-Path $Session 'clock-witness.json'
if (Test-Path -LiteralPath $Witness -PathType Leaf) { $ScoreArgs += @('--witness', $Witness) }
& $Py -B $Tool @ScoreArgs
if ($LASTEXITCODE -notin @(0, 1)) { throw 'Scoring did not complete.' }
Get-Content -LiteralPath (Join-Path $Session 'qualification.json')
```

## Optional optical clock evidence

The current combined scorer needs independent timing from the same captured frames. One setup is a pulsed LED target visible across image rows in all three sensors and a photodiode or hardware timing logger that timestamps the light transitions. The measurement needs a bounded conversion into the camera SDK clock and must cover the first through last exposed row. It also needs bounds on the SDK-to-camera and camera-to-receiver relative clock rates. A phone flash or HTTP arrival time alone supplies neither the exposure interval nor the clock conversion.

Copy `clock-witness-template.json` to `clock-witness.json` in the session and fill it from that evidence. Keep the exact `frame_key` and per-plane `sequence` values. Set `basis` to `independent_acquisition_bounds` and use the candidate’s `clock_evidence_id` as `evidence_id`. For each `rgb`, `left` and `right`, set `start_lower_s` and `end_upper_s` to conservative acquisition endpoints in DepthAI SDK host-monotonic seconds. Set `clock_drift_ppm` and `receiver_drift_ppm` to the measured integer rate bounds. The camera metadata in the template identifies the exposures. Run the score command again after saving the witness.

```powershell
$Witness = Join-Path $Session 'clock-witness.json'
Copy-Item -LiteralPath (Join-Path $Session 'clock-witness-template.json') -Destination $Witness
notepad.exe $Witness
```

`sampled_session_pass` requires a complete exact-key clock witness within the existing 67 ms timestamp pairing limit and the candidate’s full-exposure separation bound. The body lane’s actual exclusion must fully cover both markings on every scored visible frame where it produced a mask. Each cue needs a covered frame. The report counts withheld frames and any leaks. Exit `0` means the sampled session passed, `1` means evidence remains open and `2` means the command stopped on an input or capture error. This checks the sampled lighting, pose and motion range. Runtime receipt issuance, the live one-second figure deadline and sustained coverage are later live checks.

## Rollback and cleanup

Press `Ctrl+C` to stop capture or scoring and close the annotation tab. There is no host rollback step. To discard this workstation session, run the block below in the same PowerShell. Delete any copy of the annotation download outside the session separately. The optional template you generated sits at `$Candidate`.

```powershell
if (Test-Path -LiteralPath $Session -PathType Container) {
    $ResolvedLocal = (Resolve-Path -LiteralPath $Local).ProviderPath
    $Target = Get-Item -LiteralPath $Session -Force
    if ($Target.Parent.FullName -ne $ResolvedLocal -or
        $Target.Name -notmatch '^sofa-\d{8}T\d{9}Z$' -or
        ($Target.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Unexpected session path.' }
    Remove-Item -LiteralPath $Target.FullName -Recurse -Force
}
```

If you generated the optional profile template and want to discard it, remove only that file.

```powershell
$GeneratedCandidate = Join-Path $Local "candidate-$Stamp.json"
if (Test-Path -LiteralPath $GeneratedCandidate -PathType Leaf) {
    Remove-Item -LiteralPath $GeneratedCandidate
}
```

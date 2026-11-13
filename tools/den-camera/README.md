# Den camera releases

`oak-den.service` on `victoria-den-jetson` runs `capture.py` from a release directory under
`~/victoria-camera-pilot/` of its service account (`<account>` below). Perception-lab pins that script by path and SHA256 (`CAMERA_SOURCE` and
`CAMERA_SHA` in `stack/services/perception-lab/bundle_rgb_input.py`), so a camera change is a repo change,
a pin update and a release switch. Never edit a release in place. The repo copy of each release is
`tools/fixtures/installed-oak-capture-<date>.py`.

| Release | Repo copy | Change |
| --- | --- | --- |
| `pilot-20260926` | `installed-oak-capture-20261016.py` | Causal JPEG timing pilot |
| `pilot-20261111-depth` | `installed-oak-capture-20261111.py` | Measured depth correction (`DEPTH_CORRECTION`) |

Keep earlier release directories in place: perception-lab releases already on the Jetson read their own
pinned `capture.py` by path.

## Depth correction

The camera multiplies `depth_mm` by a measured factor only while the device holds the EEPROM calibration
the factor was measured with. `/calibration` and every bundle's metadata declare it as `depth_correction`,
with `applied` true or false. After a recalibration the factor switches itself off. Re-measure, then set a
new factor and calibration hash in a new release.

`depth_tape_check.py` measures the floor plane (lens height) and the facing wall's plane (square distance)
through the running service. It reads depth only, prints numbers only and saves nothing. Its last line gives
the factor that would split both tapes evenly, relative to the depth as served:

```powershell
scp tools/den-camera/depth_tape_check.py <account>@victoria-den-jetson:victoria-camera-pilot/
ssh <account>@victoria-den-jetson "~/victoria-camera/venv/bin/python -I ~/victoria-camera-pilot/depth_tape_check.py --captures 8"
```

## Ship a release

1. Stage it as the service user (no sudo, nothing restarts). Copy the script and this switcher over, then
   build the release from the running release's dependencies:

   ```powershell
   scp tools/fixtures/installed-oak-capture-20261111.py tools/den-camera/switch-capture-release.sh <account>@victoria-den-jetson:victoria-camera-pilot/
   ```

   ```bash
   cd ~/victoria-camera-pilot && mkdir -m 700 pilot-20261111-depth
   cp -a pilot-20260926/dependencies pilot-20260926/wheels pilot-20260926/requirements.txt pilot-20261111-depth/
   install -m 600 installed-oak-capture-20261111.py pilot-20261111-depth/capture.py && rm installed-oak-capture-20261111.py
   sha256sum pilot-20261111-depth/capture.py   # must equal CAMERA_SHA
   ```

2. Switch (owner, sudo). `check` changes nothing. `switch` swaps only the release directory in the drop-in's
   `ExecStart` and `PYTHONPATH`, then restarts oak-den. If oak-den isn't healthy within 120 s, it rolls back
   by itself and prints the rollback command:

   ```powershell
   ssh -t <account>@victoria-den-jetson "sudo bash ~/victoria-camera-pilot/switch-capture-release.sh check pilot-20261111-depth <CAMERA_SHA>"
   ssh -t <account>@victoria-den-jetson "sudo bash ~/victoria-camera-pilot/switch-capture-release.sh switch pilot-20261111-depth <CAMERA_SHA>"
   ```

3. Verify with `depth_tape_check.py`: `declared_depth_correction.applied` must be true, and both tape errors
   must be within a few percent.

4. Deploy perception-lab as usual so its pin follows the new release.

The switched drop-in is owned by `switch-capture-release.sh`; `pilot-service.py` refuses to touch it.

## Measured body capture timing

`build_body_clock_release.py` generates an additive camera release from the byte-exact depth-correction fixture. It leaves the installed fixture and its existing consumers' source pin intact.

```powershell
python -B tools/den-camera/build_body_clock_release.py tools/fixtures/installed-oak-capture-20261111.py .tmp/body-camera-capture.py
python -B tools/den-camera/test_body_clock_release.py
```

The output path must be new. Record the printed SHA256 for the staged release. The offline test exercises the generated capture code, including the HTTP handler, with synthetic timestamps.

The camera release issues a capture-age receipt only when it has a qualified private clock profile. Use a protected JSON file and the paired `--body-clock-profile` and `--body-clock-profile-uid` camera arguments. The switcher preserves arguments when it changes the release directory, so add these arguments through the existing owner-managed service configuration during the coordinated rollout.

The profile uses schema `den-body-clock-profile-1` and names an `evidence_id`, the exact `device_id`, the `depthai_version`, the corrected `calibration_sha256`, and the configured sensor `fps`. Use the same evidence ID in the den-live provider profile. Set `clock_drift_ppm` to a validated bound for the SDK-to-camera-host relative clock rate and `receiver_drift_ppm` to a validated bound for the camera-to-receiver elapsed-clock rate conversion. For each of `rgb` and `depth`, supply `before_stamp_s` and `after_stamp_s` enclosing the entire contributing exposure interval around that plane's SDK host timestamp. The bounds need to include synchronization and readout uncertainty, operating auto exposure, and both stereo acquisitions for the depth plane. Verify them for the installed device, SDK, pipeline and lighting range. The camera packaging time and arrival timing cannot establish these values.

The authenticated bundle response returns `X-Den-Body-Capture-Age` only with exact RGB/depth identity and valid capture bounds. Missing or incompatible timing disables figures while the existing camera outputs continue. This is the RGB pose path. An infrared pose path needs its own calibrated and timed evidence.

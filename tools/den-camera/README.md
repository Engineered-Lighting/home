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
| `pilot-20261114-slim` | `installed-oak-capture-20261114.py` | `/frame-slim.npz` (`den-camera-slim-1`) for den-live across the link |

Keep earlier release directories in place: perception-lab releases already on the Jetson read their own
pinned `capture.py` by path.

## The slim bundle (`pilot-20261114-slim`)

den-live on LA pulls the newest bundle across the routed link between the homes, a round trip of about 47 ms. A
new TCP connection for every pull, and a 650–770 kB bundle, held it to about 2 fps, with a p50 fetch of 463 ms. It
reads only `depth_mm`, `rgb_nv12` and the metadata. `/frame-slim.npz` is that frame without `left` and `right`
(about half the bytes, lossless). It's compressed in about 20 ms and published before the full bundle (about
110 ms), and it keeps its connection open: one round trip per frame, and `304` with no body while the frame is the
same (`If-None-Match`). Its metadata is the full bundle's, plus `bundle_schema: den-camera-slim-1` and
`bundle_omits`. Every other answer, on every path (`/frame.npz` included) and for every error, is exactly
`pilot-20261111-depth`'s: HTTP/1.0, then close. A slim body the client stops reading closes its connection.

- **The Lab's pin stays at `pilot-20261111-depth`.** This release changes no clock check
  (`test_slim_release.py` proves it), so keep that directory: the Lab reads it by path. No perception-lab deploy.
- **den-live falls back by itself.** On a camera without the slim path (`404`, for example after a rollback), it
  pulls `/frame.npz` as before and asks for the slim path again a minute later.
- **Measure it offline** on any recorded bundle, or on the camera host with a bundle piped in:
  `python -I -B tools/den-camera/slim_bundle_bench.py <bundle.npz>`.
- **Ship it** with "Ship a release" below, which is written for this release.

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

These steps ship `pilot-20261114-slim`; for another release, change its names. Its `<SHA256>` is the repo copy's:
`(Get-FileHash tools\fixtures\installed-oak-capture-20261114.py).Hash.ToLower()` in PowerShell.

1. Stage it as the service user (no sudo, nothing restarts). Copy the script and this switcher over, then
   build the release from the running release's dependencies:

   ```powershell
   scp tools/fixtures/installed-oak-capture-20261114.py tools/den-camera/switch-capture-release.sh <account>@victoria-den-jetson:victoria-camera-pilot/
   ```

   ```bash
   cd ~/victoria-camera-pilot && mkdir -m 700 pilot-20261114-slim
   cp -a pilot-20261111-depth/dependencies pilot-20261111-depth/wheels pilot-20261111-depth/requirements.txt pilot-20261114-slim/
   install -m 600 installed-oak-capture-20261114.py pilot-20261114-slim/capture.py && rm installed-oak-capture-20261114.py
   sha256sum pilot-20261114-slim/capture.py   # must equal <SHA256>
   ```

2. Switch (owner, sudo). `check` changes nothing. `switch` swaps only the release directory in the drop-in's
   `ExecStart` and `PYTHONPATH`, then restarts oak-den. If oak-den isn't healthy within 120 s, it rolls back
   by itself and prints the rollback command:

   ```powershell
   ssh -t <account>@victoria-den-jetson "sudo bash ~/victoria-camera-pilot/switch-capture-release.sh check pilot-20261114-slim <SHA256>"
   ssh -t <account>@victoria-den-jetson "sudo bash ~/victoria-camera-pilot/switch-capture-release.sh switch pilot-20261114-slim <SHA256>"
   ```

3. Verify with `depth_tape_check.py`: `declared_depth_correction.applied` must be true, and both tape errors
   must be within a few percent. For `pilot-20261114-slim`, den-live's fetch time (`timings_ms.fetch` in its
   health) also drops to about 0.1–0.2 s within a minute of the switch.

4. Deploy perception-lab so its pin follows the new release, but only when the release changes a clock check.
   `pilot-20261114-slim` changes none, and the Lab keeps `pilot-20261111-depth`, so skip this step.

The switched drop-in is owned by `switch-capture-release.sh`; `pilot-service.py` refuses to touch it.

#!/usr/bin/env python3
"""model.py --session on a session whose all-off frames repeat exactly (zero all-off noise).

    python3 -I probe_zero_noise_partial_write.py <branch-checkout-root> [daylight]

Runs one complete probe session against the tool's own fakes on 127.0.0.1 (synthetic.FakeHA with
den_lighting 1.0.4, synthetic.FakeCamera with code_noise 0 and a den with steady daylight, as a
static dark den under ISP temporal denoise can give), writes a passing Stage 0 response.json with
the synthetic meter-norm/v1 parameters, then runs model.main(["--session", ..., "--stage0", ...])
with METHOD's Stage 1 slots closed at their proposed values (model.load_stage1 patched; the
branch's METHOD still has them open, so the real CLI refuses first). It reports the exit code,
what main printed, and which of readings.json / light-model.json / score.json exist afterwards,
then runs main a second time. FAIL if main says "Nothing was written" while files were written,
or if light-model.json (with a verdict) exists without score.json. Temp dirs only.
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
DAYLIGHT = float(sys.argv[2]) if len(sys.argv) > 2 else 0.05
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import guard  # noqa: E402
import meter  # noqa: E402
import model  # noqa: E402
import probe  # noqa: E402
import sweep  # noqa: E402
import synthetic  # noqa: E402

HA_TOKEN, CAM_TOKEN = "h" * 32, "c" * 32
CORRUPT = False
SNAPSHOT = {"light.den_front_left": guard.scene(True, 180, 2700), "light.front_right": guard.scene(False, 120, 3000),
            "light.den_rear_left": guard.scene(True, 90, 2500), "light.den_rear_right": guard.scene(True, 200, 4000)}
STORE = {"version": 1, "minor_version": 1, "key": "den_lighting.state",
         "data": {"version": 1, "enabled": True, "gaming_enabled": False, "gaming": False, "normal": SNAPSHOT,
                  "snapshot": {}, "holds": {}}}


def main():
    m1 = model.load_stage1().proposed().closed(
        block_px=40, reduce_pixel_step=10, vacancy_s=0.2, poll_s=0.02, start_wait_limit_s=10, settle_limit_s=2,
        frames_per_capture=2, resume_check_s=3, off_check_s=0.3, walkthrough_limit_s=3, unavailable_wait_s=1.0,
        ha_timeout_s=5, frame_timeout_s=3, hold_check_wait_s=0.05)
    base0 = meter.load_method()
    m0 = meter.Method({**base0.values, "pixel_step": 10, "poll_s": 0.005}, (), base0.file_sha256, base0.block_sha256)
    ha = synthetic.FakeHA(token=HA_TOKEN, store=STORE, speed=600)
    den = synthetic.SyntheticDen(block_px=40, daylight=DAYLIGHT, drift=0.0)
    cam = synthetic.FakeCamera(den, ha, token=CAM_TOKEN, step=10, code_noise=0.0)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "s1"
        folder.mkdir()
        try:
            s = probe.Session(folder, probe.HA(ha.url, HA_TOKEN, 5), sweep.Camera(cam.url, CAM_TOKEN), m1, m0,
                              say=lambda *_: None)
            outcome, _ = s.run(json.dumps(STORE))
        finally:
            ha.close()
            cam.close()
        print(f"RESULT probe session outcome: {outcome}")
        stage0 = Path(tmp) / "stage0" / "sweep-1"
        stage0.mkdir(parents=True)
        params, digest = synthetic.meter_norm_for(m0)
        (stage0 / "response.json").write_text(json.dumps(
            {"outcome": "pass", "meter_norm": {"parameters": params, "parameters_sha256": digest}}))
        model.load_stage1 = lambda path=None: m1
        for attempt in (1, 2):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = model.main(["--session", str(folder), "--stage0", str(stage0)])
                except Exception as exc:  # noqa: BLE001
                    code = f"uncaught {type(exc).__name__}: {exc}"
            present = [n for n in ("readings.json", "light-model.json", "score.json") if (folder / n).exists()]
            print(f"RESULT main attempt {attempt}: exit={code} stdout={out.getvalue().strip()!r} "
                  f"stderr={err.getvalue().strip()!r}")
            print(f"RESULT files after attempt {attempt}: {present}")
            if attempt == 1:
                first_present, first_err = present, err.getvalue()
                if (folder / "readings.json").exists():
                    r = json.loads((folder / "readings.json").read_text())
                    spreads = [v for st in r["steps"] if st["kind"] == "all_off" for v in st["spread"] if v is not None]
                    print(f"RESULT all-off spreads: {len(spreads)} values, max {max(spreads) if spreads else None}")
                if (folder / "score.json").exists():
                    raw = (folder / "score.json").read_text()
                    try:
                        json.loads(raw)
                        print("RESULT score.json parses as JSON")
                    except ValueError as exc:
                        global CORRUPT
                        CORRUPT = True
                        print(f"RESULT score.json is NOT valid JSON ({len(raw)} bytes, ends {raw[-60:]!r}): {exc}")
                if (folder / "light-model.json").exists():
                    lm = json.loads((folder / "light-model.json").read_text())
                    print(f"RESULT light-model.json error block: { {k: lm['error'][k] for k in ('median_pct', 'p90_pct', 'verdict')} }")
    fails = 0
    if CORRUPT:
        print("FAIL score.json was left on disk as truncated, unparseable JSON")
        fails += 1
    if "Nothing was written" in first_err and first_present:
        print(f"FAIL main printed 'Nothing was written' but left {first_present}")
        fails += 1
    if "light-model.json" in first_present and "score.json" not in first_present:
        print("FAIL light-model.json (carrying a verdict) was written without score.json")
        fails += 1
    print("RESULT", "PASS (no partial write)" if not fails else f"{fails} FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

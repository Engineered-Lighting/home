#!/usr/bin/env python3
"""probe_depth_gap_boundary: the 67 ms depth staleness rule, end to end through a synthetic R take.

What it checks
    schemas.json Observation.depth_ts: "If it is more than one stereo frame
    (67 ms) from capture_ts, the depth is not used, the plane fallback applies".
    The probe writes a synthetic den-take-1 take (synth.py's writer, den-take's
    own TakeWriter) whose bundles pair each RGB frame with a depth frame
    66.9 ms / 67.0 ms / 67.1 ms older or newer than it, replays it with
    replay.replay (unscored, the default METHOD.md), and checks per record:
      gap <= 67 ms  -> freshness.depth "used", joints lifted by stereo
      gap  > 67 ms  -> freshness.depth "too_old", no stereo joint, every
                       lifted joint plane:<h>, depth_gap_s = the gap
    and that capture_ts - depth_ts equals the constructed gap (both Unix).
    It also calls observe.depth_state directly at the same gaps.

How to run
    python3 probe_depth_gap_boundary.py <branch checkout root>

Synthetic data only, in a temporary folder that is removed afterwards.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-observe"))
import detections  # noqa: E402
import fake_detector  # noqa: E402
import method as method_file  # noqa: E402
import observe  # noqa: E402
import replay  # noqa: E402
import synth  # noqa: E402

GAPS_MS = [66.9, 67.0, 67.1, -66.9, -67.1]  # positive: depth older than RGB
work = Path(tempfile.mkdtemp(prefix="probe-depth-gap-"))
fails = 0
try:
    scene = synth.Scene()
    depth = scene.depth_mm()
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - gap / 1000.0) for i, gap in enumerate(GAPS_MS)]
    take = synth.make_take(work / "take", pairs, depth)
    synth.write_check(take)
    room = work / "room.json"
    room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    rows = [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels) for i, (t, _) in enumerate(pairs)]
    detections.write_rows(work / "rows.jsonl", rows)
    replay.replay(take, room, work / "out", detections_path=work / "rows.jsonl", log=lambda *a: None)
    records = [json.loads(x) for x in (work / "out" / "observations.jsonl").read_text(encoding="utf-8").splitlines()]
    for gap, obs in zip(GAPS_MS, records):
        want = "used" if abs(gap) <= 67.0 else "too_old"
        got = obs["freshness"]["depth"]
        lifts = sorted({e["lift"].split(":")[0] for e in obs["joints_room"]})
        stereo = any(e["lift"] == "stereo" for e in obs["joints_room"])
        lifts_ok = stereo if want == "used" else (not stereo and all(
            e["lift"].startswith("plane:") or e["status"] == "unsupported" for e in obs["joints_room"]))
        diff_ms = (obs["capture_ts"] - obs["depth_ts"]) * 1000
        ok = got == want and lifts_ok and abs(diff_ms - gap) < 0.01 and abs(obs["freshness"]["depth_gap_s"] * 1000 - gap) < 0.01
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} end-to-end gap {gap:+.1f} ms: depth {got!r} (want {want!r}), lifts {lifts}, "
              f"capture_ts - depth_ts = {diff_ms:+.3f} ms, depth_gap_s = {obs['freshness']['depth_gap_s']}")
    # direct, on the function
    settings = observe.Settings(method_file.load(root / "tools" / "den-observe" / "METHOD.md"))
    from takes import Depth  # noqa: E402
    import array
    mm = array.array("H"); mm.frombytes(bytes(640 * 400 * 2)); mm[5] = 1000
    d = Depth(640, 400, mm, 0.0, 1)
    for gap in GAPS_MS:
        t = 5000.25
        state, g = observe.depth_state({"frame": {"capture_ts": t}}, {"t_depth": t - gap / 1000}, d, settings)
        want = "used" if abs(gap) <= 67.0 else "too_old"
        ok = state == want
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'} depth_state gap {gap:+.1f} ms -> {state!r} (want {want!r}, computed gap {g * 1000:.6f} ms)")
finally:
    shutil.rmtree(work, ignore_errors=True)
print(f"RESULT {'FAIL' if fails else 'PASS'}: {fails} boundary cases wrong")

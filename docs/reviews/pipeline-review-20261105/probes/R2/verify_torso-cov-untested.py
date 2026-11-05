#!/usr/bin/env python3
"""verify_torso-cov-untested: the torso room_position covariance today vs the two surviving mutants.

What it checks (independent of finding torso-cov-untested's runner)
    Replays a synthetic all-stereo take (the suite's own synth scene, the
    default METHOD.md, a 3 cm registration bound) and, per record:
      - today's room_position.cov vs sum_j(cov_j - Sigma_cal)/n^2 + Sigma_cal
        rebuilt from the four torso joints_room entries (should match);
      - what M07 (Sigma_cal dropped) and M06 (independent part / n, not n^2)
        would have written, as the ratio of the diagonal to today's.
    The surviving-mutant table itself comes from mutate_den_observe_validate.py
    (re-run: M06 and M07 both survive the whole suite).

How to run
    python3 -B verify_torso-cov-untested.py <branch checkout root>

Synthetic data only, in a temporary folder removed afterwards. Nothing connects anywhere.
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
import replay  # noqa: E402
import synth  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="verify-torso-cov-"))
try:
    scene = synth.Scene()
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(2)]
    take = synth.make_take(work / "take", pairs, scene.depth_mm())
    synth.write_check(take)
    room = work / "room.json"
    room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    detections.write_rows(work / "rows.jsonl", [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels)
                                                for i, (t, _) in enumerate(pairs)])
    replay.replay(take, room, work / "out", detections_path=work / "rows.jsonl", log=lambda *a: None)
    cal = 0.03 ** 2
    for line in (work / "out" / "observations.jsonl").read_text(encoding="utf-8").splitlines():
        obs = json.loads(line)
        torso = [e for e in obs["joints_room"] if e["j"] in (5, 6, 11, 12) and e["lift"] == "stereo"]
        n = len(torso)
        indep = [[sum(e["cov"][i][j] - (cal if i == j else 0.0) for e in torso) for j in range(3)] for i in range(3)]
        want = [[indep[i][j] / n ** 2 + (cal if i == j else 0.0) for j in range(3)] for i in range(3)]
        got = obs["room_position"]["cov"]
        worst = max(abs(got[i][j] - want[i][j]) for i in range(3) for j in range(3))
        m07 = [got[i][i] - cal for i in range(3)]
        m06 = [indep[i][i] / n + cal for i in range(3)]
        print(f"RESULT {obs['obs_id']}: n={n} stereo torso joints; |cov - formula| max {worst:.1e}; "
              f"diag today {[round(got[i][i], 6) for i in range(3)]}")
        print(f"RESULT   M07 (no Sigma_cal) diag/today {[round(m07[i] / got[i][i], 3) for i in range(3)]}; "
              f"M06 (/n) diag/today {[round(m06[i] / got[i][i], 3) for i in range(3)]}")
finally:
    shutil.rmtree(work, ignore_errors=True)

#!/usr/bin/env python3
"""Verify: model.py scores a session under a METHOD-V1 file edited after the session, without refusing.

    python3 -I verify_method_sha_not_checked_at_scoring.py <branch-checkout-root>

1. Writes METHOD copy A in a temp dir: the branch's METHOD-V1.md with every open Stage 1 slot closed at
   its proposed value (as the owner's closing commit would). Copy B = A with one scoring number edited
   (noise_floor_multiple 5 -> 60). Both are parsed by model.load_stage1(path), so their file and block
   sha256 are the real ones; the same fast test timings are then applied to both with .closed().
2. Runs one complete probe session under A against the tool's fakes on 127.0.0.1 (synthetic.FakeHA,
   synthetic.FakeCamera); session.json records A's method_sha256 / method_stage1_block_sha256.
3. model.main(--session, --stage0) under A, prints the result; deletes the three outputs (as an owner
   would to re-run); model.main again under B (model.load_stage1 patched to return B).
FAIL if the run under B is not refused although session.json's METHOD sha256s differ from B's.
Synthetic data only; temp dirs only; no other host is contacted.
"""
import contextlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import guard  # noqa: E402
import meter  # noqa: E402
import model  # noqa: E402
import probe  # noqa: E402
import sweep  # noqa: E402
import synthetic  # noqa: E402

HA_TOKEN, CAM_TOKEN = "h" * 32, "c" * 32
SNAPSHOT = {"light.den_front_left": guard.scene(True, 180, 2700), "light.front_right": guard.scene(False, 120, 3000),
            "light.den_rear_left": guard.scene(True, 90, 2500), "light.den_rear_right": guard.scene(True, 200, 4000)}
STORE = {"version": 1, "minor_version": 1, "key": "den_lighting.state",
         "data": {"version": 1, "enabled": True, "gaming_enabled": False, "gaming": False, "normal": SNAPSHOT,
                  "snapshot": {}, "holds": {}}}
FAST = dict(block_px=40, reduce_pixel_step=10, vacancy_s=0.2, poll_s=0.02, start_wait_limit_s=10, settle_limit_s=2,
            frames_per_capture=2, resume_check_s=3, off_check_s=0.3, walkthrough_limit_s=3, unavailable_wait_s=1.0,
            ha_timeout_s=5, frame_timeout_s=3, hold_check_wait_s=0.05)
OPEN = re.compile(r"^(\w+) = open(\s+)# open, proposed (\[[^\]]*\]|-?[0-9]+(?:\.[0-9]+)?)", re.M)


def score(folder, stage0, m1):
    model.load_stage1 = lambda path=None: m1
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = model.main(["--session", str(folder), "--stage0", str(stage0)])
    return code, out.getvalue().strip(), err.getvalue().strip()


def main():
    text = (ROOT / "tools" / "den-light" / "METHOD-V1.md").read_text(encoding="utf-8")
    text_a = OPEN.sub(lambda m: f"{m[1]} = {m[3]}{m[2]}# closed at the proposed value", text)
    text_b = text_a.replace("noise_floor_multiple = 5 ", "noise_floor_multiple = 60", 1)
    assert text_b != text_a
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "A.md").write_text(text_a, encoding="utf-8")
        (tmp / "B.md").write_text(text_b, encoding="utf-8")
        load = model.load_stage1
        ma, mb = load(tmp / "A.md"), load(tmp / "B.md")
        assert not ma.open and not mb.open, (ma.open, mb.open)
        m1a, m1b = ma.closed(**FAST), mb.closed(**FAST)
        print(f"RESULT METHOD A sha256 {m1a.file_sha256[:12]} block {m1a.block_sha256[:12]}; "
              f"B sha256 {m1b.file_sha256[:12]} block {m1b.block_sha256[:12]}")
        base0 = meter.load_method()
        m0 = meter.Method({**base0.values, "pixel_step": 10, "poll_s": 0.005}, (), base0.file_sha256,
                          base0.block_sha256)
        ha = synthetic.FakeHA(token=HA_TOKEN, store=STORE, speed=600)
        cam = synthetic.FakeCamera(synthetic.SyntheticDen(block_px=40), ha, token=CAM_TOKEN, step=10)
        folder = tmp / "s1"
        folder.mkdir()
        try:
            s = probe.Session(folder, probe.HA(ha.url, HA_TOKEN, 5), sweep.Camera(cam.url, CAM_TOKEN), m1a, m0,
                              say=lambda *_: None)
            outcome, _ = s.run(json.dumps(STORE))
        finally:
            ha.close()
            cam.close()
        record = json.loads((folder / "session.json").read_text())
        print(f"RESULT session outcome={outcome}; session.json method_sha256 {record['method_sha256'][:12]} "
              f"block {record['method_stage1_block_sha256'][:12]}")
        stage0 = tmp / "stage0" / "sweep-1"
        stage0.mkdir(parents=True)
        params, digest = synthetic.meter_norm_for(m0)
        (stage0 / "response.json").write_text(json.dumps(
            {"outcome": "pass", "meter_norm": {"parameters": params, "parameters_sha256": digest}}))
        code_a, out_a, err_a = score(folder, stage0, m1a)
        sa = json.loads((folder / "score.json").read_text()) if (folder / "score.json").exists() else {}
        print(f"RESULT under A: exit={code_a} out={out_a.splitlines()[:1]} err={err_a!r}")
        for name in ("readings.json", "light-model.json", "score.json"):
            (folder / name).unlink(missing_ok=True)
        code_b, out_b, err_b = score(folder, stage0, m1b)
        sb = json.loads((folder / "score.json").read_text()) if (folder / "score.json").exists() else {}
        print(f"RESULT under B (edited METHOD, same session): exit={code_b} out={out_b.splitlines()[:1]} err={err_b!r}")
        if sa and sb:
            print(f"RESULT excluded_count A={sa['error']['excluded_count']} B={sb['error']['excluded_count']}; "
                  f"count A={sa['error']['count']} B={sb['error']['count']}")
            print(f"RESULT score.json method_sha256 under B {sb.get('method_sha256', '')[:12]} vs session.json "
                  f"{record['method_sha256'][:12]}")
        if code_b != 2 and record["method_sha256"] != m1b.file_sha256:
            print("FAIL model.py scored the session under a METHOD whose sha256 differs from the one session.json "
                  "recorded, with no refusal or flag")
            return 1
        print("PASS model.py refused the edited METHOD")
        return 0


if __name__ == "__main__":
    sys.exit(main())

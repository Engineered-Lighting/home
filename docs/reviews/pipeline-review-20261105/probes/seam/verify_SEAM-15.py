#!/usr/bin/env python3
"""verify_SEAM-15: UTF-16 detector rows (as PowerShell 5.1 '>' writes them) through den-observe replay.py.

    TMPDIR=<scratch dir> python3 -B verify_SEAM-15.py <rung-2-observe checkout>

Synthetic data only (den-observe's own synth take, synth RoomModel and fake
detector), temp dirs only, no network, python -B in every child.

Steps
 1. Baseline: UTF-8 rows -> replay.py (unscored) exit 0.
 2. The same rows re-encoded as PowerShell 5.1 '>' / Out-File writes them
    (UTF-16LE with a BOM, CRLF) -> exit code, last stderr lines, --out state.
 3. Scored variant: a temp copy of METHOD.md with every open slot closed at
    its proposed value, --scored --method <copy>: UTF-8 rows (gate verdict)
    and UTF-16 rows (crash) -> do both report exit 1?
 4. The RoomModel in UTF-16 -> exit code and message.
Prints PASS/FAIL/RESULT lines. "FAIL" here means the defect reproduced.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

SETUP = r'''
import json, sys
sys.dont_write_bytecode = True
obs, work = sys.argv[1], sys.argv[2]
sys.path.insert(0, obs)
sys.path.insert(0, obs.rsplit("/", 1)[0] + "/den-take")
from pathlib import Path
import synth, fake_detector, detections
work = Path(work)
scene = synth.Scene()
frames = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(6)]
folder = synth.make_take(work / "take", frames, scene.depth_mm())
synth.write_check(folder)
(work / "room-model.json").write_text(json.dumps(synth.room_model(), ensure_ascii=False), encoding="utf-8")
pixels = {name: scene.joint_rgb(name) for name in synth.JOINTS}
rows = [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels) for i, (t, _) in enumerate(frames)]
detections.write_rows(work / "rows-utf8.jsonl", rows)
print(json.dumps({"take": str(folder)}))
'''


def ps51(src, dst):
    """What PowerShell 5.1 `Get-Content src > dst` (Out-File default) writes: UTF-16LE, BOM, CRLF."""
    text = Path(src).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
    Path(dst).write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
    return dst


def replay(obs, *args):
    proc = subprocess.run([sys.executable, "-B", str(obs / "replay.py"), *map(str, args)], capture_output=True,
                          text=True)
    return proc.returncode, proc.stdout, proc.stderr


def tail(text, n=2):
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return " | ".join(lines[-n:])


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    obs = Path(sys.argv[1]).resolve() / "tools" / "den-observe"
    work = Path(tempfile.mkdtemp(prefix="verify-seam15-"))
    print(f"RESULT temp folder {work}")
    proc = subprocess.run([sys.executable, "-B", "-c", SETUP, str(obs), str(work)], capture_output=True, text=True)
    if proc.returncode != 0:
        print("FAIL setup\n" + proc.stderr[-2000:])
        return 2
    take = json.loads(proc.stdout.strip().splitlines()[-1])["take"]
    rm, rows8 = work / "room-model.json", work / "rows-utf8.jsonl"
    rows16 = ps51(rows8, work / "rows-utf16.jsonl")
    print(f"RESULT utf-16 rows file starts with bytes {rows16.read_bytes()[:4].hex()}")

    code, out, err = replay(obs, "--take", take, "--room-model", rm, "--detections", rows8, "--out", work / "o8")
    print(("PASS" if code == 0 else "FAIL") + f" step1 UTF-8 rows unscored: exit {code}: {tail(out, 1)}")

    out_dir = work / "o16"
    code16, out, err = replay(obs, "--take", take, "--room-model", rm, "--detections", rows16, "--out", out_dir)
    tb = "Traceback" in err
    contents = sorted(p.name for p in out_dir.iterdir()) if out_dir.exists() else "absent"
    print(("FAIL" if code16 == 1 and tb else "PASS") + f" step2 UTF-16 rows unscored: exit {code16}, traceback "
          f"{tb}: {tail(err)}; --out {contents}")

    method_text = (obs / "METHOD.md").read_text(encoding="utf-8")
    closed = re.sub(r"^(\w+) = open(\s+)# open, proposed (\S+)(.*)$", r"\1 = \3\2# closed for this probe\4",
                    method_text, flags=re.M)
    method_copy = work / "METHOD-closed.md"
    method_copy.write_text(closed, encoding="utf-8")
    code_s8, out, err = replay(obs, "--take", take, "--room-model", rm, "--detections", rows8, "--out", work / "s8",
                               "--scored", "--method", method_copy)
    print(f"RESULT step3 scored UTF-8 rows: exit {code_s8}: {tail(out or err, 1)}")
    code_s16, out, err = replay(obs, "--take", take, "--room-model", rm, "--detections", rows16, "--out",
                                work / "s16", "--scored", "--method", method_copy)
    print(("FAIL" if code_s16 == 1 else "PASS") + f" step3 scored UTF-16 rows: exit {code_s16}, traceback "
          f"{'Traceback' in err}: {tail(err)}")

    rm16 = ps51(rm, work / "room-model-utf16.json")
    code_r, out, err = replay(obs, "--take", take, "--room-model", rm16, "--detections", rows8, "--out", work / "r16")
    print(f"RESULT step4 UTF-16 RoomModel: exit {code_r}: {tail(err, 1)}")

    reproduced = code16 == 1 and tb
    print("RESULT SEAM-15 " + ("REPRODUCED: UTF-16 detector rows end in a traceback with exit 1, the code replay.py "
                               "documents as 'scored and the gate failed'" if reproduced else "NOT reproduced"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

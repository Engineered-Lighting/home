#!/usr/bin/env python3
"""Verifier (intent lens) for R1 findings interrupted-fit-orphan-entry and
mount-person-in-view-invalidates.

Part A (orphan entry): in a temp copy of tools/den-register with the two appends in
run_fit swapped (the finding's suggested "write the CalibrationRecord first"),
simulate a crash between them and re-run the fit. Prints whether the re-run can
ever write the RoomModel entry. Also shows that, in the unmodified tool, the orphan
entry's pose equals the re-run's (same inputs) and survives in the new record's
supersedes_value.

Part B (person in view): on the synthetic registration frame, compare the mount
signature with later frames of the same unmoved camera holding a person-sized block
(0.45 m wide, full frame height, fx 450) at several distances, two such people, and a
too-near (invalid depth) block over 60% of the frame, using the proposed values
(50 mm, 3 %, 0.5, 16 cells).

Synthetic data only; temp dirs only; contacts nothing.

    python3 -B verify_intent2_orphan_and_person.py <root of the rung-1-register checkout>
"""
import io
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()

SWAPPED = r'''
import io, json, re, sys, tempfile
from pathlib import Path
sys.path.insert(0, ".")
import method, records, register, synthetic, worksheet as ws
text = Path("REGISTER-V1.md").read_text(encoding="utf-8")
m = method.parse(re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda g: g[1] + " = " + g[2], text, flags=re.M))
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    depth, left, seen = synthetic.render()
    take = synthetic.write_take(tmp / "take", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
    work = tmp / "work"; work.mkdir()
    (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet()), encoding="utf-8")
    sheet = ws.load(work / "worksheet.json", need_positions=False)
    s = register.ClickSession(work, take, register.frame_number(take, None, work), sheet, m)
    for pid, (u, v, _) in seen.items():
        s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
    s.done()
    real, calls = records.append_jsonl, []
    def crash(path, record):
        calls.append(Path(path).name)
        if len(calls) == 2:
            raise OSError("simulated failure of the second append")
        real(path, record)
    records.append_jsonl = crash
    try:
        register.run_fit(work, take, m=m, now=lambda: 1_800_000_500.0, out=io.StringIO())
    except OSError as exc:
        print("OUT first fit:", exc, "after writing", calls[0])
    records.append_jsonl = real
    for attempt in (1, 2):
        code, report = register.run_fit(work, take, m=m, now=lambda: 1_800_000_600.0, out=io.StringIO())
        print("OUT re-run", attempt, report["result"], [r["reason"] for r in report["reasons"]])
    print("OUT room-model.jsonl exists:", (work / "room-model.jsonl").exists(),
          "| calibration records:", len(records.read_jsonl(work / "calibration-records.jsonl", "x")))
'''


def part_a():
    print("== Part A: the suggested fix (CalibrationRecord appended first) ==")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for rel in ("tools/den-register", "tools/den-take", "docs/architecture/live-v1"):
            shutil.copytree(ROOT / rel, tmp / rel, ignore=shutil.ignore_patterns("__pycache__"))
        path = tmp / "tools" / "den-register" / "register.py"
        text = path.read_text(encoding="utf-8")
        old = ("        records.append_jsonl(work / ROOM_MODEL, write[\"revision\"])\n"
               "        records.append_jsonl(work / CALIBRATIONS, write[\"record\"])\n")
        new = ("        records.append_jsonl(work / CALIBRATIONS, write[\"record\"])\n"
               "        records.append_jsonl(work / ROOM_MODEL, write[\"revision\"])\n")
        assert text.count(old) == 1
        path.write_text(text.replace(old, new), encoding="utf-8")
        snippet = tmp / "swapped.py"
        snippet.write_text(SWAPPED, encoding="utf-8")
        out = subprocess.run([sys.executable, "-B", str(snippet)], cwd=tmp / "tools" / "den-register",
                             capture_output=True, text=True, timeout=300)
        for line in out.stdout.splitlines():
            if line.startswith("OUT "):
                print("RESULT swapped:", line[4:])
        if out.returncode:
            print("ERROR", out.stderr.strip().splitlines()[-1])

    print("== Part A: the unmodified tool, orphan vs re-run ==")
    tool = ROOT / "tools" / "den-register"
    sys.path.insert(0, str(tool))
    import method, records, register, synthetic, worksheet as ws  # noqa: E401,E402
    text = (tool / "REGISTER-V1.md").read_text(encoding="utf-8")
    m = method.parse(re.sub(r"^(\w+) = open\s+# open, proposed (\S+).*$", lambda g: g[1] + " = " + g[2], text,
                            flags=re.M))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        depth, left, seen = synthetic.render()
        take = synthetic.write_take(tmp / "take", [synthetic.bundle(synthetic.metadata(), depth, left)] * 3)
        work = tmp / "work"
        work.mkdir()
        (work / "worksheet.json").write_text(json.dumps(synthetic.worksheet()), encoding="utf-8")
        sheet = ws.load(work / "worksheet.json", need_positions=False)
        s = register.ClickSession(work, take, register.frame_number(take, None, work), sheet, m)
        for pid, (u, v, _) in seen.items():
            s.click({"id": pid, "u": round(u, 1), "v": round(v, 1)})
        s.done()
        real, calls = records.append_jsonl, []

        def crash(path, record):
            calls.append(1)
            if len(calls) == 2:
                raise OSError("simulated failure of the second append")
            real(path, record)

        records.append_jsonl = crash
        register.method_file.load = lambda path=None: m   # main() would otherwise load the open-slot repo file
        try:
            register.main(["fit", str(work), "--take", str(take)], out=io.StringIO())
            print("RESULT main() returned normally")
        except OSError as exc:
            print("RESULT main() did not catch it: OSError escapes as a traceback:", exc)
        records.append_jsonl = real
        code, report = register.run_fit(work, take, m=m, now=lambda: 1_800_000_600.0, out=io.StringIO())
        models = records.read_jsonl(work / "room-model.jsonl", "x")
        cals = records.read_jsonl(work / "calibration-records.jsonl", "x")
        orphan = models[0]["cameras"][-1]
        second = models[-1]["cameras"][-1]
        print("RESULT re-run:", report["result"], report["transform_id"],
              "| orphan pose == re-run pose:", orphan["pose"]["room_from_camera"] == second["pose"]["room_from_camera"],
              "| new record supersedes_value == orphan pose:",
              cals[-1].get("supersedes_value") == orphan["pose"]["room_from_camera"])


def part_b():
    print("== Part B: person-sized blocks in front of an unmoved camera ==")
    tool = ROOT / "tools" / "den-register"
    sys.path.insert(0, str(tool))
    import scene, synthetic  # noqa: E401,E402
    depth, left, _ = synthetic.render()
    ref = scene.mount_signature(scene.read_bundle(synthetic.bundle(synthetic.metadata(), depth, left)))
    base = list(struct.unpack(f"<{640 * 400}H", depth))

    def later(blocks):
        values = list(base)
        for x0, x1, mm in blocks:
            for y in range(400):
                for x in range(max(0, x0), min(640, x1)):
                    values[y * 640 + x] = mm
        bundle = scene.read_bundle(synthetic.bundle(synthetic.metadata(), struct.pack(f"<{640 * 400}H", *values), left))
        return scene.check_mount(ref, scene.mount_signature(bundle), tolerance_mm=50, tolerance_fraction=0.03,
                                 changed_fraction=0.5, minimum_cells=16)

    def person(d_m, centre=320):
        w = int(round(0.45 * 450 / d_m))
        return (centre - w // 2, centre - w // 2 + w, int(d_m * 1000))

    cases = [("one person at 2.0 m", [person(2.0)]), ("one person at 1.2 m", [person(1.2)]),
             ("one person at 0.6 m", [person(0.6)]),
             ("two people side by side at 1.2 m", [person(1.2, 230), person(1.2, 410)]),
             ("reporter's block: 60% of width at 1.2 m (about 1.0 m wide)", [(0, 384, 1200)]),
             ("too near for depth (0) over 60% of width", [(0, 384, 0)]),
             ("too near for depth (0) over 80% of width", [(0, 512, 0)])]
    for name, blocks in cases:
        r = later(blocks)
        width = sum(min(640, b[1]) - max(0, b[0]) for b in blocks)
        print(f"RESULT {name:<58} {width / 640:5.0%} of width -> {r['status']:<9} "
              f"({r['changed_cells']} of {r['compared_cells']} cells changed)")


part_a()
part_b()

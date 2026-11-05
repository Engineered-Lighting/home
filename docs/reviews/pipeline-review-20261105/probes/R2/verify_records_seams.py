#!/usr/bin/env python3
"""verify_records_seams: re-runs the evidence for three R2 findings on synthetic takes.

What it checks
    observation-no-record-version
        - does any observations.jsonl line carry a version key?
        - does observe.check_record flag a `schema_version` key (rung 3 reads
          `schema_version`, records.py:178)? and an extra key inside freshness?
    arrival-clock-records-unmarked
        - the same synthetic take replayed with a PASS slate check and without
          any check: are the record key sets identical, and by how much do
          capture_ts / depth_ts differ (synth.ARRIVAL_S is the true latency)?
    no-path-to-scored-rows
        - a perception-lab style result converted with
          detections.from_perception_lab on the unix clock (what the live loop
          has: observed_at) is refused by replay; the same result on the take's
          host_monotonic t_host is accepted; and no file of the tool besides
          tests calls from_perception_lab.

How to run
    python3 -B verify_records_seams.py <branch checkout root>

Synthetic data only, in a temporary folder removed afterwards. Nothing connects anywhere.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tool = root / "tools" / "den-observe"
sys.path.insert(0, str(tool))
import detections  # noqa: E402
import fake_detector  # noqa: E402
import observe  # noqa: E402
import replay  # noqa: E402
import synth  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="verify-records-seams-"))
quiet = lambda *a: None  # noqa: E731
try:
    scene = synth.Scene()
    depth = scene.depth_mm()
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(3)]

    def make(name, check):
        folder = work / name
        take = synth.make_take(folder / "take", pairs, depth)
        if check:
            synth.write_check(take)
        room = folder / "room.json"
        room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
        detections.write_rows(folder / "rows.jsonl", [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels)
                                                      for i, (t, _) in enumerate(pairs)])
        run = replay.replay(take, room, folder / "out", detections_path=folder / "rows.jsonl", log=quiet)
        lines = (folder / "out" / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        return run, [json.loads(x) for x in lines], folder, take, room

    run_s, recs_s, folder, take, room = make("slate", True)
    run_a, recs_a, *_ = make("arrival", False)

    # observation-no-record-version
    versioned = [k for r in recs_s for k in r if "version" in k.lower() or k == "schema"]
    print(f"RESULT version keys on observation lines: {sorted(set(versioned)) or 'none'} "
          f"(top-level keys: {sorted(recs_s[0])})")
    resolves = lambda tid: tid == run_s["transform_id"]  # noqa: E731
    print(f"RESULT check_record on a valid record: {observe.check_record(recs_s[0], resolves)}")
    print(f"RESULT check_record with schema_version added: "
          f"{observe.check_record(dict(recs_s[0], schema_version='observation/v1'), resolves)}")
    inner = dict(recs_s[0], freshness=dict(recs_s[0]['freshness'], clock='arrival'))
    print(f"RESULT check_record with freshness.clock added: {observe.check_record(inner, resolves)}")

    # arrival-clock-records-unmarked
    print(f"RESULT clock sources in run.json: slate run {run_s['clock']['source']!r}, "
          f"no-check run {run_a['clock']['source']!r}; scored flags {run_s['scored']}, {run_a['scored']}")
    same_keys = all(set(a) == set(s) and set(a['freshness']) == set(s['freshness']) for a, s in zip(recs_a, recs_s))
    print(f"RESULT record and freshness key sets identical between the two runs: {same_keys}")
    d_cap = [round(a['capture_ts'] - s['capture_ts'], 6) for a, s in zip(recs_a, recs_s)]
    d_dep = [round(a['depth_ts'] - s['depth_ts'], 6) for a, s in zip(recs_a, recs_s)]
    print(f"RESULT capture_ts shift (no-check minus slate): {d_cap}; depth_ts shift {d_dep}; "
          f"synth.ARRIVAL_S = {synth.ARRIVAL_S}")

    # no-path-to-scored-rows
    result = {"coordinate_space": "source_pixels", "quality": {"image_width": 640, "image_height": 360},
              "boxes": [{"x": 200.0, "y": 40.0, "w": 150.0, "h": 300.0, "label": "person",
                         "confidence": 0.9, "instance_id": 1}]}
    unix_t = pairs[0][0] + run_s["clock"]["offset_s"]
    unix_row = detections.from_perception_lab(result, capture_ts=unix_t, available_ts=unix_t + 0.3,
                                              rgb_seq=1000, clock="unix")
    host_row = detections.from_perception_lab(result, capture_ts=pairs[0][0], available_ts=pairs[0][0] + 0.3,
                                              rgb_seq=1000)
    for name, row in (("unix", unix_row), ("host_monotonic", host_row)):
        rows_path = work / f"pl-{name}.jsonl"
        detections.write_rows(rows_path, [row])
        try:
            replay.replay(take, room, work / f"out-{name}", detections_path=rows_path, log=quiet)
            print(f"RESULT perception-lab row on the {name} clock: accepted")
        except replay.Refused as exc:
            print(f"RESULT perception-lab row on the {name} clock: refused ({exc})")
    callers = sorted(p.name for p in tool.glob("*.py")
                     if "from_perception_lab(" in p.read_text(encoding="utf-8") and not p.name.startswith("test_"))
    print(f"RESULT non-test files that call or define from_perception_lab: {callers}")
finally:
    shutil.rmtree(work, ignore_errors=True)

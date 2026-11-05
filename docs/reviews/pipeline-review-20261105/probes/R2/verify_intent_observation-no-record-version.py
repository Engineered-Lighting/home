#!/usr/bin/env python3
"""verify_intent_observation-no-record-version: do rung 2's Observation lines carry a record version?

What it checks
    1. Replays the suite's synthetic take (unscored, default METHOD.md) and lists any
       version-like key on the written observations.jsonl lines (schema, schema_version,
       version, record_version) -- the spec README asks records to travel "with the
       record's field names and its version".
    2. Adds schema_version "observation/v1" (rung 3's OBSERVATION_VERSION) to one written
       Observation and runs observe.check_record on it: does rung 2's own gate check
       call the version an unknown field?
    3. Optional second arg: a rung-3-track checkout root. Feeds the written lines to
       den-track's records.read_observations and prints its counts (does it count them
       as 'unversioned'?).

How to run
    python3 -B verify_intent_observation-no-record-version.py <rung-2-observe root> [<rung-3-track root>]

Synthetic data only, temp dirs only; nothing connects anywhere.
"""
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "tools" / "den-observe"))
import detections  # noqa: E402
import fake_detector  # noqa: E402
import observe  # noqa: E402
import replay  # noqa: E402
import synth  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="verify-version-"))
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
    run = replay.replay(take, room, work / "out", detections_path=work / "rows.jsonl", log=lambda *a: None)
    path = work / "out" / "observations.jsonl"
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    keys = sorted({k for obs in lines for k in obs if k in ("schema", "schema_version", "version", "record_version")})
    print(f"RESULT 1: {len(lines)} observation lines; version-like keys on them: {keys or 'none'}; "
          f"run.json schema {run['schema']!r}")
    versioned = dict(lines[0], schema_version="observation/v1")
    problems = observe.check_record(versioned, lambda tid: tid == lines[0]["transform_id"])
    print(f"RESULT 2: check_record on a line with schema_version added: {problems}")
    if len(sys.argv) > 2:
        spec = importlib.util.spec_from_file_location(
            "dt_records", Path(sys.argv[2]).resolve() / "tools" / "den-track" / "records.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cfg = {"floor_sd_m": 0.05, "merge_iou": 0.3, "merge_m": 0.5,
               "heading": {"pairs": [[5, 6], [11, 12]], "min_pair_m": 0.15, "max_sd_deg": 30.0},
               "pairs": [[5, 6], [11, 12]], "min_pair_m": 0.15, "max_sd_deg": 30.0}
        try:
            _, counts = mod.read_observations(path, cfg)
            print(f"RESULT 3: den-track read_observations counts: {counts}")
        except Exception as exc:  # report, do not hide
            print(f"RESULT 3: den-track read_observations raised {type(exc).__name__}: {exc}")
finally:
    shutil.rmtree(work, ignore_errors=True)

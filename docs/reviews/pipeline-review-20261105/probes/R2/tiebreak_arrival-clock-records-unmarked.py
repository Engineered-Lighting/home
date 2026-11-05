#!/usr/bin/env python3
"""tiebreak_arrival-clock-records-unmarked: does an arrival-clocked den-observe run
mislead its pinned consumer (rung 3, den-track)?

What it checks (synthetic data only, temp folders, nothing connects anywhere):
  1. One synthetic take replayed by den-observe twice: once with a PASS check
     (slate clock) and once with no check (arrival clock). Reports run.json's
     clock source, whether the observation lines differ in any key, and the
     constant shift of capture_ts / depth_ts / freshness times.
  2. The same with a FAIL check (also arrival), so the arrival path is shown to
     be reached only by takes that CHECK-V1 does not retain.
  3. Each observations.jsonl is copied alone (no run.json, as rung 3's README
     instructs) into a rung-3 session folder and tracked with den-track's own
     CLI (`den_track.py track`). Compares the two tracks.jsonl outputs: same
     records, states and positions, with times shifted by one constant?
  4. Whether observe.check_record accepts a clock marker nested in freshness.

How to run
  python3 -B tiebreak_arrival-clock-records-unmarked.py <rung-2-observe checkout> [<rung-3-track checkout>]
The second argument defaults to the sibling folder `rung-3-track` next to the first.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
r3 = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else root.parent / "rung-3-track"
tool = root / "tools" / "den-observe"
sys.path.insert(0, str(tool))
import detections  # noqa: E402
import fake_detector  # noqa: E402
import observe  # noqa: E402
import replay  # noqa: E402
import synth  # noqa: E402

quiet = lambda *a: None  # noqa: E731
work = Path(tempfile.mkdtemp(prefix="tiebreak-arrival-"))
ok = True
try:
    scene = synth.Scene()
    depth = scene.depth_mm()
    pixels = {n: scene.joint_rgb(n) for n in synth.JOINTS}
    pairs = [(5000.0 + 0.25 * i, 5000.0 + 0.25 * i - 0.004) for i in range(16)]

    def make(name, verdict):
        folder = work / name
        take = synth.make_take(folder / "take", pairs, depth)
        if verdict:
            synth.write_check(take, verdict=verdict)
        room = folder / "room.json"
        room.write_text(json.dumps(synth.room_model()), encoding="utf-8")
        detections.write_rows(folder / "rows.jsonl", [fake_detector.row(t, rgb_seq=1000 + i, pixels=pixels)
                                                      for i, (t, _) in enumerate(pairs)])
        run = replay.replay(take, room, folder / "out", detections_path=folder / "rows.jsonl", log=quiet)
        path = folder / "out" / "observations.jsonl"
        return run, [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()], path

    run_s, recs_s, path_s = make("slate", "PASS")
    run_a, recs_a, path_a = make("arrival", None)
    run_f, recs_f, path_f = make("fail", "FAIL")
    print(f"RESULT clock sources: PASS check {run_s['clock']['source']!r}, no check {run_a['clock']['source']!r}, "
          f"FAIL check {run_f['clock']['source']!r}; scored flags {run_s['scored']}, {run_a['scored']}, {run_f['scored']}")
    same = all(set(a) == set(s) and set(a['freshness']) == set(s['freshness']) for a, s in zip(recs_a, recs_s))
    print(f"RESULT {len(recs_s)} records each; top-level and freshness key sets identical: {same}")
    shifts = set()
    for a, s in zip(recs_a, recs_s):
        for key in ("capture_ts", "depth_ts"):
            shifts.add(round(a[key] - s[key], 6))
        for key in ("available_ts", "valid_until"):
            if a["freshness"].get(key) is not None:
                shifts.add(round(a["freshness"][key] - s["freshness"][key], 6))
    print(f"RESULT distinct time shifts (arrival minus slate) over every time field: {sorted(shifts)}; "
          f"synth.ARRIVAL_S = {synth.ARRIVAL_S}")
    other = [k for a, s in zip(recs_a, recs_s) for k in a
             if k not in ("obs_id", "capture_ts", "depth_ts", "freshness") and a[k] != s[k]]
    print(f"RESULT non-time fields that differ between the two runs: {sorted(set(other)) or 'none'}")
    resolves = lambda tid: tid == run_s["transform_id"]  # noqa: E731
    marked = dict(recs_a[0], freshness=dict(recs_a[0]["freshness"], clock="arrival"))
    print(f"RESULT check_record with a nested freshness.clock marker: {observe.check_record(marked, resolves)}; "
          f"with a top-level clock key: {observe.check_record(dict(recs_a[0], clock='arrival'), resolves)}")

    # rung 3, through its own CLI, on observations.jsonl alone (no run.json)
    cli = r3 / "tools" / "den-track" / "den_track.py"
    if not cli.is_file():
        print(f"RESULT rung 3 CLI not found at {cli}; consumer part skipped")
    else:
        tracks = {}
        for name, path in (("slate", path_s), ("arrival", path_a)):
            session = work / f"session-{name}"
            (session / "take-01").mkdir(parents=True)
            shutil.copy(path, session / "take-01" / "observations.jsonl")
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            proc = subprocess.run([sys.executable, "-B", str(cli), "track", "--session", str(session)],
                                  capture_output=True, text=True, env=env, timeout=120)
            print(f"RESULT den_track track ({name}) exit {proc.returncode}: "
                  f"{' | '.join(l for l in proc.stdout.splitlines() if l.strip())[:300]}")
            if proc.returncode != 0:
                print(proc.stderr[-800:])
                ok = False
                continue
            tracks[name] = [json.loads(l) for l in (session / "take-01" / "tracks.jsonl").read_text(
                encoding="utf-8").splitlines()]
            runrec = json.loads((session / "take-01" / "tracks.run.json").read_text(encoding="utf-8"))
            print(f"RESULT tracks.run.json ({name}) keys mentioning a clock: "
                  f"{[k for k in runrec if 'clock' in k.lower()] or 'none'}; counts {runrec['observation_counts']}")
        if len(tracks) == 2:
            ts, ta = tracks["slate"], tracks["arrival"]
            print(f"RESULT track records: slate {len(ts)}, arrival {len(ta)}")

            def strip(rec, shift):
                out = {}
                for k, v in rec.items():
                    if k == "last_obs_ids":
                        out[k] = len(v)
                    elif isinstance(v, (int, float)) and not isinstance(v, bool) and k.endswith(("_ts", "as_of")):
                        out[k] = round(v - shift, 4)
                    elif isinstance(v, dict):
                        out[k] = {kk: (round(vv - shift, 4) if isinstance(vv, (int, float))
                                       and not isinstance(vv, bool) and kk.endswith(("_ts", "_at", "since", "as_of"))
                                       else vv) for kk, vv in v.items()}
                    else:
                        out[k] = v
                return out
            shift = synth.ARRIVAL_S
            diffs = [i for i, (x, y) in enumerate(zip(ts, ta)) if strip(x, 0.0) != strip(y, shift)]
            print(f"RESULT track records equal after removing the {shift} s shift: "
                  f"{len(ts) == len(ta) and not diffs} (differing indexes: {diffs[:5]})")
            if diffs:
                i = diffs[0]
                print("  slate  :", json.dumps(strip(ts[i], 0.0))[:600])
                print("  arrival:", json.dumps(strip(ta[i], shift))[:600])
            states = sorted({(r.get("state"), (r.get("association") or {}).get("status")) for r in ts})
            print(f"RESULT track states seen: {states}")
    print("PASS probe ran" if ok else "FAIL probe could not complete")
finally:
    shutil.rmtree(work, ignore_errors=True)

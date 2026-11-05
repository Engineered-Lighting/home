#!/usr/bin/env python3
"""Probe (sanity, no finding expected): score.py refuses changed frozen N1 inputs.

Checks on the branch's own synthetic world (temp dirs, fake check.py):
  dev run:      a changed development label file, a changed development boxes file, a changed split.json
  held-out run: a changed fixture surface-map.json, an extra file in labels-v1, a changed held-out label
                file, a replay log whose tick differs from GATE-V1's (stand-in), a receipt with another
                prompt version (stand-in)
Each must be refused with nothing written.

Run:  python3 -B probe_freeze-refusals.py <root of the claude/den-n2 checkout>
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-n2"))
import test_den_n2 as T  # noqa: E402
score = T.score


def attempt(world, split, name, mutate):
    tmp = world.root / "_save"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(world.root / "n1", tmp / "n1")
    shutil.copytree(world.root / "n2", tmp / "n2")
    restore = mutate()
    before = sorted((world.n2 / "eval").glob("*")) if (world.n2 / "eval").exists() else []
    try:
        world.run(split)
        outcome = "SCORED"
    except score.Refused as exc:
        outcome = f"refused: {str(exc)[:90]}"
    after = sorted((world.n2 / "eval").glob("*")) if (world.n2 / "eval").exists() else []
    for sub in ("n1", "n2"):
        shutil.rmtree(world.root / sub)
        shutil.copytree(tmp / sub, world.root / sub)
    ok = outcome.startswith("refused") and before == after
    print(f"{'PASS' if ok else 'FAIL'} {split}: {name}: {outcome}")
    return ok


tmp = Path(tempfile.mkdtemp(prefix="n2-probe-fz-"))
try:
    world = T.World(tmp, T.FOUR_DAYS)
    held = sorted(world.held_out())
    dev = sorted(set(world.split) - set(held))
    lab, fx = world.n1 / "labels-v1", world.n1 / "fixture-v1"

    def append(path, data=b" "):
        return lambda: path.write_bytes(path.read_bytes() + data)

    def tick():
        log = world.n2 / "replay" / held[0] / "seg-1" / "log.jsonl"
        lines = log.read_text(encoding="ascii").splitlines()
        header = json.loads(lines[0])
        header["tick_s"] = 1.0
        log.write_text("\n".join([json.dumps(header, sort_keys=True, separators=(",", ":"))] + lines[1:]) + "\n",
                       encoding="ascii")

    def prompt():
        asks = world.n2 / "asks" / f"{held[1]}.jsonl"
        lines = [json.loads(x) for x in asks.read_text(encoding="utf-8").splitlines()]
        lines[3]["receipt"]["prompt_version"] = "lab-live-scene-prompt-2"
        T.World.write_jsonl(asks, lines)
    results = [
        attempt(world, "development", "changed development label file", append(lab / "sessions" / f"{dev[0]}.json")),
        attempt(world, "development", "changed development boxes file", append(fx / "boxes" / f"{dev[0]}.csv", b"\n")),
        attempt(world, "development", "changed split.json", append(lab / "split.json")),
        attempt(world, "held_out", "changed fixture surface-map.json", append(fx / "surface-map.json")),
        attempt(world, "held_out", "extra file in labels-v1", lambda: (lab / "notes.txt").write_text("x")),
        attempt(world, "held_out", "changed held-out label file", append(lab / "sessions" / f"{held[0]}.json")),
        attempt(world, "held_out", "replay tick 1.0 vs gate 2.0", tick),
        attempt(world, "held_out", "a receipt under another prompt version", prompt),
    ]
    print(f"RESULT {sum(results)}/{len(results)} refused as expected")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

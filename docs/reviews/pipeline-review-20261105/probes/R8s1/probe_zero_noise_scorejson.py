#!/usr/bin/env python3
"""probe_zero_noise_scorejson: zero all-off noise breaks model.py in two ways.

(a) A block whose all-off frames are identical (a black, after-dark den: every pixel at code 0) has
    all-off noise 0, so score() records that pair's SNR as float('inf'); score.json is dumped with
    allow_nan=False and raises ValueError half-way through. model.main() prints
    "Refused: ... Nothing was written." and exits 2, but readings.json, a complete light-model.json
    (with a verdict in its error field) and a truncated score.json are on disk, and a re-run refuses
    because readings.json exists.
(b) frames_per_capture = 1 (an open slot; nothing refuses it) gives every block spread 0, so the bracket
    test flags a block for any difference at all between two all-off readings (5 x max(0, 1e-12)); with
    any daylight drift every block is flagged and excluded, the gate has 0 pairs and can never pass, and
    the printed line reads "median 0.0%, p90 0.0% over 0 pairs".

How to run (synthetic captures in memory, temp dirs only, no network):
    python3 -B probe_zero_noise_scorejson.py <root of the claude/rung-8-stage1 checkout>
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import meter  # noqa: E402
import model  # noqa: E402
import synthetic  # noqa: E402
import test_stage1 as T  # noqa: E402


def captures_for(den, m, frames):
    owner = model.pixel_blocks(m)
    plan = model.schedule(m, T.SNAPSHOT, T.RANGES, T.WARMEST)
    out, t = [], 0.0
    for step in plan["steps"]:
        t += 2.5
        blocks = den.reading(step["scene"], t)
        values = [blocks[b] for b in owner]
        out.append({"step": step, "t": t, "frames": [(values, [False] * len(owner))] * frames})
    return out, plan["temperatures"]


def run_main(tmp, m, captures, temps):
    m0 = T.method0()
    params, digest = synthetic.meter_norm_for(m0)
    s0 = tmp / "stage0-x" / "sweep-1"
    s0.mkdir(parents=True)
    (s0 / "response.json").write_text(json.dumps({"outcome": "pass", "meter_norm": {
        "version": meter.METER_NORM_VERSION, "parameters": params, "parameters_sha256": digest}}))
    session = tmp / "s1"
    session.mkdir()
    (session / "session.json").write_text(json.dumps({"outcome": "complete", "id": "s1", "temperatures": temps,
                                                      "stage0": {"meter_norm_sha256": digest}}))
    (session / "fixtures.jsonl").write_text("".join(json.dumps(f) + "\n" for f in T.FIXTURES))
    (session / "probes.jsonl").write_text("")
    results = []
    for _ in range(2):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(model, "load_stage1", lambda path=None: m), \
                mock.patch.object(model, "session_captures", lambda folder, parameters, method1: captures), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = model.main(["--session", str(session), "--stage0", str(s0)])
        results.append((code, (out.getvalue() + err.getvalue()).strip()))
    return session, results


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # (a) black den: daylight 0, identical frames
        m = T.method1()
        den = synthetic.SyntheticDen(block_px=40, daylight=0.0, drift=0.0, noise=0.0)
        captures, temps = captures_for(den, m, frames=m["frames_per_capture"])
        session, results = run_main(tmp / "a", m, captures, temps)
        print("RESULT (a) first run:", results[0])
        print("RESULT (a) second run:", results[1])
        files = {n: (session / n).exists() for n in ("readings.json", "light-model.json", "score.json")}
        print("RESULT (a) files on disk:", files)
        valid = None
        if files["score.json"]:
            try:
                json.loads((session / "score.json").read_text())
                valid = True
            except ValueError:
                valid = False
        print("RESULT (a) score.json is valid JSON:", valid)
        if files["light-model.json"]:
            lm = json.loads((session / "light-model.json").read_text())
            print("RESULT (a) light-model.json error:", {k: lm["error"][k] for k in ("median_pct", "p90_pct",
                                                                                      "count", "verdict")})
        if results[0][0] == 2 and "Nothing was written" in results[0][1] and files["light-model.json"]:
            print("FAIL (a): 'Nothing was written' (exit 2) but readings.json, light-model.json and a truncated "
                  "score.json were written, and the re-run refuses")
        else:
            print("PASS (a)")

        # (b) frames_per_capture = 1 with a little daylight drift
        m1 = T.method1(frames_per_capture=1)
        den = synthetic.SyntheticDen(block_px=40, daylight=0.004, drift=0.2, noise=0.0)
        captures, temps = captures_for(den, m1, frames=1)
        r = model.reduce_captures(captures, m1, model.pixel_blocks(m1))
        r["temperatures"] = temps
        fitted = model.fit(r, temps, m1)
        scored = model.score(r, fitted, m1)
        e = scored["error"]
        print("RESULT (b) blocks:", fitted["blocks"], "excluded:", len(fitted["excluded"]),
              "flagged pairs:", len(fitted["flagged"]), "pairs scored:", e["count"], "verdict:", e["verdict"])
        line = (f"{e['verdict'].upper()}: median {100 * (e['median'] or 0):.1f}%, p90 {100 * (e['p90'] or 0):.1f}% "
                f"over {e['count']} pairs")
        print("RESULT (b) model.main would print:", line)
        # the same den with two frames (non-zero spread from a little code noise) scores normally
        m2 = T.method1()
        den2 = synthetic.SyntheticDen(block_px=40, daylight=0.004, drift=0.2, noise=2e-5)
        r2 = T.readings_for(den2, m2)
        f2 = model.fit(r2, r2["temperatures"], m2)
        e2 = model.score(r2, f2, m2)["error"]
        print("RESULT (b) control with non-zero spread: pairs", e2["count"], "verdict", e2["verdict"])
        if e["count"] == 0 and e2["count"] > 0:
            print("FAIL (b): frames_per_capture = 1 is accepted but makes every block's noise 0, flags and excludes "
                  "every block, so the gate cannot pass; the printed median/p90 read 0.0%")
        else:
            print("PASS (b)")


if __name__ == "__main__":
    main()

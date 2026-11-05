#!/usr/bin/env python3
"""Probe: --score's skip rules (delta 29f0695f) on constructed day folders.

Cases (synthetic frames, fake camera on 127.0.0.1, temp folders only):
  A complete: the suite's two_days plan                      -> expect PASS, nothing skipped
  B held-out day all void (desk, sofa under daylight)       -> expect INCOMPLETE, both listed "outcome void"
  C held-out day all interrupted (kept frames, no capture.json) -> expect INCOMPLETE, both listed interrupted
  D held-out day complete plus one interrupted folder        -> expect PASS, the folder listed
  E an "error" capture.json beside a complete set            -> expect PASS, listed "outcome error"
  F the only folder of the only day is interrupted           -> expect not PASS (and no crash)
For each case it prints the gate, the held-out result and missing lines, the
skipped_folders list and whether the console mentions the skipped folders.

Run:
    python3 -B probe_skip_rules_constructed_days.py <root of the branch checkout>
Prints one RESULT line per case and PASS/FAIL against the expectation.
"""
import sys

sys.dont_write_bytecode = True
import json  # noqa: E402
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))

import photometry  # noqa: E402
import sweep  # noqa: E402
import test_photometry as T  # noqa: E402


class Probe(T.ScoreCase):
    def runTest(self):
        pass


def score(ws):
    out = []
    try:
        _, report = photometry.score_root(ws.root, ws.room, ws.token, ws.method, ws.den, None, say=out.append)
        return report, out, None
    except sweep.Refused as exc:
        return None, out, f"Refused: {exc}"
    except Exception as exc:  # a crash is a result too
        return None, out, f"CRASH {type(exc).__name__}: {exc}"


def interrupted_copy(src, dst):
    """A capture cut off after its holds: frames and frames.jsonl, no capture.json, no preview."""
    dst.mkdir()
    shutil.copytree(src / "frames", dst / "frames")
    shutil.copy2(src / "frames.jsonl", dst / "frames.jsonl")


def void_capture(p, ws, day, point, value, auto):
    p.set_scene(value, auto)
    p.fake.scene_after_posts = (p.fake.posts + 2, T.scene(value * 0.6))
    _, record, _ = ws.capture(p.fake, day, point, "daylight", blinds="open", light="daylight only")
    p.fake.scene_after_posts = None
    return record["outcome"]


def show(case, expect, report, out, refused):
    if refused:
        ok = expect == "refused-or-not-pass" or (expect != "pass" and "CRASH" not in refused)
        print(f"RESULT case {case}: {refused}")
        print(("PASS" if ok and "CRASH" not in refused else "FAIL") + f" case {case}: expected {expect}")
        return
    held = report["result"]["held_out"]
    listed = report.get("skipped_folders")
    console = [line for line in out if "skip" in line.lower() or "interrupted" in line.lower()
               or "void" in line.lower()]
    print(f"RESULT case {case}: gate={report['gate']} held_out={held['result']} missing={held['missing']} "
          f"skipped_folders={listed} console_lines_about_skipped={console}")
    ok = (report["gate"] == "pass") if expect == "pass" else (report["gate"] != "pass")
    print(("PASS" if ok else "FAIL") + f" case {case}: expected {expect}, got {report['gate']}")


def main():
    p = Probe()
    p.setUp()
    try:
        with T.workspace() as ws:  # A
            p.two_days(ws)
            show("A complete", "pass", *score(ws))
        with T.workspace() as ws:  # B
            p.two_days(ws, day2=False)
            day2 = ws.new_day("20261111")
            outcomes = [void_capture(p, ws, day2, "desk", 80.0, (16, 100)),
                        void_capture(p, ws, day2, "sofa", 40.0, (32, 100))]
            print(f"RESULT case B outcomes on day 2: {outcomes}")
            show("B held-out day all void", "incomplete", *score(ws))
        with T.workspace() as ws:  # C
            day1 = p.two_days(ws)
            day2 = ws.root / "day-20261111"
            for n in (1, 2):
                src = day2 / f"capture-{n}"
                tmp = day2 / f"kept-{n}"
                src.rename(tmp)
                interrupted_copy(tmp, src)
                shutil.rmtree(tmp)
            (day2 / "worksheet.csv").write_text(",".join(photometry.COLUMNS) + "\n", encoding="utf-8")
            show("C held-out day all interrupted", "incomplete", *score(ws))
        with T.workspace() as ws:  # D
            p.two_days(ws)
            day2 = ws.root / "day-20261111"
            interrupted_copy(day2 / "capture-1", day2 / "capture-3")
            show("D complete plus one interrupted", "pass", *score(ws))
        with T.workspace() as ws:  # E
            day1 = p.two_days(ws)
            src = day1 / "capture-3"
            dst = day1 / "capture-5"
            shutil.copytree(src, dst)
            cap = json.loads((dst / "capture.json").read_text(encoding="utf-8"))
            cap.update(outcome="error", detail="OSError: a write after the data was complete", capture=5)
            (dst / "capture.json").write_text(json.dumps(cap), encoding="utf-8")
            show("E an error capture beside a complete set", "pass", *score(ws))
        with T.workspace() as ws:  # F
            day1 = ws.new_day("20261110")
            (day1 / "capture-1" / "frames").mkdir(parents=True)
            show("F only an interrupted folder", "not pass", *score(ws))
    finally:
        p.doCleanups()


if __name__ == "__main__":
    main()

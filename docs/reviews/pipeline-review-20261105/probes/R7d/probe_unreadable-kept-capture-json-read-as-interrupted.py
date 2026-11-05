#!/usr/bin/env python3
"""Probe: a KEPT capture whose capture.json cannot be read is now treated as an
"interrupted" capture folder by --score (photometry.py score_root, delta 29f0695f).

What it checks (synthetic data, fake camera on 127.0.0.1, temp folders only):
  1. Two passing days (the test suite's own two_days plan), then day 2's desk
     reading is typed 20 lx off, so score 1 FAILS on a held-out point.
  2. That kept capture's capture.json is damaged the way a Windows editor or a
     cut-off write leaves it (case a: UTF-8 BOM added, as PowerShell 5.1
     `Set-Content -Encoding UTF8` writes; case b: truncated). Its frames and
     frames.jsonl are intact and its worksheet row is still there.
  3. What the score says: the refusal names the worksheet ROW ("a row for
     capture 1, which is not a kept capture"), not the unreadable capture.json.
  4. The owner follows that message and deletes the row: the score now PASSES,
     the failing held-out capture is gone, the console prints nothing about it,
     and report.json lists it as "no capture.json (interrupted)" although the
     file is there.
At the reviewed pin 1931c831 step 3 says "no readable ...capture.json" and step 4
still refuses (no silent drop); at 9a5023e3 step 4 passes.

Run:
    python3 -B probe_unreadable-kept-capture-json-read-as-interrupted.py <root of the branch checkout>
Prints PASS/FAIL/RESULT lines; exit 1 when the defect reproduces.
"""
import sys

sys.dont_write_bytecode = True
import csv  # noqa: E402
import io  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))

import photometry  # noqa: E402
import sweep  # noqa: E402
import test_photometry as T  # noqa: E402


class Probe(T.ScoreCase):
    def runTest(self):
        pass


def drop_row(day, n):
    table = list(csv.reader((day / "worksheet.csv").read_text(encoding="utf-8").splitlines()))
    kept = [r for r in table if not (r and r[0] == str(n))]
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerows(kept)
    (day / "worksheet.csv").write_bytes(out.getvalue().encode("utf-8"))


def score(ws):
    out = []
    try:
        folder, report = photometry.score_root(ws.root, ws.room, ws.token, ws.method, ws.den, None, say=out.append)
        return report, out, None
    except sweep.Refused as exc:
        return None, out, str(exc)


def damage(path, how):
    raw = path.read_bytes()
    if how == "bom":
        path.write_bytes(b"\xef\xbb\xbf" + raw)
    else:
        path.write_bytes(raw[: len(raw) // 2])


def main():
    p = Probe()
    p.setUp()
    defects = 0
    try:
        for how in ("bom", "truncated"):
            with T.workspace() as ws:
                p.two_days(ws)
                day2 = ws.root / "day-20261111"
                T.type_row(day2, 1, E_before_lx="160", E_after_lx="160")  # day 2 desk, 20 lx off
                report, _, refused = score(ws)
                print(f"RESULT [{how}] score with the off reading: gate={report and report['gate']} "
                      f"held_out={report and report['result']['held_out']['result']}")
                cap_json = day2 / "capture-1" / "capture.json"
                print(f"RESULT [{how}] capture-1 outcome before damage: "
                      f"{photometry.json.loads(cap_json.read_text(encoding='utf-8'))['outcome']}")
                damage(cap_json, how)
                print(f"RESULT [{how}] capture-1 still holds: {sorted(x.name for x in (day2 / 'capture-1').iterdir())}")
                report, _, refused = score(ws)
                print(f"RESULT [{how}] score after damage: refused={refused!r} gate={report and report['gate']}")
                if report and report["gate"] == "pass":
                    defects += 1
                    print(f"FAIL [{how}] with its row still there, the kept failing capture was dropped and the "
                          f"gate reads PASS; skipped_folders={report['skipped_folders']}")
                misleading = refused is not None and "not a kept capture" in refused
                if misleading:
                    print(f"FAIL [{how}] the refusal blames the worksheet row, not the unreadable capture.json")
                else:
                    print(f"PASS [{how}] the refusal does not blame the worksheet row: {refused!r}")
                drop_row(day2, 1)
                report, out, refused = score(ws)
                if refused:
                    print(f"PASS [{how}] after deleting the row the score still refuses: {refused}")
                    continue
                shown = [line for line in out if "20261111-1" in line or "capture-1" in line]
                print(f"RESULT [{how}] after deleting the row: gate={report['gate']} "
                      f"held_out={report['result']['held_out']['result']} "
                      f"n_points={report['result']['held_out']['n_points']}")
                print(f"RESULT [{how}] skipped_folders={report['skipped_folders']}")
                print(f"RESULT [{how}] console lines naming day 2 capture 1: {shown}")
                if report["gate"] == "pass":
                    defects += 1
                    print(f"FAIL [{how}] a kept, failing held-out capture was dropped and the gate reads PASS")
    finally:
        p.doCleanups()
    print("RESULT DEFECT REPRODUCED" if defects else "RESULT NOT REPRODUCED")
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main())

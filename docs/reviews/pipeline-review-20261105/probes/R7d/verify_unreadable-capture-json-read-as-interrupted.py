#!/usr/bin/env python3
"""verify_unreadable-capture-json-read-as-interrupted: independent re-check (verifier) of finding
"unreadable-capture-json-read-as-interrupted" on den-photometry's --score (rung 7).

What it checks, through the owner's own command line (photometry.main --score), with synthetic
frames, the suite's fake camera on 127.0.0.1 and temporary folders only:
  1. Two passing days (the suite's ScoreCase.two_days plan). Day 2's desk reading is typed 20 lx
     off, so the score exits 1 with RUNG 7 GATE: FAIL.
  2. That KEPT capture's capture.json is made unreadable while the file, its frames, frames.jsonl and
     its worksheet row all stay: (bom) a UTF-8 BOM prepended, (trunc) cut in half, (lock) reading it
     raises PermissionError (as a Windows share lock would), (array) it holds a JSON array.
  3. Records what --score prints and its exit code: does the refusal name the worksheet row or the file?
  4. Deletes that worksheet row (what the refusal points at) and scores again: exit code, gate line,
     whether any console line names the dropped capture, and report.json's skipped_folders reason.
A defect is counted when step 4 exits 0 with RUNG 7 GATE: PASS (the failing capture silently gone).

Run:
    python3 -B verify_unreadable-capture-json-read-as-interrupted.py <root of the branch checkout>
Exit 1 when the defect reproduces, 0 otherwise.
"""
import sys

sys.dont_write_bytecode = True
import csv  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import pathlib  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-photometry"))

import card  # noqa: E402
import meter  # noqa: E402
import photometry  # noqa: E402
import test_photometry as T  # noqa: E402


class Fixture(T.ScoreCase):
    def runTest(self):
        pass


def cli_score(ws, patch_read=None):
    out = []
    with mock.patch.object(card, "load_method", return_value=ws.method), \
            mock.patch.object(meter, "load_method", return_value=ws.den):
        if patch_read is None:
            code = photometry.main(["--score", str(ws.root), "--room-model", str(ws.room),
                                    "--token-file", str(ws.token)], say=out.append)
        else:
            with mock.patch.object(pathlib.Path, "read_text", patch_read):
                code = photometry.main(["--score", str(ws.root), "--room-model", str(ws.room),
                                        "--token-file", str(ws.token)], say=out.append)
    return code, out


def delete_row(day, n):
    table = list(csv.reader((day / "worksheet.csv").read_text(encoding="utf-8").splitlines()))
    table = [r for r in table if not (r and r[0] == str(n))]
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerows(table)
    (day / "worksheet.csv").write_bytes(buf.getvalue().encode("utf-8"))


def latest_report(ws):
    folders = sorted((p for p in ws.root.iterdir() if p.name.startswith("score-")),
                     key=lambda p: int(p.name.split("-")[1]))
    return json.loads((folders[-1] / "report.json").read_text(encoding="utf-8")) if folders else None


def main():
    fx = Fixture()
    fx.setUp()
    defects, misleading = 0, 0
    real_read_text = pathlib.Path.read_text
    try:
        for how in ("bom", "trunc", "lock", "array"):
            with T.workspace() as ws:
                fx.two_days(ws)
                day2 = ws.root / "day-20261111"
                T.type_row(day2, 1, E_before_lx="160", E_after_lx="160")  # desk on day 2: 20 lx off
                code, out = cli_score(ws)
                gate = [line for line in out if line.startswith("RUNG 7 GATE")]
                print(f"RESULT [{how}] before damage: exit={code} {gate}")
                target = day2 / "capture-1" / "capture.json"
                assert json.loads(target.read_text(encoding="utf-8"))["outcome"] == "kept"
                patch = None
                if how == "bom":
                    target.write_bytes(b"\xef\xbb\xbf" + target.read_bytes())
                elif how == "trunc":
                    raw = target.read_bytes()
                    target.write_bytes(raw[: len(raw) // 2])
                elif how == "array":
                    target.write_text("[]\n", encoding="utf-8")
                else:
                    def patch(self, *a, **k):
                        if self.name == "capture.json" and self.parent.name == "capture-1" \
                                and self.parent.parent.name == "day-20261111":
                            raise PermissionError(13, "The process cannot access the file", str(self))
                        return real_read_text(self, *a, **k)
                print(f"RESULT [{how}] capture-1 holds: {sorted(p.name for p in target.parent.iterdir())}")
                code, out = cli_score(ws, patch)
                gate = [line for line in out if line.startswith("RUNG 7 GATE")]
                print(f"RESULT [{how}] after damage: exit={code} last line={out[-1] if out else None!r} {gate}")
                if code == 0 and gate == ["RUNG 7 GATE: PASS"]:
                    defects += 1
                    print(f"FAIL [{how}] with its row still there, the kept failing capture was dropped and the "
                          f"gate reads PASS (no owner step needed)")
                if code == 2 and out and "not a kept capture" in out[-1]:
                    misleading += 1
                    print(f"FAIL [{how}] the refusal names the worksheet row, not capture.json")
                elif code == 2 and out and "capture.json" in out[-1]:
                    print(f"PASS [{how}] the refusal names capture.json")
                delete_row(day2, 1)
                code, out = cli_score(ws, patch)
                gate = [line for line in out if line.startswith("RUNG 7 GATE")]
                named = [line for line in out if "20261111-1" in line or "capture-1" in line]
                print(f"RESULT [{how}] after deleting the row: exit={code} {gate or out[-1:]}")
                print(f"RESULT [{how}] console lines naming day 2 capture 1: {named}")
                if code == 0 and gate == ["RUNG 7 GATE: PASS"]:
                    report = latest_report(ws)
                    print(f"RESULT [{how}] report.json held_out n_points="
                          f"{report['result']['held_out']['n_points']} skipped_folders={report['skipped_folders']}")
                    defects += 1
                    print(f"FAIL [{how}] the kept, failing capture was dropped and the gate reads PASS")
                else:
                    print(f"PASS [{how}] no silent PASS after deleting the row")
    finally:
        fx.doCleanups()
    print(f"RESULT misleading refusals={misleading} silent passes={defects}")
    print("RESULT DEFECT REPRODUCED" if defects else "RESULT NOT REPRODUCED")
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main())

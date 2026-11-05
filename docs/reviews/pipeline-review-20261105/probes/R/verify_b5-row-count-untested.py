#!/usr/bin/env python3
"""verify b5-row-count-untested: is dropping B5's row-count check (mutant M13) observable?

    python3 -B verify_b5-row-count-untested.py <rung-r checkout root>

Builds one clean synthetic take with make_take (temp dir only), removes one frames.csv
row from the middle of the activity (not in a slate window), re-records frames.csv's
sha256 in take.json but leaves counts.kept alone (a hand repair or a writer bug), and
runs check.py with the real code and with M13 applied in memory
('or len(rows) != counts.kept):' -> 'or False):'). Also re-runs the suite's own
row-deletion case (hash NOT re-recorded) under M13 to show the hash alone catches it.
Expectation if the finding holds: real code FAIL ['B5_hashes'] bad_files ['frames.csv'];
M13 PASS on the re-hashed edit; both FAIL when the hash is stale.
"""
import io
import json
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-take"))
import check  # noqa: E402
import take  # noqa: E402
import test_den_take as t  # noqa: E402

OLD = 'or len(rows) != (take.get("counts") or {}).get("kept")):'
NEW = 'or False):'
source = Path(check.__file__).read_text(encoding="utf-8")
assert source.count(OLD) == 1, "M13 old text not found exactly once"
mutant = types.ModuleType("check_m13")
mutant.__file__ = check.__file__
exec(compile(source.replace(OLD, NEW), check.__file__, "exec"), mutant.__dict__)

def run(module, folder):
    code, report, _ = module.run_check(folder, ask=lambda: "none", show=lambda p: None,
                                       out=io.StringIO(), console=t.Console())
    return report["verdict"], report["failures"], report["rules"]["B5_hashes"]["bad_files"]

tmp = Path(tempfile.mkdtemp(prefix="verify-b5-"))
ok = True
try:
    base = t.make_take(tmp)
    record = json.loads((base / "take.json").read_text(encoding="utf-8"))
    begin = next(c["pc_mono_s"] for c in record["cues"] if c["cue"] == "begin")
    for rehash in (True, False):
        for name, module in (("real", check), ("M13", mutant)):
            folder = tmp / f"{rehash}-{name}"
            shutil.copytree(base, folder)
            csv_path = folder / "frames.csv"
            lines = csv_path.read_text(encoding="utf-8").splitlines(keepends=True)
            col = lines[0].rstrip("\n").split(",").index("recv_pc_mono_s")
            drop = next(i for i, l in enumerate(lines[1:], 1) if float(l.split(",")[col]) > begin + 10)
            csv_path.write_text("".join(lines[:drop] + lines[drop + 1:]), encoding="utf-8", newline="\n")
            rec = json.loads((folder / "take.json").read_text(encoding="utf-8"))
            if rehash:
                rec["sha256"]["frames.csv"] = check.file_sha256(csv_path)
            take.write_json(folder / "take.json", rec)
            verdict, failures, bad = run(module, folder)
            expect_fail = name == "real" or not rehash
            expect = ("FAIL", ["B5_hashes"], ["frames.csv"]) if expect_fail else ("PASS", [], [])
            good = (verdict, failures, bad) == expect
            ok &= good
            print(f"{'PASS' if good else 'FAIL'} row {drop} removed, hash {'re-recorded' if rehash else 'stale'}, "
                  f"kept={rec['counts']['kept']} rows now {len(lines) - 2}, {name}: verdict {verdict} "
                  f"failures {failures} bad_files {bad} (expected {expect[:2]})")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("RESULT", "REPRODUCED: only the row count catches a re-hashed row removal; M13 passes it"
      if ok else "NOT REPRODUCED")

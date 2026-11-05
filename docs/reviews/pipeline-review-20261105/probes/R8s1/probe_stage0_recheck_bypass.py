#!/usr/bin/env python3
"""probe_stage0_recheck_bypass: Stage 1 (probe.py --session and model.py) accepts a Stage 0 sweep whose
response.json FAILED, as soon as any recheck-<n>.json beside it says pass, even when that recheck
records method_matches_sweep false (the frozen Stage 0 block was edited after the sweep).

What it checks: probe.stage0_meter_norm() returns the first record, newest recheck first, whose outcome
is "pass" and that carries meter_norm. It never reads method_matches_sweep or same_as_saved. It also
orders rechecks as strings, so recheck-9 is preferred to a newer recheck-10.

How to run (synthetic files in a temp dir only):
    python3 -B probe_stage0_recheck_bypass.py <root of the claude/rung-8-stage1 checkout>
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / "tools" / "den-light"))
import probe  # noqa: E402
import sweep  # noqa: E402


def norm(tag):
    return {"version": "meter-norm/v1", "parameters": {"tag": tag}, "parameters_sha256": tag * 8}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "sweep-1"
        folder.mkdir()
        (folder / "response.json").write_text(json.dumps({
            "schema": "den-light-stage0-response-1", "outcome": "fail", "method_stage0_block_sha256": "a" * 64,
            "check": {"reasons": ["residual over residual_max_codes in G"]}, "meter_norm": norm("fail")}))
        (folder / "recheck-1.json").write_text(json.dumps({
            "schema": "den-light-stage0-recheck-1", "saved_outcome": "fail", "outcome": "pass",
            "method_stage0_block_sha256": "b" * 64, "method_matches_sweep": False, "same_as_saved": False,
            "meter_norm": norm("edit")}))
        try:
            got, source = probe.stage0_meter_norm(folder)
            print("RESULT stage0_meter_norm used:", source, "parameters tag:", got["parameters"]["tag"])
            if source.startswith("recheck"):
                print("FAIL: a recheck under an edited Stage 0 block (method_matches_sweep false) overrides a "
                      "failed response.json; probe.py --session and model.py would proceed on it")
            else:
                print("PASS")
        except sweep.Refused as exc:
            print("PASS: refused:", exc)

        # string ordering of recheck numbers
        folder2 = Path(tmp) / "sweep-2"
        folder2.mkdir()
        (folder2 / "response.json").write_text(json.dumps({"outcome": "fail", "meter_norm": norm("fail")}))
        for n, outcome in ((9, "pass"), (10, "fail")):
            (folder2 / f"recheck-{n}.json").write_text(json.dumps({
                "outcome": outcome, "method_matches_sweep": True, "same_as_saved": outcome == "fail",
                "meter_norm": norm(f"r{n}")}))
        got, source = probe.stage0_meter_norm(folder2)
        print("RESULT with recheck-9 pass and the newer recheck-10 fail, stage0_meter_norm used:", source)
        if source == "recheck-9.json":
            print("FAIL: the newest recheck (recheck-10, fail) is skipped; recheck-9's pass is used")
        else:
            print("PASS")


if __name__ == "__main__":
    main()

"""mutate_den_take: apply gate-logic mutants to a temp copy of tools/den-take and run the narrowest tests.

Each mutant is one exact text replacement in check.py or take.py (the old text must occur once).
For each, the tools/den-take folder is copied to a fresh temp dir, the mutant applied, and
`python3 -B -m unittest -f <tests>` run there. Exit 0 = the mutant SURVIVED; non-zero = CAUGHT.

How to run
  python3 -B mutate_den_take.py <branch checkout root> [mutant ids...]
Synthetic data only; the suites use their own fake camera on 127.0.0.1; temp dirs only.
"""
import re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
SRC = ROOT / "tools" / "den-take"
T = "test_den_take."
F = T + "FaultTests."

MUTANTS = [
    ("M1", "check.py", 'round(elapsed, 9) <= m["elapsed_bound_s"]', 'round(elapsed, 9) < m["elapsed_bound_s"]',
     "elapsed bound <= becomes <", [F + "test_offset_between_slates", F + "test_offset_near_five_seconds_wraps", F + "test_clean_take_passes", T + "SlateRuleTests"]),
    ("M2", "check.py", 'elapsed = abs(delta) + (start["width_s"] + end["width_s"]) / 2', 'elapsed = abs(delta)',
     "elapsed bound drops the half-widths", [T + "FaultTests.test_offset_between_slates"]),
    ("M3", "check.py", 'delta = wrap(end["c_s"] - start["c_s"], m["slate_period_s"])',
     'delta = end["c_s"] - start["c_s"]', "no wrap of c_end - c_start", [T + "FaultTests.test_offset_near_five_seconds_wraps"]),
    ("M4", "check.py", 'and health_sessions == [next(iter(sessions))] * 2', '',
     "B2 ignores /health before and after", [F + "test_session_change", F + "test_clean_take_passes", T + "EndToEndTests"]),
    ("M5", "check.py", '            if any(f["stale"] for f in window):', '            if False:',
     "judged slate ignores stale frames", [T + "FaultTests.test_one_stale_frame_inside_a_slate", T + "FaultTests.test_stale_image_inside_a_slate"]),
    ("M6", "check.py", '            or len(rows) != (take.get("counts") or {}).get("kept")):', '            ):',
     "B5 ignores the recorded row count", [F + "test_edited_frames_csv_fails_hash_rule", F + "test_row_that_disagrees_with_its_bundle_fails_hash_rule", F + "test_changed_file_fails_hash_rule", T + "EndToEndTests"]),
    ("M7", "check.py", 'and abs(slope) * 1e6 <= m["max_two_clock_slope_ppm"]', '',
     "B4 drops the slope limit", [T + "FaultTests.test_device_clock_drifts_too_fast"]),
    ("M8", "check.py", 'separation is not None and separation <= m["max_slate_separation_s"]', 'separation is not None',
     "no slate separation limit", [T + "FaultTests.test_slates_too_far_apart"]),
    ("M9", "check.py", 'ok = bool(chosen and chosen["window_count"] == 1  # readable implies this; checked again\n                  and round(chosen["width_s"], 9) <= m["max_window_s"])',
     'ok = bool(chosen)', "judged slate width not re-checked", [T + "SlateRuleTests"]),
    ("M10", "take.py", '        if take["stop_reason"] is None and poller.stop_reason:  # stopped by itself near the end\n            take["stop_reason"] = poller.stop_reason\n',
     '', "poller stop after the script is not recorded", [T + "TakeScriptTests"]),
    ("M11", "take.py", '        self.pause(begin + staged["end_s"] - clock(), until=enter)\n', '',
     "staged take: end slate starts right after the last block, not at end_s", [T + "StagedScriptTests"]),
    ("M12", "check.py", '                  and float(row["t_host_s"]) == meta["t_host"]\n', '\n',
     "B5 ignores a t_host_s that disagrees with its bundle", [T + "FaultTests.test_row_that_disagrees_with_its_bundle_fails_hash_rule"]),
]


def main():
    wanted = set(sys.argv[2:])
    rows = []
    for mid, fname, old, new, what, tests in MUTANTS:
        if wanted and mid not in wanted:
            continue
        work = Path(tempfile.mkdtemp(prefix=f"mut-{mid}-")).resolve()
        try:
            dst = work / "den-take"
            shutil.copytree(SRC, dst)
            path = dst / fname
            text = path.read_text(encoding="utf-8")
            if text.count(old) != 1:
                rows.append((mid, fname, what, "not-run", f"old text found {text.count(old)} times"))
                continue
            path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
            t0 = time.time()
            proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f", *tests], cwd=dst,
                                  capture_output=True, text=True, timeout=900)
            took = time.time() - t0
            failed = re.findall(r"^(?:FAIL|ERROR): (\S+) \(([^)]+)\)", proc.stderr, re.M)
            result = "survived" if proc.returncode == 0 else "caught"
            by = ", ".join(f"{b}.{a}" for a, b in failed) or ("-" if result == "survived" else proc.stderr[-300:])
            ran = re.findall(r"Ran (\d+) tests?", proc.stderr)
            rows.append((mid, fname, what, result, f"{by} [ran {ran[-1] if ran else '?'} in {took:.0f}s]"))
            print(f"{mid} {result}: {by} [ran {ran[-1] if ran else '?'} in {took:.0f}s]", flush=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    print("\nRESULT id   file      result    mutant / caught by")
    for mid, fname, what, result, by in rows:
        print(f"RESULT {mid:<4} {fname:<9} {result:<9} {what} :: {by}")


if __name__ == "__main__":
    main()

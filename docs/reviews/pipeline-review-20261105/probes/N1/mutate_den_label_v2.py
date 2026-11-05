#!/usr/bin/env python3
"""Mutation runner for tools/den-label (N1, validate lens, second runner).

What it checks: for each targeted mutant of the N1 gate/verdict logic in
tools/den-label/n1.py and label.py, whether the narrowest relevant den-label
tests (failfast) catch it.

How to run (stdlib only, synthetic data only, temp dirs only):

    python3 -B mutate_den_label_v2.py <branch-root> [work-dir] [MUTANT_ID ...]
    python3 -B mutate_den_label_v2.py <branch-root> <work-dir> --combined M04,M05,...

<branch-root> is a clean checkout of claude/den-label (it is only read).
Each mutant is applied to a fresh copy of tools/den-label and tools/den-take
under <work-dir> (default: a new temp dir), and
`python3 -B -m unittest -f <tests>` is run from the copied tools/den-label.
--combined applies the listed mutants together and runs the whole den-label
suite once: if it still passes, each listed mutant survives the full suite.
Prints one RESULT line per mutant and a caught/survived table at the end.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

T = "test_den_label."
MUTANTS = [
    ("M01", "n1.py", "re-mark match threshold 0.5 -> 0.3 of each length",
     [('overlap >= 0.5 * (a["offset_t_s"] - a["onset_t_s"])', 'overlap >= 0.3 * (a["offset_t_s"] - a["onset_t_s"])'),
      ('overlap >= 0.5 * (b["offset_t_s"] - b["onset_t_s"])', 'overlap >= 0.3 * (b["offset_t_s"] - b["onset_t_s"])')],
     [T + "RemarkTests.test_agreement_on_hand_made_cases"]),
    ("M02", "n1.py", "re-mark match drops the second interval's half-length condition",
     [('\n                    and overlap >= 0.5 * (b["offset_t_s"] - b["onset_t_s"]) and overlap > 0):',
       ' and overlap > 0):')],
     [T + "RemarkTests.test_agreement_on_hand_made_cases"]),
    ("M03", "n1.py", "re-mark pairing in index order instead of largest overlap first",
     [("for _, i, j in sorted(candidates):", "for _, i, j in candidates:")],
     [T + "RemarkTests.test_agreement_on_hand_made_cases"]),
    ("M15", "n1.py", "re-mark p90 of end differences computed as p95",
     [('"p90_s": percentile(diffs, 90) and round(percentile(diffs, 90), 6)',
       '"p90_s": percentile(diffs, 95) and round(percentile(diffs, 95), 6)')],
     [T + "RemarkTests.test_agreement_on_hand_made_cases"]),
    ("M08", "n1.py", "load_split no longer checks split == rule(order)",
     [(' or entry["split"] != split_for(entry["order"]))', ")")],
     [T + "SplitTests"]),
    ("M09", "n1.py", "pause rule '<= pause_s' -> '< pause_s'",
     [('item["onset_t_s"] - prev["offset_t_s"] <= protocol.settings["pause_s"]',
       'item["onset_t_s"] - prev["offset_t_s"] < protocol.settings["pause_s"]')],
     [T + "LabelTests.test_the_pause_rule_never_applies_across_a_cut", T + "LabelTests.test_every_protocol_check"]),
    ("M10", "label.py", "re-mark wait 3 days -> 2 days",
     [("if n1.now() < last_edit + days * 86400:", "if n1.now() < last_edit + (days - 1) * 86400:")],
     [T + "RemarkTests.test_picks_the_second_development_session_waits_and_hides_the_first_labels",
      T + "RemarkBlindTests.test_the_wait_counts_from_the_first_passs_last_edit"]),
    ("M12", "n1.py", "union test skipped (newest check need not cover earlier cuts)",
     [("elif not covers(person_cuts(newest), required):", "elif False:")],
     [T + "SecondPersonTests.test_the_union_test"]),
    ("M13", "n1.py", "quiet seconds past tau_off ignore tau_off",
     [("scored_from = max(start, before[-1] + tau_off_s) if before else start", "scored_from = start")],
     [T + "StatusTests"]),
    ("M11", "n1.py", "freeze labels no longer refuses a first pass saved after the re-mark started",
     [('if first["actor"]["saved_unix"] and first["actor"]["saved_unix"] > remark["actor"]["started_unix"]:',
       "if False:")],
     [T + "RemarkBlindTests.test_agreement_comes_from_the_locked_first_pass"]),
    ("M04", "n1.py", "verify_folder no longer reports a missing file of a frozen folder",
     [('            problems.append(f"missing {rel}")', "            pass")],
     [T + "FreezeTests.test_freeze_verify_versions_and_refusals", T + "FreezeLogTests"]),
    ("M05", "n1.py", "verify_folder no longer reports an extra file in a frozen folder",
     [("for rel in sorted(present - set(expected)):", "for rel in sorted(set()):")],
     [T + "FreezeTests.test_freeze_verify_versions_and_refusals", T + "FreezeLogTests"]),
    ("M06", "n1.py", "verify_folder no longer checks the manifest is canonical JSON",
     [("    if canonical(manifest) != raw:\n        problems.append", "    if False:\n        problems.append")],
     [T + "FreezeTests.test_freeze_verify_versions_and_refusals", T + "FreezeLogTests"]),
    ("M07", "n1.py", "freeze labels no longer checks the fixture covers the same sessions and checks",
     [('if {s["take"]: s["check_sha256"] for s in fixture["sessions"]} != checks:', "if False:")],
     [T + "FreezeTests", T + "FreezeRecheckTests", T + "LabelTests.test_saves_go_through_the_checks_and_freeze_needs_end_to_end"]),
]
BY_ID = {m[0]: m for m in MUTANTS}


def fresh_copy(root, work, tag):
    dest = Path(tempfile.mkdtemp(prefix=f"{tag}-", dir=work))
    for name in ("den-label", "den-take"):
        shutil.copytree(Path(root) / "tools" / name, dest / "tools" / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dest


def apply(dest, file, edits):
    path = dest / "tools" / "den-label" / file
    text = path.read_text(encoding="utf-8")
    for old, new in edits:
        if text.count(old) != 1:
            raise SystemExit(f"mutant edit not unique/present in {file}: {old[:60]!r} (count {text.count(old)})")
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8", newline="\n")


def run_tests(dest, tests, timeout):
    cwd = dest / "tools" / "den-label"
    cmd = [sys.executable, "-B", "-m", "unittest", "-f"] + tests if tests else \
        [sys.executable, "-B", "-m", "unittest", "discover", "-f", "-s", "."]
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        out = proc.stdout + proc.stderr
        code = proc.returncode
    except subprocess.TimeoutExpired:
        return None, "TIMEOUT", time.time() - started
    tail = [line for line in out.splitlines() if line.startswith(("Ran ", "OK", "FAILED"))]
    first_fail = next((line for line in out.splitlines() if line.startswith(("FAIL:", "ERROR:"))), "")
    return code, " ".join(tail) + (f" [{first_fail}]" if first_fail else ""), time.time() - started


def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    root = Path(args[0]).resolve()
    work = Path(args[1]) if len(args) > 1 and not args[1].startswith(("M", "--")) else Path(tempfile.mkdtemp())
    rest = args[2:] if len(args) > 1 and not args[1].startswith(("M", "--")) else args[1:]
    work.mkdir(parents=True, exist_ok=True)
    if rest and rest[0] == "--combined":
        ids = rest[1].split(",")
        dest = fresh_copy(root, work, "combined")
        for mid in ids:
            apply(dest, BY_ID[mid][1], BY_ID[mid][3])
        code, summary, secs = run_tests(dest, None, 3000)
        verdict = "survived" if code == 0 else "caught"
        print(f"RESULT combined {','.join(ids)} full suite: {verdict} ({secs:.0f} s) :: {summary}", flush=True)
        shutil.rmtree(dest, ignore_errors=True)
        return
    ids = rest or [m[0] for m in MUTANTS]
    rows = []
    for mid in ids:
        _, file, what, edits, tests = BY_ID[mid]
        dest = fresh_copy(root, work, mid)
        apply(dest, file, edits)
        code, summary, secs = run_tests(dest, tests, 1500)
        verdict = "survived" if code == 0 else ("timeout" if code is None else "caught")
        rows.append((mid, verdict, what, summary, secs, tests))
        print(f"RESULT {mid} {verdict} ({secs:.0f} s) {what} :: {summary} :: tests={' '.join(tests)}", flush=True)
        shutil.rmtree(dest, ignore_errors=True)
    print("\nmutant  result    description")
    for mid, verdict, what, summary, secs, tests in rows:
        print(f"{mid:7s} {verdict:9s} {what}")


if __name__ == "__main__":
    main()

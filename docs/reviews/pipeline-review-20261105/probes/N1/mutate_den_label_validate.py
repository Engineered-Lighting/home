#!/usr/bin/env python3
"""mutate_den_label_validate: targeted mutants on den-label's gate and statistic logic, one at a time.

(VALIDATE lens. Saved under its own name because probes/N1/mutate_den_label.py is used by
another reviewer of the same unit.)

For each mutant: copies <checkout>/tools/den-label and <checkout>/tools/den-take into a fresh temp
dir under the given work dir, applies one exact text replacement (it must match exactly once),
runs the narrowest unittest names that should catch it with failfast (-f), and records
caught (tests fail) or survived (tests pass). Prints a caught/survived table.
Synthetic takes only (the branch's own test helpers); nothing leaves 127.0.0.1.

Run:
    python3 mutate_den_label_validate.py /path/to/den-label-checkout /path/to/scratch/work/dir [mutant-id ...]
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve()
ONLY = set(sys.argv[3:])

MUTANTS = [
    ("M1", "n1.py", "re-mark match threshold 0.5 -> 0.3 of each length",
     ['            if (overlap >= 0.5 * (a["offset_t_s"] - a["onset_t_s"])\n'
      '                    and overlap >= 0.5 * (b["offset_t_s"] - b["onset_t_s"]) and overlap > 0):',
      '            if (overlap >= 0.3 * (a["offset_t_s"] - a["onset_t_s"])\n'
      '                    and overlap >= 0.3 * (b["offset_t_s"] - b["onset_t_s"]) and overlap > 0):'],
     ["test_den_label.RemarkTests.test_agreement_on_hand_made_cases"]),
    ("M2", "n1.py", "re-mark match drops the second interval's half-length condition",
     ['                    and overlap >= 0.5 * (b["offset_t_s"] - b["onset_t_s"]) and overlap > 0):',
      '                    and overlap > 0):'],
     ["test_den_label.RemarkTests.test_agreement_on_hand_made_cases"]),
    ("M3", "n1.py", "verify_folder no longer reports an extra file in a frozen folder",
     ['    for rel in sorted(present - set(expected)):', '    for rel in sorted(set()):'],
     ["test_den_label.FreezeTests.test_freeze_verify_versions_and_refusals", "test_den_label.FreezeLogTests"]),
    ("M4", "n1.py", "load_split drops the split == rule(order) check",
     ['                or entry["order"] != counts[entry["script"]] or entry["split"] != split_for(entry["order"])):',
      '                or entry["order"] != counts[entry["script"]]):'],
     ["test_den_label.SplitTests"]),
    ("M5", "n1.py", "pause rule '<= pause_s' -> '< pause_s'",
     ['                and item["onset_t_s"] - prev["offset_t_s"] <= protocol.settings["pause_s"]):',
      '                and item["onset_t_s"] - prev["offset_t_s"] < protocol.settings["pause_s"]):'],
     ["test_den_label.LabelTests.test_the_pause_rule_never_applies_across_a_cut",
      "test_den_label.LabelTests.test_every_protocol_check"]),
    ("M6", "n1.py", "quiet/uncertain overlap test made strict (touching ends allowed)",
     ['            if a <= d and c <= b:', '            if a < d and c < b:'],
     ["test_den_label.LabelTests.test_every_protocol_check"]),
    ("M7", "n1.py", "freeze_labels skips the 'first pass saved after the re-mark started' refusal",
     ['        if first["actor"]["saved_unix"] and first["actor"]["saved_unix"] > remark["actor"]["started_unix"]:',
      '        if False:'],
     ["test_den_label.RemarkBlindTests"]),
    ("M8", "label.py", "re-mark wait counted from the end-to-end mark instead of the last edit",
     ['        last_edit = first["actor"]["saved_unix"] or first["actor"]["end_to_end"]',
      '        last_edit = first["actor"]["end_to_end"] or first["actor"]["saved_unix"]'],
     ["test_den_label.RemarkBlindTests.test_the_wait_counts_from_the_first_passs_last_edit"]),
    ("M9", "n1.py", "union test compares the newest check with the previous check only",
     ['    required = merge(s for report in parsed[:-1] for s in person_cuts(report))',
      '    required = merge(s for report in parsed[-2:-1] for s in person_cuts(report))'],
     ["test_den_label.SecondPersonTests.test_the_union_test"]),
    ("M10", "n1.py", "freeze_fixture skips the audited-end-to-end refusal",
     ['            if not audited_all(draft["audited"], usable):\n'
      '                raise Refused(f"{entry[\'session\']} was not audited end to end")',
      '            if False:\n'
      '                raise Refused(f"{entry[\'session\']} was not audited end to end")'],
     ["test_den_label.FixtureTests.test_refuses_an_unaudited_session_and_box_edits_after_the_label_pass_starts"]),
    ("M11", "n1.py", "quiet seconds past tau_off ignore tau_off",
     ['        scored_from = max(start, before[-1] + tau_off_s) if before else start',
      '        scored_from = start'],
     ["test_den_label.StatusTests"]),
    ("M12", "n1.py", "verify_folder no longer checks that the manifest is canonical JSON",
     ['    if canonical(manifest) != raw:', '    if False:'],
     ["test_den_label.FreezeTests.test_freeze_verify_versions_and_refusals", "test_den_label.FreezeLogTests"]),
]


def run(mid, fname, what, repl, tests):
    work = Path(tempfile.mkdtemp(prefix=f"mut-{mid}-", dir=WORK))
    for sub in ("den-label", "den-take"):
        shutil.copytree(ROOT / "tools" / sub, work / "tools" / sub,
                        ignore=shutil.ignore_patterns("__pycache__"))
    target = work / "tools" / "den-label" / fname
    text = target.read_text(encoding="utf-8")
    old, new = repl
    if text.count(old) != 1:
        return mid, what, "not-run", f"pattern found {text.count(old)} times"
    target.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
    started = time.time()
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-f"] + tests,
                          cwd=work / "tools" / "den-label", capture_output=True, text=True, timeout=1500)
    tail = (proc.stderr.strip().splitlines() or [""])[-1]
    result = "caught" if proc.returncode != 0 else "survived"
    shutil.rmtree(work, ignore_errors=True)
    return mid, what, result, f"{tail} ({time.time() - started:.0f} s) tests={' '.join(t.split('.', 1)[1] for t in tests)}"


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    rows = []
    for m in MUTANTS:
        if ONLY and m[0] not in ONLY:
            continue
        row = run(*m)
        rows.append(row)
        print(f"{row[0]:4} {row[2]:9} {row[1]} :: {row[3]}", flush=True)
    print("\nTABLE")
    for mid, what, result, note in rows:
        print(f"{mid:4} | {result:9} | {what}")
    print(f"RESULT caught {sum(r[2] == 'caught' for r in rows)} survived {sum(r[2] == 'survived' for r in rows)} "
          f"not-run {sum(r[2] == 'not-run' for r in rows)}")


if __name__ == "__main__":
    main()

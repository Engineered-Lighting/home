#!/usr/bin/env python3
"""verify-tdd.py — enforces the TDD discipline ritual (Addendum 18a AR2-2).

For each test that claims to catch a specific bug, this script:
    1. Runs the test → MUST currently pass
    2. Reverts the fix (via git stash of a specified line range OR
       a marker comment hint OR a custom revert script)
    3. Re-runs the test → MUST fail
    4. Restores the fix
    5. Re-runs the test → MUST pass

If the fail-then-pass pattern is NOT observed, the test is vacuous
(it doesn't actually catch the bug it claims to catch). Exit code is
0 only when every requested test passes the ritual.

Usage:
    py -3 tools/verify-tdd.py --test-cmd "node tools/run-state-sync-tests.js" \\
                              --revert-file app/src/home-control.jsx \\
                              --revert-marker "mountFetchedRef"

Modes:
    --revert-file + --revert-marker → comments out lines matching marker
    --revert-script <path>          → runs a custom script that reverts
    --revert-git-range A:B           → uses git restore + range

By default the script never touches anything outside the test file
and the revert region — and ALWAYS restores even on Ctrl+C.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        cwd=str(cwd or REPO_ROOT), env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _backup_file(path: Path) -> Path:
    fd, tmp_str = tempfile.mkstemp(suffix=path.suffix)
    os.close(fd)  # Windows: must close handle before shutil can write
    tmp = Path(tmp_str)
    shutil.copy2(path, tmp)
    return tmp


def _restore_file(backup: Path, target: Path) -> None:
    shutil.copy2(backup, target)
    backup.unlink(missing_ok=True)


def _comment_out_marker(path: Path, marker: str) -> int:
    """Comment out every line containing `marker` using the file's
    natural comment syntax. Returns the number of lines commented.
    Currently supports JS/JSX (// prefix) and Python (# prefix)."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    suffix = path.suffix.lower()
    if suffix in (".js", ".jsx", ".ts", ".tsx"):
        prefix = "// REVERTED-BY-TDD: "
    elif suffix in (".py",):
        prefix = "# REVERTED-BY-TDD: "
    else:
        prefix = "/* REVERTED-BY-TDD */ "
    count = 0
    new_lines = []
    for line in lines:
        if marker in line and not line.lstrip().startswith(("//", "#")):
            new_lines.append(prefix + line)
            count += 1
        else:
            new_lines.append(line)
    path.write_text("".join(new_lines), encoding="utf-8")
    return count


@contextlib.contextmanager
def _reverted_marker(path: Path, marker: str):
    """Context manager that reverts (comments out) marker lines and
    ALWAYS restores from backup on exit (even on Ctrl+C / exception)."""
    backup = _backup_file(path)
    try:
        n = _comment_out_marker(path, marker)
        if n == 0:
            raise SystemExit(f"verify-tdd: no lines matched marker {marker!r} in {path}")
        print(f"[verify-tdd] reverted {n} line(s) matching {marker!r} in {path}")
        yield n
    finally:
        _restore_file(backup, path)
        print(f"[verify-tdd] restored {path}")


@contextlib.contextmanager
def _reverted_script(script: Path):
    """Context manager that runs `script revert` on enter and
    `script restore` on exit. The script is responsible for its own
    backup/restore mechanism."""
    rc, _, err = _run([str(script), "revert"])
    if rc != 0:
        raise SystemExit(f"verify-tdd: revert script failed:\n{err}")
    try:
        yield
    finally:
        rc, _, err = _run([str(script), "restore"])
        if rc != 0:
            print(f"WARN: restore script returned {rc}: {err}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-cmd", required=True,
                        help="Shell command that runs the specific test")
    parser.add_argument("--revert-file",
                        help="File to comment-revert a marker in")
    parser.add_argument("--revert-marker",
                        help="String to grep for + comment out")
    parser.add_argument("--revert-script",
                        help="Path to a script with `revert`/`restore` modes")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if not (args.revert_marker and args.revert_file) and not args.revert_script:
        parser.error("Must provide either --revert-script OR (--revert-file AND --revert-marker)")

    test_cmd = args.test_cmd.split() if isinstance(args.test_cmd, str) else args.test_cmd

    def _run_test(label: str) -> bool:
        if not args.quiet:
            print(f"\n[verify-tdd] {label}: {' '.join(test_cmd)}")
        rc, out, err = _run(test_cmd)
        if not args.quiet:
            # Show only the footer line
            footer_lines = [l for l in out.splitlines() if "pass" in l.lower() and "fail" in l.lower()]
            if footer_lines:
                print(f"  → {footer_lines[-1].strip()}")
            else:
                print(f"  → exit {rc}")
        return rc == 0

    # Step 1: test currently passes
    print("\n=== Step 1: test currently passes (post-fix) ===")
    if not _run_test("RUN"):
        print("FAIL: test does not currently pass. Run the test by hand first.", file=sys.stderr)
        return 1

    # Step 2-3: revert + test fails
    revert_ctx = (_reverted_marker(Path(args.revert_file), args.revert_marker)
                  if args.revert_file else
                  _reverted_script(Path(args.revert_script)))

    with revert_ctx:
        print("\n=== Step 2/3: test fails after revert ===")
        if _run_test("RUN-AFTER-REVERT"):
            print("\nFAIL: test STILL passes after reverting the fix. "
                  "This means the test is vacuous — it doesn't actually "
                  "depend on the fix.", file=sys.stderr)
            # File is restored automatically by the context manager.
            return 2

    # Step 4-5: restored, test passes again
    print("\n=== Step 4/5: test passes after restore ===")
    if not _run_test("RUN-AFTER-RESTORE"):
        print("\nFAIL: test does not pass after restore. The restore "
              "may have failed — check the file.", file=sys.stderr)
        return 3

    print("\nverify-tdd PASS — the test catches the bug it claims to catch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Offline prune of the LA camera pilot's live-scene ledger (speed plan S1.4).

Run with victoria-camera-pilot.service stopped, from the deployed release
directory, before the restart that lowers retention_days:

    python3 -m home_shared.lab_live_prune --config <private>/live-scene.json [--dry-run]

It reads retention_days from live-scene.json (enabled or not), opens the pilot
ledger beside it (admission.sqlite; never creates one), takes the ledger's
lifetime lock, so it refuses while the pilot runs, and deletes the terminal
live_requests rows accepted before now - retention_days, oldest first, in
bounded batches of one short statement each. Unresolved live rows and every
camera row are never touched. --dry-run counts only. One summary line goes to
stdout; exit 0, or 2 with "live ledger prune refused: <reason>" on stderr.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys
import time
from typing import Callable

from .admission import LedgerLocked, _LifetimeLock
from .lab_live_pilot import LEDGER_NAME, prune_live_rows, read_live_config

BATCH_ROWS = 5000   # rows per delete statement (the pilot is stopped; this bounds each transaction)


def prune(config_path: str | Path, *, dry_run: bool = False, enforce_linux: bool = False,
          clock: Callable[[], float] = time.time, batch_rows: int = BATCH_ROWS) -> dict:
    """Counts {expired, deleted, remaining, batches, max_records}; raises ValueError
    (or LedgerLocked, an AdmissionError) when it must refuse."""
    if type(batch_rows) is not int or not 1 <= batch_rows <= 100_000:
        raise ValueError("bounded batch size required")
    config = read_live_config(config_path, enforce_linux=enforce_linux)
    if config is None:
        raise ValueError("live-scene.json not found")
    ledger = Path(config_path).absolute().parent / LEDGER_NAME
    if not ledger.is_file() or ledger.is_symlink():
        raise ValueError("pilot ledger not found beside live-scene.json")
    lock = _LifetimeLock(Path(str(ledger) + ".lock"))
    try:
        db = sqlite3.connect(ledger.as_uri() + "?mode=rw", uri=True, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA synchronous=FULL")
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_requests'").fetchone() is None:
                return {"expired": 0, "deleted": 0, "remaining": 0, "batches": 0, "max_records": config.max_records}
            cutoff = clock() - config.retention_days * 86400
            expired = db.execute("SELECT COUNT(*) FROM live_requests WHERE state='terminal' AND accepted<?",
                                 (cutoff,)).fetchone()[0]
            deleted = batches = 0
            while not dry_run:
                count = prune_live_rows(db, cutoff, batch_rows)
                if count <= 0:
                    break
                deleted, batches = deleted + count, batches + 1
            remaining = db.execute("SELECT COUNT(*) FROM live_requests").fetchone()[0]
        finally:
            db.close()
    finally:
        lock.close()
    return {"expired": expired, "deleted": deleted, "remaining": remaining, "batches": batches,
            "max_records": config.max_records}


def main(argv: list[str] | None = None, *, enforce_linux: bool | None = None,
         clock: Callable[[], float] = time.time) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m home_shared.lab_live_prune",
                                     description="Prune expired live-scene ledger rows with the pilot stopped.")
    parser.add_argument("--config", required=True, help="path of private/live-scene.json")
    parser.add_argument("--dry-run", action="store_true", help="count the expired rows; delete nothing")
    arguments = parser.parse_args(argv)
    try:
        result = prune(arguments.config, dry_run=arguments.dry_run, clock=clock,
                       enforce_linux=sys.platform == "linux" if enforce_linux is None else enforce_linux)
    except LedgerLocked:
        print("live ledger prune refused: the camera pilot is running (ledger locked)", file=sys.stderr, flush=True)
        return 2
    except (OSError, ValueError, sqlite3.Error) as error:
        print("live ledger prune refused: " + type(error).__name__ + ": " + str(error)[:120], file=sys.stderr, flush=True)
        return 2
    print("live ledger prune%s: expired=%d deleted=%d batches=%d remaining=%d max_records=%d" % (
        " (dry run)" if arguments.dry_run else "", result["expired"], result["deleted"], result["batches"],
        result["remaining"], result["max_records"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

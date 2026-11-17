"""Hop 3 on each click: is this take still the one that was recorded?

den-observe's replay checks only each bundle's sequence and time against frames.csv, so a take with
one scene's pixels and another's depth replays without a word. This hashes every frame file against
frames.csv's npz_sha256, and frames.csv against take.json (and, when given, the take digest that
prepare-receipt.json is keyed by). Anything that differs reads MISSING, with the reason.
"""
import csv
import hashlib
import io
import json
import re
from pathlib import Path

FRAME_FILE = re.compile(r"frames/[0-9]{6}-[0-9]+\.npz")


def check_take(take_dir, expected_digest=None):
    """{"ok", "word", "take_digest", "frames", "reason"}: word is None when ok, else "MISSING"."""
    folder, digest, rows = Path(take_dir), None, []

    def missing(reason):
        return {"ok": False, "word": "MISSING", "take_digest": digest, "frames": len(rows), "reason": reason}
    try:
        table = (folder / "frames.csv").read_bytes()
        recorded = json.loads((folder / "take.json").read_text(encoding="utf-8"))["sha256"]["frames.csv"]
        rows = list(csv.DictReader(io.StringIO(table.decode("utf-8"), newline="")))
    except (OSError, ValueError, KeyError, TypeError):
        return missing("take.json or frames.csv is missing or unreadable")
    digest = hashlib.sha256(table).hexdigest()
    if digest != recorded:
        return missing("frames.csv does not match the digest take.json recorded")
    if expected_digest is not None and digest != expected_digest:
        return missing("this is not the take prepare recorded: its frames.csv digest differs")
    for row in rows:
        name = row.get("file") or ""
        try:
            same = FRAME_FILE.fullmatch(name) and hashlib.sha256((folder / name).read_bytes()).hexdigest() == row.get("npz_sha256")
        except OSError:
            same = False
        if not same:
            return missing(f"frame {row.get('n')} ({name}) changed or is gone since it was recorded")
    if not rows:
        return missing("the take has no frames")
    return {"ok": True, "word": None, "take_digest": digest, "frames": len(rows), "reason": None}

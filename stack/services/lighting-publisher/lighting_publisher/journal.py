"""The publisher's decision journal: one JSONL file per day, kept 30 days.

Every tick's decision (and every refusal to decide) is appended as one JSON
object per line to ``<dir>/decisions-YYYY-MM-DD.jsonl``. The date comes from
the timestamp the caller passes, so the journal is as deterministic as the
machines are. Files older than ``RETENTION_DAYS`` are deleted when the day
rolls over and once at startup.

The journal is best effort on purpose: the container's only writable mount is
the journal volume, and a full disk, a read-only remount or a permission
change must never stop the publisher from publishing. Write failures are
counted and reported through ``/healthz``; they never raise.

Nothing here formats a household name, an entity value or a secret: the
caller decides what a record contains.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re

RETENTION_DAYS = 30
FILE_PREFIX = "decisions"
FILE_SUFFIX = ".jsonl"
FILE_RE = re.compile(r"^" + FILE_PREFIX + r"-(\d{4})-(\d{2})-(\d{2})" + re.escape(FILE_SUFFIX) + r"$")
DIR_MODE = 0o755
FILE_MODE = 0o644


class Journal:
    """Append-only daily JSONL with retention. Never raises on I/O failure."""

    def __init__(self, directory: str | os.PathLike | None, retention_days: int = RETENTION_DAYS,
                 enabled: bool = True) -> None:
        self.directory = pathlib.Path(directory) if directory is not None else None
        self.retention_days = int(retention_days)
        self.enabled = bool(enabled and self.directory is not None)
        self.writes = 0
        self.errors = 0
        self.last_error: str | None = None
        self.removed: list[str] = []
        self._current_day: dt.date | None = None

    # --- paths ---------------------------------------------------------------

    def path_for(self, day: dt.date) -> pathlib.Path:
        """The file a record stamped on ``day`` belongs in."""
        assert self.directory is not None
        return self.directory / f"{FILE_PREFIX}-{day.isoformat()}{FILE_SUFFIX}"

    def _ensure_dir(self) -> bool:
        assert self.directory is not None
        try:
            self.directory.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
            return True
        except OSError as exc:
            self._note_error(exc)
            return False

    def _note_error(self, exc: Exception) -> None:
        self.errors += 1
        # The class and errno only: a path or payload could carry house detail.
        self.last_error = type(exc).__name__

    # --- the two operations ---------------------------------------------------

    def write(self, record: dict, now: dt.datetime) -> bool:
        """Append one record; rotate first when the day changed. Never raises."""
        if not self.enabled:
            return False
        day = now.date()
        if day != self._current_day:
            self._current_day = day
            self.rotate(now)
        if not self._ensure_dir():
            return False
        try:
            line = json.dumps(record, sort_keys=True, ensure_ascii=True)
        except (TypeError, ValueError) as exc:
            self._note_error(exc)
            return False
        try:
            path = self.path_for(day)
            with open(path, "a", encoding="ascii") as handle:
                handle.write(line + "\n")
            self.writes += 1
            return True
        except OSError as exc:
            self._note_error(exc)
            return False

    def rotate(self, now: dt.datetime) -> list[str]:
        """Delete journals older than the retention window. Never raises."""
        if not self.enabled:
            return []
        cutoff = now.date() - dt.timedelta(days=self.retention_days)
        removed: list[str] = []
        try:
            names = sorted(os.listdir(self.directory))
        except OSError as exc:
            self._note_error(exc)
            return []
        for name in names:
            match = FILE_RE.match(name)
            if not match:
                continue
            try:
                day = dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                continue
            if day >= cutoff:
                continue
            try:
                os.remove(self.directory / name)
                removed.append(name)
            except OSError as exc:
                self._note_error(exc)
        self.removed.extend(removed)
        return removed

    def as_dict(self) -> dict:
        """What ``/healthz`` reports about the journal."""
        return {"enabled": self.enabled, "writes": self.writes, "errors": self.errors,
                "last_error": self.last_error, "retention_days": self.retention_days}

#!/usr/bin/env python3
"""Fail-closed structural validation for a staged pgBackRest repository."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import sys
from typing import Sequence


FULL_BACKUP_LABEL = re.compile(r"[0-9]{8}-[0-9]{6}F")
STANZA_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class RepositoryValidationError(ValueError):
    """The repository contains an entry outside the restore-drill contract."""


def _lstat(path: Path, description: str) -> os.stat_result:
    try:
        return path.lstat()
    except FileNotFoundError as exc:
        raise RepositoryValidationError(f"{description} is missing") from exc
    except OSError as exc:
        raise RepositoryValidationError(
            f"cannot inspect {description}: {exc.strerror or type(exc).__name__}"
        ) from exc


def _require_plain_directory(
    path: Path,
    description: str,
    repository_device: int,
) -> os.stat_result:
    metadata = _lstat(path, description)
    if not stat.S_ISDIR(metadata.st_mode):
        raise RepositoryValidationError(
            f"{description} must be an existing non-symlink directory"
        )
    if metadata.st_dev != repository_device:
        raise RepositoryValidationError(
            f"{description} crosses the repository filesystem boundary"
        )
    return metadata


def _validate_latest_link(
    latest: Path,
    stanza_directory: Path,
    repository_device: int,
) -> None:
    try:
        target = os.readlink(latest)
    except OSError as exc:
        raise RepositoryValidationError(
            f"cannot read the pgBackRest latest link: "
            f"{exc.strerror or type(exc).__name__}"
        ) from exc

    if FULL_BACKUP_LABEL.fullmatch(target) is None:
        raise RepositoryValidationError(
            "pgBackRest latest must use a safe relative full-backup label target"
        )

    target_path = stanza_directory / target
    target_metadata = _require_plain_directory(
        target_path,
        "pgBackRest latest target",
        repository_device,
    )
    if stat.S_ISLNK(target_metadata.st_mode):
        # S_ISDIR above already rejects this, but retain the explicit invariant
        # beside the canonical containment check.
        raise RepositoryValidationError(
            "pgBackRest latest target may not be a symlink"
        )

    try:
        stanza_resolved = stanza_directory.resolve(strict=True)
        target_resolved = target_path.resolve(strict=True)
    except OSError as exc:
        raise RepositoryValidationError(
            "cannot resolve the pgBackRest latest target"
        ) from exc

    if (
        target_resolved.parent != stanza_resolved
        or target_resolved.name != target
    ):
        raise RepositoryValidationError(
            "pgBackRest latest target must resolve inside the same stanza directory"
        )


def validate_repository(repository: Path, stanza: str) -> None:
    """Validate the repository without following unapproved links."""

    if not repository.is_absolute():
        raise RepositoryValidationError("repository path must be absolute")
    if STANZA_NAME.fullmatch(stanza) is None:
        raise RepositoryValidationError("invalid pgBackRest stanza name")

    repository_metadata = _lstat(repository, "repository root")
    if not stat.S_ISDIR(repository_metadata.st_mode):
        raise RepositoryValidationError(
            "repository root must be an existing non-symlink directory"
        )
    repository_device = repository_metadata.st_dev

    backup_directory = repository / "backup"
    _require_plain_directory(
        backup_directory,
        "pgBackRest backup directory",
        repository_device,
    )
    stanza_directory = backup_directory / stanza
    _require_plain_directory(
        stanza_directory,
        "pgBackRest stanza directory",
        repository_device,
    )
    allowed_latest = stanza_directory / "latest"

    pending = [repository]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise RepositoryValidationError(
                f"cannot enumerate repository directory: "
                f"{exc.strerror or type(exc).__name__}"
            ) from exc

        for entry in entries:
            path = Path(entry.path)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise RepositoryValidationError(
                    f"cannot inspect repository entry: "
                    f"{exc.strerror or type(exc).__name__}"
                ) from exc

            if path == allowed_latest:
                if not stat.S_ISLNK(metadata.st_mode):
                    raise RepositoryValidationError(
                        "backup/<stanza>/latest is reserved for the pgBackRest "
                        "latest symlink"
                    )
                _validate_latest_link(
                    path,
                    stanza_directory,
                    repository_device,
                )
                continue

            if stat.S_ISLNK(metadata.st_mode):
                raise RepositoryValidationError(
                    "repository contains a symlink outside backup/<stanza>/latest"
                )
            if metadata.st_dev != repository_device:
                raise RepositoryValidationError(
                    "repository entry crosses the repository filesystem boundary"
                )
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(path)
            elif not stat.S_ISREG(metadata.st_mode):
                raise RepositoryValidationError(
                    "repository contains a special file"
                )


def main(argv: Sequence[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: pgbackrest_repository_guard.py <repository> <stanza>",
            file=sys.stderr,
        )
        return 64

    try:
        validate_repository(Path(argv[1]), argv[2])
    except RepositoryValidationError as exc:
        print(f"pgBackRest repository guard: {exc}", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

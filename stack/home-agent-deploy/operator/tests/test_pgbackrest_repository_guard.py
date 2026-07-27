from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


OPERATOR = Path(__file__).resolve().parents[1]
GUARD_PATH = OPERATOR / "pgbackrest_repository_guard.py"
SPEC = importlib.util.spec_from_file_location("pgbackrest_repository_guard", GUARD_PATH)
assert SPEC is not None and SPEC.loader is not None
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


FULL_LABEL = "20260719-031500F"
DIFFERENTIAL_LABEL = "20260719-031500F_20260720-031500D"


class PgBackRestRepositoryGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repository = Path(self.temporary_directory.name).resolve() / "repository"
        self.stanza = self.repository / "backup" / "home-agent"
        self.full_backup = self.stanza / FULL_LABEL
        self.full_backup.mkdir(parents=True)
        (self.stanza / "backup.info").write_text("fixture", encoding="utf-8")
        archive = self.repository / "archive" / "home-agent"
        archive.mkdir(parents=True)
        (archive / "archive.info").write_text("fixture", encoding="utf-8")

    def make_symlink(
        self,
        target: str | Path,
        link: Path,
        *,
        target_is_directory: bool = True,
    ) -> None:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")

    def assert_rejected(self, message: str) -> None:
        with self.assertRaisesRegex(guard.RepositoryValidationError, message):
            guard.validate_repository(self.repository, "home-agent")

    def test_repository_without_latest_link_is_valid(self) -> None:
        guard.validate_repository(self.repository, "home-agent")

    def test_exact_latest_link_to_existing_full_backup_is_valid(self) -> None:
        self.make_symlink(FULL_LABEL, self.stanza / "latest")

        guard.validate_repository(self.repository, "home-agent")

    def test_latest_rejects_absolute_parent_and_non_full_targets(self) -> None:
        targets = (
            str(self.full_backup),
            f"../home-agent/{FULL_LABEL}",
            DIFFERENTIAL_LABEL,
            "not-a-backup",
        )
        for target in targets:
            with self.subTest(target=target):
                latest = self.stanza / "latest"
                if latest.is_symlink():
                    latest.unlink()
                self.make_symlink(target, latest)
                self.assert_rejected("safe relative full-backup label")

    def test_latest_rejects_missing_and_non_directory_targets(self) -> None:
        latest = self.stanza / "latest"
        self.make_symlink("20260721-031500F", latest)
        self.assert_rejected("latest target is missing")

        latest.unlink()
        regular_target = self.stanza / "20260722-031500F"
        regular_target.write_text("not a directory", encoding="utf-8")
        self.make_symlink(regular_target.name, latest, target_is_directory=False)
        self.assert_rejected("existing non-symlink directory")

    def test_latest_rejects_a_symlinked_full_backup_directory(self) -> None:
        linked_label = self.stanza / "20260723-031500F"
        self.make_symlink(FULL_LABEL, linked_label)
        self.make_symlink(linked_label.name, self.stanza / "latest")

        self.assert_rejected(
            "existing non-symlink directory|"
            "symlink outside backup/<stanza>/latest"
        )

    def test_reserved_latest_path_rejects_regular_files_and_directories(self) -> None:
        latest = self.stanza / "latest"
        latest.write_text("not a link", encoding="utf-8")
        self.assert_rejected("reserved for the pgBackRest latest symlink")

        latest.unlink()
        latest.mkdir()
        self.assert_rejected("reserved for the pgBackRest latest symlink")

    def test_every_other_symlink_is_rejected(self) -> None:
        self.make_symlink("backup.info", self.stanza / "unexpected-link")

        self.assert_rejected("symlink outside backup/<stanza>/latest")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation is unavailable")
    def test_special_file_is_rejected(self) -> None:
        fifo = self.full_backup / "unexpected-fifo"
        try:
            os.mkfifo(fifo)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"FIFO creation is unavailable: {exc}")

        self.assert_rejected("special file")


if __name__ == "__main__":
    unittest.main()

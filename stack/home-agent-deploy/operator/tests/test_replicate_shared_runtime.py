from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


OPERATOR = Path(__file__).resolve().parents[1]
SCRIPT = OPERATOR / "replicate_shared_runtime.py"
SYSTEMD = OPERATOR / "systemd"
DOC = OPERATOR / "SHARED-RUNTIME-BACKUP.md"
SPEC = importlib.util.spec_from_file_location("replicate_shared_runtime", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
backup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backup
SPEC.loader.exec_module(backup)

TOOLS = backup.Tools(openssl="openssl", rclone="rclone", findmnt="findmnt")
SECRET_TEXT = "never-print-this-secret-value"
FAKE_DER = b"\x30\x82fake-recipient-certificate-der"
FAKE_PEM = (
    b"-----BEGIN CERTIFICATE-----\n"
    + base64.encodebytes(FAKE_DER)
    + b"-----END CERTIFICATE-----\n"
)
FAKE_FINGERPRINT = hashlib.sha256(FAKE_DER).hexdigest()


def env_text(**overrides: str) -> bytes:
    values = {
        "HOME_AGENT_SHARED_RUNTIME_SOURCE_ROOT": "/srv/home-agent/shared-preferences/prepared-20260928",
        "HOME_AGENT_SHARED_RUNTIME_RCLONE_CONFIG": "/srv/home-agent/secrets/shared-runtime-rclone/rclone.conf",
        "HOME_AGENT_SHARED_RUNTIME_REMOTE": "home-agent-offhost",
        "HOME_AGENT_SHARED_RUNTIME_RECIPIENT_CERT": "/srv/home-agent/config/shared-runtime-backup-recipient.pem",
        "HOME_AGENT_SHARED_RUNTIME_RECIPIENT_SHA256": FAKE_FINGERPRINT,
    }
    values.update(overrides)
    return "".join(f"{key}={value}\n" for key, value in values.items() if value).encode()


def make_database(path: Path, *, wal: bool = False, rows: int = 3) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL" if wal else "PRAGMA journal_mode=DELETE")
        connection.execute("CREATE TABLE entries(id INTEGER PRIMARY KEY, value BLOB NOT NULL)")
        connection.executemany(
            "INSERT INTO entries(value) VALUES (?)", [(os.urandom(16),) for _ in range(rows)]
        )
        connection.commit()
    finally:
        connection.close()


class FakeRemote:
    def __init__(self, existing: dict[str, bytes] | None = None) -> None:
        self.objects: dict[str, bytes] = dict(existing or {})
        self.calls: list[tuple[str, ...]] = []
        self.corrupt_downloads = False

    def rclone(self, tools, config, *arguments, timeout=None, accepted_codes=frozenset({0})):
        self.calls.append(arguments)
        verb = arguments[0]
        if verb == "lsf":
            root = arguments[-1].split(":", 1)[1] + "/"
            names = sorted({key[len(root):].split("/")[0] + "/" for key in self.objects if key.startswith(root)})
            return "".join(f"{name}\n" for name in names).encode()
        if verb == "copyto":
            assert "--immutable" in arguments
            local, remote = arguments[-2], arguments[-1].split(":", 1)[1]
            if remote in self.objects:
                raise backup.BackupError("rclone failed")
            self.objects[remote] = Path(local).read_bytes()
            return b""
        if verb == "purge":
            root = arguments[-1].split(":", 1)[1] + "/"
            for key in [key for key in self.objects if key.startswith(root)]:
                del self.objects[key]
            return b""
        raise AssertionError(f"unexpected rclone verb {verb}")

    def download(self, tools, config, remote_path, *, maximum):
        self.calls.append(("cat", remote_path))
        data = self.objects[remote_path.split(":", 1)[1]]
        if self.corrupt_downloads:
            data = data[:-1] + bytes([data[-1] ^ 1])
        if len(data) > maximum:
            raise backup.BackupError("remote verification size mismatch")
        return hashlib.sha256(data).hexdigest(), len(data), data[: backup.MAX_SMALL_BYTES]


class SharedRuntimeFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "prepared-20260928"
        for service in backup.SERVICE_DIRS:
            for subdir in backup.SERVICE_SUBDIRS:
                (self.source / service / subdir).mkdir(parents=True)
            (self.source / service / "config" / "listener.json").write_text("{}", encoding="utf-8")
            (self.source / service / "secrets" / "service_token").write_text(SECRET_TEXT, encoding="utf-8")
        (self.source / "authority").mkdir()
        (self.source / "authority" / "ca.pem").write_text("ca", encoding="utf-8")
        (self.source / "authority" / "ca.key").write_text(SECRET_TEXT, encoding="utf-8")
        (self.source / "commissioning.env").write_text("x", encoding="utf-8")
        make_database(self.source / "echo-preferences" / "journals" / "consent.sqlite")
        make_database(self.source / "link-coordinator" / "journals" / "issuance.sqlite")
        make_database(self.source / "link-coordinator" / "journals" / "confirmations")
        bff = self.source / "echo-bff" / "journals"
        make_database(bff / "auth-proof.sqlite")
        make_database(bff / "ha-revocation.sqlite", wal=True)
        make_database(bff / "ha-revocation.sqlite.owner.sqlite")
        make_database(bff / "sessions.sqlite", wal=True)
        make_database(bff / "sessions.sqlite.owner.sqlite")
        (bff / "sessions.sqlite-wal").write_bytes(b"")
        self.config = backup.Config(
            source_root=self.source,
            rclone_config=self.root / "rclone.conf",
            remote="home-agent-offhost",
            prefix="HomeAgent/SharedRuntime",
            recipient_cert=self.root / "recipient.pem",
            recipient_sha256=FAKE_FINGERPRINT,
            keep=7,
            expected_mapper="/dev/mapper/home-agent",
        )
        self.config.recipient_cert.write_bytes(FAKE_PEM)
        self.work = self.root / "work"
        self.work.mkdir()


class EnvironmentTests(unittest.TestCase):
    def test_reviewed_environment_parses_with_defaults(self) -> None:
        config = backup.parse_env(b"# reviewed\n\n" + env_text())
        self.assertEqual(config.prefix, "HomeAgent/SharedRuntime")
        self.assertEqual(config.keep, 30)
        self.assertEqual(config.expected_mapper, "/dev/mapper/home-agent")
        self.assertEqual(config.source_root.as_posix(), "/srv/home-agent/shared-preferences/prepared-20260928")

    def test_unsafe_environment_fails_closed(self) -> None:
        cases = {
            "unknown key": env_text() + b"HOME_AGENT_OTHER=1\n",
            "duplicate": env_text() + b"HOME_AGENT_SHARED_RUNTIME_REMOTE=other\n",
            "quoted": env_text(HOME_AGENT_SHARED_RUNTIME_REMOTE="'x'"),
            "expansion": env_text(HOME_AGENT_SHARED_RUNTIME_REMOTE="$REMOTE"),
            "missing": env_text(HOME_AGENT_SHARED_RUNTIME_RECIPIENT_SHA256=""),
            "pgbackrest": env_text(HOME_AGENT_SHARED_RUNTIME_PREFIX="HomeAgent/pgBackRest/shared"),
            "pgbackrest parent": env_text(HOME_AGENT_SHARED_RUNTIME_PREFIX="HomeAgent"),
            "ledger": env_text(HOME_AGENT_SHARED_RUNTIME_PREFIX="HomeAgent/erasure-ledger"),
            "traversal": env_text(HOME_AGENT_SHARED_RUNTIME_PREFIX="HomeAgent/../x"),
            "source nested": env_text(
                HOME_AGENT_SHARED_RUNTIME_SOURCE_ROOT="/srv/home-agent/shared-preferences/a/b"
            ),
            "source outside": env_text(HOME_AGENT_SHARED_RUNTIME_SOURCE_ROOT="/srv/home-agent/durable"),
            "rclone shared": env_text(
                HOME_AGENT_SHARED_RUNTIME_RCLONE_CONFIG="/srv/home-agent/secrets/offhost-rclone/rclone.conf"
            ),
            "recipient outside": env_text(HOME_AGENT_SHARED_RUNTIME_RECIPIENT_CERT="/tmp/recipient.pem"),
            "keep": env_text(HOME_AGENT_SHARED_RUNTIME_KEEP="2"),
            "fingerprint": env_text(HOME_AGENT_SHARED_RUNTIME_RECIPIENT_SHA256="ABC"),
        }
        for label, raw in cases.items():
            with self.subTest(label), self.assertRaises(backup.BackupError):
                backup.parse_env(raw)

    def test_recipient_is_a_pinned_public_certificate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recipient.pem"
            path.write_bytes(FAKE_PEM)
            backup.load_recipient(path, FAKE_FINGERPRINT)
            with self.assertRaises(backup.BackupError):
                backup.load_recipient(path, "0" * 64)
            path.write_bytes(FAKE_PEM + b"-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n")
            with self.assertRaises(backup.BackupError):
                backup.load_recipient(path, FAKE_FINGERPRINT)
            path.write_bytes(FAKE_PEM + FAKE_PEM)
            with self.assertRaises(backup.BackupError):
                backup.load_recipient(path, FAKE_FINGERPRINT)

    def test_operator_manifest_must_cover_and_match_installed_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / "usr/local/libexec/home-agent/shared-runtime-backup.py"
            installed.parent.mkdir(parents=True)
            installed.write_bytes(b"script")
            digest = hashlib.sha256(b"script").hexdigest()
            manifest = root / "manifest"
            manifest.write_text(f"{digest}  usr/local/libexec/home-agent/shared-runtime-backup.py\n")
            required = [Path("/usr/local/libexec/home-agent/shared-runtime-backup.py")]
            backup.verify_operator_manifest(manifest, required, root=root)
            with self.assertRaises(backup.BackupError):
                backup.verify_operator_manifest(manifest, [*required, Path("/srv/home-agent/config/x.env")], root=root)
            installed.write_bytes(b"tampered")
            with self.assertRaises(backup.BackupError):
                backup.verify_operator_manifest(manifest, required, root=root)


class CoverageTests(SharedRuntimeFixture):
    def test_journals_config_secrets_and_authority_are_covered(self) -> None:
        plan = backup.collect(self.source)
        names = {member.name for member in plan.members}
        databases = {member.name for member in plan.databases}
        self.assertEqual(
            databases,
            {
                "echo-preferences/journals/consent.sqlite",
                "link-coordinator/journals/issuance.sqlite",
                "link-coordinator/journals/confirmations",
                "echo-bff/journals/auth-proof.sqlite",
                "echo-bff/journals/ha-revocation.sqlite",
            },
        )
        self.assertIn("victoria-bff/secrets/service_token", names)
        self.assertIn("authority/ca.key", names)
        self.assertIn("victoria-identity/journals", names)
        self.assertEqual(
            dict(plan.excluded),
            {
                "echo-bff/journals/ha-revocation.sqlite.owner.sqlite": "owner-lock",
                "echo-bff/journals/sessions.sqlite": "session-store",
                "echo-bff/journals/sessions.sqlite-wal": "session-store",
                "echo-bff/journals/sessions.sqlite.owner.sqlite": "session-store",
            },
        )
        self.assertEqual(plan.ignored_top_level, 1)
        self.assertNotIn("commissioning.env", names)

    def test_journal_names_are_classified_explicitly(self) -> None:
        classify = backup.classify_journal_file
        self.assertEqual(classify("session.db", b""), "session-store")
        self.assertEqual(classify("session.sqlite-shm", b""), "session-store")
        self.assertEqual(classify("session-revocation.sqlite", b""), "database")
        self.assertEqual(classify("consent.sqlite-journal", b""), "sidecar")
        self.assertEqual(classify("pairing.db", b""), "database")
        self.assertEqual(classify("pairing", backup.SQLITE_HEADER), "database")
        with self.assertRaises(backup.BackupError):
            classify("notes.txt", b"plain")

    def test_unexpected_material_fails_closed(self) -> None:
        cases = [
            ("unclassified journal", self.source / "echo-identity/journals/readme.txt"),
            ("database in secrets", self.source / "victoria-bff/secrets/cache.sqlite"),
            ("service entry", self.source / "echo-identity/stray"),
        ]
        for label, path in cases:
            with self.subTest(label):
                path.write_text("x", encoding="utf-8")
                try:
                    with self.assertRaises(backup.BackupError):
                        backup.collect(self.source)
                finally:
                    path.unlink()
        shutil.rmtree(self.source / "victoria-preferences")
        with self.assertRaises(backup.BackupError):
            backup.collect(self.source)

    def test_symbolic_links_fail_closed(self) -> None:
        link = self.source / "echo-bff/config/tls.pem"
        try:
            link.symlink_to(self.source / "authority/ca.pem")
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links unavailable")
        with self.assertRaises(backup.BackupError):
            backup.collect(self.source)


class SnapshotTests(SharedRuntimeFixture):
    def test_snapshot_uses_readonly_online_backup_and_retries_when_busy(self) -> None:
        source = self.source / "echo-preferences/journals/consent.sqlite"
        destination = self.work / "journal-0001.sqlite"
        holder = sqlite3.connect(source, isolation_level=None)
        self.addCleanup(holder.close)
        holder.execute("BEGIN EXCLUSIVE")
        sleeps: list[float] = []

        def release(seconds: float) -> None:
            sleeps.append(seconds)
            holder.execute("COMMIT")

        with mock.patch.object(backup, "SQLITE_TIMEOUT_MS", 50):
            backup.snapshot_database(TOOLS, source, destination, label="consent", sleep=release)
        self.assertEqual(len(sleeps), 1)
        copied = sqlite3.connect(destination)
        try:
            self.assertEqual(copied.execute("SELECT count(*) FROM entries").fetchone()[0], 3)
        finally:
            copied.close()

    def test_snapshot_gives_up_after_bounded_retries(self) -> None:
        source = self.source / "echo-preferences/journals/consent.sqlite"
        holder = sqlite3.connect(source, isolation_level=None)
        self.addCleanup(holder.close)
        holder.execute("BEGIN EXCLUSIVE")
        sleeps: list[float] = []
        with mock.patch.object(backup, "SQLITE_TIMEOUT_MS", 50):
            with self.assertRaisesRegex(backup.BackupError, "snapshot failed"):
                backup.snapshot_database(
                    TOOLS, source, self.work / "copy.sqlite", label="consent", sleep=sleeps.append
                )
        self.assertEqual(len(sleeps), backup.SNAPSHOT_ATTEMPTS - 1)
        holder.execute("COMMIT")

    def test_failed_integrity_check_fails_closed(self) -> None:
        source = self.source / "echo-preferences/journals/consent.sqlite"
        with mock.patch.object(backup, "integrity_check", lambda _path: [("*** in database main ***",)]):
            with self.assertRaisesRegex(backup.BackupError, "integrity_check"):
                backup.snapshot_database(TOOLS, source, self.work / "copy.sqlite", label="consent")

    def test_source_journal_is_never_written(self) -> None:
        source = self.source / "echo-preferences/journals/consent.sqlite"
        before = source.read_bytes()
        backup.snapshot_database(TOOLS, source, self.work / "copy.sqlite", label="consent")
        self.assertEqual(source.read_bytes(), before)


class ArchiveTests(SharedRuntimeFixture):
    def snapshots(self, plan, directory: Path) -> dict[str, Path]:
        directory.mkdir()
        result = {}
        for index, member in enumerate(plan.databases, start=1):
            destination = directory / f"journal-{index:04d}.sqlite"
            backup.snapshot_database(TOOLS, member.source, destination, label=member.name)
            result[member.name] = destination
        return result

    def test_archive_is_deterministic_with_a_matching_manifest(self) -> None:
        plan = backup.collect(self.source)
        snapshots = self.snapshots(plan, self.work / "snap")
        first, second = self.work / "first.tar", self.work / "second.tar"
        digest, entries = backup.build_archive(plan, snapshots, first)
        again, _ = backup.build_archive(plan, snapshots, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(digest, again)
        self.assertEqual(digest, hashlib.sha256(first.read_bytes()).hexdigest())

        with tarfile.open(first) as archive:
            members = archive.getmembers()
            self.assertEqual(members[0].name, backup.MANIFEST_MEMBER)
            manifest = archive.extractfile(members[0]).read().decode()
            listed = dict(line.split("  ", 1)[::-1] for line in manifest.splitlines())
            files = [member for member in members[1:] if member.isfile()]
            self.assertEqual(len(listed), entries)
            self.assertEqual({member.name for member in files}, set(listed))
            for member in members:
                self.assertEqual((member.mtime, member.uname, member.gname), (0, "", ""))
            for member in files:
                data = archive.extractfile(member).read()
                self.assertEqual(hashlib.sha256(data).hexdigest(), listed[member.name])
        self.assertFalse(any("sessions.sqlite" in name for name in listed))
        self.assertFalse(any(name.endswith(".owner.sqlite") for name in listed))
        self.assertEqual([member.name for member in members[1:]], sorted(member.name for member in members[1:]))

    @unittest.skipUnless(shutil.which("openssl"), "openssl unavailable")
    def test_openssl_cms_round_trip_uses_only_the_public_certificate(self) -> None:
        key, cert = self.work / "recipient.key", self.work / "recipient.pem"
        subprocess.run(
            [
                shutil.which("openssl"), "req", "-x509", "-newkey", "ec",
                "-pkeyopt", "ec_paramgen_curve:P-384", "-nodes", "-keyout", str(key),
                "-out", str(cert), "-days", "2", "-subj", "/CN=shared-runtime-test",
            ],
            check=True, capture_output=True,
        )
        der = subprocess.run(
            [shutil.which("openssl"), "x509", "-in", str(cert), "-outform", "DER"],
            check=True, capture_output=True,
        ).stdout
        backup.load_recipient(cert, hashlib.sha256(der).hexdigest())
        archive, encrypted = self.work / "a.tar", self.work / "a.tar.cms"
        archive.write_bytes(os.urandom(4096))
        tools = backup.Tools(openssl=shutil.which("openssl"), rclone="rclone", findmnt="findmnt")
        with mock.patch.object(backup, "CHILD_ENV", None):
            backup.encrypt_archive(tools, archive, cert, encrypted)
        self.assertNotIn(archive.read_bytes()[:64], encrypted.read_bytes())
        plain = subprocess.run(
            [
                shutil.which("openssl"), "cms", "-decrypt", "-binary", "-inform", "DER",
                "-in", str(encrypted), "-inkey", str(key), "-recip", str(cert),
            ],
            check=True, capture_output=True,
        ).stdout
        self.assertEqual(plain, archive.read_bytes())


class PublicationTests(SharedRuntimeFixture):
    EPOCH = "20260929T043700Z"

    def publish(self, remote: FakeRemote):
        encrypted = self.work / backup.ARCHIVE_NAME
        encrypted.write_bytes(b"ciphertext" * 100)
        with mock.patch.object(backup, "_rclone", remote.rclone), mock.patch.object(
            backup, "_download_digest", remote.download
        ):
            return backup.publish_epoch(TOOLS, self.config, self.EPOCH, encrypted, self.work)

    def test_epoch_is_verified_before_its_commit_marker(self) -> None:
        remote = FakeRemote()
        digest, size = self.publish(remote)
        prefix = f"HomeAgent/SharedRuntime/epochs/{self.EPOCH}"
        self.assertEqual(set(remote.objects), {f"{prefix}/{backup.ARCHIVE_NAME}", f"{prefix}/complete.sha256"})
        self.assertEqual(remote.objects[f"{prefix}/complete.sha256"], f"{digest}  {backup.ARCHIVE_NAME}\n".encode())
        verbs = [call[0] if call[0] != "cat" else f"cat:{call[1].rsplit('/', 1)[1]}" for call in remote.calls]
        self.assertEqual(verbs, ["lsf", "copyto", f"cat:{backup.ARCHIVE_NAME}", "copyto", "cat:complete.sha256"])
        self.assertEqual(size, 1000)

    def test_existing_or_newer_epoch_is_never_overwritten(self) -> None:
        for existing in (self.EPOCH, "20261001T000000Z"):
            with self.subTest(existing):
                remote = FakeRemote({f"HomeAgent/SharedRuntime/epochs/{existing}/complete.sha256": b"x"})
                with self.assertRaises(backup.BackupError):
                    self.publish(remote)
                self.assertFalse(any(call[0] == "copyto" for call in remote.calls))

    def test_download_mismatch_withholds_the_commit_marker(self) -> None:
        remote = FakeRemote()
        remote.corrupt_downloads = True
        with self.assertRaisesRegex(backup.BackupError, "verification"):
            self.publish(remote)
        self.assertFalse(any(key.endswith("complete.sha256") for key in remote.objects))

    def test_retention_prunes_only_older_epochs_of_this_tool(self) -> None:
        names = [f"202609{day:02d}T043700Z" for day in range(1, 11)] + ["manual-copy", "current"]
        self.assertEqual(
            backup.expired_epochs(names, keep=7, current="20260910T043700Z"),
            ["20260901T043700Z", "20260902T043700Z", "20260903T043700Z"],
        )
        self.assertEqual(backup.expired_epochs(names[:3], keep=7, current="20260903T043700Z"), [])
        with self.assertRaises(backup.BackupError):
            backup.expired_epochs(names, keep=7, current="20260905T043700Z")


class EndToEndTests(SharedRuntimeFixture):
    def test_backup_once_publishes_encrypted_epoch_and_content_free_receipt(self) -> None:
        old = {f"HomeAgent/SharedRuntime/epochs/202609{day:02d}T043700Z/complete.sha256": b"x" for day in range(1, 9)}
        old["HomeAgent/SharedRuntime/epochs/manual/keep.txt"] = b"x"
        remote = FakeRemote(old)
        plaintexts: list[bytes] = []

        def run(command, **kwargs):
            if command[0] == "openssl":
                if "-encrypt" in command:
                    source = Path(command[command.index("-in") + 1])
                    plaintexts.append(source.read_bytes())
                    output = Path(command[command.index("-out") + 1])
                    sealed = bytes(byte ^ 0x5A for byte in source.read_bytes())
                    output.write_bytes(b"CMS" + sealed + b"\x00" * 16)
                return b""
            raise AssertionError(f"unexpected command {command[0]}")

        now = datetime(2026, 9, 29, 4, 37, tzinfo=timezone.utc)
        with mock.patch.object(backup, "_run", run), mock.patch.object(
            backup, "_rclone", remote.rclone
        ), mock.patch.object(backup, "_download_digest", remote.download):
            receipt = backup.backup_once(TOOLS, self.config, work_root=self.work, now=now)

        self.assertEqual(receipt["epoch"], "20260929T043700Z")
        self.assertEqual(receipt["created_at"], "2026-09-29T04:37:00Z")
        self.assertEqual(receipt["database_count"], 5)
        self.assertEqual(receipt["remote_verification"], "downloaded_sha256_match")
        self.assertEqual(receipt["archive_sha256"], hashlib.sha256(plaintexts[0]).hexdigest())
        self.assertEqual((receipt["retained_epochs"], receipt["deleted_epochs"]), (7, 2))
        stored = json.loads((self.work / backup.RECEIPT_NAME).read_text(encoding="utf-8"))
        self.assertEqual(stored, receipt)
        self.assertNotIn(SECRET_TEXT, json.dumps(stored))
        self.assertEqual([path.name for path in self.work.iterdir()], [backup.RECEIPT_NAME])

        uploaded = b"".join(remote.objects.values())
        self.assertNotIn(SECRET_TEXT.encode(), uploaded)
        self.assertNotIn(b"SQLite format 3", uploaded)
        self.assertIn("HomeAgent/SharedRuntime/epochs/manual/keep.txt", remote.objects)
        self.assertNotIn("HomeAgent/SharedRuntime/epochs/20260901T043700Z/complete.sha256", remote.objects)
        with tarfile.open(fileobj=io.BytesIO(plaintexts[0])) as archive:
            self.assertIn("authority/ca.key", archive.getnames())


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SCRIPT.read_text(encoding="utf-8")
        cls.service = (SYSTEMD / "home-agent-shared-runtime-backup.service").read_text(encoding="utf-8")
        cls.timer = (SYSTEMD / "home-agent-shared-runtime-backup.timer").read_text(encoding="utf-8")

    def test_main_requires_no_arguments_and_root(self) -> None:
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(backup.main(["--force"]), 64)
            if not (os.name == "posix" and os.geteuid() == 0):
                self.assertEqual(backup.main([]), 78)

    def test_tool_never_uses_the_pgbackrest_writer_or_its_prefix(self) -> None:
        self.assertNotIn("off_host_backup_writer", self.source)
        self.assertNotIn("offhost-rclone", self.source)
        self.assertIn('RESERVED_PREFIXES = ("HomeAgent/pgBackRest",)', self.source)
        self.assertNotIn("--ignore-existing", self.source)
        self.assertIn('"copyto", "--immutable"', self.source)

    def test_service_is_bounded_and_sandboxed(self) -> None:
        for line in (
            "RequiresMountsFor=/srv/home-agent",
            "ConditionPathIsMountPoint=/srv/home-agent",
            "ConditionPathIsDirectory=/srv/home-agent/shared-runtime-backup",
            "ExecStart=/usr/bin/python3 -I -s /usr/local/libexec/home-agent/shared-runtime-backup.py",
            "CPUQuota=25%",
            "MemoryMax=512M",
            "TasksMax=64",
            "Nice=10",
            "IOSchedulingClass=best-effort",
            "IOSchedulingPriority=7",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ReadWritePaths=/srv/home-agent/shared-runtime-backup",
            "ReadWritePaths=/srv/home-agent/secrets/shared-runtime-rclone",
        ):
            self.assertIn(line + "\n", self.service)
        self.assertNotIn("ReadWritePaths=/srv/home-agent\n", self.service)
        self.assertNotIn("pgbackrest", self.service.lower())
        self.assertNotIn("EnvironmentFile", self.service)
        self.assertIn("OnCalendar=*-*-* 04:37:00", self.timer)
        self.assertIn("Persistent=true", self.timer)
        self.assertIn("Unit=home-agent-shared-runtime-backup.service", self.timer)

    def test_runbook_documents_scope_restore_and_write_gate(self) -> None:
        doc = " ".join(DOC.read_text(encoding="utf-8").split())
        for phrase in (
            "sessions.sqlite",
            ".owner.sqlite",
            "PRAGMA integrity_check",
            "openssl cms -decrypt",
            "sha256sum -c",
            "before any link or consent write",
        ):
            self.assertIn(phrase, doc)


if __name__ == "__main__":
    unittest.main()

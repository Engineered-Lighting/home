from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch


OPERATOR = Path(__file__).resolve().parents[1]
MODULE_PATH = OPERATOR / "shared_preferences_migration.py"
SPEC = importlib.util.spec_from_file_location("shared_preferences_migration", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = migration
SPEC.loader.exec_module(migration)


SOURCE = migration.SOURCE_REVISION
TARGET = migration.TARGET_REVISION
CORE_IMAGE = "sha256:" + "b" * 64
ROLLBACK_IMAGE = "sha256:" + "c" * 64
COMMIT = "e7a7d62de560a200f641281f19db1b4526f5b390"
LABEL = "20260928-123053F"
NOW = datetime(2026, 9, 28, 18, 0, tzinfo=UTC)
ROOT = Path("/srv/home-agent/shared-preferences/migration")


def runtime_override(image: str, revision: str) -> dict:
    return {
        "services": {
            name: {
                "image": image,
                "environment": {
                    "HOME_AGENT_READINESS_MIGRATION": revision,
                    "HOME_AGENT_DATABASE_POOL_SIZE": "2",
                },
            }
            for name in migration.CORE_SERVICES
        }
    }


def inputs(**changes) -> object:
    values = {
        "env": Path("/srv/home-agent/config/home-agent.env"),
        "compose_file": ROOT / "receipts/home-agent-compose.0123456789abcdef.yml",
        "project_directory": Path("/opt/home/home-agent/stack"),
        "pool_limits": ROOT / "core-pool-limits.json",
        "runtime_override": ROOT / "core-runtime-0047.json",
        "rollback_override": ROOT / "core-runtime-0031.json",
        "core_image": CORE_IMAGE,
        "rollback_image": ROLLBACK_IMAGE,
        "receipt_dir": ROOT / "receipts",
        "run_migrations": "0",
    }
    values.update(changes)
    return migration.Inputs(**values)


def backup_info(*labels_and_stops: tuple[str, int]) -> list:
    return [
        {
            "status": {"code": 0},
            "cipher": "aes-256-cbc",
            "backup": [
                {"type": "full", "error": False, "label": label, "timestamp": {"stop": stop}}
                for label, stop in labels_and_stops
            ],
        }
    ]


def off_host_receipt(*, label: str = LABEL, copied_at: datetime | None = None) -> dict:
    return {
        "contract": migration.OFF_HOST_CONTRACT,
        "backup_label": label,
        "copied_at": (copied_at or NOW - timedelta(minutes=30)).isoformat(),
        "destination_class": "operator_controlled_off_host",
        "verification_status": "checksum_verified",
    }


def maintenance_receipt(**changes) -> dict:
    value = {
        "contract": migration.MAINTENANCE_CONTRACT,
        "backup_label": LABEL,
        "schema_revision": SOURCE,
        "database_system_identifier": "7391234567890123456",
        "completed_at": "2026-09-28T16:26:01Z",
        "restore_status": "passed",
    }
    value.update(changes)
    return value


def compose_verb(command: list[str]) -> tuple[str, list[str]]:
    index = 2
    while command[index].startswith("-"):
        index += 2
    return command[index], command[index + 1:]


class FakeHost:
    """Docker and Compose stand-in tracking live schema and running runtime."""

    def __init__(
        self,
        *,
        migrate_code: int = 0,
        commit_on_failure: bool = False,
        migrate_timeout: bool = False,
        target_ready: bool = True,
        stop_code: int = 0,
    ) -> None:
        self.migrate_code = migrate_code
        self.commit_on_failure = commit_on_failure
        self.migrate_timeout = migrate_timeout
        self.target_ready = target_ready
        self.stop_code = stop_code
        self.revision = SOURCE
        self.runtime = SOURCE
        self.running = set(migration.CORE_SERVICES)
        self.calls: list[tuple[list[str], dict]] = []

    def compose_calls(self) -> list[tuple[str, list[str], list[str], dict]]:
        found = []
        for command, env in self.calls:
            if command[:2] == ["docker", "compose"]:
                verb, rest = compose_verb(command)
                found.append((verb, rest, command, env))
        return found

    def __call__(self, command, **kwargs):
        command = list(command)
        self.calls.append((command, dict(kwargs.get("env") or {})))
        code, out = 0, b""
        if command[:3] == ["docker", "image", "inspect"]:
            out = json.dumps(
                [{"Id": command[3], "Config": {"Labels": {"org.opencontainers.image.revision": COMMIT}}}]
            ).encode()
        elif command[:2] == ["docker", "inspect"]:
            service = command[-1].removeprefix("home-agent-").removesuffix("-1")
            healthy = service == "postgres" or service in self.running
            out = json.dumps({"Health": {"Status": "healthy" if healthy else "unhealthy"}}).encode()
        elif command[:3] == ["docker", "exec", migration.API_CONTAINER]:
            if "core-api" not in self.running:
                code = 1
            else:
                ready = self.revision == self.runtime and (
                    self.runtime == SOURCE or self.target_ready
                )
                out = json.dumps(
                    {
                        "ready": ready,
                        "migration": self.revision,
                        "expected_migration": self.runtime,
                        "restore_gate": "current",
                        "rollout_authorization": "authorized",
                        "worker_maintenance": "current" if ready else "stale",
                        "outbox": "current",
                    }
                ).encode()
        elif command[:3] == ["docker", "exec", migration.POSTGRES_CONTAINER]:
            out = json.dumps(backup_info(("20260927-120000F", 1), (LABEL, 2))).encode()
        elif command[:2] == ["docker", "compose"]:
            verb, rest = compose_verb(command)
            if verb == "stop":
                code = self.stop_code
                if not code:
                    self.running.discard(rest[0])
            elif verb == "ps":
                out = "\n".join(sorted(self.running)).encode()
            elif verb == "run" and rest[-1] == migration.ENTRYPOINT:
                if self.migrate_timeout:
                    raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))
                code = self.migrate_code
                if code == 0 or self.commit_on_failure:
                    self.revision = TARGET
            elif verb == "run" and "--entrypoint" in rest:
                code = 0 if rest[-1] == self.revision else 1
            elif verb == "up":
                self.running.update(rest[-3:])
                self.runtime = (
                    TARGET if str(inputs().runtime_override) in command else SOURCE
                )
        return subprocess.CompletedProcess(command, code, out, b"")


class HostPatches(unittest.TestCase):
    def setUp(self) -> None:
        self.written: dict[str, tuple[dict, int]] = {}
        self.receipts = {
            migration.OFF_HOST_RECEIPT_PATH: off_host_receipt(),
            migration.MAINTENANCE_RESTORE_PATH: maintenance_receipt(),
        }
        self.clock = iter(range(0, 10_000, 5))
        for target, replacement in (
            ("_protected_receipt", lambda path: self.receipts.get(path)),
            ("_write_private", self._capture),
            ("_now", lambda: NOW),
        ):
            patcher = patch.object(migration, target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, replacement in (
            ("sleep", lambda seconds: None),
            ("monotonic", lambda: float(next(self.clock))),
        ):
            patcher = patch.object(migration.time, target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _capture(self, path: Path, value: dict, mode: int) -> None:
        assert path.name not in self.written
        self.written[path.name] = (json.loads(json.dumps(value)), mode)

    def run_with(self, host: FakeHost, value=None) -> tuple[int, str]:
        value = value or inputs()
        stderr = io.StringIO()
        with patch.object(migration.subprocess, "run", host), redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            report = migration.preflight(value, now=NOW)
            self.assertEqual(report["blockers"], [])
            code = migration.run(value, report)
        return code, stderr.getvalue()

    def receipt(self) -> tuple[dict, int]:
        names = [name for name in self.written if name.startswith("shared-preferences-migration-")]
        self.assertEqual(len(names), 1)
        return self.written[names[0]]


class ValidationTests(unittest.TestCase):
    def test_runtime_override_pins_one_immutable_image_and_revision(self) -> None:
        self.assertEqual(
            migration.validate_runtime_override(runtime_override(CORE_IMAGE, TARGET), revision=TARGET),
            CORE_IMAGE,
        )
        mixed = runtime_override(CORE_IMAGE, TARGET)
        mixed["services"]["core-worker"]["image"] = ROLLBACK_IMAGE
        extra = runtime_override(CORE_IMAGE, TARGET)
        extra["services"]["bff"] = {"image": CORE_IMAGE}
        tagged = runtime_override("engineered-lighting/home-agent-core:local", TARGET)
        for rejected, revision in (
            (runtime_override(CORE_IMAGE, SOURCE), TARGET),
            (mixed, TARGET),
            (extra, TARGET),
            (tagged, TARGET),
            ({"services": {}}, TARGET),
        ):
            with self.assertRaises(migration.MigrationError):
                migration.validate_runtime_override(rejected, revision=revision)

    def test_pool_limits_may_only_set_core_environment(self) -> None:
        migration.validate_pool_limits(
            {"services": {"core-api": {"environment": {"HOME_AGENT_DATABASE_POOL_SIZE": "2"}}}}
        )
        for rejected in (
            {"services": {"core-api": {"image": CORE_IMAGE, "environment": {}}}},
            {"services": {"migrate": {"environment": {}}}},
            {"services": {}},
        ):
            with self.assertRaises(migration.MigrationError):
                migration.validate_pool_limits(rejected)

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with self.assertRaises(migration.MigrationError):
            migration.decode_json(b'{"services": {}, "services": {}}')

    def test_off_host_receipt_must_cover_newest_full_backup_within_two_hours(self) -> None:
        latest = migration.latest_full_backup(backup_info((LABEL, 2), ("20260927-120000F", 1)))
        self.assertEqual(latest, LABEL)
        self.assertIsNone(migration.latest_full_backup([{"status": {"code": 0}, "cipher": "none", "backup": []}]))
        self.assertTrue(migration.valid_off_host_receipt(off_host_receipt(), latest_label=latest, now=NOW))
        self.assertFalse(
            migration.valid_off_host_receipt(
                off_host_receipt(copied_at=NOW - timedelta(hours=2, minutes=1)), latest_label=latest, now=NOW
            )
        )
        self.assertFalse(
            migration.valid_off_host_receipt(
                off_host_receipt(label="20260927-120000F"), latest_label=latest, now=NOW
            )
        )
        self.assertFalse(migration.valid_off_host_receipt(off_host_receipt(), latest_label=None, now=NOW))

    def test_maintenance_restore_receipt_records_source_success(self) -> None:
        self.assertTrue(migration.valid_maintenance_restore(maintenance_receipt()))
        self.assertFalse(migration.valid_maintenance_restore(maintenance_receipt(restore_status="failed")))
        self.assertFalse(migration.valid_maintenance_restore(maintenance_receipt(schema_revision=TARGET)))
        self.assertFalse(migration.valid_maintenance_restore(None))

    def test_readyz_summary_is_closed_vocabulary(self) -> None:
        probe = {
            "ready": True,
            "migration": SOURCE,
            "expected_migration": SOURCE,
            "restore_gate": "current",
            "rollout_authorization": "authorized",
            "worker_maintenance": "current",
            "outbox": "postgresql://user:secret@host/db",
        }
        with patch.object(migration, "_json_probe", lambda command: probe):
            summary = migration.readyz()
        self.assertIsNone(summary["outbox"])
        self.assertTrue(migration.readyz_current(summary, SOURCE))
        self.assertFalse(migration.readyz_current(summary, TARGET))
        with patch.object(migration, "_json_probe", lambda command: {**probe, "extra": 1}):
            self.assertIsNone(migration.readyz())

    def test_generated_override_bounds_migrate_to_verified_image(self) -> None:
        self.assertEqual(
            migration.generated_override(CORE_IMAGE),
            {
                "services": {
                    "migrate": {
                        "image": CORE_IMAGE,
                        "cpus": "0.5",
                        "pids_limit": 64,
                        "mem_limit": "512m",
                        "environment": {"HOME_AGENT_EXPECTED_DB_REVISION": TARGET},
                    }
                }
            },
        )

    def test_trusted_execution_requires_isolated_installed_runner(self) -> None:
        with self.assertRaisesRegex(migration.MigrationError, "python3 -I"):
            migration.validate_trusted_execution()

    def test_main_requires_root(self) -> None:
        argv = ["check"]
        for name in ("env", "compose-file", "compose-sha256", "pool-limits", "runtime-override", "rollback-override", "core-image", "receipt-dir"):
            argv += [f"--{name}", "/x"]
        with patch.object(migration, "_is_root", lambda: False), redirect_stderr(io.StringIO()):
            self.assertEqual(migration.main(argv), 77)


class PreflightTests(HostPatches):
    def test_check_passes_without_any_compose_or_mutation(self) -> None:
        host = FakeHost()
        with patch.object(migration.subprocess, "run", host):
            report = migration.preflight(inputs(), now=NOW)
        self.assertEqual(report, {"blockers": [], "core_image_revision": COMMIT, "backup_label": LABEL})
        self.assertEqual(host.compose_calls(), [])
        self.assertEqual(self.written, {})

    def test_each_gate_blocks(self) -> None:
        host = FakeHost()
        host.revision = host.runtime = TARGET
        host.running.discard("core-ingest")
        self.receipts[migration.OFF_HOST_RECEIPT_PATH] = off_host_receipt(
            copied_at=NOW - timedelta(hours=3)
        )
        self.receipts[migration.MAINTENANCE_RESTORE_PATH] = None
        with patch.object(migration.subprocess, "run", host):
            report = migration.preflight(inputs(run_migrations="1"), now=NOW)
        self.assertEqual(
            report["blockers"],
            [
                "automatic_startup_migration_enabled",
                "core_not_ready_at_source_revision",
                "required_container_not_healthy",
                "off_host_backup_not_current",
                "maintenance_restore_receipt_missing",
            ],
        )


class RunTests(HostPatches):
    def test_successful_run_is_exact_and_bounded(self) -> None:
        host = FakeHost()
        code, _ = self.run_with(host)
        self.assertEqual(code, 0)
        calls = host.compose_calls()
        self.assertEqual(
            [(verb, rest[-1]) for verb, rest, _, _ in calls],
            [
                ("stop", "core-worker"),
                ("stop", "core-ingest"),
                ("stop", "core-api"),
                ("ps", "--services"),
                ("run", migration.ENTRYPOINT),
                ("up", "core-api"),
            ],
        )
        value = inputs()
        _, migrate_rest, migrate_command, migrate_env = calls[4]
        self.assertEqual(migrate_rest, ["--rm", "--no-deps", "--pull", "never", "migrate", migration.ENTRYPOINT])
        files = [migrate_command[i + 1] for i, item in enumerate(migrate_command) if item == "-f"]
        self.assertEqual(
            files,
            [
                str(value.compose_file),
                str(value.pool_limits),
                str(value.runtime_override),
                str(value.receipt_dir / "shared-preferences-migrate-20260928T180000Z.override.json"),
            ],
        )
        self.assertIn("--profile", migrate_command)
        self.assertEqual(migrate_env.get("HOME_AGENT_EXPECTED_DB_REVISION"), TARGET)
        _, up_rest, up_command, up_env = calls[5]
        self.assertEqual(up_rest, ["-d", "--no-deps", "--no-build", "--pull", "never", *migration.CORE_SERVICES])
        self.assertIn(str(value.runtime_override), up_command)
        self.assertNotIn(str(value.rollback_override), up_command)
        self.assertNotIn("HOME_AGENT_EXPECTED_DB_REVISION", up_env)
        flat = " ".join(" ".join(command) for _, _, command, _ in calls)
        for forbidden in ("grant", "restore", "backup", "--build", "pull always"):
            self.assertNotIn(forbidden, flat)

        override, mode = self.written["shared-preferences-migrate-20260928T180000Z.override.json"]
        self.assertEqual(mode, 0o600)
        self.assertEqual(override, migration.generated_override(CORE_IMAGE))
        receipt, mode = self.receipt()
        self.assertEqual(mode, 0o400)
        self.assertEqual(receipt["outcome"], "migrated")
        self.assertEqual(receipt["from_revision"], SOURCE)
        self.assertEqual(receipt["to_revision"], TARGET)
        self.assertEqual(receipt["core_image"], CORE_IMAGE)
        self.assertEqual(receipt["core_image_revision"], COMMIT)
        self.assertEqual(receipt["backup_label"], LABEL)
        self.assertEqual(receipt["readyz"]["migration"], TARGET)
        self.assertTrue(all(step["exit_code"] == 0 for step in receipt["steps"]))
        self.assertNotIn("HOME_AGENT", json.dumps({k: v for k, v in receipt.items() if k != "steps"}))

    def test_failed_migration_verifies_source_then_restores_0031_runtime(self) -> None:
        host = FakeHost(migrate_code=1)
        code, _ = self.run_with(host)
        self.assertEqual(code, 4)
        verbs = [(verb, rest) for verb, rest, _, _ in host.compose_calls()]
        self.assertEqual(verbs[5][0], "run")
        self.assertEqual(verbs[5][1][-1], SOURCE)
        self.assertIn("--entrypoint", verbs[5][1])
        _, _, up_command, _ = host.compose_calls()[6]
        self.assertIn(str(inputs().rollback_override), up_command)
        self.assertNotIn(str(inputs().runtime_override), up_command)
        receipt, _ = self.receipt()
        self.assertEqual(receipt["outcome"], "failed_rolled_back")
        self.assertEqual(receipt["readyz"]["migration"], SOURCE)
        self.assertEqual(
            [step["step"] for step in receipt["steps"]][-3:],
            ["migrate", "verify-source-revision", "start-core-0031"],
        )

    def test_failure_after_commit_never_starts_0031_runtime(self) -> None:
        host = FakeHost(migrate_code=1, commit_on_failure=True)
        code, stderr = self.run_with(host)
        self.assertEqual(code, 5)
        self.assertNotIn("up", [verb for verb, *_ in host.compose_calls()])
        self.assertEqual(self.receipt()[0]["outcome"], "migration_state_unverified")
        self.assertIn("Core roles remain stopped", stderr)

    def test_migrate_timeout_leaves_state_untouched(self) -> None:
        host = FakeHost(migrate_timeout=True)
        code, _ = self.run_with(host)
        self.assertEqual(code, 5)
        self.assertEqual([verb for verb, *_ in host.compose_calls()][-1], "run")
        receipt, _ = self.receipt()
        self.assertEqual(receipt["outcome"], "migration_state_unverified")
        self.assertIsNone(receipt["steps"][-1]["exit_code"])

    def test_unready_0047_core_is_not_rolled_back_to_0031_image(self) -> None:
        host = FakeHost(target_ready=False)
        code, stderr = self.run_with(host)
        self.assertEqual(code, 5)
        ups = [command for verb, _, command, _ in host.compose_calls() if verb == "up"]
        self.assertEqual(len(ups), 1)
        self.assertNotIn(str(inputs().rollback_override), ups[0])
        receipt, _ = self.receipt()
        self.assertEqual(receipt["outcome"], "migrated_core_unready")
        self.assertFalse(receipt["readyz"]["ready"])
        self.assertIn("alembic downgrade 0031_relationship_uniqueness_e5r", stderr)
        self.assertIn("fix forward", stderr.lower())

    def test_stop_failure_restores_source_without_migrating(self) -> None:
        host = FakeHost(stop_code=1)
        code, _ = self.run_with(host)
        self.assertEqual(code, 4)
        verbs = [verb for verb, *_ in host.compose_calls()]
        self.assertEqual(verbs, ["stop", "up"])
        self.assertEqual(self.receipt()[0]["outcome"], "failed_rolled_back")


class ComposePinTests(unittest.TestCase):
    def test_live_compose_file_is_pinned_by_digest_into_a_private_copy(self) -> None:
        import hashlib
        import tempfile

        with tempfile.TemporaryDirectory() as work:
            work = Path(work).resolve()
            stack = work / "stack"
            stack.mkdir()
            source = stack / "home-agent-compose.yml"
            original = b"services: {}\n"
            source.write_bytes(original)
            receipts = work / "receipts"
            receipts.mkdir()
            digest = hashlib.sha256(original).hexdigest()
            with patch.object(migration.os, "fchown", lambda *_: None, create=True):
                copy, project = migration.pin_compose_file(source, digest, receipts)
                again, _ = migration.pin_compose_file(source, digest, receipts)
            self.assertEqual(copy, again)
            self.assertEqual(project, stack)
            self.assertEqual(copy.read_bytes(), source.read_bytes())
            self.assertEqual(copy.parent, receipts)
            source.write_bytes(b"services: {changed: {}}\n")
            with self.assertRaisesRegex(migration.MigrationError, "reviewed digest"):
                migration.pin_compose_file(source, digest, receipts)
            with self.assertRaisesRegex(migration.MigrationError, "sha256"):
                migration.pin_compose_file(source, "not-a-digest", receipts)

    def test_compose_commands_keep_the_live_project_directory(self) -> None:
        command = migration.compose(inputs())
        self.assertEqual(Path(command[command.index("--project-directory") + 1]), Path("/opt/home/home-agent/stack"))
        self.assertEqual(command[command.index("--project-name") + 1], "home-agent")


if __name__ == "__main__":
    unittest.main()

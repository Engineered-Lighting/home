from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def service_block(compose: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\n(?P<body>.*?)(?=^  [a-z0-9][a-z0-9_-]*:\n|\Z)",
        compose,
    )
    if match is None:
        raise AssertionError(f"missing Compose service: {service}")
    return match.group(0)


def local_pgbackrest_example() -> tuple[Path, str]:
    examples = sorted(
        (ROOT / "stack/home-agent-deploy").glob("pgbackrest*.conf.example")
    )
    matches = [
        (path, path.read_text(encoding="utf-8"))
        for path in examples
        if re.search(r"(?m)^repo1-type=posix$", path.read_text(encoding="utf-8"))
    ]
    if len(matches) != 1:
        raise AssertionError(
            "expected exactly one pgBackRest example for the local POSIX topology; "
            f"found {len(matches)}"
        )
    return matches[0]


class LocalPgBackRestRepositoryContractTests(unittest.TestCase):
    def test_local_topology_and_host_repository_are_explicit_defaults(self) -> None:
        environment = read("stack/home-agent.env.example")

        self.assertRegex(environment, r"(?m)^HOME_AGENT_BACKUP_TOPOLOGY=local$")
        self.assertRegex(
            environment,
            r"(?m)^HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT="
            r"/srv/home-agent/durable/pgbackrest-repository$",
        )
        preflight = read("stack/home-agent-deploy/preflight.sh")
        self.assertIn("active deployment requires HOME_AGENT_BACKUP_TOPOLOGY=local", preflight)
        self.assertNotIn("HOME_AGENT_PGBACKREST_SFTP_KEY", preflight)

    def test_local_repo1_is_posix_encrypted_and_has_no_drop_threshold(self) -> None:
        path, config = local_pgbackrest_example()

        self.assertRegex(config, r"(?m)^repo1-type=posix$")
        self.assertRegex(config, r"(?m)^repo1-path=/repository$")
        self.assertRegex(config, r"(?m)^repo1-cipher-type=aes-256-cbc$")
        self.assertRegex(config, r"(?m)^repo1-cipher-pass=[^\s#]+$")
        self.assertNotRegex(config, r"(?m)^repo1-sftp-")
        self.assertNotRegex(config, r"(?m)^repo(?:[2-9]|[1-9][0-9]+)-")
        self.assertNotRegex(config, r"(?m)^archive-push-queue-max=")
        self.assertNotRegex(config, r"(?m)^archive-queue-max=")
        self.assertTrue(path.name.endswith(".conf.example"))

    def test_active_preflight_is_local_only_and_legacy_is_offline(self) -> None:
        preflight = read("stack/home-agent-deploy/preflight.sh")
        for variable in (
            "HOME_AGENT_PGBACKREST_SFTP_KEY",
            "HOME_AGENT_PGBACKREST_SFTP_PUBLIC_KEY",
            "HOME_AGENT_PGBACKREST_SFTP_KNOWN_HOSTS",
        ):
            self.assertNotIn(variable, preflight)
        self.assertRegex(preflight, r"(?m)^  local\) ;;$")
        self.assertIn("contains missing or unapproved options", preflight)
        self.assertIn(
            "sftp_legacy", read("stack/home-agent-deploy/operator/isolated_restore_drill.sh")
        )
        self.assertTrue(
            (ROOT / "stack/home-agent-deploy/pgbackrest.sftp-legacy.conf.example").is_file()
        )

    def test_local_preflight_requires_an_encrypted_repo_and_only_repo1(self) -> None:
        preflight = read("stack/home-agent-deploy/preflight.sh")

        self.assertIn("HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT", preflight)
        self.assertIn("/dev/mapper/*", preflight)
        self.assertRegex(
            preflight,
            r'findmnt[^\n]*-T "\$private_path"',
        )
        self.assertIn('install -d -m 0700 -o 999 -g 999 "$private_path"', preflight)
        self.assertIn("HOME_AGENT_PGBACKREST_SPOOL_ROOT", preflight)
        self.assertIn("pgbackrest-spool-local-v1", preflight)
        self.assertIn('"repo1-type": "posix"', preflight)
        self.assertIn('"repo1-path": "/repository"', preflight)
        self.assertIn('"repo1-cipher-type": "aes-256-cbc"', preflight)
        self.assertIn('re.fullmatch(r"[0-9a-f]{64}"', preflight)
        self.assertIn("non-canonical whitespace-prefixed directive", preflight)
        self.assertIn('"repo1-retention-full": "4"', preflight)
        self.assertIn('"repo1-retention-archive-type": "full"', preflight)
        self.assertIn('parser.sections() != ["global", "home-agent"]', preflight)
        self.assertIn(
            'HOME_AGENT_PGBACKREST_CONF" = "$HOME_AGENT_DEPLOYMENT_ROOT/config/pgbackrest-local.conf',
            preflight,
        )
        self.assertIn(
            'HOME_AGENT_LOCAL_BACKUP_MANIFEST" = "$HOME_AGENT_DEPLOYMENT_ROOT/config/local-backup-operator.sha256',
            preflight,
        )

    def test_preflight_authenticates_env_and_paths_before_mutation(self) -> None:
        preflight = read("stack/home-agent-deploy/preflight.sh")
        source_index = preflight.index('. "$env_file"')
        self.assertLess(
            preflight.index("deployment environment must use the reviewed installed path"),
            source_index,
        )
        self.assertLess(
            preflight.index("trusted deployment parent may not be group/world-writable"),
            source_index,
        )
        self.assertLess(
            preflight.index("must be the exact encrypted-volume mount point"),
            source_index,
        )
        self.assertLess(preflight.index('stat -c \'%u\' "$env_file"'), source_index)
        self.assertLess(preflight.index("deployment environment may not be"), source_index)
        first_install = preflight.index("install -d")
        self.assertLess(
            preflight.index("HOME_AGENT_DEPLOYMENT_ROOT must be the reviewed"),
            first_install,
        )
        self.assertLess(
            preflight.index("required encrypted deployment root is absent or unsafe"),
            first_install,
        )

    def test_local_repository_is_rw_only_for_postgres_and_backup_gate(self) -> None:
        compose = read("stack/home-agent-compose.yml")
        source_token = "HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT"

        self.assertEqual(compose.count(source_token), 2)
        self.assertEqual(compose.count(':/repository"'), 2)
        for service in ("postgres", "backup-gate"):
            block = service_block(compose, service)
            self.assertIn(source_token, block)
            repository_mount = re.search(
                rf'(?m)^      - "\$\{{{source_token}[^\n]*:/repository"$',
                block,
            )
            self.assertIsNotNone(repository_mount)
            assert repository_mount is not None
            self.assertNotIn(":ro", repository_mount.group(0))

        for service in re.findall(r"(?m)^  ([a-z0-9][a-z0-9_-]*):$", compose):
            if service not in {"postgres", "backup-gate"}:
                self.assertNotIn("/repository", service_block(compose, service))
        self.assertNotIn("/run/pgbackrest-sftp", compose)
        self.assertNotIn("backup-egress", service_block(compose, "postgres"))
        self.assertIn("network_mode: none", service_block(compose, "backup-gate"))
        self.assertIn(
            "postgres-socket:/var/run/postgresql\n", service_block(compose, "postgres")
        )
        self.assertIn(
            "postgres-socket:/var/run/postgresql:ro",
            service_block(compose, "backup-gate"),
        )

    def test_archive_backup_and_restore_coordinate_on_one_lock(self) -> None:
        compose = read("stack/home-agent-compose.yml")
        gate = read("stack/home-agent-deploy/backup-gate.sh")
        preflight = read("stack/home-agent-deploy/preflight.sh")
        restore = read("stack/home-agent-deploy/operator/isolated_restore_drill.sh")
        self.assertEqual(compose.count("HOME_AGENT_PGBACKREST_LOCK_FILE"), 2)
        self.assertEqual(compose.count("HOME_AGENT_PGBACKREST_BACKUP_LOCK_FILE"), 1)
        self.assertIn("archive_command=flock -s", compose)
        self.assertIn('flock -w "$lock_wait_seconds" -x 7', gate)
        self.assertIn('flock -w "$lock_wait_seconds" -s 8', gate)
        self.assertIn("flock -n -x 8", restore)
        self.assertIn("HOME_AGENT_PGBACKREST_LOCK_FILE", restore)
        self.assertIn('install -d -m 0700 -o root -g root "$lock_parent"', preflight)
        self.assertIn("stat -c '%h'", preflight)

    def test_backup_gate_has_capacity_floor_and_fresh_spool(self) -> None:
        gate = read("stack/home-agent-deploy/backup-gate.sh")
        compose = read("stack/home-agent-compose.yml")
        self.assertIn("HOME_AGENT_BACKUP_MIN_FREE_KIB", gate)
        self.assertIn('"$minimum_free_kib" -ge 2097152', gate)
        self.assertIn("data_kib * 3", gate)
        self.assertIn("ten_percent_kib", gate)
        self.assertIn("df -Pk /repository", gate)
        self.assertEqual(compose.count("HOME_AGENT_PGBACKREST_SPOOL_ROOT"), 2)
        self.assertNotIn("/pgbackrest-spool:/var/spool/pgbackrest", compose)
        self.assertIn("HOME_AGENT_BACKUP_TYPE: full", service_block(compose, "backup-gate"))
        self.assertNotIn("${HOME_AGENT_BACKUP_TYPE", compose)

    def test_local_backup_schedule_advances_retention_without_builds(self) -> None:
        operator = read("stack/home-agent-deploy/operator/local_backup.sh")
        service = read(
            "stack/home-agent-deploy/operator/systemd/home-agent-local-backup.service"
        )
        timer = read(
            "stack/home-agent-deploy/operator/systemd/home-agent-local-backup.timer"
        )
        runbook = read("docs/HOME-AGENT-RUNBOOK.md")
        self.assertIn("HOME_AGENT_BACKUP_TYPE=full", operator)
        self.assertNotIn("docker compose", operator)
        self.assertIn("docker run --rm --pull never", operator)
        self.assertIn("HOME_AGENT_PGBACKREST_IMAGE_ID", operator)
        self.assertIn("local-backup-operator.sha256", operator)
        self.assertIn("flock -n 9", operator)
        self.assertIn("unexpected environment path", operator)
        self.assertIn("is not the exact deployment mount point", operator)
        self.assertIn('--name "$container_name"', operator)
        self.assertIn('--cidfile "$cidfile"', operator)
        self.assertIn("trap cleanup_container EXIT", operator)
        self.assertIn("HOME_AGENT_BACKUP_LOCK_WAIT_SECONDS=300", operator)
        self.assertIn("/usr/local/libexec/home-agent", operator)
        self.assertIn("/srv/home-agent/locks", operator)
        self.assertIn("--entrypoint /usr/bin/ionice", operator)
        self.assertIn("dst=/var/run/postgresql,readonly", operator)
        self.assertIn("CPUQuota=50%", service)
        self.assertIn("MemoryMax=768M", service)
        self.assertIn("RequiresMountsFor=/srv/home-agent", service)
        self.assertIn("ConditionPathIsMountPoint=/srv/home-agent", service)
        self.assertNotIn("ConditionPathIsRegular=", service)
        self.assertEqual(service.count("ExecStartPre=/usr/bin/test -f "), 4)
        self.assertIn(
            "ExecStartPre=/usr/bin/test -f /srv/home-agent/config/home-agent.env",
            service,
        )
        self.assertIn(
            "ExecStartPre=/usr/bin/test -f "
            "/srv/home-agent/config/local-backup-operator.sha256",
            service,
        )
        self.assertIn("ExecStart=/usr/local/libexec/home-agent/local-backup.sh", service)
        self.assertIn("ExecStopPost=-/usr/bin/docker rm -f home-agent-local-backup", service)
        self.assertIn("RuntimeDirectory=home-agent-local-backup", service)
        self.assertNotIn("WantedBy=multi-user.target", service)
        self.assertIn("srv/home-agent/config/pgbackrest-local.conf", runbook)
        self.assertIn("Persistent=false", timer)

    def test_backup_gate_has_exactly_one_repo_and_pins_backup_selector(
        self,
    ) -> None:
        gate = read("stack/home-agent-deploy/backup-gate.sh")
        commands = [
            line.strip()
            for line in gate.splitlines()
            if line.strip().startswith("pgbackrest ")
        ]

        self.assertEqual(len(commands), 3)
        self.assertTrue(any(" stanza-create" in command for command in commands))
        self.assertTrue(any(" check" in command for command in commands))
        self.assertTrue(any(" backup" in command for command in commands))
        stanza_create = next(c for c in commands if " stanza-create" in c)
        check = next(c for c in commands if " check" in c)
        backup = next(c for c in commands if " backup" in c)
        self.assertNotIn("--repo=1", stanza_create)
        self.assertNotIn("--repo=1", check)
        self.assertIn("--repo=1", backup)

        postgres = service_block(read("stack/home-agent-compose.yml"), "postgres")
        archive_command = next(
            line.strip() for line in postgres.splitlines() if "archive-push %p" in line
        )
        self.assertNotIn("--repo=1", archive_command)


if __name__ == "__main__":
    unittest.main()

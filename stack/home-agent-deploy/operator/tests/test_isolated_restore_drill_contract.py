from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = (
    ROOT
    / "stack"
    / "home-agent-deploy"
    / "operator"
    / "isolated_restore_drill.sh"
)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class IsolatedRestoreDrillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = SCRIPT_PATH.read_text(encoding="utf-8")

    def test_script_is_strict_bash_and_parses_when_bash_is_available(self) -> None:
        self.assertTrue(self.script.startswith("#!/usr/bin/env bash\nset -Eeuo pipefail"))
        self.assertNotIn("set -x", self.script)

        bash = shutil.which("bash")
        if os.name == "nt" or bash is None:
            return
        subprocess.run([bash, "-n", str(SCRIPT_PATH)], check=True)

    def test_remote_stage_uses_only_native_strict_openssh(self) -> None:
        for value in (
            "sftp -q -F /dev/null -b",
            "StrictHostKeyChecking=yes",
            "UserKnownHostsFile=$temporary_known_hosts",
            "GlobalKnownHostsFile=/dev/null",
            "UpdateHostKeys=no",
            "VerifyHostKeyDNS=no",
            "ProxyCommand=none",
            "ProxyJump=none",
            "ClearAllForwardings=yes",
            "BatchMode=yes",
            "IdentitiesOnly=yes",
            "IdentityAgent=none",
            "PasswordAuthentication=no",
            "KbdInteractiveAuthentication=no",
            "PreferredAuthentications=publickey",
        ):
            self.assertIn(value, self.script)
        self.assertNotIn("ssh-keyscan", self.script)
        self.assertIn("known_hosts_contains_target", self.script)
        self.assertIn("repo1-sftp-host-fingerprint", self.script)

    def test_private_material_never_enters_an_argument_or_environment(self) -> None:
        self.assertIn(
            'install -m 0600 -o root -g root "$HOME_AGENT_PGBACKREST_SFTP_KEY"',
            self.script,
        )
        self.assertIn('require_stat 0:0:600 "$temporary_key"', self.script)
        self.assertIn('chmod 0400 "$local_pgbackrest_config"', self.script)
        self.assertIn("scrub_file \"$temporary_key\"", self.script)
        self.assertIn("scrub_file \"$local_pgbackrest_config\"", self.script)
        self.assertNotIn("--repo1-cipher-pass", self.script)
        self.assertIsNone(re.search(r'PGPASSWORD="\$[A-Za-z_]', self.script))
        self.assertIn(
            'export PGPASSWORD="$(cat /run/secrets/postgres_owner_password)"',
            self.script,
        )

    def test_pgbackrest_restore_is_local_posix_and_networkless(self) -> None:
        self.assertIn("repo1-type=posix", self.script)
        self.assertIn("repo1-path=/repository", self.script)
        self.assertIn("--set=\"$backup_label\"", self.script)
        self.assertIn("--process-max=1", self.script)
        self.assertIn("--type=immediate --target-action=promote", self.script)
        self.assertIn("--archive-mode=off restore", self.script)

        restore_block = self.script.split(
            'info "restoring the selected backup from the local POSIX repository"', 1
        )[1].split('info "booting the restored database', 1)[0]
        self.assertIn("common_readonly", restore_block)
        self.assertIn("--network none", self.script.split("common_readonly=(", 1)[1])
        self.assertNotIn("repo1-type=sftp", restore_block)

    def test_database_boot_has_no_network_or_published_port(self) -> None:
        boot_block = self.script.split(
            'info "booting the restored database with network_mode=none"', 1
        )[1].split('info "stopping the restored database cleanly"', 1)[0]
        self.assertIn("docker run -d", boot_block)
        self.assertIn("--network none", boot_block)
        self.assertIn("-c listen_addresses=", boot_block)
        self.assertIn("--tmpfs /var/spool/pgbackrest", boot_block)
        self.assertIn("PortBindings", boot_block)
        self.assertIn('docker port "$database_container"', boot_block)
        self.assertNotIn("--publish", boot_block)
        self.assertIsNone(re.search(r"(?m)^\s+-p(?:\s|=)", boot_block))

    def test_image_and_path_guards_are_fail_closed(self) -> None:
        for value in (
            "HOME_AGENT_POSTGRES_IMAGE must use an immutable sha256 digest",
            "org.opencontainers.image.base.name",
            "pgBackRest image was not built from the configured pinned PostgreSQL image",
            "assert_disjoint_paths \"$production_data\" \"$restore_root\"",
            "refusing to remove production PostgreSQL data",
            "refusing cross-mount cleanup",
            "find \"$canonical\" -xdev -depth -mindepth 1 -delete",
            "another restore drill is already running",
        ):
            self.assertIn(value, self.script)
        self.assertNotIn("rm -rf", self.script)

    def test_recovery_contract_checks_revision_schema_dump_and_checksums(self) -> None:
        for value in (
            "HOME_AGENT_EXPECTED_DB_REVISION",
            "EXPECTED_SCHEMAS=engagement,identity,ingest,knowledge,media,operations,privacy",
            "data_checksums",
            "public.alembic_version",
            "operations.worker_maintenance_state",
            "relpersistence::text",
            "worker_maintenance_persistence=u",
            "worker_maintenance_rows=0",
            "pg_dump",
            "--schema-only --no-owner --no-acl",
            "Database cluster state",
            "'shut down'",
            "pg_checksums",
            "--check -D /var/lib/postgresql/data",
        ):
            self.assertIn(value, self.script)

    def test_environment_preflight_and_image_label_support_the_drill(self) -> None:
        environment = read("stack/home-agent.env.example")
        preflight = read("stack/home-agent-deploy/preflight.sh")
        dockerfile = read("stack/home-agent-deploy/postgres.Dockerfile")
        for value in (
            "HOME_AGENT_BACKUP_TOPOLOGY",
            "HOME_AGENT_PGBACKREST_LOCAL_REPO_ROOT",
            "HOME_AGENT_RESTORE_DRILL_ROOT",
            "HOME_AGENT_PGBACKREST_IMAGE",
            "HOME_AGENT_EXPECTED_DB_REVISION",
        ):
            self.assertIn(value, environment)
            self.assertIn(value, preflight)
        self.assertIn("/dev/mapper", preflight)
        self.assertNotIn("HOME_AGENT_PGBACKREST_SFTP_KNOWN_HOSTS", preflight)
        self.assertIn("HOME_AGENT_PGBACKREST_SFTP_KNOWN_HOSTS", self.script)
        self.assertIn('"repo1-bundle": "y"', preflight)
        self.assertIn("repo1-bundle=y", read("stack/home-agent-deploy/pgbackrest.conf.example"))
        self.assertIn('org.opencontainers.image.base.name="${POSTGRES_BASE_IMAGE}"', dockerfile)

    def test_operator_documentation_states_scope_and_residual_gate(self) -> None:
        documentation = read(
            "stack/home-agent-deploy/operator/RESTORE-DRILL.md"
        )
        self.assertIn("does **not** restore directly", documentation)
        self.assertIn("StrictHostKeyChecking=yes", documentation)
        self.assertIn("network_mode=none", documentation)
        self.assertIn("post-migration full backup", documentation)
        self.assertIn("full isolated Home Assistant application restore", documentation)


if __name__ == "__main__":
    unittest.main()

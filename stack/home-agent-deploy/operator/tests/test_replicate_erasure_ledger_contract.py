from __future__ import annotations

import subprocess
from pathlib import Path
import unittest


OPERATOR = Path(__file__).resolve().parents[1]
SCRIPT = OPERATOR / "replicate_erasure_ledger.sh"
SYSTEMD = OPERATOR / "systemd"


class ErasureLedgerReplicationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def test_replication_script_is_valid_posix_shell(self) -> None:
        subprocess.run(["sh", "-n", str(SCRIPT)], check=True)

    def test_immutable_epoch_precedes_mutable_current_view(self) -> None:
        marker_upload = self.source.index('"$remote/complete.sha256" --immutable')
        marker_verify = self.source.index(
            '"$stage/remote-complete.sha256"', marker_upload
        )
        current_update = self.source.index(
            '"$remote_root/current/$name" --checksum'
        )

        self.assertLess(marker_upload, marker_verify)
        self.assertLess(marker_verify, current_update)
        self.assertIn("immutable epoch marker collision", self.source)
        self.assertIn("remote commit-marker verification failed", self.source)
        self.assertIn("current commit-marker verification failed", self.source)

    def test_replication_is_idempotent_and_mapper_bound(self) -> None:
        self.assertIn('flock -n 9 || exit 0', self.source)
        self.assertIn('require_expected_mapper "$base"', self.source)
        self.assertIn('require_expected_mapper "$config"', self.source)
        self.assertIn('"$expected_mapper"|"$expected_mapper"[[]*', self.source)
        self.assertIn(
            'HOME_AGENT_EXPECTED_MAPPER:-/dev/mapper/home-agent', self.source
        )
        self.assertIn(
            'HOME_AGENT_STAGE_ROOT:-/srv/home-agent/ledger-replication', self.source
        )
        self.assertIn('grep -Fxq complete.sha256', self.source)
        self.assertIn('cmp -s "$stage/complete.sha256"', self.source)
        self.assertLess(
            self.source.index("current-fast-complete.sha256"),
            self.source.index(
                'rclone --config "$config" mkdir "$remote_root/epochs"'
            ),
        )
        self.assertNotIn("--ignore-existing", self.source)

    def test_systemd_unit_requires_encrypted_mount_and_external_config(self) -> None:
        service = (SYSTEMD / "home-agent-ledger-backup.service").read_text(
            encoding="utf-8"
        )
        timer = (SYSTEMD / "home-agent-ledger-backup.timer").read_text(
            encoding="utf-8"
        )

        self.assertIn(r"Requires=srv-home\x2dagent.mount", service)
        self.assertIn("ConditionPathIsMountPoint=/srv/home-agent", service)
        self.assertIn("EnvironmentFile=/etc/home-agent/ledger-backup.env", service)
        self.assertIn(
            "ConditionPathIsDirectory=/srv/home-agent/ledger-replication", service
        )
        self.assertIn("TimeoutStartSec=4min30s", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn(
            "ReadWritePaths=/srv/home-agent/ledger-replication", service
        )
        self.assertIn("ReadWritePaths=/srv/home-agent/config/rclone.conf", service)
        self.assertNotIn("ReadWritePaths=/srv/home-agent /run/lock", service)
        self.assertIn("OnCalendar=*-*-* *:0/5:00", timer)
        self.assertIn("Persistent=true", timer)


if __name__ == "__main__":
    unittest.main()

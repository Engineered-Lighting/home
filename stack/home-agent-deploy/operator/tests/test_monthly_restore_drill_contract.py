from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest


OPERATOR = Path(__file__).resolve().parents[1]
SCRIPT = OPERATOR / "monthly_restore_drill.sh"
SYSTEMD = OPERATOR / "systemd"


class MonthlyRestoreDrillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def test_selector_is_strict_posix_shell(self) -> None:
        shell = shutil.which("sh")
        if shell is not None:
            subprocess.run([shell, "-n", str(SCRIPT)], check=True)
        self.assertTrue(self.source.startswith("#!/bin/sh\nset -eu\n"))

    def test_selector_uses_only_completed_full_backups(self) -> None:
        self.assertIn('.error == false and .type == "full"', self.source)
        self.assertIn("sort_by(.timestamp.stop)", self.source)
        self.assertIn("monthly restore drill selected completed backup", self.source)
        self.assertNotIn("--set=latest", self.source)

    def test_selector_delegates_to_guarded_operator(self) -> None:
        self.assertIn("isolated_restore_drill.sh", self.source)
        self.assertIn('exec bash "$operator" "$env_file" "$label"', self.source)
        self.assertIn("flock -n 9", self.source)
        self.assertNotIn("rm -rf", self.source)

    def test_selector_refuses_the_incident_host_before_workload(self) -> None:
        host_guard = self.source.index('assert_name_not_quarantined host "$(uname -n)"')
        docker_probe = self.source.index("docker info --format")
        backup_query = self.source.index('docker exec "$postgres_container"')
        self.assertLess(host_guard, docker_probe)
        self.assertLess(docker_probe, backup_query)
        self.assertIn("engineeredlightingserver1|home-app", self.source)
        self.assertIn('assert_name_not_quarantined "Docker daemon"', self.source)
        self.assertNotIn("HOME_AGENT_ALLOW", self.source)

    def test_systemd_schedule_is_monthly_and_bounded(self) -> None:
        service = (SYSTEMD / "home-agent-monthly-restore-drill.service").read_text(
            encoding="utf-8"
        )
        timer = (SYSTEMD / "home-agent-monthly-restore-drill.timer").read_text(
            encoding="utf-8"
        )
        self.assertIn(r"Requires=docker.service srv-home\x2dagent.mount", service)
        self.assertIn("TimeoutStartSec=2h", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("ReadWritePaths=/srv/home-agent/restore-drills", service)
        self.assertIn("ReadWritePaths=/srv/home-agent/config", service)
        self.assertIn("ConditionHost=!EngineeredLightingServer1", service)
        self.assertIn("ConditionHost=!home-app", service)
        self.assertIn("CPUQuota=100%", service)
        self.assertIn("MemoryMax=2G", service)
        self.assertIn("MemorySwapMax=0", service)
        self.assertIn("TasksMax=256", service)
        self.assertIn("IOWeight=10", service)
        self.assertIn("OnCalendar=Sun *-*-01..07 04:30:00", timer)
        self.assertIn("Persistent=false", timer)


if __name__ == "__main__":
    unittest.main()

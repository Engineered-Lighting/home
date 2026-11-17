from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


OPERATOR = Path(__file__).resolve().parents[1]
SCRIPT = OPERATOR / "ha_reachable.sh"
SYSTEMD = OPERATOR / "systemd"

LAN = "http://lan.invalid:8123"
TAILSCALE = "http://ts.invalid:8123"
PROVIDERS = '{"providers": [{"name": "Home Assistant Local", "id": null, "type": "homeassistant"}], "preselect_remember_me": true}'


class HaReachableContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SCRIPT.read_text(encoding="utf-8")
        cls.service = (SYSTEMD / "ha-reachable.service").read_text(encoding="utf-8")
        cls.timer = (SYSTEMD / "ha-reachable.timer").read_text(encoding="utf-8")

    def test_probe_never_authenticates(self) -> None:
        self.assertIn("/auth/providers", self.source)
        for forbidden in ("Authorization", "Bearer", "TOKEN", "/api/\"", "-H "):
            self.assertNotIn(forbidden, self.source)

    def test_no_addresses_are_committed(self) -> None:
        ipv4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
        self.assertIsNone(ipv4.search(self.source))
        self.assertIsNone(ipv4.search(self.service))
        self.assertIn("EnvironmentFile=/etc/default/ha-reachable", self.service)

    def test_alerting_pipeline_is_kept(self) -> None:
        self.assertIn("OnFailure=ntfy-alert@%n.service", self.service)
        self.assertIn("ExecStartPost=/usr/local/sbin/ntfy-alert-reset %n", self.service)
        self.assertIn("OnUnitActiveSec=120s", self.timer)
        self.assertIn("/run/ha-maintenance", self.source)

    def _run(self, responses: dict[str, str], gateway_up: bool = True) -> subprocess.CompletedProcess[str]:
        shell = shutil.which("sh")
        if shell is None:
            self.skipTest("no POSIX sh on this workstation")
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            cases = "\n".join(
                f"  *{host}*) printf '%s\\n%s' '{body}' '{code}' ;;"
                for host, (body, code) in responses.items()
            )
            (bin_dir / "curl").write_text(
                "#!/bin/sh\n"
                'for a; do url=$a; done\n'
                'case "$url" in\n'
                f"{cases}\n"
                "  *) printf '\\n000'; exit 28 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            (bin_dir / "ping").write_text(
                f"#!/bin/sh\nexit {0 if gateway_up else 1}\n", encoding="utf-8"
            )
            for stub in ("curl", "ping"):
                (bin_dir / stub).chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "HA_LAN_URL": LAN,
                "HA_TAILSCALE_URL": TAILSCALE,
                "LAN_GATEWAY": "gateway.invalid",
            }
            return subprocess.run(
                [shell, str(SCRIPT)], env=env, capture_output=True, text=True, check=False
            )

    def test_lan_providers_200_is_reachable(self) -> None:
        result = self._run({"lan.invalid": (PROVIDERS, "200")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("via LAN", result.stdout)

    def test_tailscale_fallback(self) -> None:
        result = self._run({"ts.invalid": (PROVIDERS, "200")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("via Tailscale", result.stdout)

    def test_200_without_providers_is_not_reachable(self) -> None:
        result = self._run({"lan.invalid": ("<html>cached</html>", "200")})
        self.assertEqual(result.returncode, 1)
        self.assertIn("FATAL", result.stderr)

    def test_down_everywhere_fails_with_last_code(self) -> None:
        result = self._run({"lan.invalid": ("", "502"), "ts.invalid": ("", "503")})
        self.assertEqual(result.returncode, 1)
        self.assertIn("last HTTP code 503", result.stderr)

    def test_local_network_down_is_blamed_on_lan(self) -> None:
        result = self._run({}, gateway_up=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("LOCAL NETWORK DOWN", result.stderr)


if __name__ == "__main__":
    unittest.main()

"""Contract checks for the tailnet browser-origin node deployment files.

The extra origin nodes run on the production LA host beside its primary
tailscaled. These checks keep them userspace-only, isolated from the primary
node's state and firewall, resource bounded, and free of Funnel or primary-node
Serve resets.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "stack/home-agent-deploy/tailnet-origins"


def _text(name: str) -> str:
    return (DIR / name).read_text(encoding="utf-8")


def _directives(name: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _text(name).splitlines():
        if "=" in line and not line.startswith(("#", "[")):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


class TailnetOriginContractTests(unittest.TestCase):
    def test_origin_node_is_userspace_and_isolated_from_primary_node(self) -> None:
        unit = _directives("tailscaled-origin@.service")
        exec_start = unit["ExecStart"]
        self.assertIn("--tun=userspace-networking", exec_start)
        self.assertIn("--socket=/run/tailscale-origin-%i/tailscaled.sock", exec_start)
        self.assertIn("--statedir=/var/lib/tailscale-origin/%i", exec_start)
        self.assertIn("--port=${TS_ORIGIN_PORT}", exec_start)
        self.assertNotIn("/var/lib/tailscale/", exec_start)
        self.assertNotIn("/run/tailscale/", exec_start)
        self.assertEqual(unit["Type"], "notify")
        # `tailscaled --cleanup` would tear down the primary node's tailscale0
        # interface and firewall chains.
        self.assertNotIn("ExecStopPost", unit)
        self.assertNotIn("--cleanup", _text("tailscaled-origin@.service"))

    def test_origin_node_is_resource_bounded(self) -> None:
        unit = _directives("tailscaled-origin@.service")
        self.assertEqual(unit["MemoryMax"], "256M")
        self.assertEqual(unit["MemorySwapMax"], "0")
        self.assertEqual(unit["CPUQuota"], "25%")
        self.assertEqual(unit["TasksMax"], "256")
        self.assertEqual(unit["NoNewPrivileges"], "true")
        self.assertEqual(unit["Restart"], "on-failure")

    def test_origin_ports_are_distinct_from_primary_and_each_other(self) -> None:
        ports = []
        for name in ("echo-agent.env.example", "victoria-agent.env.example"):
            match = re.search(r"^TS_ORIGIN_PORT=(\d+)$", _text(name), re.MULTILINE)
            self.assertIsNotNone(match, name)
            ports.append(int(match.group(1)))
        self.assertEqual(len(set(ports)), 2)
        self.assertNotIn(41641, ports)

    def test_certificate_refresh_never_stages_where_others_can_write(self) -> None:
        script = _text("refresh-origin-cert.sh")
        self.assertIn('mktemp -d "$state/.refresh.XXXXXX"', script)
        self.assertNotIn('mktemp -d "${TS_ORIGIN_CERT_DIR}', script)
        self.assertIn("cert path not root-owned", script)
        self.assertIn("cert path writable by others", script)
        self.assertIn('install -o root -g "$TS_ORIGIN_CERT_GROUP" -m 0440', script)
        self.assertNotIn("chown", script)
        self.assertNotIn("TS_ORIGIN_CERT_OWNER", script)
        self.assertNotRegex(script, r"cat\s+[^|]*browser\.key")
        env = _text("victoria-agent.env.example")
        self.assertNotIn("victoria-bff/config", env)
        self.assertIn("TS_ORIGIN_CERT_GROUP=1000", env)

    def test_deferred_restart_is_persistent_and_eventually_alerts(self) -> None:
        script = _text("refresh-origin-cert.sh")
        self.assertIn('pending="$state/restart-pending"', script)
        self.assertIn("/run/tailscale-origin-hold", script)
        self.assertIn("-gt 604800", script)
        self.assertIn("-checkend 1209600", script)
        self.assertIn("trap 'exit 143' TERM INT HUP", script)
        # The marker is written before the files are replaced.
        self.assertLess(script.index('date +%s > "$pending"'), script.index('mv -f "$dir/.browser.key.new"'))

    def test_no_funnel_or_primary_serve_reset(self) -> None:
        for path in DIR.iterdir():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("tailscale funnel", text.lower(), path.name)
            for line in text.splitlines():
                if "serve reset" in line:
                    self.assertIn("never", line.lower(), f"{path.name}: {line}")

    def test_readme_tailscale_commands_target_an_origin_socket_or_are_allowlisted(self) -> None:
        allowed_primary = {
            "sudo tailscale serve status",
            "sudo tailscale serve --tls-terminated-tcp=10001 off",
        }
        for line in _text("README.md").splitlines():
            command = line.strip().rstrip(".").strip("`")
            if not re.match(r"(sudo )?tailscale\s", command):
                continue
            if command.split("#", 1)[0].strip() in allowed_primary:
                continue
            self.assertIn("--socket=", command, command)

    def test_victoria_origin_uses_raw_passthrough_and_echo_uses_https_proxy(self) -> None:
        readme = _text("README.md")
        self.assertIn("--tcp=443 tcp://172.23.0.36:9450", readme)
        self.assertIn("--https=443 http://172.23.0.10:8096", readme)


if __name__ == "__main__":
    unittest.main()

"""Contract checks for the tailnet browser-origin node deployment files.

The extra origin nodes run on the production LA host beside its primary
tailscaled. These checks keep them userspace-only, resource bounded, and free
of Funnel or primary-node Serve resets.
"""

from __future__ import annotations

from pathlib import Path
import re

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


def test_origin_node_is_userspace_and_isolated_from_primary_node():
    unit = _directives("tailscaled-origin@.service")
    exec_start = unit["ExecStart"]
    assert "--tun=userspace-networking" in exec_start
    assert "--socket=/run/tailscale-origin-%i/tailscaled.sock" in exec_start
    assert "--statedir=/var/lib/tailscale-origin/%i" in exec_start
    # Never the primary node's state or socket.
    assert "/var/lib/tailscale/" not in exec_start
    assert "/run/tailscale/" not in exec_start
    assert "--port=${TS_ORIGIN_PORT}" in exec_start


def test_origin_node_is_resource_bounded():
    unit = _directives("tailscaled-origin@.service")
    assert unit["MemoryMax"] == "128M"
    assert unit["MemorySwapMax"] == "0"
    assert unit["CPUQuota"] == "25%"
    assert unit["TasksMax"] == "64"
    assert unit["NoNewPrivileges"] == "true"
    assert unit["Restart"] == "on-failure"


def test_origin_ports_are_distinct_from_primary_and_each_other():
    ports = []
    for name in ("echo-agent.env.example", "victoria-agent.env.example"):
        match = re.search(r"^TS_ORIGIN_PORT=(\d+)$", _text(name), re.MULTILINE)
        assert match, name
        ports.append(int(match.group(1)))
    assert len(set(ports)) == 2
    assert 41641 not in ports


def test_certificate_refresh_defers_restart_during_owner_window():
    script = _text("refresh-origin-cert.sh")
    assert "/run/tailscale-origin-hold" in script
    assert "-checkend 1209600" in script
    assert "set -eu" in script
    # The key is written readable only by its owner and never echoed.
    assert "chmod 0400" in script
    assert not re.search(r"cat\s+[^|]*browser\.key", script)


def test_no_funnel_or_primary_serve_reset():
    for path in DIR.iterdir():
        text = path.read_text(encoding="utf-8")
        assert "tailscale funnel" not in text.lower(), path.name
        for line in text.splitlines():
            if "serve reset" in line:
                assert "never" in line.lower(), f"{path.name}: {line}"


def test_victoria_origin_uses_raw_passthrough_and_echo_uses_https_proxy():
    readme = _text("README.md")
    assert "--tcp=443 tcp://172.23.0.36:9450" in readme
    assert "--https=443 http://172.23.0.10:8096" in readme

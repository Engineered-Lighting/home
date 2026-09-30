"""The lighting override keeps the private-listener hardening of the other Core services."""
from __future__ import annotations

import json
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "shared-preferences"


def test_lighting_override_matches_the_hardened_private_listeners():
    base = json.loads((DEPLOY / "compose.json").read_text(encoding="utf-8"))
    override = json.loads((DEPLOY / "lighting.json").read_text(encoding="utf-8"))
    lighting, coordinator = override["services"]["lighting"], base["services"]["link-coordinator"]
    assert lighting["command"] == ["lighting-api"] and lighting["image"] == coordinator["image"]
    for key in ("user", "read_only", "security_opt", "cap_drop", "init", "tmpfs", "logging", "profiles"):
        assert lighting[key] == coordinator[key], key
    # Restarts on its own after a crash or reboot, like the other private listeners
    # (after an 8-hour sustained check on 2026-09-29).
    assert lighting["restart"] == coordinator["restart"] == "unless-stopped"
    assert lighting["environment"]["HOME_AGENT_LIGHTING_PROFILE_FILE"] == "/config/listener.json"
    assert "HOME_AGENT_LINK_COORDINATOR_PROFILE_FILE" not in lighting["environment"]
    assert all("LINK_COORDINATOR" not in json.dumps(value) for value in lighting.values())
    assert all(volume["bind"]["create_host_path"] is False for volume in lighting["volumes"])
    assert {v["target"]: v["read_only"] for v in lighting["volumes"]}["/journals"] is False
    assert set(lighting["networks"]) == {"api", "database", "lighting-egress"}
    # Both homes' endpoints are Serve ports on the LA host; its name is pinned to the
    # Tailscale IPv4 exactly as for victoria-link, which the lighting firewall profile checks.
    assert lighting["extra_hosts"] == [
        "${HOME_AGENT_VICTORIA_HA_HOSTNAME:?set HA origin hostname}:${HOME_AGENT_HA_TAILSCALE_IPV4:?set pinned Tailscale IPv4}"]
    network = override["networks"]["lighting-egress"]
    assert network["name"] == "home-agent_lighting-egress" and network["enable_ipv6"] is False
    assert "lighting" not in base["services"]  # existing commands never need lighting variables

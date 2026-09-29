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
    # Commissioning: started explicitly; a restarting policy follows a sustained check.
    assert lighting["restart"] == "no"
    assert lighting["environment"]["HOME_AGENT_LIGHTING_PROFILE_FILE"] == "/config/listener.json"
    assert "HOME_AGENT_LINK_COORDINATOR_PROFILE_FILE" not in lighting["environment"]
    assert all("LINK_COORDINATOR" not in json.dumps(value) for value in lighting.values())
    assert all(volume["bind"]["create_host_path"] is False for volume in lighting["volumes"])
    assert {v["target"]: v["read_only"] for v in lighting["volumes"]}["/journals"] is False
    assert set(lighting["networks"]) == {"api", "database", "lighting-egress"}
    network = override["networks"]["lighting-egress"]
    assert network["name"] == "home-agent_lighting-egress" and network["enable_ipv6"] is False
    assert "lighting" not in base["services"]  # existing commands never need lighting variables

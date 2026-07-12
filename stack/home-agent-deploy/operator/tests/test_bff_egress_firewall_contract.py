from __future__ import annotations

import copy
import hashlib
import ipaddress
import sys
import unittest
from pathlib import Path


DEPLOY = Path(__file__).resolve().parents[2]
HELPER = DEPLOY / "bff-egress"
sys.path.insert(0, str(HELPER))

from firewall_contract import (  # noqa: E402
    ContractError,
    UFW_HOOK_SHA256,
    contract_from_env,
    guard_rule_spec,
    validate_container,
    validate_guard_rules,
    validate_network,
    validate_tailnet_endpoint,
)


HA_URL = "https://home-app.example.ts.net:10000"


def env(**updates: str) -> dict[str, str]:
    value = {
        "HOME_AGENT_HA_URL": HA_URL,
        "HOME_AGENT_HA_TAILSCALE_IPV4": "100.87.94.18",
        "HOME_AGENT_BFF_BIND_ADDR": "127.0.0.1",
        "HOME_AGENT_BFF_EGRESS_SUBNET": "172.22.0.0/24",
        "HOME_AGENT_BFF_EGRESS_GATEWAY": "172.22.0.1",
        "HOME_AGENT_BFF_EGRESS_IP": "172.22.0.10",
        "HOME_AGENT_BFF_EGRESS_BRIDGE": "ha-bff-egress0",
    }
    value.update(updates)
    return value


def network_shape():
    return {
        "Name": "home-agent_bff-public",
        "Driver": "bridge",
        "Scope": "local",
        "Internal": False,
        "EnableIPv6": False,
        "Options": {"com.docker.network.bridge.name": "ha-bff-egress0"},
        "IPAM": {
            "Config": [{"Subnet": "172.22.0.0/24", "Gateway": "172.22.0.1"}]
        },
        "Containers": {
            "id": {"Name": "home-agent-bff-1", "IPv4Address": "172.22.0.10/24"}
        },
    }


def container_shape():
    return {
        "Name": "/home-agent-bff-1",
        "Config": {
            "Labels": {
                "com.docker.compose.project": "home-agent",
                "com.docker.compose.service": "bff",
            },
            "Env": [f"HOME_AGENT_HA_URL={HA_URL}"],
        },
        "HostConfig": {
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "SecurityOpt": ["no-new-privileges:true"],
            "PortBindings": {
                "8097/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8097"}]
            },
        },
        "NetworkSettings": {
            "Networks": {
                "home-agent_api-net": {
                    "IPAddress": "172.23.128.1",
                    "GlobalIPv6Address": "",
                },
                "home-agent_bff-public": {
                    "IPAddress": "172.22.0.10",
                    "GlobalIPv6Address": "",
                },
            }
        },
    }


class BffEgressFirewallContractTests(unittest.TestCase):
    def test_locked_contract_accepts_exact_live_shapes(self) -> None:
        contract = contract_from_env(env())
        validate_network(contract, network_shape())
        validate_container(contract, container_shape())
        tail_ip = validate_tailnet_endpoint(
            contract,
            {
                "Self": {
                    "Online": True,
                    "DNSName": "home-app.example.ts.net.",
                    "TailscaleIPs": ["100.87.94.18", "fd7a:115c:a1e0::1"],
                }
            },
            {ipaddress.IPv4Address("100.87.94.18")},
        )
        self.assertEqual(str(tail_ip), "100.87.94.18")

    def test_contract_rejects_non_https_non_loopback_and_bad_addressing(self) -> None:
        for values in (
            env(HOME_AGENT_HA_URL="http://home-app.example.ts.net:10000"),
            env(HOME_AGENT_BFF_BIND_ADDR="0.0.0.0"),
            env(HOME_AGENT_BFF_EGRESS_IP="172.22.0.1"),
            env(HOME_AGENT_BFF_EGRESS_IP="172.23.0.10"),
            env(HOME_AGENT_BFF_EGRESS_BRIDGE="bridge-name-is-too-long"),
            env(HOME_AGENT_HA_TAILSCALE_IPV4=""),
            env(HOME_AGENT_HA_TAILSCALE_IPV4="192.168.0.100"),
        ):
            with self.subTest(values=values), self.assertRaises(ContractError):
                contract_from_env(values)

    def test_tailnet_identity_and_dns_must_be_exact(self) -> None:
        contract = contract_from_env(env())
        base = {
            "Self": {
                "Online": True,
                "DNSName": "home-app.example.ts.net.",
                "TailscaleIPs": ["100.87.94.18"],
            }
        }
        cases = (
            ({"Self": {**base["Self"], "Online": False}}, {"100.87.94.18"}),
            ({"Self": {**base["Self"], "DNSName": "other.example.ts.net."}}, {"100.87.94.18"}),
            (base, {"100.87.94.19"}),
            (base, {"100.87.94.18", "100.87.94.19"}),
            (
                {
                    "Self": {
                        **base["Self"],
                        "TailscaleIPs": ["100.87.94.19"],
                    }
                },
                {"100.87.94.19"},
            ),
        )
        for status, resolved in cases:
            with self.subTest(status=status, resolved=resolved), self.assertRaises(ContractError):
                validate_tailnet_endpoint(
                    contract,
                    status,
                    {ipaddress.IPv4Address(value) for value in resolved},
                )

    def test_network_rejects_extra_members_dynamic_ip_and_boundary_drift(self) -> None:
        contract = contract_from_env(env())
        mutations = []
        extra = network_shape()
        extra["Containers"]["other"] = {
            "Name": "legacy-gateway",
            "IPv4Address": "172.22.0.11/24",
        }
        mutations.append(extra)
        for key, value in (
            ("Internal", True),
            ("EnableIPv6", True),
            ("Driver", "overlay"),
        ):
            item = network_shape()
            item[key] = value
            mutations.append(item)
        wrong_ip = network_shape()
        wrong_ip["Containers"]["id"]["IPv4Address"] = "172.22.0.99/24"
        mutations.append(wrong_ip)
        wrong_bridge = network_shape()
        wrong_bridge["Options"]["com.docker.network.bridge.name"] = "docker-dynamic"
        mutations.append(wrong_bridge)
        for value in mutations:
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_network(contract, value)

    def test_container_rejects_extra_network_public_bind_and_hardening_drift(self) -> None:
        contract = contract_from_env(env())
        mutations = []
        extra = container_shape()
        extra["NetworkSettings"]["Networks"]["legacy"] = {
            "IPAddress": "172.30.0.2",
            "GlobalIPv6Address": "",
        }
        mutations.append(extra)
        public = container_shape()
        public["HostConfig"]["PortBindings"]["8097/tcp"][0]["HostIp"] = "0.0.0.0"
        mutations.append(public)
        privileged = container_shape()
        privileged["HostConfig"]["Privileged"] = True
        mutations.append(privileged)
        stale_endpoint = container_shape()
        stale_endpoint["Config"]["Env"][0] = (
            "HOME_AGENT_HA_URL=https://other.example.ts.net:10000"
        )
        mutations.append(stale_endpoint)
        for value in mutations:
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_container(contract, value)

    def test_first_input_guard_makes_later_broad_accepts_unreachable(self) -> None:
        contract = contract_from_env(env())
        tail_ip = ipaddress.IPv4Address("100.87.94.18")
        jump, allow, deny = guard_rule_spec(contract, tail_ip)
        validate_guard_rules(
            ["-P INPUT DROP", jump, "-A INPUT -i ha-bff-egress0 -j ACCEPT"],
            ["-N HOME_AGENT_BFF_INPUT", allow, deny],
            contract,
            tail_ip,
        )

    def test_guard_rejects_earlier_bypass_duplicate_jump_and_chain_drift(self) -> None:
        contract = contract_from_env(env())
        tail_ip = ipaddress.IPv4Address("100.87.94.18")
        jump, allow, deny = guard_rule_spec(contract, tail_ip)
        cases = (
            (
                ["-P INPUT DROP", "-A INPUT -i ha-bff-egress0 -j ACCEPT", jump],
                ["-N HOME_AGENT_BFF_INPUT", allow, deny],
            ),
            (
                ["-P INPUT DROP", jump, jump],
                ["-N HOME_AGENT_BFF_INPUT", allow, deny],
            ),
            (
                ["-P INPUT DROP", jump],
                [
                    "-N HOME_AGENT_BFF_INPUT",
                    "-A HOME_AGENT_BFF_INPUT -j ACCEPT",
                    allow,
                    deny,
                ],
            ),
            (
                ["-P INPUT DROP", jump],
                ["-N HOME_AGENT_BFF_INPUT", allow],
            ),
        )
        for input_lines, guard_lines in cases:
            with self.subTest(
                input_lines=input_lines, guard_lines=guard_lines
            ), self.assertRaises(ContractError):
                validate_guard_rules(
                    input_lines, guard_lines, contract, tail_ip
                )

    def test_compose_pins_single_source_bridge_and_disables_ipv6(self) -> None:
        compose = (DEPLOY.parent / "home-agent-compose.yml").read_text(encoding="utf-8")
        self.assertIn('ipv4_address: "${HOME_AGENT_BFF_EGRESS_IP:-172.22.0.10}"', compose)
        self.assertIn("name: home-agent_bff-public", compose)
        self.assertIn(
            'com.docker.network.bridge.name: "${HOME_AGENT_BFF_EGRESS_BRIDGE:-ha-bff-egress0}"',
            compose,
        )
        self.assertIn('subnet: "${HOME_AGENT_BFF_EGRESS_SUBNET:-172.22.0.0/24}"', compose)
        bff_network = compose.split("  bff-public:\n", 1)[1].split("  backup-egress:", 1)[0]
        self.assertIn("enable_ipv6: false", bff_network)

    def test_systemd_runs_only_the_isolated_trusted_install(self) -> None:
        service = (
            DEPLOY / "operator/systemd/home-agent-bff-egress-verify.service"
        ).read_text(encoding="utf-8")
        timer = (
            DEPLOY / "operator/systemd/home-agent-bff-egress-verify.timer"
        ).read_text(encoding="utf-8")
        hook = (DEPLOY / "bff-egress/ufw_after_init.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "ExecStart=/usr/bin/python3 -I "
            "/usr/local/libexec/home-agent-bff-egress/firewall_contract.py apply",
            service,
        )
        self.assertNotIn("/opt/home/home-github", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("OnUnitActiveSec=5min", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn('start|stop|flush-all)', hook)
        self.assertIn('guard --env "$environment"', hook)
        self.assertNotIn("/opt/home/home-github", hook)
        self.assertEqual(
            hashlib.sha256(hook.encode()).hexdigest(), UFW_HOOK_SHA256
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import http.server
import io
import ipaddress
import itertools
import socket
import subprocess
import sys
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


DEPLOY = Path(__file__).resolve().parents[2]
HELPER = DEPLOY / "bff-egress"
sys.path.insert(0, str(HELPER))

from firewall_contract import (  # noqa: E402
    BFF,
    LIGHTING,
    PROFILES,
    PYTHON_PROBE,
    VICTORIA_LINK,
    ContractError,
    UFW_HOOK_SHA256,
    apply_guard,
    apply_rule,
    contract_from_env,
    expected_guard_lines,
    guard_rule_spec,
    guard_spec,
    main as firewall_main,
    probe_commands,
    remove_rule,
    rule_present,
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
        jump, established, allow, deny = guard_rule_spec(contract, tail_ip)
        validate_guard_rules(
            ["-P INPUT DROP", jump, "-A INPUT -i ha-bff-egress0 -j ACCEPT"],
            ["-N HOME_AGENT_BFF_INPUT", established, allow, deny],
            contract,
            tail_ip,
        )
        self.assertIn("-s 172.22.0.10/32 -d 172.22.0.1/32", established)
        self.assertIn("-p tcp -m tcp --sport 8097", established)
        self.assertIn("-m conntrack --ctstate RELATED,ESTABLISHED", established)
        self.assertNotIn("NEW", established)

    def test_guard_apply_adds_only_established_loopback_return_before_ha(self) -> None:
        contract = contract_from_env(env())
        tail_ip = ipaddress.IPv4Address("100.87.94.18")
        commands: list[list[str]] = []

        def record(command: list[str], _label: str):
            commands.append(command)
            return None

        with (
            patch("firewall_contract._input_lines", return_value=["-P INPUT DROP"]),
            patch("firewall_contract._iptables_chain_lines", return_value=None),
            patch("firewall_contract._run", side_effect=record),
            patch("firewall_contract.validate_guard_live"),
        ):
            apply_guard(contract, tail_ip)

        self.assertEqual(
            commands,
            [
                ["iptables", "-N", "HOME_AGENT_BFF_INPUT"],
                [
                    "iptables", "-A", "HOME_AGENT_BFF_INPUT",
                    "-s", "172.22.0.10/32", "-d", "172.22.0.1/32",
                    "-p", "tcp", "--sport", "8097",
                    "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED",
                    "-j", "ACCEPT",
                ],
                [
                    "iptables", "-A", "HOME_AGENT_BFF_INPUT",
                    "-s", "172.22.0.10/32", "-d", "100.87.94.18/32",
                    "-p", "tcp", "--dport", "10000", "-j", "ACCEPT",
                ],
                ["iptables", "-A", "HOME_AGENT_BFF_INPUT", "-j", "DROP"],
                [
                    "iptables", "-I", "INPUT", "1", "-i", "ha-bff-egress0",
                    "-j", "HOME_AGENT_BFF_INPUT",
                ],
            ],
        )

    def test_guard_apply_upgrades_only_the_exact_legacy_chain_in_place(self) -> None:
        contract = contract_from_env(env())
        tail_ip = ipaddress.IPv4Address("100.87.94.18")
        jump, established, allow, deny = guard_rule_spec(contract, tail_ip)
        commands: list[list[str]] = []

        def record(command: list[str], _label: str):
            commands.append(command)
            return None

        with (
            patch("firewall_contract._input_lines", return_value=["-P INPUT DROP", jump]),
            patch(
                "firewall_contract._iptables_chain_lines",
                return_value=["-N HOME_AGENT_BFF_INPUT", allow, deny],
            ),
            patch("firewall_contract._run", side_effect=record),
            patch("firewall_contract.validate_guard_live"),
        ):
            apply_guard(contract, tail_ip)

        self.assertEqual(
            commands,
            [[
                "iptables", "-I", "HOME_AGENT_BFF_INPUT", "1",
                "-s", "172.22.0.10/32", "-d", "172.22.0.1/32",
                "-p", "tcp", "--sport", "8097",
                "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED",
                "-j", "ACCEPT",
            ]],
        )
        self.assertIn("RELATED,ESTABLISHED", established)

    def test_guard_rejects_earlier_bypass_duplicate_jump_and_chain_drift(self) -> None:
        contract = contract_from_env(env())
        tail_ip = ipaddress.IPv4Address("100.87.94.18")
        jump, established, allow, deny = guard_rule_spec(contract, tail_ip)
        cases = (
            (
                ["-P INPUT DROP", "-A INPUT -i ha-bff-egress0 -j ACCEPT", jump],
                ["-N HOME_AGENT_BFF_INPUT", established, allow, deny],
            ),
            (
                ["-P INPUT DROP", jump, jump],
                ["-N HOME_AGENT_BFF_INPUT", established, allow, deny],
            ),
            (
                ["-P INPUT DROP", jump],
                [
                    "-N HOME_AGENT_BFF_INPUT",
                    "-A HOME_AGENT_BFF_INPUT -j ACCEPT",
                    established,
                    allow,
                    deny,
                ],
            ),
            (
                ["-P INPUT DROP", jump],
                ["-N HOME_AGENT_BFF_INPUT", established, allow],
            ),
            (
                ["-P INPUT DROP", jump],
                ["-N HOME_AGENT_BFF_INPUT", allow, established, deny],
            ),
            (
                ["-P INPUT DROP", jump],
                [
                    "-N HOME_AGENT_BFF_INPUT",
                    established.replace("RELATED,ESTABLISHED", "NEW,ESTABLISHED"),
                    allow,
                    deny,
                ],
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
            "/usr/local/libexec/home-agent-bff-egress/firewall_contract.py verify",
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


VICTORIA_HA_URL = "https://home-app.example.ts.net:10001"


def victoria_env(**updates: str) -> dict[str, str]:
    value = env(HOME_AGENT_VICTORIA_HA_URL=VICTORIA_HA_URL)
    value.update(updates)
    return value


def victoria_network_shape():
    shape = network_shape()
    shape["Name"] = "home-agent_victoria-link-egress"
    shape["Options"] = {"com.docker.network.bridge.name": "ha-vlink-egr0"}
    shape["IPAM"] = {"Config": [{"Subnet": "172.26.0.0/24", "Gateway": "172.26.0.1"}]}
    shape["Containers"] = {"id": {
        "Name": "home-shared-preferences-victoria-link-1", "IPv4Address": "172.26.0.10/24"}}
    return shape


def victoria_container_shape():
    shape = container_shape()
    shape["Name"] = "/home-shared-preferences-victoria-link-1"
    shape["Config"] = {"Labels": {
        "com.docker.compose.project": "home-shared-preferences",
        "com.docker.compose.service": "victoria-link",
    }, "Env": []}
    shape["HostConfig"]["PortBindings"] = {}
    shape["NetworkSettings"] = {"Networks": {
        "home-agent_api-net": {"IPAddress": "172.23.0.36", "GlobalIPv6Address": ""},
        "home-agent_victoria-link-egress": {"IPAddress": "172.26.0.10", "GlobalIPv6Address": ""},
    }}
    return shape


class VictoriaLinkEgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = contract_from_env(victoria_env(), VICTORIA_LINK)
        self.tail_ip = ipaddress.IPv4Address("100.87.94.18")

    def test_default_profile_is_unchanged_bff(self) -> None:
        self.assertIs(contract_from_env(env()).profile, BFF)
        self.assertEqual(BFF.chain, "HOME_AGENT_BFF_INPUT")
        self.assertEqual(BFF.published_port, 8097)

    def test_profile_accepts_exact_live_shapes_without_host_publication(self) -> None:
        self.assertEqual(self.contract.ha_url, VICTORIA_HA_URL)
        self.assertEqual(self.contract.ha_port, 10001)
        self.assertIsNone(self.contract.bind_address)
        validate_network(self.contract, victoria_network_shape())
        validate_container(self.contract, victoria_container_shape())

    def test_profile_rejects_publication_bff_identity_and_extra_networks(self) -> None:
        published = victoria_container_shape()
        published["HostConfig"]["PortBindings"] = {
            "9450/tcp": [{"HostIp": "127.0.0.1", "HostPort": "9450"}]}
        bff_identity = victoria_container_shape()
        bff_identity["Name"] = "/home-agent-bff-1"
        extra = victoria_container_shape()
        extra["NetworkSettings"]["Networks"]["home-agent_bff-public"] = {
            "IPAddress": "172.22.0.11", "GlobalIPv6Address": ""}
        for value in (published, bff_identity, extra):
            with self.subTest(value=value["Name"]), self.assertRaises(ContractError):
                validate_container(self.contract, value)
        with self.assertRaises(ContractError):
            validate_network(self.contract, network_shape())

    def test_unprovisioned_profile_has_no_contract(self) -> None:
        with self.assertRaises(ContractError):
            contract_from_env(env(), VICTORIA_LINK)

    def test_guard_has_no_return_flow_rule_and_its_own_chain(self) -> None:
        jump, established, allow, deny = guard_rule_spec(self.contract, self.tail_ip)
        self.assertIsNone(established)
        self.assertEqual(jump, "-A INPUT -i ha-vlink-egr0 -j HOME_AGENT_VLINK_INPUT")
        self.assertIn("-s 172.26.0.10/32 -d 100.87.94.18/32", allow)
        self.assertIn("--dport 10001", allow)
        commands: list[list[str]] = []
        with (
            patch("firewall_contract._input_lines", return_value=["-P INPUT DROP"]),
            patch("firewall_contract._iptables_chain_lines", return_value=None),
            patch("firewall_contract._run", side_effect=lambda c, _l: commands.append(c)),
            patch("firewall_contract.validate_guard_live"),
        ):
            apply_guard(self.contract, self.tail_ip)
        self.assertEqual(commands, [
            ["iptables", "-N", "HOME_AGENT_VLINK_INPUT"],
            ["iptables", "-A", "HOME_AGENT_VLINK_INPUT", "-s", "172.26.0.10/32",
             "-d", "100.87.94.18/32", "-p", "tcp", "--dport", "10001", "-j", "ACCEPT"],
            ["iptables", "-A", "HOME_AGENT_VLINK_INPUT", "-j", "DROP"],
            ["iptables", "-I", "INPUT", "1", "-i", "ha-vlink-egr0", "-j", "HOME_AGENT_VLINK_INPUT"],
        ])

    def test_both_first_hop_guards_may_lead_input_in_either_order(self) -> None:
        bff = contract_from_env(env())
        bff_jump, established, bff_allow, bff_deny = guard_rule_spec(bff, self.tail_ip)
        v_jump, _none, v_allow, v_deny = guard_rule_spec(self.contract, self.tail_ip)
        bff_chain = ["-N HOME_AGENT_BFF_INPUT", established, bff_allow, bff_deny]
        v_chain = ["-N HOME_AGENT_VLINK_INPUT", v_allow, v_deny]
        for order in ([bff_jump, v_jump], [v_jump, bff_jump]):
            lines = ["-P INPUT DROP", *order, "-A INPUT -j ufw-before-input"]
            validate_guard_rules(lines, bff_chain, bff, self.tail_ip)
            validate_guard_rules(lines, v_chain, self.contract, self.tail_ip)
        # A non-guard rule before the Victoria jump makes it bypassable.
        with self.assertRaises(ContractError):
            validate_guard_rules(
                ["-P INPUT DROP", bff_jump, "-A INPUT -i ha-vlink-egr0 -j ACCEPT", v_jump],
                v_chain, self.contract, self.tail_ip)

    def test_only_exact_other_profile_jumps_may_lead(self) -> None:
        bff = contract_from_env(env())
        bff_jump, established, bff_allow, bff_deny = guard_rule_spec(bff, self.tail_ip)
        bff_chain = ["-N HOME_AGENT_BFF_INPUT", established, bff_allow, bff_deny]
        for loose in (
            "-A INPUT -j HOME_AGENT_VLINK_INPUT",                      # no bridge match
            "-A INPUT -i ha-bff-egress0 -j HOME_AGENT_VLINK_INPUT",    # the BFF's own bridge
            "-A INPUT -i ha-vlink-egr0 -p tcp -j HOME_AGENT_VLINK_INPUT",
        ):
            with self.subTest(loose=loose), self.assertRaises(ContractError):
                validate_guard_rules(["-P INPUT DROP", loose, bff_jump], bff_chain, bff, self.tail_ip)

    def test_restoration_does_not_reorder_an_already_leading_guard(self) -> None:
        bff = contract_from_env(env())
        bff_jump = guard_rule_spec(bff, self.tail_ip)[0]
        v_jump = guard_rule_spec(self.contract, self.tail_ip)[0]
        chain = ["-N HOME_AGENT_VLINK_INPUT", *guard_rule_spec(self.contract, self.tail_ip)[2:]]
        commands: list[list[str]] = []
        with (
            patch("firewall_contract._input_lines",
                  return_value=["-P INPUT DROP", bff_jump, v_jump]),
            patch("firewall_contract._iptables_chain_lines", return_value=chain),
            patch("firewall_contract._run", side_effect=lambda c, _l: commands.append(c)),
            patch("firewall_contract.validate_guard_live"),
        ):
            apply_guard(self.contract, self.tail_ip)
        self.assertEqual(commands, [])

    def test_hook_guards_victoria_only_when_configured(self) -> None:
        hook = (DEPLOY / "bff-egress/ufw_after_init.sh").read_text(encoding="utf-8")
        self.assertIn("guard --profile victoria-link", hook)
        self.assertIn("--if-configured", hook)


# The exact guard, UFW, and probe output of the single-URL profiles before
# LIGHTING generalized profiles to URL tuples. These must never change.
BFF_JUMP = "-A INPUT -i ha-bff-egress0 -j HOME_AGENT_BFF_INPUT"
BFF_GUARD = [
    "-N HOME_AGENT_BFF_INPUT",
    "-A HOME_AGENT_BFF_INPUT -s 172.22.0.10/32 -d 172.22.0.1/32 -p tcp -m tcp "
    "--sport 8097 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
    "-A HOME_AGENT_BFF_INPUT -s 172.22.0.10/32 -d 100.87.94.18/32 -p tcp -m tcp "
    "--dport 10000 -j ACCEPT",
    "-A HOME_AGENT_BFF_INPUT -j DROP",
]
VLINK_JUMP = "-A INPUT -i ha-vlink-egr0 -j HOME_AGENT_VLINK_INPUT"
VLINK_GUARD = [
    "-N HOME_AGENT_VLINK_INPUT",
    "-A HOME_AGENT_VLINK_INPUT -s 172.26.0.10/32 -d 100.87.94.18/32 -p tcp -m tcp "
    "--dport 10001 -j ACCEPT",
    "-A HOME_AGENT_VLINK_INPUT -j DROP",
]
LIGHT_JUMP = "-A INPUT -i ha-light-egr0 -j HOME_AGENT_LIGHT_INPUT"
LIGHT_GUARD = [
    "-N HOME_AGENT_LIGHT_INPUT",
    "-A HOME_AGENT_LIGHT_INPUT -s 172.27.0.10/32 -d 100.87.94.18/32 -p tcp -m tcp "
    "--dport 10000 -j ACCEPT",
    "-A HOME_AGENT_LIGHT_INPUT -s 172.27.0.10/32 -d 100.87.94.18/32 -p tcp -m tcp "
    "--dport 10001 -j ACCEPT",
    "-A HOME_AGENT_LIGHT_INPUT -j DROP",
]
NODE_PROBE_TAIL = (
    "+'/auth/token',{method:'GET',redirect:'error',"
    "signal:AbortSignal.timeout(5000)})"
    ".then(r=>process.exit(r.status===405?0:2)).catch(()=>process.exit(3))"
)
TAIL_IP = ipaddress.IPv4Address("100.87.94.18")


class _Completed:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def _record_rule_commands(action, contract, present):
    """Run apply_rule/remove_rule with a scripted ``iptables -C`` sequence."""

    checks: list[list[str]] = []
    commands: list[list[str]] = []
    answers = iter(present)

    def check(command, **_kwargs):
        checks.append(command)
        return _Completed(0 if next(answers) else 1)

    with (
        patch("firewall_contract.subprocess.run", side_effect=check),
        patch("firewall_contract._run", side_effect=lambda c, _l: commands.append(c)),
    ):
        action(contract, TAIL_IP)
    return checks, commands


def _record_apply_guard(contract, input_lines, chain_lines):
    commands: list[list[str]] = []
    with (
        patch("firewall_contract._input_lines", return_value=input_lines),
        patch("firewall_contract._iptables_chain_lines", return_value=chain_lines),
        patch("firewall_contract._run", side_effect=lambda c, _l: commands.append(c)),
        patch("firewall_contract.validate_guard_live"),
    ):
        apply_guard(contract, TAIL_IP)
    return commands


class SingleUrlProfilesAreByteIdenticalTests(unittest.TestCase):
    def test_bff_guard_lines_are_unchanged(self) -> None:
        contract = contract_from_env(env())
        self.assertEqual(BFF.url_keys, ("HOME_AGENT_HA_URL",))
        self.assertEqual(contract.ha_urls, (HA_URL,))
        self.assertEqual(contract.ha_ports, (10000,))
        self.assertEqual(expected_guard_lines(contract, TAIL_IP), BFF_GUARD)
        self.assertEqual(
            list(guard_rule_spec(contract, TAIL_IP)), [BFF_JUMP, *BFF_GUARD[1:]]
        )
        self.assertEqual(
            guard_spec(contract, TAIL_IP),
            (BFF_JUMP, BFF_GUARD[1], (BFF_GUARD[2],), BFF_GUARD[3]),
        )
        validate_guard_rules(["-P INPUT DROP", BFF_JUMP], BFF_GUARD, contract, TAIL_IP)

    def test_victoria_guard_lines_are_unchanged(self) -> None:
        contract = contract_from_env(victoria_env(), VICTORIA_LINK)
        self.assertEqual(VICTORIA_LINK.url_keys, ("HOME_AGENT_VICTORIA_HA_URL",))
        self.assertEqual(expected_guard_lines(contract, TAIL_IP), VLINK_GUARD)
        self.assertEqual(
            list(guard_rule_spec(contract, TAIL_IP)),
            [VLINK_JUMP, None, *VLINK_GUARD[1:]],
        )
        validate_guard_rules(["-P INPUT DROP", VLINK_JUMP], VLINK_GUARD, contract, TAIL_IP)

    def test_ufw_rule_and_probe_commands_are_unchanged(self) -> None:
        cases = (
            (contract_from_env(env()), "ha-bff-egress0", "172.22.0.10", "10000",
             "home-agent-bff-to-local-ha-oauth"),
            (contract_from_env(victoria_env(), VICTORIA_LINK), "ha-vlink-egr0",
             "172.26.0.10", "10001", "home-agent-victoria-link-to-victoria-ha"),
        )
        for contract, bridge, source, port, comment in cases:
            check = [
                "iptables", "-C", "ufw-user-input", "-i", bridge,
                "-s", f"{source}/32", "-d", "100.87.94.18/32",
                "-p", "tcp", "--dport", port, "-j", "ACCEPT",
            ]
            with self.subTest(profile=contract.profile.name):
                checks, commands = _record_rule_commands(
                    apply_rule, contract, [False, True]
                )
                self.assertEqual(checks, [check, check])
                self.assertEqual(commands, [[
                    "ufw", "allow", "in", "on", bridge, "from", source,
                    "to", "100.87.94.18", "port", port, "proto", "tcp",
                    "comment", comment,
                ]])
                checks, commands = _record_rule_commands(
                    remove_rule, contract, [True, False]
                )
                self.assertEqual(checks, [check, check])
                self.assertEqual(commands, [[
                    "ufw", "--force", "delete", "allow", "in", "on", bridge,
                    "from", source, "to", "100.87.94.18", "port", port,
                    "proto", "tcp",
                ]])
        self.assertEqual(probe_commands(cases[0][0]), [[
            "docker", "exec", "home-agent-bff-1", "node", "-e",
            "fetch(process.env.HOME_AGENT_HA_URL" + NODE_PROBE_TAIL,
        ]])
        self.assertEqual(probe_commands(cases[1][0]), [[
            "docker", "exec", "home-shared-preferences-victoria-link-1", "node", "-e",
            'fetch("https://home-app.example.ts.net:10001"' + NODE_PROBE_TAIL,
        ]])


def lighting_env(**updates: str) -> dict[str, str]:
    value = victoria_env(
        HOME_AGENT_LIGHTING_EGRESS_SUBNET="172.27.0.0/24",
        HOME_AGENT_LIGHTING_EGRESS_GATEWAY="172.27.0.1",
        HOME_AGENT_LIGHTING_EGRESS_IP="172.27.0.10",
    )
    value.update(updates)
    return {key: item for key, item in value.items() if item is not None}


def lighting_network_shape():
    shape = network_shape()
    shape["Name"] = "home-agent_lighting-egress"
    shape["Options"] = {"com.docker.network.bridge.name": "ha-light-egr0"}
    shape["IPAM"] = {"Config": [{"Subnet": "172.27.0.0/24", "Gateway": "172.27.0.1"}]}
    shape["Containers"] = {"id": {
        "Name": "home-shared-preferences-lighting-1", "IPv4Address": "172.27.0.10/24"}}
    return shape


def lighting_container_shape():
    shape = container_shape()
    shape["Name"] = "/home-shared-preferences-lighting-1"
    shape["Config"] = {"Labels": {
        "com.docker.compose.project": "home-shared-preferences",
        "com.docker.compose.service": "lighting",
    }, "Env": []}
    shape["HostConfig"]["PortBindings"] = {}
    shape["NetworkSettings"] = {"Networks": {
        "home-agent_api-net": {"IPAddress": "172.23.0.40", "GlobalIPv6Address": ""},
        "home-agent_lighting-egress": {"IPAddress": "172.27.0.10", "GlobalIPv6Address": ""},
    }}
    return shape


class LightingEgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = contract_from_env(lighting_env(), LIGHTING)

    def test_profile_reaches_both_ha_urls_in_order(self) -> None:
        self.assertEqual(
            LIGHTING.url_keys, ("HOME_AGENT_HA_URL", "HOME_AGENT_VICTORIA_HA_URL")
        )
        self.assertEqual(self.contract.ha_urls, (HA_URL, VICTORIA_HA_URL))
        self.assertEqual(self.contract.ha_ports, (10000, 10001))
        self.assertEqual(self.contract.ha_host, "home-app.example.ts.net")
        self.assertIsNone(self.contract.bind_address)
        self.assertEqual(str(self.contract.bff_ip), "172.27.0.10")
        self.assertEqual(self.contract.bridge, "ha-light-egr0")

    def test_guard_has_two_exact_accepts_and_its_own_chain(self) -> None:
        self.assertEqual(expected_guard_lines(self.contract, TAIL_IP), LIGHT_GUARD)
        self.assertEqual(
            guard_spec(self.contract, TAIL_IP),
            (LIGHT_JUMP, None, tuple(LIGHT_GUARD[1:3]), LIGHT_GUARD[3]),
        )
        validate_guard_rules(
            ["-P INPUT DROP", LIGHT_JUMP, "-A INPUT -i ha-light-egr0 -j ACCEPT"],
            LIGHT_GUARD, self.contract, TAIL_IP,
        )
        # The single-URL view refuses to hide the second accept.
        with self.assertRaises(ContractError):
            guard_rule_spec(self.contract, TAIL_IP)

    def test_apply_creates_both_accepts_before_the_drop(self) -> None:
        commands = _record_apply_guard(self.contract, ["-P INPUT DROP"], None)
        self.assertEqual(commands, [
            ["iptables", "-N", "HOME_AGENT_LIGHT_INPUT"],
            ["iptables", "-A", "HOME_AGENT_LIGHT_INPUT", "-s", "172.27.0.10/32",
             "-d", "100.87.94.18/32", "-p", "tcp", "--dport", "10000", "-j", "ACCEPT"],
            ["iptables", "-A", "HOME_AGENT_LIGHT_INPUT", "-s", "172.27.0.10/32",
             "-d", "100.87.94.18/32", "-p", "tcp", "--dport", "10001", "-j", "ACCEPT"],
            ["iptables", "-A", "HOME_AGENT_LIGHT_INPUT", "-j", "DROP"],
            ["iptables", "-I", "INPUT", "1", "-i", "ha-light-egr0",
             "-j", "HOME_AGENT_LIGHT_INPUT"],
        ])
        # An exact, already-leading guard is left alone.
        self.assertEqual(
            _record_apply_guard(
                self.contract, ["-P INPUT DROP", LIGHT_JUMP], LIGHT_GUARD
            ),
            [],
        )

    def test_mismatched_hosts_and_shared_ports_are_rejected(self) -> None:
        for values in (
            lighting_env(HOME_AGENT_VICTORIA_HA_URL="https://victoria.example.ts.net:10001"),
            lighting_env(HOME_AGENT_HA_URL="https://other-app.example.ts.net:10000"),
            lighting_env(HOME_AGENT_VICTORIA_HA_URL="https://home-app.example.ts.net.:10001"),
            lighting_env(HOME_AGENT_VICTORIA_HA_URL="https://192.168.1.60:10001"),
            lighting_env(HOME_AGENT_VICTORIA_HA_URL=HA_URL),
        ):
            with self.subTest(values=values), self.assertRaises(ContractError):
                contract_from_env(values, LIGHTING)

    def test_a_missing_or_non_canonical_url_is_rejected(self) -> None:
        for values in (
            lighting_env(HOME_AGENT_VICTORIA_HA_URL=None),
            lighting_env(HOME_AGENT_HA_URL=None),
            lighting_env(HOME_AGENT_VICTORIA_HA_URL=""),
            lighting_env(HOME_AGENT_VICTORIA_HA_URL="http://home-app.example.ts.net:10001"),
            lighting_env(HOME_AGENT_VICTORIA_HA_URL=VICTORIA_HA_URL + "/api"),
            lighting_env(HOME_AGENT_HA_TAILSCALE_IPV4="192.168.1.60"),
            lighting_env(HOME_AGENT_LIGHTING_EGRESS_IP="172.26.0.10"),
        ):
            with self.subTest(values=values), self.assertRaises(ContractError):
                contract_from_env(values, LIGHTING)

    def test_unexpected_extra_rules_are_refused(self) -> None:
        accept_ha, accept_victoria, deny = LIGHT_GUARD[1], LIGHT_GUARD[2], LIGHT_GUARD[3]
        extra_port = accept_victoria.replace("--dport 10001", "--dport 8123")
        cases = (
            ["-N HOME_AGENT_LIGHT_INPUT", accept_ha, accept_victoria, extra_port, deny],
            ["-N HOME_AGENT_LIGHT_INPUT", accept_ha, accept_victoria, deny, deny],
            ["-N HOME_AGENT_LIGHT_INPUT", "-A HOME_AGENT_LIGHT_INPUT -j ACCEPT",
             accept_ha, accept_victoria, deny],
            ["-N HOME_AGENT_LIGHT_INPUT", accept_ha, accept_ha, accept_victoria, deny],
            ["-N HOME_AGENT_LIGHT_INPUT", accept_victoria, accept_ha, deny],
            ["-N HOME_AGENT_LIGHT_INPUT", accept_ha, deny],
            ["-N HOME_AGENT_LIGHT_INPUT", accept_ha, accept_victoria],
        )
        for guard in cases:
            with self.subTest(guard=guard):
                with self.assertRaises(ContractError):
                    validate_guard_rules(
                        ["-P INPUT DROP", LIGHT_JUMP], guard, self.contract, TAIL_IP
                    )
                # apply refuses to adopt or rewrite an unreviewed chain.
                with self.assertRaises(ContractError):
                    _record_apply_guard(
                        self.contract, ["-P INPUT DROP", LIGHT_JUMP], guard
                    )
        for input_lines in (
            ["-P INPUT DROP", "-A INPUT -i ha-light-egr0 -j ACCEPT", LIGHT_JUMP],
            ["-P INPUT DROP", LIGHT_JUMP, LIGHT_JUMP],
            ["-P INPUT DROP"],
        ):
            with self.subTest(input_lines=input_lines), self.assertRaises(ContractError):
                validate_guard_rules(input_lines, LIGHT_GUARD, self.contract, TAIL_IP)

    def test_all_three_first_hop_guards_may_lead_input_in_any_order(self) -> None:
        bff = contract_from_env(env())
        victoria = contract_from_env(victoria_env(), VICTORIA_LINK)
        chains = (
            (bff, BFF_GUARD), (victoria, VLINK_GUARD), (self.contract, LIGHT_GUARD)
        )
        for order in itertools.permutations((BFF_JUMP, VLINK_JUMP, LIGHT_JUMP)):
            lines = ["-P INPUT DROP", *order, "-A INPUT -j ufw-before-input"]
            for contract, guard in chains:
                with self.subTest(order=order, profile=contract.profile.name):
                    validate_guard_rules(lines, guard, contract, TAIL_IP)
        for loose in (
            "-A INPUT -j HOME_AGENT_LIGHT_INPUT",
            "-A INPUT -i ha-bff-egress0 -j HOME_AGENT_LIGHT_INPUT",
            "-A INPUT -i ha-light-egr0 -p tcp -j HOME_AGENT_LIGHT_INPUT",
            "-A INPUT -i ha-light-egr0 -j ACCEPT",
        ):
            with self.subTest(loose=loose), self.assertRaises(ContractError):
                validate_guard_rules(
                    ["-P INPUT DROP", loose, BFF_JUMP], BFF_GUARD, bff, TAIL_IP
                )

    def test_profile_accepts_exact_live_shapes_and_rejects_drift(self) -> None:
        validate_network(self.contract, lighting_network_shape())
        validate_container(self.contract, lighting_container_shape())
        published = lighting_container_shape()
        published["HostConfig"]["PortBindings"] = {
            "9460/tcp": [{"HostIp": "127.0.0.1", "HostPort": "9460"}]}
        victoria_identity = lighting_container_shape()
        victoria_identity["Name"] = "/home-shared-preferences-victoria-link-1"
        extra = lighting_container_shape()
        extra["NetworkSettings"]["Networks"]["home-agent_victoria-link-egress"] = {
            "IPAddress": "172.26.0.11", "GlobalIPv6Address": ""}
        for value in (published, victoria_identity, extra):
            with self.subTest(value=value["Name"]), self.assertRaises(ContractError):
                validate_container(self.contract, value)
        for shape in (victoria_network_shape(), network_shape()):
            with self.subTest(shape=shape["Name"]), self.assertRaises(ContractError):
                validate_network(self.contract, shape)

    def test_ufw_has_one_commented_rule_per_ha_url(self) -> None:
        def check(port: str) -> list[str]:
            return [
                "iptables", "-C", "ufw-user-input", "-i", "ha-light-egr0",
                "-s", "172.27.0.10/32", "-d", "100.87.94.18/32",
                "-p", "tcp", "--dport", port, "-j", "ACCEPT",
            ]

        def allow(port: str) -> list[str]:
            return [
                "ufw", "allow", "in", "on", "ha-light-egr0", "from", "172.27.0.10",
                "to", "100.87.94.18", "port", port, "proto", "tcp",
                "comment", "home-agent-lighting-to-home-ha",
            ]

        checks, commands = _record_rule_commands(
            apply_rule, self.contract, [False, True, False, True]
        )
        self.assertEqual(checks, [check("10000")] * 2 + [check("10001")] * 2)
        self.assertEqual(commands, [allow("10000"), allow("10001")])
        # Only the missing rule is added.
        _checks, commands = _record_rule_commands(
            apply_rule, self.contract, [True, False, True]
        )
        self.assertEqual(commands, [allow("10001")])
        _checks, commands = _record_rule_commands(
            remove_rule, self.contract, [True, False, True, False]
        )
        self.assertEqual(commands, [
            ["ufw", "--force", "delete", "allow", "in", "on", "ha-light-egr0",
             "from", "172.27.0.10", "to", "100.87.94.18", "port", port,
             "proto", "tcp"]
            for port in ("10000", "10001")
        ])
        # verify requires both rules.
        answers = iter([True, False])
        with patch(
            "firewall_contract.subprocess.run",
            side_effect=lambda *_a, **_k: _Completed(0 if next(answers) else 1),
        ):
            self.assertFalse(rule_present(self.contract, TAIL_IP))

    def test_probes_each_ha_url_with_python(self) -> None:
        self.assertEqual(probe_commands(self.contract), [
            ["docker", "exec", "home-shared-preferences-lighting-1",
             "python3", "-I", "-c", PYTHON_PROBE, HA_URL],
            ["docker", "exec", "home-shared-preferences-lighting-1",
             "python3", "-I", "-c", PYTHON_PROBE, VICTORIA_HA_URL],
        ])

    def test_python_probe_passes_only_on_a_direct_405(self) -> None:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                status = {"/405/auth/token": 405, "/302/auth/token": 302}.get(self.path, 200)
                self.send_response(status)
                if status == 302:
                    self.send_header("Location", "/405/auth/token")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args) -> None:
                return None

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with socket.socket() as closed:
                closed.bind(("127.0.0.1", 0))
                refused = f"http://127.0.0.1:{closed.getsockname()[1]}"
            for url, expected in (
                (f"{base}/405", 0), (f"{base}/302", 2), (f"{base}/200", 2), (refused, 3),
            ):
                with self.subTest(url=url):
                    result = subprocess.run(
                        [sys.executable, "-I", "-c", PYTHON_PROBE, url],
                        capture_output=True, timeout=30, check=False,
                    )
                    self.assertEqual(result.returncode, expected)
        finally:
            server.shutdown()
            server.server_close()

    def test_if_configured_follows_the_lighting_egress_address(self) -> None:
        def run(values: dict[str, str]):
            with (
                patch("firewall_contract.os.geteuid", return_value=0, create=True),
                patch("firewall_contract.validate_trusted_execution"),
                patch("firewall_contract.sanitize_process_environment"),
                patch("firewall_contract.validate_root_config"),
                patch("firewall_contract.read_env", return_value=values),
                patch("firewall_contract.validate_lifecycle_hook"),
                patch("firewall_contract.apply_guard") as guard,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                code = firewall_main(
                    ["guard", "--profile", "lighting", "--if-configured",
                     "--env", "/srv/home-agent/config/home-agent.env"]
                )
            return code, guard

        # Both HA URLs exist for the other profiles; that alone is not lighting.
        code, guard = run(lighting_env(HOME_AGENT_LIGHTING_EGRESS_IP=None))
        self.assertEqual(code, 0)
        guard.assert_not_called()
        code, guard = run(lighting_env())
        self.assertEqual(code, 0)
        self.assertEqual(guard.call_args.args[0].profile, LIGHTING)
        self.assertEqual(guard.call_args.args[0].ha_ports, (10000, 10001))
        # Once provisioned, a missing HA URL fails closed instead of skipping.
        code, guard = run(lighting_env(HOME_AGENT_VICTORIA_HA_URL=None))
        self.assertEqual(code, 1)
        guard.assert_not_called()

    def test_profiles_have_distinct_bridges_chains_and_subnets(self) -> None:
        profiles = list(PROFILES.values())
        self.assertEqual([p.name for p in profiles], ["bff", "victoria-link", "lighting"])
        for field in ("network", "container", "comment", "chain", "default_bridge",
                      "egress_prefix", "provision_key"):
            values = [getattr(p, field) for p in profiles]
            self.assertEqual(len(set(values)), len(values), field)
        subnets = [ipaddress.ip_network(p.default_subnet) for p in profiles]
        for index, left in enumerate(subnets):
            for right in subnets[index + 1:]:
                self.assertFalse(left.overlaps(right))

    def test_hook_and_timer_guard_lighting_only_when_configured(self) -> None:
        hook = (DEPLOY / "bff-egress/ufw_after_init.sh").read_text(encoding="utf-8")
        self.assertIn(
            'exec /usr/bin/python3 -I "$helper" guard --profile lighting \\\n'
            '      --if-configured --env "$environment"',
            hook,
        )
        self.assertLess(
            hook.index("--profile victoria-link"), hook.index("--profile lighting")
        )
        self.assertEqual(hook.count("exec "), 1)
        service = (
            DEPLOY / "operator/systemd/home-agent-lighting-egress-verify.service"
        ).read_text(encoding="utf-8")
        timer = (
            DEPLOY / "operator/systemd/home-agent-lighting-egress-verify.timer"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "ExecStart=/usr/bin/python3 -I "
            "/usr/local/libexec/home-agent-bff-egress/firewall_contract.py "
            "verify --profile lighting --env /srv/home-agent/config/home-agent.env",
            service,
        )
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("Unit=home-agent-lighting-egress-verify.service", timer)


if __name__ == "__main__":
    unittest.main()

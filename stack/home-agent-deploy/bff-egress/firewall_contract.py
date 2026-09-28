#!/usr/bin/env python3
"""Reconcile each reviewed container's one permitted host-local HA OAuth path.

The browser reaches Home Assistant through Tailscale Serve.  The BFF must use
the same TLS endpoint for code exchange, refresh, whoami, and revocation, but a
default-deny host firewall also applies to packets sourced by Docker bridges.
This tool validates the complete live identity/network contract before adding
or accepting one exact UFW rule.  It never reads or prints OAuth material.

Two reviewed profiles exist. ``bff`` (the default) is the Echo BFF reaching
Echo HA; ``victoria-link`` is the separately provisioned Victoria linking
service reaching Victoria HA through its own added Serve port on this same
node. Each profile has its own bridge, source address, guard chain and rule.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


NETWORK_NAME = "home-agent_bff-public"
API_NETWORK_NAME = "home-agent_api-net"
CONTAINER_NAME = "home-agent-bff-1"
COMPOSE_PROJECT = "home-agent"
COMPOSE_SERVICE = "bff"
RULE_COMMENT = "home-agent-bff-to-local-ha-oauth"
GUARD_CHAIN = "HOME_AGENT_BFF_INPUT"
DEFAULT_SUBNET = "172.22.0.0/24"
DEFAULT_GATEWAY = "172.22.0.1"
DEFAULT_BFF_IP = "172.22.0.10"
DEFAULT_BRIDGE = "ha-bff-egress0"
TRUSTED_INSTALL = Path(
    "/usr/local/libexec/home-agent-bff-egress/firewall_contract.py"
)
UFW_HOOK = Path("/etc/ufw/after.init")
UFW_HOOK_SHA256 = "e0f9d04732f1bdb809957ffd68a0966bd736eb7d887b6fb1d5dda18ac933dfd7"
UFW_CONFIG = Path("/etc/ufw/ufw.conf")
TRUSTED_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


class ContractError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    network: str
    container: str
    compose_project: str
    compose_service: str
    comment: str
    chain: str
    url_key: str
    egress_prefix: str
    default_subnet: str
    default_gateway: str
    default_ip: str
    default_bridge: str
    # The BFF publishes one loopback port whose replies must return through
    # the guard; a profile without host publication has no return-flow rule.
    published_port: int | None


BFF = Profile(
    name="bff",
    network=NETWORK_NAME,
    container=CONTAINER_NAME,
    compose_project=COMPOSE_PROJECT,
    compose_service=COMPOSE_SERVICE,
    comment=RULE_COMMENT,
    chain=GUARD_CHAIN,
    url_key="HOME_AGENT_HA_URL",
    egress_prefix="HOME_AGENT_BFF_EGRESS_",
    default_subnet=DEFAULT_SUBNET,
    default_gateway=DEFAULT_GATEWAY,
    default_ip=DEFAULT_BFF_IP,
    default_bridge=DEFAULT_BRIDGE,
    published_port=8097,
)
VICTORIA_LINK = Profile(
    name="victoria-link",
    network="home-agent_victoria-link-egress",
    container="home-shared-preferences-victoria-link-1",
    compose_project="home-shared-preferences",
    compose_service="victoria-link",
    comment="home-agent-victoria-link-to-victoria-ha",
    chain="HOME_AGENT_VLINK_INPUT",
    url_key="HOME_AGENT_VICTORIA_HA_URL",
    egress_prefix="HOME_AGENT_VICTORIA_LINK_EGRESS_",
    default_subnet="172.26.0.0/24",
    default_gateway="172.26.0.1",
    default_ip="172.26.0.10",
    default_bridge="ha-vlink-egr0",
    published_port=None,
)
PROFILES = {profile.name: profile for profile in (BFF, VICTORIA_LINK)}


@dataclass(frozen=True, slots=True)
class Contract:
    ha_url: str
    ha_host: str
    ha_port: int
    tail_ip: ipaddress.IPv4Address
    subnet: ipaddress.IPv4Network
    gateway: ipaddress.IPv4Address
    bff_ip: ipaddress.IPv4Address
    bridge: str
    bind_address: ipaddress.IPv4Address | None
    profile: Profile = BFF


def validate_trusted_execution() -> None:
    """Reject sudo execution from a mutable checkout or injected Python."""

    if sys.flags.isolated != 1:
        raise ContractError("invoke the installed verifier with python3 -I")
    source = Path(__file__)
    if source.is_symlink():
        raise ContractError("installed verifier may not be a symlink")
    try:
        resolved = source.resolve(strict=True)
    except OSError as exc:
        raise ContractError("installed verifier is unavailable") from exc
    if resolved != TRUSTED_INSTALL:
        raise ContractError("verifier must run from its trusted installed path")
    candidate = resolved
    while True:
        metadata = candidate.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise ContractError("verifier install path is not root-owned and immutable")
        if candidate == candidate.parent:
            break
        candidate = candidate.parent


def sanitize_process_environment() -> None:
    os.environ["PATH"] = TRUSTED_PATH
    os.environ["LANG"] = "C"
    os.environ["LC_ALL"] = "C"
    for key in (
        "CDPATH",
        "ENV",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
    ):
        os.environ.pop(key, None)


def validate_root_config(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink():
        raise ContractError("firewall environment must be an absolute regular file")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ContractError("firewall environment is unavailable") from exc
    if resolved != candidate:
        raise ContractError("firewall environment path may not traverse symlinks")
    current = resolved
    while True:
        metadata = current.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise ContractError(
                "firewall environment path is not root-owned and immutable"
            )
        if current == resolved and not stat.S_ISREG(metadata.st_mode):
            raise ContractError("firewall environment must be a regular file")
        if current == current.parent:
            break
        current = current.parent
    return resolved


def validate_lifecycle_hook() -> None:
    if UFW_HOOK.is_symlink():
        raise ContractError("UFW lifecycle hook may not be a symlink")
    try:
        resolved = UFW_HOOK.resolve(strict=True)
        content = resolved.read_bytes()
    except OSError as exc:
        raise ContractError("UFW lifecycle hook is unavailable") from exc
    if resolved != UFW_HOOK:
        raise ContractError("UFW lifecycle hook path may not traverse symlinks")
    current = resolved
    while True:
        metadata = current.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise ContractError("UFW lifecycle hook path is not root-owned and immutable")
        if current == resolved and not stat.S_ISREG(metadata.st_mode):
            raise ContractError("UFW lifecycle hook must be a regular file")
        if current == current.parent:
            break
        current = current.parent
    if hashlib.sha256(content).hexdigest() != UFW_HOOK_SHA256:
        raise ContractError("UFW lifecycle hook differs from reviewed policy")


def read_env(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise ContractError(f"invalid environment line {number}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in values:
            raise ContractError(f"invalid or duplicate environment key on line {number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def contract_from_env(values: dict[str, str], profile: Profile = BFF) -> Contract:
    ha_url = values.get(profile.url_key, "")
    try:
        parsed = urlsplit(ha_url)
        port = parsed.port or 443
    except ValueError as exc:
        raise ContractError(f"{profile.url_key} is invalid") from exc
    host = parsed.hostname or ""
    canonical = f"https://{host}" + (f":{port}" if port != 443 else "")
    if (
        parsed.scheme != "https"
        or not host
        or host != host.lower()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or ha_url.rstrip("/") != canonical
    ):
        raise ContractError(f"{profile.url_key} must be one canonical HTTPS root")

    prefix = profile.egress_prefix
    try:
        subnet = ipaddress.ip_network(
            values.get(f"{prefix}SUBNET", profile.default_subnet), strict=True
        )
        gateway = ipaddress.ip_address(
            values.get(f"{prefix}GATEWAY", profile.default_gateway)
        )
        bff_ip = ipaddress.ip_address(
            values.get(f"{prefix}IP", profile.default_ip)
        )
        tail_ip = ipaddress.ip_address(
            values.get("HOME_AGENT_HA_TAILSCALE_IPV4", "")
        )
        bind_address = (
            ipaddress.ip_address(values.get("HOME_AGENT_BFF_BIND_ADDR", "127.0.0.1"))
            if profile.published_port is not None
            else None
        )
    except ValueError as exc:
        raise ContractError("BFF egress address contract is invalid") from exc
    if not isinstance(subnet, ipaddress.IPv4Network) or not all(
        isinstance(value, ipaddress.IPv4Address)
        for value in (gateway, bff_ip, tail_ip)
    ) or (bind_address is not None and not isinstance(bind_address, ipaddress.IPv4Address)):
        raise ContractError("BFF egress contract must use IPv4")
    if gateway not in subnet or bff_ip not in subnet:
        raise ContractError("BFF egress gateway and address must be inside the subnet")
    if gateway in (subnet.network_address, subnet.broadcast_address):
        raise ContractError("BFF egress gateway is not a usable address")
    if bff_ip in (subnet.network_address, subnet.broadcast_address, gateway):
        raise ContractError("BFF egress address is not independently usable")
    if tail_ip not in ipaddress.ip_network("100.64.0.0/10"):
        raise ContractError("HA endpoint must use a pinned Tailscale IPv4 address")
    if bind_address is not None and not bind_address.is_loopback:
        raise ContractError("BFF host publication must remain loopback-only")

    bridge = values.get(f"{prefix}BRIDGE", profile.default_bridge)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", bridge):
        raise ContractError("BFF egress bridge name is invalid")
    return Contract(
        ha_url=canonical,
        ha_host=host,
        ha_port=port,
        tail_ip=tail_ip,
        subnet=subnet,
        gateway=gateway,
        bff_ip=bff_ip,
        bridge=bridge,
        bind_address=bind_address,
        profile=profile,
    )


def _run(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContractError(f"{label} failed") from exc
    if result.returncode != 0:
        raise ContractError(f"{label} failed")
    return result


def _json_command(command: list[str], label: str):
    result = _run(command, label)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ContractError(f"{label} returned malformed JSON") from exc


def validate_tailnet_endpoint(
    contract: Contract,
    status: dict,
    resolved: set[ipaddress.IPv4Address],
) -> ipaddress.IPv4Address:
    own = status.get("Self", {})
    dns_name = str(own.get("DNSName", "")).rstrip(".").lower()
    if own.get("Online") is not True or dns_name != contract.ha_host:
        raise ContractError("HA OAuth host is not this online Tailscale node")
    addresses: list[ipaddress.IPv4Address] = []
    for value in own.get("TailscaleIPs", []) or []:
        try:
            parsed = ipaddress.ip_address(value)
        except ValueError:
            continue
        if isinstance(parsed, ipaddress.IPv4Address):
            addresses.append(parsed)
    if (
        len(addresses) != 1
        or resolved != {addresses[0]}
        or addresses[0] != contract.tail_ip
    ):
        raise ContractError("HA OAuth DNS does not resolve exactly to this Tailscale node")
    return contract.tail_ip


def validate_network(contract: Contract, value: dict) -> None:
    if (
        value.get("Name") != contract.profile.network
        or value.get("Driver") != "bridge"
        or value.get("Scope") != "local"
        or value.get("Internal") is not False
        or value.get("EnableIPv6") is not False
    ):
        raise ContractError("live BFF egress network boundary differs from policy")
    if (value.get("Options") or {}).get("com.docker.network.bridge.name") != contract.bridge:
        raise ContractError("live BFF egress bridge name differs from policy")
    configs = (value.get("IPAM") or {}).get("Config") or []
    if len(configs) != 1 or configs[0].get("Subnet") != str(contract.subnet):
        raise ContractError("live BFF egress subnet differs from policy")
    if configs[0].get("Gateway") != str(contract.gateway):
        raise ContractError("live BFF egress gateway differs from policy")
    members = value.get("Containers") or {}
    if len(members) != 1:
        raise ContractError("BFF egress network must contain exactly one container")
    member = next(iter(members.values()))
    if member.get("Name") != contract.profile.container:
        raise ContractError("unexpected container attached to BFF egress network")
    member_ip = str(member.get("IPv4Address", "")).split("/", 1)[0]
    if member_ip != str(contract.bff_ip):
        raise ContractError("live BFF egress source address differs from policy")


def _container_environment(value: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in (value.get("Config") or {}).get("Env") or []:
        if not isinstance(item, str) or "=" not in item:
            raise ContractError("BFF environment shape is invalid")
        key, content = item.split("=", 1)
        if not key or key in result:
            raise ContractError("BFF environment contains duplicate keys")
        result[key] = content
    return result


def validate_container(contract: Contract, value: dict) -> None:
    profile = contract.profile
    config = value.get("Config") or {}
    host = value.get("HostConfig") or {}
    labels = config.get("Labels") or {}
    if (
        value.get("Name") != f"/{profile.container}"
        or labels.get("com.docker.compose.project") != profile.compose_project
        or labels.get("com.docker.compose.service") != profile.compose_service
    ):
        raise ContractError("live BFF does not have the reviewed Compose identity")
    networks = ((value.get("NetworkSettings") or {}).get("Networks") or {})
    if set(networks) != {API_NETWORK_NAME, profile.network}:
        raise ContractError("live BFF has an unreviewed network attachment")
    if networks[profile.network].get("IPAddress") != str(contract.bff_ip):
        raise ContractError("live BFF source address differs from policy")
    if networks[profile.network].get("GlobalIPv6Address") not in (None, ""):
        raise ContractError("live BFF received an unreviewed IPv6 address")
    if (
        host.get("ReadonlyRootfs") is not True
        or host.get("Privileged") is not False
        or set(host.get("CapDrop") or []) != {"ALL"}
        or host.get("CapAdd") not in (None, [])
        or "no-new-privileges:true" not in (host.get("SecurityOpt") or [])
    ):
        raise ContractError("live BFF hardening differs from policy")
    bindings = host.get("PortBindings") or {}
    if profile.published_port is None:
        # The Victoria link service is reachable only on the private API
        # network and through its tailnet origin node; it publishes nothing,
        # and its HA origin comes from its mounted profile, not environment.
        if bindings:
            raise ContractError("live BFF host publication differs from policy")
        return
    published = f"{profile.published_port}/tcp"
    if set(bindings) != {published} or len(bindings[published] or []) != 1:
        raise ContractError("live BFF host publication differs from policy")
    binding = bindings[published][0]
    if binding.get("HostIp") != str(contract.bind_address):
        raise ContractError("live BFF host publication is not the reviewed loopback")
    environment = _container_environment(value)
    if environment.get(profile.url_key) != contract.ha_url:
        raise ContractError("live BFF HA endpoint differs from policy")


def live_contract(contract: Contract) -> ipaddress.IPv4Address:
    network_payload = _json_command(
        ["docker", "network", "inspect", contract.profile.network],
        "Docker network inspection",
    )
    container_payload = _json_command(
        ["docker", "inspect", contract.profile.container], "Docker BFF inspection"
    )
    status = _json_command(["tailscale", "status", "--json"], "Tailscale inspection")
    if not isinstance(network_payload, list) or len(network_payload) != 1:
        raise ContractError("Docker returned an unexpected BFF network shape")
    if not isinstance(container_payload, list) or len(container_payload) != 1:
        raise ContractError("Docker returned an unexpected BFF container shape")
    try:
        resolved = {
            ipaddress.IPv4Address(item[4][0])
            for item in socket.getaddrinfo(
                contract.ha_host,
                contract.ha_port,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            )
        }
    except (OSError, ValueError) as exc:
        raise ContractError("HA OAuth DNS resolution failed") from exc
    tail_ip = validate_tailnet_endpoint(contract, status, resolved)
    validate_network(contract, network_payload[0])
    validate_container(contract, container_payload[0])
    return tail_ip


def _rule_command(contract: Contract, tail_ip: ipaddress.IPv4Address) -> list[str]:
    return [
        "iptables", "-C", "ufw-user-input",
        "-i", contract.bridge,
        "-s", f"{contract.bff_ip}/32",
        "-d", f"{tail_ip}/32",
        "-p", "tcp", "--dport", str(contract.ha_port),
        "-j", "ACCEPT",
    ]


def guard_rule_spec(
    contract: Contract, tail_ip: ipaddress.IPv4Address
) -> tuple[str, str | None, str, str]:
    """Return the canonical jump, return-flow allow (if any), HA allow, and drop."""

    chain = contract.profile.chain
    jump = f"-A INPUT -i {contract.bridge} -j {chain}"
    established = None
    if contract.profile.published_port is not None:
        established = (
            f"-A {chain} -s {contract.bff_ip}/32 "
            f"-d {contract.gateway}/32 -p tcp -m tcp "
            f"--sport {contract.profile.published_port} -m conntrack "
            "--ctstate RELATED,ESTABLISHED -j ACCEPT"
        )
    allow = (
        f"-A {chain} -s {contract.bff_ip}/32 -d {tail_ip}/32 "
        f"-p tcp -m tcp --dport {contract.ha_port} -j ACCEPT"
    )
    deny = f"-A {chain} -j DROP"
    return jump, established, allow, deny


def expected_guard_lines(
    contract: Contract, tail_ip: ipaddress.IPv4Address
) -> list[str]:
    _jump, established, allow, deny = guard_rule_spec(contract, tail_ip)
    return [f"-N {contract.profile.chain}", *([established] if established else []), allow, deny]


def _targets_guard(line: str, chain: str = GUARD_CHAIN) -> bool:
    tokens = line.split()
    return any(
        tokens[index] == "-j" and tokens[index + 1] == chain
        for index in range(len(tokens) - 1)
    )


def _reviewed_leading_jumps(input_rules: list[str], contract: Contract) -> list[str]:
    """The leading INPUT rules that are exact reviewed first-hop guard jumps.

    Only this profile's own exact jump, or another reviewed profile's exact
    ``-A INPUT -i <other bridge> -j <other chain>`` jump, may precede it.
    """

    own = guard_rule_spec(contract, contract.tail_ip)[0]
    others = {profile.chain for profile in PROFILES.values()} - {contract.profile.chain}
    leading: list[str] = []
    for line in input_rules:
        tokens = line.split()
        other = (
            len(tokens) == 6
            and tokens[:3] == ["-A", "INPUT", "-i"]
            and tokens[4] == "-j"
            and tokens[3] != contract.bridge
            and tokens[5] in others
        )
        if line != own and not other:
            break
        leading.append(line)
    return leading


def validate_guard_rules(
    input_lines: list[str],
    guard_lines: list[str],
    contract: Contract,
    tail_ip: ipaddress.IPv4Address,
) -> None:
    """Prove every packet from the BFF bridge meets the exact allow/drop guard.

    The jump must be the first INPUT rule. The private chain first permits only
    conntrack-confirmed return traffic to the reviewed loopback publication,
    then the exact HA tuple, and finally drops everything else. Broader accepts
    in UFW, Docker, or a later custom chain are unreachable for this bridge.
    """

    jump = guard_rule_spec(contract, tail_ip)[0]
    input_rules = [line for line in input_lines if line.startswith("-A INPUT ")]
    references = [
        line for line in input_rules if _targets_guard(line, contract.profile.chain)
    ]
    # Each reviewed bridge has its own first-hop jump. Only another reviewed
    # profile's guard jump (which matches a different bridge) may precede it.
    if references != [jump] or jump not in _reviewed_leading_jumps(input_rules, contract):
        raise ContractError("BFF OAuth guard is not the sole first INPUT jump")
    if guard_lines != expected_guard_lines(contract, tail_ip):
        raise ContractError("BFF OAuth guard chain differs from exact allow/drop policy")


def _iptables_chain_lines(chain: str) -> list[str] | None:
    result = subprocess.run(
        ["iptables", "-S", chain],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode == 0:
        return result.stdout.splitlines()
    if result.returncode == 1 and not result.stdout.strip():
        return None
    raise ContractError("host input-chain inspection failed")


def _input_lines() -> list[str]:
    return _run(["iptables", "-S", "INPUT"], "host input-chain inspection").stdout.splitlines()


def validate_guard_live(
    contract: Contract, tail_ip: ipaddress.IPv4Address
) -> None:
    guard_lines = _iptables_chain_lines(contract.profile.chain)
    if guard_lines is None:
        raise ContractError("BFF OAuth first-hop guard is absent")
    validate_guard_rules(_input_lines(), guard_lines, contract, tail_ip)


def _delete_guard_jump(contract: Contract) -> None:
    _run(
        ["iptables", "-D", "INPUT", "-i", contract.bridge, "-j", contract.profile.chain],
        "BFF OAuth guard-jump removal",
    )


def apply_guard(contract: Contract, tail_ip: ipaddress.IPv4Address) -> None:
    chain = contract.profile.chain
    port = contract.profile.published_port
    jump = guard_rule_spec(contract, tail_ip)[0]
    expected_guard = expected_guard_lines(contract, tail_ip)
    _jump, _established, allow, deny = guard_rule_spec(contract, tail_ip)
    # Only the BFF ever had the earlier two-rule guard.
    legacy_guard = [f"-N {chain}", allow, deny] if port is not None else None
    input_lines = _input_lines()
    references = [line for line in input_lines if _targets_guard(line, chain)]
    guard_lines = _iptables_chain_lines(chain)
    created = False

    if guard_lines is None:
        if references:
            raise ContractError("INPUT references an absent BFF OAuth guard")
        try:
            _run(["iptables", "-N", chain], "BFF OAuth guard creation")
            created = True
            if port is not None:
                _run(
                    [
                        "iptables", "-A", chain,
                        "-s", f"{contract.bff_ip}/32",
                        "-d", f"{contract.gateway}/32",
                        "-p", "tcp", "--sport", str(port),
                        "-m", "conntrack",
                        "--ctstate", "RELATED,ESTABLISHED",
                        "-j", "ACCEPT",
                    ],
                    "BFF loopback established-return guard allow",
                )
            _run(
                [
                    "iptables", "-A", chain,
                    "-s", f"{contract.bff_ip}/32",
                    "-d", f"{tail_ip}/32",
                    "-p", "tcp", "--dport", str(contract.ha_port),
                    "-j", "ACCEPT",
                ],
                "BFF OAuth exact guard allow",
            )
            _run(
                ["iptables", "-A", chain, "-j", "DROP"],
                "BFF OAuth terminal guard drop",
            )
            guard_lines = expected_guard
        except ContractError:
            if created:
                subprocess.run(
                    ["iptables", "-F", chain],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                subprocess.run(
                    ["iptables", "-X", chain],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            raise
    elif legacy_guard is not None and guard_lines == legacy_guard:
        # Upgrade only the exact previously reviewed guard in place. Inserting
        # the return-flow rule before the existing HA allow/drop pair creates
        # no fail-open interval and leaves all new BFF-originated flows denied.
        _run(
            [
                "iptables", "-I", chain, "1",
                "-s", f"{contract.bff_ip}/32",
                "-d", f"{contract.gateway}/32",
                "-p", "tcp", "--sport", str(port),
                "-m", "conntrack",
                "--ctstate", "RELATED,ESTABLISHED",
                "-j", "ACCEPT",
            ],
            "BFF loopback established-return guard migration",
        )
        guard_lines = expected_guard
    elif guard_lines != expected_guard:
        raise ContractError("existing BFF OAuth guard chain is unreviewed")

    input_lines = _input_lines()
    references = [line for line in input_lines if _targets_guard(line, chain)]
    if not references:
        _run(
            ["iptables", "-I", "INPUT", "1", "-i", contract.bridge, "-j", chain],
            "BFF OAuth first-hop guard insertion",
        )
    elif references == [jump]:
        input_rules = [line for line in input_lines if line.startswith("-A INPUT ")]
        if jump not in _reviewed_leading_jumps(input_rules, contract):
            _delete_guard_jump(contract)
            _run(
                ["iptables", "-I", "INPUT", "1", "-i", contract.bridge, "-j", chain],
                "BFF OAuth first-hop guard restoration",
            )
    else:
        raise ContractError("INPUT contains an unreviewed BFF OAuth guard reference")
    validate_guard_live(contract, tail_ip)


def remove_guard(contract: Contract, tail_ip: ipaddress.IPv4Address) -> None:
    chain = contract.profile.chain
    guard_lines = _iptables_chain_lines(chain)
    input_lines = _input_lines()
    references = [line for line in input_lines if _targets_guard(line, chain)]
    if guard_lines is None and not references:
        return
    if guard_lines is None:
        raise ContractError("INPUT references an absent BFF OAuth guard")
    validate_guard_rules(input_lines, guard_lines, contract, tail_ip)
    _delete_guard_jump(contract)
    _run(["iptables", "-F", chain], "BFF OAuth guard flush")
    _run(["iptables", "-X", chain], "BFF OAuth guard deletion")


def rule_present(contract: Contract, tail_ip: ipaddress.IPv4Address) -> bool:
    result = subprocess.run(
        _rule_command(contract, tail_ip),
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def validate_default_deny() -> None:
    config = validate_root_config(UFW_CONFIG).read_text(encoding="utf-8")
    enabled = {
        line.strip().upper()
        for line in config.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if "ENABLED=YES" not in enabled:
        raise ContractError("UFW is not enabled")
    _run(["iptables", "-S", "ufw-user-input"], "UFW input-chain inspection")
    policy = _run(["iptables", "-S", "INPUT"], "host input-policy inspection").stdout
    if "-P INPUT DROP" not in policy.splitlines():
        raise ContractError("host IPv4 input policy is not default-deny")


def apply_rule(contract: Contract, tail_ip: ipaddress.IPv4Address) -> None:
    if rule_present(contract, tail_ip):
        return
    _run(
        [
            "ufw", "allow", "in", "on", contract.bridge,
            "from", str(contract.bff_ip),
            "to", str(tail_ip),
            "port", str(contract.ha_port),
            "proto", "tcp",
            "comment", contract.profile.comment,
        ],
        "UFW BFF OAuth rule application",
    )
    if not rule_present(contract, tail_ip):
        raise ContractError("UFW did not materialize the exact BFF OAuth rule")


def remove_rule(contract: Contract, tail_ip: ipaddress.IPv4Address) -> None:
    if not rule_present(contract, tail_ip):
        return
    _run(
        [
            "ufw", "--force", "delete", "allow", "in", "on", contract.bridge,
            "from", str(contract.bff_ip),
            "to", str(tail_ip),
            "port", str(contract.ha_port),
            "proto", "tcp",
        ],
        "UFW BFF OAuth rule removal",
    )
    if rule_present(contract, tail_ip):
        raise ContractError("UFW retained the BFF OAuth rule after removal")


def probe_from_bff(contract: Contract | None = None) -> None:
    # GET intentionally carries no code, token, cookie, or identity. HA's
    # token endpoint must be reachable and reject that method with 405. The
    # canonical URL is non-secret and already validated by the contract.
    profile = contract.profile if contract is not None else BFF
    target = (
        "process.env.HOME_AGENT_HA_URL"
        if profile.published_port is not None
        else json.dumps(contract.ha_url)
    )
    program = (
        f"fetch({target}+'/auth/token',"
        "{method:'GET',redirect:'error',signal:AbortSignal.timeout(5000)})"
        ".then(r=>process.exit(r.status===405?0:2)).catch(()=>process.exit(3))"
    )
    _run(["docker", "exec", profile.container, "node", "-e", program], "BFF OAuth path probe")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("apply", "verify", "remove", "guard"))
    parser.add_argument("--env", required=True)
    parser.add_argument("--profile", choices=tuple(PROFILES), default=BFF.name)
    parser.add_argument(
        "--if-configured",
        action="store_true",
        help="exit successfully when the profile's HA URL is not provisioned",
    )
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("BFF OAuth firewall contract requires root", file=sys.stderr)
        return 77
    try:
        validate_trusted_execution()
        sanitize_process_environment()
        profile = PROFILES[args.profile]
        values = read_env(validate_root_config(args.env))
        if args.if_configured and not values.get(profile.url_key):
            # An unprovisioned profile has no bridge allow rule; UFW's
            # default-deny input policy already drops anything from it.
            print(f"{profile.name} OAuth firewall contract not configured")
            return 0
        contract = contract_from_env(values, profile)
        if args.action != "remove":
            validate_lifecycle_hook()
        if args.action == "guard":
            # UFW invokes this lifecycle action before Docker or Tailscale may
            # be online. The root-owned environment pins the already-reviewed
            # Tailscale IPv4, so the first-hop guard needs no network lookup.
            apply_guard(contract, contract.tail_ip)
            print("BFF OAuth firewall contract guard passed")
            return 0
        tail_ip = live_contract(contract)
        validate_default_deny()
        if args.action == "apply":
            apply_rule(contract, tail_ip)
            apply_guard(contract, tail_ip)
            probe_from_bff(contract)
        elif args.action == "verify":
            if not rule_present(contract, tail_ip):
                raise ContractError("exact BFF OAuth firewall rule is absent")
            validate_guard_live(contract, tail_ip)
            probe_from_bff(contract)
        else:
            remove_guard(contract, tail_ip)
            remove_rule(contract, tail_ip)
    except (ContractError, OSError) as exc:
        print(f"BFF OAuth firewall contract failed: {exc}", file=sys.stderr)
        return 1
    print(f"BFF OAuth firewall contract {args.action} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

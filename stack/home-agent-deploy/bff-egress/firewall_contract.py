#!/usr/bin/env python3
"""Reconcile the BFF's one permitted host-local HA OAuth path.

The browser reaches Home Assistant through Tailscale Serve.  The BFF must use
the same TLS endpoint for code exchange, refresh, whoami, and revocation, but a
default-deny host firewall also applies to packets sourced by Docker bridges.
This tool validates the complete live identity/network contract before adding
or accepting one exact UFW rule.  It never reads or prints OAuth material.
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
UFW_HOOK_SHA256 = "530be4a84d913d9cbe9dc1b50bb0f04220490cd7a9f7937197f3988adfa38de8"
UFW_CONFIG = Path("/etc/ufw/ufw.conf")
TRUSTED_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


class ContractError(RuntimeError):
    pass


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
    bind_address: ipaddress.IPv4Address


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


def contract_from_env(values: dict[str, str]) -> Contract:
    ha_url = values.get("HOME_AGENT_HA_URL", "")
    try:
        parsed = urlsplit(ha_url)
        port = parsed.port or 443
    except ValueError as exc:
        raise ContractError("HOME_AGENT_HA_URL is invalid") from exc
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
        raise ContractError("HOME_AGENT_HA_URL must be one canonical HTTPS root")

    try:
        subnet = ipaddress.ip_network(
            values.get("HOME_AGENT_BFF_EGRESS_SUBNET", DEFAULT_SUBNET), strict=True
        )
        gateway = ipaddress.ip_address(
            values.get("HOME_AGENT_BFF_EGRESS_GATEWAY", DEFAULT_GATEWAY)
        )
        bff_ip = ipaddress.ip_address(
            values.get("HOME_AGENT_BFF_EGRESS_IP", DEFAULT_BFF_IP)
        )
        tail_ip = ipaddress.ip_address(
            values.get("HOME_AGENT_HA_TAILSCALE_IPV4", "")
        )
        bind_address = ipaddress.ip_address(
            values.get("HOME_AGENT_BFF_BIND_ADDR", "127.0.0.1")
        )
    except ValueError as exc:
        raise ContractError("BFF egress address contract is invalid") from exc
    if not isinstance(subnet, ipaddress.IPv4Network) or not all(
        isinstance(value, ipaddress.IPv4Address)
        for value in (gateway, bff_ip, tail_ip, bind_address)
    ):
        raise ContractError("BFF egress contract must use IPv4")
    if gateway not in subnet or bff_ip not in subnet:
        raise ContractError("BFF egress gateway and address must be inside the subnet")
    if gateway in (subnet.network_address, subnet.broadcast_address):
        raise ContractError("BFF egress gateway is not a usable address")
    if bff_ip in (subnet.network_address, subnet.broadcast_address, gateway):
        raise ContractError("BFF egress address is not independently usable")
    if tail_ip not in ipaddress.ip_network("100.64.0.0/10"):
        raise ContractError("HA endpoint must use a pinned Tailscale IPv4 address")
    if not bind_address.is_loopback:
        raise ContractError("BFF host publication must remain loopback-only")

    bridge = values.get("HOME_AGENT_BFF_EGRESS_BRIDGE", DEFAULT_BRIDGE)
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
        value.get("Name") != NETWORK_NAME
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
    if member.get("Name") != CONTAINER_NAME:
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
    config = value.get("Config") or {}
    host = value.get("HostConfig") or {}
    labels = config.get("Labels") or {}
    if (
        value.get("Name") != f"/{CONTAINER_NAME}"
        or labels.get("com.docker.compose.project") != COMPOSE_PROJECT
        or labels.get("com.docker.compose.service") != COMPOSE_SERVICE
    ):
        raise ContractError("live BFF does not have the reviewed Compose identity")
    networks = ((value.get("NetworkSettings") or {}).get("Networks") or {})
    if set(networks) != {API_NETWORK_NAME, NETWORK_NAME}:
        raise ContractError("live BFF has an unreviewed network attachment")
    if networks[NETWORK_NAME].get("IPAddress") != str(contract.bff_ip):
        raise ContractError("live BFF source address differs from policy")
    if networks[NETWORK_NAME].get("GlobalIPv6Address") not in (None, ""):
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
    if set(bindings) != {"8097/tcp"} or len(bindings["8097/tcp"] or []) != 1:
        raise ContractError("live BFF host publication differs from policy")
    binding = bindings["8097/tcp"][0]
    if binding.get("HostIp") != str(contract.bind_address):
        raise ContractError("live BFF host publication is not the reviewed loopback")
    environment = _container_environment(value)
    if environment.get("HOME_AGENT_HA_URL") != contract.ha_url:
        raise ContractError("live BFF HA endpoint differs from policy")


def live_contract(contract: Contract) -> ipaddress.IPv4Address:
    network_payload = _json_command(
        ["docker", "network", "inspect", NETWORK_NAME], "Docker network inspection"
    )
    container_payload = _json_command(
        ["docker", "inspect", CONTAINER_NAME], "Docker BFF inspection"
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
) -> tuple[str, str, str, str]:
    """Return the canonical jump, return-flow allow, HA allow, and drop."""

    jump = f"-A INPUT -i {contract.bridge} -j {GUARD_CHAIN}"
    established = (
        f"-A {GUARD_CHAIN} -s {contract.bff_ip}/32 "
        f"-d {contract.gateway}/32 -p tcp -m tcp --sport 8097 -m conntrack "
        "--ctstate RELATED,ESTABLISHED -j ACCEPT"
    )
    allow = (
        f"-A {GUARD_CHAIN} -s {contract.bff_ip}/32 -d {tail_ip}/32 "
        f"-p tcp -m tcp --dport {contract.ha_port} -j ACCEPT"
    )
    deny = f"-A {GUARD_CHAIN} -j DROP"
    return jump, established, allow, deny


def _targets_guard(line: str) -> bool:
    tokens = line.split()
    return any(
        tokens[index] == "-j" and tokens[index + 1] == GUARD_CHAIN
        for index in range(len(tokens) - 1)
    )


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

    jump, established, allow, deny = guard_rule_spec(contract, tail_ip)
    input_rules = [line for line in input_lines if line.startswith("-A INPUT ")]
    references = [line for line in input_rules if _targets_guard(line)]
    if not input_rules or input_rules[0] != jump or references != [jump]:
        raise ContractError("BFF OAuth guard is not the sole first INPUT jump")
    if guard_lines != [f"-N {GUARD_CHAIN}", established, allow, deny]:
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
    guard_lines = _iptables_chain_lines(GUARD_CHAIN)
    if guard_lines is None:
        raise ContractError("BFF OAuth first-hop guard is absent")
    validate_guard_rules(_input_lines(), guard_lines, contract, tail_ip)


def _delete_guard_jump(contract: Contract) -> None:
    _run(
        ["iptables", "-D", "INPUT", "-i", contract.bridge, "-j", GUARD_CHAIN],
        "BFF OAuth guard-jump removal",
    )


def apply_guard(contract: Contract, tail_ip: ipaddress.IPv4Address) -> None:
    jump, established, allow, deny = guard_rule_spec(contract, tail_ip)
    expected_guard = [f"-N {GUARD_CHAIN}", established, allow, deny]
    legacy_guard = [f"-N {GUARD_CHAIN}", allow, deny]
    input_lines = _input_lines()
    references = [line for line in input_lines if _targets_guard(line)]
    guard_lines = _iptables_chain_lines(GUARD_CHAIN)
    created = False

    if guard_lines is None:
        if references:
            raise ContractError("INPUT references an absent BFF OAuth guard")
        try:
            _run(["iptables", "-N", GUARD_CHAIN], "BFF OAuth guard creation")
            created = True
            _run(
                [
                    "iptables", "-A", GUARD_CHAIN,
                    "-s", f"{contract.bff_ip}/32",
                    "-d", f"{contract.gateway}/32",
                    "-p", "tcp", "--sport", "8097",
                    "-m", "conntrack",
                    "--ctstate", "RELATED,ESTABLISHED",
                    "-j", "ACCEPT",
                ],
                "BFF loopback established-return guard allow",
            )
            _run(
                [
                    "iptables", "-A", GUARD_CHAIN,
                    "-s", f"{contract.bff_ip}/32",
                    "-d", f"{tail_ip}/32",
                    "-p", "tcp", "--dport", str(contract.ha_port),
                    "-j", "ACCEPT",
                ],
                "BFF OAuth exact guard allow",
            )
            _run(
                ["iptables", "-A", GUARD_CHAIN, "-j", "DROP"],
                "BFF OAuth terminal guard drop",
            )
            guard_lines = expected_guard
        except ContractError:
            if created:
                subprocess.run(
                    ["iptables", "-F", GUARD_CHAIN],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                subprocess.run(
                    ["iptables", "-X", GUARD_CHAIN],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            raise
    elif guard_lines == legacy_guard:
        # Upgrade only the exact previously reviewed guard in place. Inserting
        # the return-flow rule before the existing HA allow/drop pair creates
        # no fail-open interval and leaves all new BFF-originated flows denied.
        _run(
            [
                "iptables", "-I", GUARD_CHAIN, "1",
                "-s", f"{contract.bff_ip}/32",
                "-d", f"{contract.gateway}/32",
                "-p", "tcp", "--sport", "8097",
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
    references = [line for line in input_lines if _targets_guard(line)]
    if not references:
        _run(
            ["iptables", "-I", "INPUT", "1", "-i", contract.bridge, "-j", GUARD_CHAIN],
            "BFF OAuth first-hop guard insertion",
        )
    elif references == [jump]:
        input_rules = [line for line in input_lines if line.startswith("-A INPUT ")]
        if not input_rules or input_rules[0] != jump:
            _delete_guard_jump(contract)
            _run(
                ["iptables", "-I", "INPUT", "1", "-i", contract.bridge, "-j", GUARD_CHAIN],
                "BFF OAuth first-hop guard restoration",
            )
    else:
        raise ContractError("INPUT contains an unreviewed BFF OAuth guard reference")
    validate_guard_live(contract, tail_ip)


def remove_guard(contract: Contract, tail_ip: ipaddress.IPv4Address) -> None:
    guard_lines = _iptables_chain_lines(GUARD_CHAIN)
    input_lines = _input_lines()
    references = [line for line in input_lines if _targets_guard(line)]
    if guard_lines is None and not references:
        return
    if guard_lines is None:
        raise ContractError("INPUT references an absent BFF OAuth guard")
    validate_guard_rules(input_lines, guard_lines, contract, tail_ip)
    _delete_guard_jump(contract)
    _run(["iptables", "-F", GUARD_CHAIN], "BFF OAuth guard flush")
    _run(["iptables", "-X", GUARD_CHAIN], "BFF OAuth guard deletion")


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
            "comment", RULE_COMMENT,
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


def probe_from_bff() -> None:
    # GET intentionally carries no code, token, cookie, or identity. HA's
    # token endpoint must be reachable and reject that method with 405.
    program = (
        "fetch(process.env.HOME_AGENT_HA_URL+'/auth/token',"
        "{method:'GET',redirect:'error',signal:AbortSignal.timeout(5000)})"
        ".then(r=>process.exit(r.status===405?0:2)).catch(()=>process.exit(3))"
    )
    _run(["docker", "exec", CONTAINER_NAME, "node", "-e", program], "BFF OAuth path probe")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("apply", "verify", "remove", "guard"))
    parser.add_argument("--env", required=True)
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("BFF OAuth firewall contract requires root", file=sys.stderr)
        return 77
    try:
        validate_trusted_execution()
        sanitize_process_environment()
        contract = contract_from_env(read_env(validate_root_config(args.env)))
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
            probe_from_bff()
        elif args.action == "verify":
            if not rule_present(contract, tail_ip):
                raise ContractError("exact BFF OAuth firewall rule is absent")
            validate_guard_live(contract, tail_ip)
            probe_from_bff()
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

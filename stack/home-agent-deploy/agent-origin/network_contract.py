#!/usr/bin/env python3
"""Fail-closed validation for the Agent origin's internal Docker address."""

from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_SUBNET = "172.23.0.0/16"
DEFAULT_DYNAMIC_RANGE = "172.23.128.0/17"
DEFAULT_GATEWAY = "172.23.0.1"
DEFAULT_NETWORK = "home-agent_api-net"
ORIGIN_PORT = 8096
EXPECTED_ORIGIN_CONTAINER = "home-agent-origin-origin-1"
EXPECTED_ORIGIN_IMAGE = "engineered-lighting/home-agent-origin:local"
EXPECTED_BFF_CONTAINER = "home-agent-bff-1"


class ContractError(ValueError):
    pass


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


def expected_serve_target(origin_ip: ipaddress.IPv4Address) -> str:
    return f"http://{origin_ip}:{ORIGIN_PORT}"


def validate_serve_target(value: str, origin_ip: ipaddress.IPv4Address) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname != str(origin_ip)
        or parsed.port != ORIGIN_PORT
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or value != expected_serve_target(origin_ip)
    ):
        raise ContractError("Serve target does not exactly match the reviewed origin address")


def validate_oauth_contract(base_env: dict[str, str], origin_env: dict[str, str]) -> str:
    public_origin = origin_env.get("HOME_AGENT_WEB_PUBLIC_ORIGIN", "")
    try:
        parsed = urlsplit(public_origin)
        port = parsed.port
    except ValueError as exc:
        raise ContractError("Agent public origin is invalid") from exc
    canonical_host = parsed.hostname or ""
    canonical = f"https://{canonical_host}"
    if port is not None and port != 443:
        canonical += f":{port}"
    if (
        parsed.scheme != "https"
        or not canonical_host
        or canonical_host != canonical_host.lower()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or public_origin != canonical
    ):
        raise ContractError("Agent public origin must be one exact canonical HTTPS origin")
    if base_env.get("HOME_AGENT_ALLOWED_ORIGINS") != public_origin:
        raise ContractError("BFF allowed origin does not exactly match the Agent origin")
    if base_env.get("HOME_AGENT_OAUTH_CLIENT_ID") != public_origin:
        raise ContractError("BFF OAuth client ID does not exactly match the Agent origin")
    if base_env.get("HOME_AGENT_OAUTH_REDIRECT_URI") != (
        f"{public_origin}/api/agent/auth/callback"
    ):
        raise ContractError("BFF OAuth redirect does not exactly match the Agent callback")
    return public_origin


def validate_address_contract(
    subnet_value: str,
    dynamic_value: str,
    gateway_value: str,
    origin_value: str,
    serve_target: str,
) -> tuple[ipaddress.IPv4Network, ipaddress.IPv4Network, ipaddress.IPv4Address, ipaddress.IPv4Address]:
    try:
        subnet = ipaddress.ip_network(subnet_value, strict=True)
        dynamic = ipaddress.ip_network(dynamic_value, strict=True)
        gateway = ipaddress.ip_address(gateway_value)
        origin = ipaddress.ip_address(origin_value)
    except ValueError as exc:
        raise ContractError("network contract contains invalid IP notation") from exc
    if not all(isinstance(value, (ipaddress.IPv4Network, ipaddress.IPv4Address)) for value in (
        subnet, dynamic, gateway, origin
    )):
        raise ContractError("network contract must use IPv4")
    if not dynamic.subnet_of(subnet):
        raise ContractError("dynamic range must be contained by the API subnet")
    if gateway not in subnet or gateway in (subnet.network_address, subnet.broadcast_address):
        raise ContractError("gateway must be a usable address inside the API subnet")
    if gateway in dynamic:
        raise ContractError("gateway must be outside the dynamic allocation range")
    if origin not in subnet or origin in (subnet.network_address, subnet.broadcast_address):
        raise ContractError("origin address must be usable inside the API subnet")
    if origin == gateway or origin in dynamic:
        raise ContractError("origin address must be reserved outside gateway and dynamic allocations")
    validate_serve_target(serve_target, origin)
    return subnet, dynamic, gateway, origin


def validate_network_inspect(
    inspect: dict,
    *,
    subnet: ipaddress.IPv4Network,
    dynamic: ipaddress.IPv4Network,
    gateway: ipaddress.IPv4Address,
    origin: ipaddress.IPv4Address,
    require_origin: bool = False,
) -> None:
    if inspect.get("Driver") != "bridge" or inspect.get("Scope") != "local":
        raise ContractError("API network must use the local bridge driver")
    if inspect.get("Internal") is not True:
        raise ContractError("API network is not internal")
    if inspect.get("EnableIPv6") is not False:
        raise ContractError("API network must have IPv6 explicitly disabled")
    configs = inspect.get("IPAM", {}).get("Config", [])
    if len(configs) != 1:
        raise ContractError("API network must have exactly one IPAM configuration")
    config = configs[0]
    if config.get("Subnet") != str(subnet):
        raise ContractError("live API subnet does not match the pinned contract")
    if config.get("IPRange") != str(dynamic):
        raise ContractError("live dynamic range does not match the pinned contract")
    if config.get("Gateway") != str(gateway):
        raise ContractError("live gateway does not match the pinned contract")

    origin_members = []
    for member in inspect.get("Containers", {}).values():
        address = str(member.get("IPv4Address", "")).split("/", 1)[0]
        if address != str(origin):
            continue
        name = str(member.get("Name", ""))
        if name != EXPECTED_ORIGIN_CONTAINER:
            raise ContractError("reviewed origin address is occupied by another container")
        origin_members.append(name)
    if len(origin_members) > 1:
        raise ContractError("reviewed origin address has multiple container occupants")
    if require_origin and len(origin_members) != 1:
        raise ContractError("Agent origin is not attached at its reviewed address")


def inspect_network(name: str) -> dict:
    if not name or any(character.isspace() for character in name):
        raise ContractError("invalid Docker network name")
    result = subprocess.run(
        ["docker", "network", "inspect", name],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise ContractError("Docker API network inspection failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ContractError("Docker returned malformed network inspection") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise ContractError("Docker returned an unexpected network inspection shape")
    return payload[0]


def inspect_container(name: str) -> dict:
    if not name or any(character.isspace() for character in name):
        raise ContractError("invalid Docker container name")
    result = subprocess.run(
        ["docker", "inspect", name],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise ContractError("Docker container inspection failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ContractError("Docker returned malformed container inspection") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise ContractError("Docker returned an unexpected container inspection shape")
    return payload[0]


def inspect_all_networks() -> list[dict]:
    listing = subprocess.run(
        ["docker", "network", "ls", "-q"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    identifiers = [value for value in listing.stdout.split() if value]
    if listing.returncode != 0 or not identifiers:
        raise ContractError("Docker network inventory failed")
    result = subprocess.run(
        ["docker", "network", "inspect", *identifiers],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise ContractError("Docker network inventory inspection failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ContractError("Docker returned malformed network inventory") from exc
    if not isinstance(payload, list) or not all(isinstance(value, dict) for value in payload):
        raise ContractError("Docker returned an unexpected network inventory shape")
    return payload


def inspect_host_routes() -> list[dict]:
    result = subprocess.run(
        ["ip", "-j", "-4", "route", "show", "table", "all"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise ContractError("host route inventory failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ContractError("host returned malformed route inventory") from exc
    if not isinstance(payload, list) or not all(isinstance(value, dict) for value in payload):
        raise ContractError("host returned an unexpected route inventory shape")
    return payload


def inspect_tailscale_routes() -> list[str]:
    result = subprocess.run(
        ["tailscale", "status", "--json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise ContractError("Tailscale route inventory failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ContractError("Tailscale returned malformed route inventory") from exc
    routes: list[str] = []

    def collect(value) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"PrimaryRoutes", "AdvertisedRoutes"} and isinstance(child, list):
                    routes.extend(str(route) for route in child)
                else:
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(payload)
    return routes


def ipv4_network(value: str) -> ipaddress.IPv4Network | None:
    try:
        parsed = ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None
    return parsed if isinstance(parsed, ipaddress.IPv4Network) else None


def validate_candidate_collisions(
    subnet: ipaddress.IPv4Network,
    gateway: ipaddress.IPv4Address,
    network_name: str,
    *,
    docker_networks: list[dict],
    host_routes: list[dict],
    tailscale_routes: list[str],
) -> None:
    expected_network = next(
        (network for network in docker_networks if network.get("Name") == network_name),
        None,
    )
    expected_bridge = None
    if expected_network:
        if (
            expected_network.get("Driver") != "bridge"
            or expected_network.get("Scope") != "local"
            or expected_network.get("Internal") is not True
            or expected_network.get("EnableIPv6") is not False
        ):
            raise ContractError("existing API network boundary differs from the reviewed posture")
        configs = expected_network.get("IPAM", {}).get("Config", []) or []
        if (
            len(configs) != 1
            or configs[0].get("Subnet") != str(subnet)
            or configs[0].get("Gateway") != str(gateway)
        ):
            raise ContractError("existing API network subnet or gateway differs from the candidate")
        identifier = str(expected_network.get("Id", ""))
        expected_bridge = (
            expected_network.get("Options", {}).get("com.docker.network.bridge.name")
            or (f"br-{identifier[:12]}" if len(identifier) >= 12 else None)
        )

    for network in docker_networks:
        for config in network.get("IPAM", {}).get("Config", []) or []:
            allocated = ipv4_network(str(config.get("Subnet", "")))
            if not allocated or not allocated.overlaps(subnet):
                continue
            if network.get("Name") != network_name or allocated != subnet:
                raise ContractError("candidate API subnet overlaps another Docker network")

    for route in host_routes:
        destination = str(route.get("dst", ""))
        if destination in ("", "default", "0.0.0.0/0"):
            continue
        allocated = ipv4_network(destination)
        if not allocated or not allocated.overlaps(subnet):
            continue
        if not (
            expected_bridge
            and route.get("dev") == expected_bridge
            and allocated.subnet_of(subnet)
        ):
            raise ContractError("candidate API subnet overlaps a host route")

    for value in tailscale_routes:
        allocated = ipv4_network(value)
        if allocated and allocated.prefixlen and allocated.overlaps(subnet):
            raise ContractError("candidate API subnet overlaps a Tailscale route")


def container_environment(inspect: dict) -> dict[str, str]:
    values: dict[str, str] = {}
    for entry in inspect.get("Config", {}).get("Env", []) or []:
        if not isinstance(entry, str) or "=" not in entry:
            raise ContractError("container environment has an invalid shape")
        key, value = entry.split("=", 1)
        if not key or key in values:
            raise ContractError("container environment has duplicate keys")
        values[key] = value
    return values


def validate_bff_inspect(inspect: dict, public_origin: str) -> None:
    labels = inspect.get("Config", {}).get("Labels", {}) or {}
    if (
        inspect.get("Name") != f"/{EXPECTED_BFF_CONTAINER}"
        or labels.get("com.docker.compose.project") != "home-agent"
        or labels.get("com.docker.compose.service") != "bff"
    ):
        raise ContractError("running BFF does not have the expected Compose identity")
    environment = container_environment(inspect)
    expected = {
        "HOME_AGENT_ALLOWED_ORIGINS": public_origin,
        "HOME_AGENT_OAUTH_CLIENT_ID": public_origin,
        "HOME_AGENT_OAUTH_REDIRECT_URI": f"{public_origin}/api/agent/auth/callback",
    }
    if any(environment.get(key) != value for key, value in expected.items()):
        raise ContractError("running BFF OAuth boundary differs from reviewed configuration")


def validate_origin_inspect(
    inspect: dict,
    *,
    network_name: str,
    origin: ipaddress.IPv4Address,
    public_origin: str,
) -> None:
    config = inspect.get("Config", {})
    host = inspect.get("HostConfig", {})
    network_settings = inspect.get("NetworkSettings", {})
    labels = config.get("Labels", {}) or {}
    if (
        inspect.get("Name") != f"/{EXPECTED_ORIGIN_CONTAINER}"
        or labels.get("com.docker.compose.project") != "home-agent-origin"
        or labels.get("com.docker.compose.service") != "origin"
    ):
        raise ContractError("running Agent origin does not have the expected Compose identity")
    networks = network_settings.get("Networks", {}) or {}
    if set(networks) != {network_name}:
        raise ContractError("Agent origin must have exactly one reviewed network attachment")
    attachment = networks[network_name]
    if str(attachment.get("IPAddress", "")) != str(origin):
        raise ContractError("Agent origin container address differs from the reviewed address")
    if attachment.get("GlobalIPv6Address") not in (None, ""):
        raise ContractError("Agent origin has an unreviewed IPv6 address")
    if host.get("NetworkMode") != network_name:
        raise ContractError("Agent origin primary network mode is not the reviewed API network")
    if host.get("PortBindings") not in (None, {}):
        raise ContractError("Agent origin has a Docker host port binding")
    published = network_settings.get("Ports", {}) or {}
    if any(value not in (None, []) for value in published.values()):
        raise ContractError("Agent origin has a published port")
    if config.get("Image") != EXPECTED_ORIGIN_IMAGE:
        raise ContractError("Agent origin is running an unexpected image reference")
    if config.get("User") != "1000:1000":
        raise ContractError("Agent origin is not running as the reviewed unprivileged user")
    if host.get("ReadonlyRootfs") is not True:
        raise ContractError("Agent origin root filesystem is not read-only")
    if host.get("Privileged") is not False:
        raise ContractError("Agent origin is privileged")
    if set(host.get("CapDrop") or []) != {"ALL"} or host.get("CapAdd") not in (None, []):
        raise ContractError("Agent origin Linux capabilities differ from the reviewed posture")
    if "no-new-privileges:true" not in (host.get("SecurityOpt") or []):
        raise ContractError("Agent origin is missing no-new-privileges")
    if inspect.get("Mounts") not in (None, []):
        raise ContractError("Agent origin has an unexpected runtime mount")
    if host.get("PidsLimit") != 64 or host.get("Memory") != 128 * 1024 * 1024:
        raise ContractError("Agent origin resource limits differ from the reviewed posture")
    state = inspect.get("State", {})
    if state.get("Running") is not True or state.get("Health", {}).get("Status") != "healthy":
        raise ContractError("Agent origin is not running and healthy")
    environment = container_environment(inspect)
    if environment.get("HOME_AGENT_WEB_PUBLIC_ORIGIN") != public_origin:
        raise ContractError("running Agent origin public origin differs from reviewed configuration")


def configuration(base_env: dict[str, str], origin_env: dict[str, str]) -> dict[str, str]:
    return {
        "network": origin_env.get("HOME_AGENT_API_NETWORK", DEFAULT_NETWORK),
        "subnet": base_env.get("HOME_AGENT_API_SUBNET", DEFAULT_SUBNET),
        "dynamic": base_env.get("HOME_AGENT_API_DYNAMIC_RANGE", DEFAULT_DYNAMIC_RANGE),
        "gateway": base_env.get("HOME_AGENT_API_GATEWAY", DEFAULT_GATEWAY),
        "origin": origin_env.get("HOME_AGENT_WEB_INTERNAL_IP", ""),
        "serve_target": origin_env.get("HOME_AGENT_WEB_SERVE_TARGET", ""),
        "public_origin": origin_env.get("HOME_AGENT_WEB_PUBLIC_ORIGIN", ""),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-env", required=True)
    parser.add_argument("--origin-env", required=True)
    parser.add_argument("--require-origin", action="store_true")
    parser.add_argument("--candidate-only", action="store_true")
    parser.add_argument("--print-serve-target", action="store_true")
    args = parser.parse_args(argv)
    if args.candidate_only and args.require_origin:
        parser.error("--candidate-only and --require-origin are mutually exclusive")
    try:
        base_env = read_env(args.base_env)
        origin_env = read_env(args.origin_env)
        values = configuration(base_env, origin_env)
        public_origin = validate_oauth_contract(base_env, origin_env)
        subnet, dynamic, gateway, origin = validate_address_contract(
            values["subnet"],
            values["dynamic"],
            values["gateway"],
            values["origin"],
            values["serve_target"],
        )
        networks = inspect_all_networks()
        validate_candidate_collisions(
            subnet,
            gateway,
            values["network"],
            docker_networks=networks,
            host_routes=inspect_host_routes(),
            tailscale_routes=inspect_tailscale_routes(),
        )
        validate_bff_inspect(inspect_container(EXPECTED_BFF_CONTAINER), public_origin)
        if args.candidate_only:
            if args.print_serve_target:
                print(expected_serve_target(origin))
            else:
                print("home-agent-origin candidate network preflight passed")
            return 0
        validate_network_inspect(
            inspect_network(values["network"]),
            subnet=subnet,
            dynamic=dynamic,
            gateway=gateway,
            origin=origin,
            require_origin=args.require_origin,
        )
        if args.require_origin:
            validate_origin_inspect(
                inspect_container(EXPECTED_ORIGIN_CONTAINER),
                network_name=values["network"],
                origin=origin,
                public_origin=public_origin,
            )
    except (ContractError, OSError, subprocess.SubprocessError) as exc:
        print(f"home-agent-origin network preflight failed: {exc}", file=sys.stderr)
        return 1
    if args.print_serve_target:
        print(expected_serve_target(origin))
    else:
        print("home-agent-origin network preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

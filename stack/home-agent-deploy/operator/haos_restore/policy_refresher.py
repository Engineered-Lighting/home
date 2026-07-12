#!/usr/bin/env python3
"""Atomically refresh short-lived nft sets for reviewed HAOS endpoints."""

from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path
import re
import subprocess
import sys
import time

from policy_compiler import PolicyError, load_policy, policy_sha256


SET_NAMES = ("artifact_v4", "ntp_v4")
PROHIBITED = tuple(
    ipaddress.ip_network(value)
    for value in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)


def validate_resolver(expected: str) -> None:
    expected_ip = ipaddress.ip_address(expected)
    if expected_ip.version != 4 or not expected_ip.is_global:
        raise PolicyError("upstream resolver is not global IPv4")


def resolve_exact(domain: str, upstream: str) -> set[ipaddress.IPv4Address]:
    completed = subprocess.run(
        [
            "dig",
            "+short",
            "+time=5",
            "+tries=2",
            "+retry=0",
            "@" + upstream,
            domain + ".",
            "A",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if completed.returncode != 0:
        raise PolicyError(f"reviewed endpoint did not resolve: {domain}")
    addresses: set[ipaddress.IPv4Address] = set()
    for raw in completed.stdout.splitlines():
        field = raw.split(maxsplit=1)[0]
        try:
            address = ipaddress.ip_address(field)
        except ValueError:
            continue
        if not isinstance(address, ipaddress.IPv4Address):
            continue
        if not address.is_global or any(address in network for network in PROHIBITED):
            raise PolicyError(f"reviewed endpoint resolved to prohibited space: {domain}")
        addresses.add(address)
    if not addresses:
        raise PolicyError(f"reviewed endpoint has no acceptable IPv4 address: {domain}")
    return addresses


def render_transaction(
    policy: Path, table: str, timeout_seconds: int, upstream: str
) -> str:
    grouped: dict[str, set[ipaddress.IPv4Address]] = {name: set() for name in SET_NAMES}
    resolved: dict[str, set[ipaddress.IPv4Address]] = {}
    for entry in load_policy(policy):
        if entry.domain not in resolved:
            resolved[entry.domain] = resolve_exact(entry.domain, upstream)
        grouped[entry.nft_set].update(resolved[entry.domain])

    if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
        raise PolicyError("unsafe nft table name")
    lines: list[str] = []
    for set_name in SET_NAMES:
        lines.append(f"flush set inet {table} {set_name}")
        elements = ", ".join(
            f"{address} timeout {timeout_seconds}s" for address in sorted(grouped[set_name])
        )
        if elements:
            lines.append(f"add element inet {table} {set_name} {{ {elements} }}")
    return "\n".join(lines) + "\n"


def nft_command(netns_pid: int | None, netns_marker: str | None) -> list[str]:
    if netns_pid is None:
        if netns_marker is not None:
            raise PolicyError("namespace marker requires a namespace PID")
        return ["nft", "-f", "-"]
    if netns_pid < 2 or not netns_marker or not re.fullmatch(r"[a-z0-9-]{1,63}", netns_marker):
        raise PolicyError("invalid namespace target")
    try:
        command_line = Path(f"/proc/{netns_pid}/cmdline").read_bytes()
    except OSError as exc:
        raise PolicyError("namespace target is unavailable") from exc
    if netns_marker.encode("ascii") not in command_line.split(b"\0"):
        raise PolicyError("namespace target identity changed")
    return [
        "nsenter",
        "--target",
        str(netns_pid),
        "--user",
        "--mount",
        "--net",
        "--setuid",
        "0",
        "--setgid",
        "0",
        "--",
        "nft",
        "-f",
        "-",
    ]


def refresh(args: argparse.Namespace) -> None:
    if policy_sha256(args.policy) != args.expected_sha256:
        raise PolicyError("policy digest changed")
    validate_resolver(args.upstream)
    transaction = render_transaction(
        args.policy, args.table, args.timeout_seconds, args.upstream
    )
    subprocess.run(
        nft_command(
            getattr(args, "netns_pid", None), getattr(args, "netns_marker", None)
        ),
        input=transaction,
        text=True,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def clear_sets(
    table: str, netns_pid: int | None = None, netns_marker: str | None = None
) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
        return
    transaction = "".join(
        f"flush set inet {table} {set_name}\n" for set_name in SET_NAMES
    )
    subprocess.run(
        nft_command(netns_pid, netns_marker),
        input=transaction,
        text=True,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--table", default="haos_restore")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--interval-seconds", type=int, default=120)
    parser.add_argument("--netns-pid", type=int)
    parser.add_argument("--netns-marker")
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{64}", args.expected_sha256):
        print("policy refresh: invalid expected digest", file=sys.stderr)
        return 78
    if not 60 <= args.timeout_seconds <= 600:
        print("policy refresh: timeout must be 60..600 seconds", file=sys.stderr)
        return 78
    if not 30 <= args.interval_seconds < args.timeout_seconds:
        print("policy refresh: interval must be shorter than timeout", file=sys.stderr)
        return 78
    try:
        while True:
            refresh(args)
            if args.once:
                return 0
            time.sleep(args.interval_seconds)
    except (OSError, PolicyError, subprocess.SubprocessError) as exc:
        # A failed refresh narrows immediately to an empty allow-set. It never
        # leaves stale destinations usable until their normal timeout.
        try:
            clear_sets(args.table, args.netns_pid, args.netns_marker)
        except (OSError, PolicyError, subprocess.SubprocessError):
            print("policy refresh: could not clear endpoint sets", file=sys.stderr)
            return 78
        print(f"policy refresh: {exc}", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())

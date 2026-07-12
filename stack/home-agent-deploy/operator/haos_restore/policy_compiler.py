#!/usr/bin/env python3
"""Validate and compile the HAOS restore drill's exact egress policy.

The compiler deliberately accepts only exact lowercase DNS names and the two
protocol/port pairs required by the drill.  It never accepts wildcards, CIDRs,
literal IP addresses, URL paths, or arbitrary ports.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import ipaddress
from pathlib import Path
import re
import sys


DOMAIN_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
PURPOSE_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
ALLOWED_FLOWS = {("tcp", 443): "artifact_v4", ("udp", 123): "ntp_v4"}
PROHIBITED_SUFFIXES = (".local", ".internal", ".home", ".lan")


class PolicyError(ValueError):
    """Raised when a reviewed policy violates the closed grammar."""


@dataclass(frozen=True, order=True)
class Entry:
    domain: str
    protocol: str
    port: int
    purpose: str

    @property
    def nft_set(self) -> str:
        return ALLOWED_FLOWS[(self.protocol, self.port)]


def load_policy(path: Path) -> list[Entry]:
    if not path.is_file() or path.is_symlink():
        raise PolicyError("policy must be a regular, non-symlink file")

    entries: list[Entry] = []
    seen: set[tuple[str, str, int]] = set()
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 4 or any(field != field.strip() for field in fields):
            raise PolicyError(f"line {number}: expected four tab-separated fields")
        domain, protocol, port_text, purpose = fields
        if domain != domain.lower() or not DOMAIN_RE.fullmatch(domain):
            raise PolicyError(f"line {number}: domain is not an exact lowercase FQDN")
        if "*" in domain or domain.endswith(PROHIBITED_SUFFIXES):
            raise PolicyError(f"line {number}: prohibited domain")
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            pass
        else:
            raise PolicyError(f"line {number}: literal IP addresses are prohibited")
        if not port_text.isascii() or not port_text.isdecimal():
            raise PolicyError(f"line {number}: invalid port")
        port = int(port_text)
        if (protocol, port) not in ALLOWED_FLOWS:
            raise PolicyError(f"line {number}: flow is not tcp/443 or udp/123")
        if not PURPOSE_RE.fullmatch(purpose):
            raise PolicyError(f"line {number}: invalid purpose code")
        key = (domain, protocol, port)
        if key in seen:
            raise PolicyError(f"line {number}: duplicate flow")
        seen.add(key)
        entries.append(Entry(domain, protocol, port, purpose))

    if not entries:
        raise PolicyError("policy contains no reviewed entries")
    return sorted(entries)


def policy_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_dnsmasq(
    entries: list[Entry],
    *,
    interface: str,
    gateway: str,
    guest: str,
    upstream: str,
    lease_file: str,
) -> str:
    for value, label in (
        (interface, "interface"),
        (lease_file, "lease file"),
    ):
        if not re.fullmatch(r"[A-Za-z0-9._:/-]+", value):
            raise PolicyError(f"unsafe {label}")
    for value, label in ((gateway, "gateway"), (guest, "guest"), (upstream, "upstream")):
        try:
            parsed = ipaddress.ip_address(value)
        except ValueError as exc:
            raise PolicyError(f"invalid {label} address") from exc
        if parsed.version != 4:
            raise PolicyError(f"{label} must be IPv4")

    lines = [
        # DNS is served by exact_dns_proxy.py. dnsmasq provides DHCP only.
        "port=0",
        f"interface={interface}",
        "bind-interfaces",
        "no-resolv",
        "no-hosts",
        "domain-needed",
        "bogus-priv",
        "stop-dns-rebind",
        "dhcp-authoritative",
        f"dhcp-range={guest},{guest},255.255.255.0,1h",
        f"dhcp-option=option:router,{gateway}",
        f"dhcp-option=option:dns-server,{gateway}",
        f"dhcp-leasefile={lease_file}",
    ]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("policy", type=Path)
    validate.add_argument("--expected-sha256", required=True)

    digest = subparsers.add_parser("digest")
    digest.add_argument("policy", type=Path)

    dnsmasq = subparsers.add_parser("dnsmasq")
    dnsmasq.add_argument("policy", type=Path)
    dnsmasq.add_argument("--expected-sha256", required=True)
    dnsmasq.add_argument("--interface", required=True)
    dnsmasq.add_argument("--gateway", required=True)
    dnsmasq.add_argument("--guest", required=True)
    dnsmasq.add_argument("--upstream", required=True)
    dnsmasq.add_argument("--lease-file", required=True)
    return parser


def require_digest(path: Path, expected: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise PolicyError("expected digest is not lowercase SHA-256")
    actual = policy_sha256(path)
    if actual != expected:
        raise PolicyError(f"policy digest mismatch: expected {expected}, got {actual}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "digest":
            load_policy(args.policy)
            print(policy_sha256(args.policy))
            return 0
        require_digest(args.policy, args.expected_sha256)
        entries = load_policy(args.policy)
        if args.command == "validate":
            return 0
        if args.command == "dnsmasq":
            sys.stdout.write(
                render_dnsmasq(
                    entries,
                    interface=args.interface,
                    gateway=args.gateway,
                    guest=args.guest,
                    upstream=args.upstream,
                    lease_file=args.lease_file,
                )
            )
            return 0
    except (OSError, PolicyError) as exc:
        print(f"egress policy: {exc}", file=sys.stderr)
        return 78
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())

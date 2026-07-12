#!/usr/bin/env python3
"""Minimal exact-QNAME DNS proxy for the isolated HAOS restore namespace."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import re
import secrets
import socket
import socketserver
import struct
import sys
import threading

from policy_compiler import PolicyError, load_policy, policy_sha256


MAX_MESSAGE = 65535
TYPE_A = 1
TYPE_AAAA = 28
CLASS_IN = 1
RCODE_FORMERR = 1
RCODE_SERVFAIL = 2
RCODE_REFUSED = 5
LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


class DnsError(ValueError):
    """Raised for malformed or mismatched DNS wire data."""


@dataclass(frozen=True)
class Question:
    transaction_id: int
    flags: int
    name: str
    query_type: int
    query_class: int
    wire: bytes

    def canonical_query(self, transaction_id: int) -> bytes:
        # Preserve only recursion desired. QNAME bytes are already lower-case;
        # additional records, flags, padding, and client-chosen IDs never
        # become an upstream covert-data channel.
        return (
            struct.pack("!HHHHHH", transaction_id, self.flags & 0x0100, 1, 0, 0, 0)
            + self.wire
        )


def parse_question(packet: bytes) -> Question:
    if len(packet) < 12:
        raise DnsError("short DNS header")
    transaction_id, flags, qdcount, _, _, _ = struct.unpack("!HHHHHH", packet[:12])
    if flags & 0x8000 or ((flags >> 11) & 0xF) != 0 or qdcount != 1:
        raise DnsError("unsupported DNS message")

    offset = 12
    labels: list[str] = []
    wire_length = 1
    while True:
        if offset >= len(packet):
            raise DnsError("truncated QNAME")
        length = packet[offset]
        offset += 1
        if length == 0:
            break
        if length & 0xC0 or length > 63 or offset + length > len(packet):
            raise DnsError("compressed or invalid QNAME")
        raw = packet[offset : offset + length]
        offset += length
        try:
            label = raw.decode("ascii").lower()
        except UnicodeDecodeError as exc:
            raise DnsError("non-ASCII QNAME") from exc
        if not LABEL_RE.fullmatch(label):
            raise DnsError("invalid QNAME label")
        labels.append(label)
        wire_length += length + 1
        if wire_length > 255:
            raise DnsError("QNAME exceeds wire limit")
    if not labels or offset + 4 > len(packet):
        raise DnsError("missing DNS question fields")
    query_type, query_class = struct.unpack("!HH", packet[offset : offset + 4])
    question_end = offset + 4
    canonical_name = b"".join(
        bytes((len(label),)) + label.encode("ascii") for label in labels
    ) + b"\0"
    canonical_wire = canonical_name + struct.pack("!HH", query_type, query_class)
    return Question(
        transaction_id=transaction_id,
        flags=flags,
        name=".".join(labels),
        query_type=query_type,
        query_class=query_class,
        wire=canonical_wire,
    )


def error_response(packet: bytes, rcode: int) -> bytes:
    transaction_id = struct.unpack("!H", packet[:2].ljust(2, b"\0"))[0]
    try:
        question = parse_question(packet)
    except DnsError:
        flags = 0x8000 | rcode
        return struct.pack("!HHHHHH", transaction_id, flags, 0, 0, 0, 0)
    flags = 0x8000 | 0x0080 | (question.flags & 0x0100) | rcode
    return (
        struct.pack("!HHHHHH", question.transaction_id, flags, 1, 0, 0, 0)
        + question.wire
    )


def receive_exact(stream: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.recv(remaining)
        if not chunk:
            raise DnsError("truncated TCP DNS response")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class UpstreamForwarder:
    def __init__(self, upstream: str, timeout: float = 5.0) -> None:
        address = ipaddress.ip_address(upstream)
        if address.version != 4 or not address.is_global:
            raise PolicyError("DNS upstream must be global IPv4")
        self.upstream = upstream
        self.timeout = timeout

    def __call__(self, packet: bytes, *, tcp: bool) -> bytes:
        if tcp:
            with socket.create_connection((self.upstream, 53), self.timeout) as stream:
                stream.sendall(struct.pack("!H", len(packet)) + packet)
                size = struct.unpack("!H", receive_exact(stream, 2))[0]
                if not 12 <= size <= MAX_MESSAGE:
                    raise DnsError("invalid upstream TCP DNS length")
                response = receive_exact(stream, size)
        else:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
                datagram.settimeout(self.timeout)
                datagram.sendto(packet, (self.upstream, 53))
                response, peer = datagram.recvfrom(MAX_MESSAGE)
                if peer[0] != self.upstream or peer[1] != 53:
                    raise DnsError("unexpected DNS upstream peer")
        query = parse_question(packet)
        if len(response) < 12:
            raise DnsError("short upstream response")
        response_id, response_flags = struct.unpack("!HH", response[:4])
        if response_id != query.transaction_id or not (response_flags & 0x8000):
            raise DnsError("mismatched upstream DNS response")
        return response


class UnixForwarder:
    """Send sanitized DNS messages to the root-owned host-side forwarder."""

    def __init__(self, path: Path, timeout: float = 5.0) -> None:
        if not path.is_absolute() or ".." in path.parts:
            raise PolicyError("unsafe DNS upstream socket path")
        self.path = path
        self.timeout = timeout

    def __call__(self, packet: bytes, *, tcp: bool) -> bytes:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.settimeout(self.timeout)
            stream.connect(str(self.path))
            stream.sendall(bytes((int(tcp),)) + struct.pack("!H", len(packet)) + packet)
            size = struct.unpack("!H", receive_exact(stream, 2))[0]
            if not 12 <= size <= MAX_MESSAGE:
                raise DnsError("invalid local DNS response length")
            return receive_exact(stream, size)


class ExactResolver:
    def __init__(self, allowed_names: set[str], forwarder: object) -> None:
        self.allowed_names = frozenset(allowed_names)
        self.forwarder = forwarder

    def handle(self, packet: bytes, *, tcp: bool) -> bytes:
        try:
            question = parse_question(packet)
        except DnsError:
            return error_response(packet, RCODE_FORMERR)
        if (
            question.name not in self.allowed_names
            or question.query_class != CLASS_IN
            or question.query_type not in (TYPE_A, TYPE_AAAA)
        ):
            return error_response(packet, RCODE_REFUSED)
        try:
            upstream_id = secrets.randbits(16)
            response = self.forwarder(  # type: ignore[operator]
                question.canonical_query(upstream_id), tcp=tcp
            )
            return struct.pack("!H", question.transaction_id) + response[2:]
        except (DnsError, OSError):
            return error_response(packet, RCODE_SERVFAIL)


class ExactUDPServer(socketserver.UDPServer):
    allow_reuse_address = False


class ExactTCPServer(socketserver.TCPServer):
    allow_reuse_address = False


class ExactUnixServer(socketserver.UnixStreamServer):
    allow_reuse_address = False


class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        packet, channel = self.request
        response = self.server.resolver.handle(packet, tcp=False)  # type: ignore[attr-defined]
        channel.sendto(response, self.client_address)


class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(5)
        size = struct.unpack("!H", receive_exact(self.request, 2))[0]
        if not 12 <= size <= MAX_MESSAGE:
            return
        packet = receive_exact(self.request, size)
        response = self.server.resolver.handle(packet, tcp=True)  # type: ignore[attr-defined]
        self.request.sendall(struct.pack("!H", len(response)) + response)


class UnixHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(5)
        tcp = receive_exact(self.request, 1) == b"\1"
        size = struct.unpack("!H", receive_exact(self.request, 2))[0]
        if not 12 <= size <= MAX_MESSAGE:
            return
        packet = receive_exact(self.request, size)
        response = self.server.resolver.handle(packet, tcp=tcp)  # type: ignore[attr-defined]
        self.request.sendall(struct.pack("!H", len(response)) + response)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--upstream")
    parser.add_argument("--upstream-unix", type=Path)
    parser.add_argument("--serve-unix", type=Path)
    parser.add_argument("--bind")
    parser.add_argument("--port", type=int, default=53)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not re.fullmatch(r"[0-9a-f]{64}", args.expected_sha256):
            raise PolicyError("invalid expected policy digest")
        if policy_sha256(args.policy) != args.expected_sha256:
            raise PolicyError("policy digest changed")
        if args.port != 53:
            raise PolicyError("DNS proxy port must be 53")
        allowed_names = {entry.domain for entry in load_policy(args.policy)}
        if args.serve_unix is not None:
            if args.upstream is None or args.upstream_unix is not None or args.bind is not None:
                raise PolicyError("host DNS mode requires only upstream and serve-unix")
            if not args.serve_unix.is_absolute() or ".." in args.serve_unix.parts:
                raise PolicyError("unsafe DNS service socket path")
            if args.serve_unix.exists() or args.serve_unix.is_symlink():
                raise PolicyError("DNS service socket path already exists")
            resolver = ExactResolver(allowed_names, UpstreamForwarder(args.upstream))
            unix = ExactUnixServer(str(args.serve_unix), UnixHandler)
            unix.resolver = resolver  # type: ignore[attr-defined]
            try:
                os.chmod(args.serve_unix, 0o600)
                unix.serve_forever()
            finally:
                unix.server_close()
                args.serve_unix.unlink(missing_ok=True)
            return 0
        if args.upstream is not None or args.upstream_unix is None or args.bind is None:
            raise PolicyError("guest DNS mode requires bind and upstream-unix")
        bind = ipaddress.ip_address(args.bind)
        if bind.version != 4 or bind.is_unspecified or bind.is_loopback:
            raise PolicyError("DNS bind address is not the isolated guest gateway")
        resolver = ExactResolver(allowed_names, UnixForwarder(args.upstream_unix))
        udp = ExactUDPServer((args.bind, args.port), UDPHandler)
        tcp = ExactTCPServer((args.bind, args.port), TCPHandler)
        udp.resolver = resolver  # type: ignore[attr-defined]
        tcp.resolver = resolver  # type: ignore[attr-defined]
        thread = threading.Thread(target=tcp.serve_forever, daemon=True)
        thread.start()
        try:
            udp.serve_forever()
        finally:
            udp.server_close()
            tcp.shutdown()
            tcp.server_close()
            thread.join(timeout=5)
        return 0
    except (DnsError, OSError, PolicyError) as exc:
        print(f"exact DNS proxy: {exc}", file=sys.stderr)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())

"""Scan a packet for anything that must never leave the house.

The guard mirrors the desktop app's external-reasoning privacy test (see
docs/RUNBOOK.md, "Privacy verification") and extends it: Home Assistant entity
ids (any case), ``hav-*`` container names, LAN addresses, bare ``.local`` /
``.lan`` / ``.home`` / ``.internal`` hostnames, JWT prefixes, local model
names, the OS username (a parameter, never hard-coded), RTSP and HTTP URLs,
digit runs of six or more (epochs, Frigate event ids) and separated digit
groups (phone numbers, thousands-separated ids), email addresses, ISO dates
and timestamps, slashed dates (camera OSD overlays), times of day, and
household names from a roster file that must be mode 0600 (matched as whole
names and as individual name parts of three or more characters).

Text is NFKC-normalised with Unicode format characters (zero-width joiners
and friends) removed before the regexes run, so a zero-width space cannot
split an entity id; the presence of such a character is itself a finding
(``format_char``), as is any non-ASCII character (``non_ascii``).

Findings name the pattern and the key path, never the matched text. When a
key itself matches, its path segment is replaced by a positional placeholder
(``<key#N>``) so the leaking key is not echoed by the finding, the exception
message or anything that journals them.

There is deliberately no strip or redact helper: a finding blocks the call and
the packet builder is fixed instead. ``assert_clean`` is the only entry point a
sender should use; ``packet.build_packet`` runs the same roster-free patterns
itself so it is fail-closed on its own.
"""
from __future__ import annotations

import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

ENTITY_DOMAINS = (
    "light", "binary_sensor", "sensor", "media_player", "input_boolean", "input_text",
    "input_number", "input_select", "switch", "automation", "script", "camera", "person",
    "device_tracker", "climate", "lock", "cover", "fan", "scene", "number", "select", "zone",
)

MODEL_NAME_PATTERNS = (
    r"\bqwen[0-9a-z_.-]*", r"\bvllm\b", r"\bv-?jepa[0-9a-z_.-]*", r"\brtmw\b", r"\byolox\b",
    r"\bjev-\d[0-9.]*", r"\bparakeet\b", r"\bchatterbox\b", r"\bkokoro\b", r"\bmoshi\b",
    r"\bpersonaplex\b", r"\bfrigate\b",
)

LOCAL_TLDS = ("local", "lan", "home", "internal")

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ha_entity_id", re.compile(r"\b(?:" + "|".join(ENTITY_DOMAINS) + r")\.[a-z0-9_]+\b", re.IGNORECASE)),
    ("container_name", re.compile(r"\bhav-[a-z0-9_-]+", re.IGNORECASE)),
    ("lan_ip", re.compile(r"\b(?:192\.168|10|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    ("local_hostname", re.compile(r"\b[a-z0-9-]+\.(?:" + "|".join(LOCAL_TLDS) + r")\b", re.IGNORECASE)),
    ("jwt_prefix", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}")),
    ("model_name", re.compile("|".join(MODEL_NAME_PATTERNS), re.IGNORECASE)),
    ("rtsp_url", re.compile(r"\brtsps?://\S+", re.IGNORECASE)),
    ("digit_run", re.compile(r"\d{6,}")),
    ("digit_group", re.compile(r"(?<![\d])\d(?:[ ,.\-]?\d){5,}(?![\d])")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+")),
    ("http_url", re.compile(r"\bhttps?://\S+", re.IGNORECASE)),
    ("iso_timestamp", re.compile(
        r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b")),
    ("slashed_date", re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")),
    ("time_of_day", re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s*(?:am|pm))?\b", re.IGNORECASE)),
)

# Character-class checks that are not regex-expressible over ASCII source.
FORMAT_CHAR = "format_char"
NON_ASCII = "non_ascii"

ROSTER_MODE = 0o600
ROSTER_TOKEN_MIN_LEN = 3


class RosterError(ValueError):
    """The roster file is missing, has the wrong mode, or is not a regular file."""


class LeakError(ValueError):
    """The packet contains something that must not leave the house."""

    def __init__(self, findings: list["Finding"]):
        self.findings = findings
        super().__init__("; ".join(f"{f.pattern} at {f.key_path}" for f in findings))


@dataclass(frozen=True)
class Finding:
    """One hit: which pattern, where, and how long the match was. No text.

    ``in_key`` is True when the key itself matched; the path then ends in a
    ``<key#N>`` placeholder rather than the key.
    """

    pattern: str
    key_path: str
    match_len: int
    in_key: bool = False

    def as_dict(self) -> dict:
        return {"pattern": self.pattern, "key_path": self.key_path,
                "match_len": self.match_len, "in_key": self.in_key}


def load_roster(path: str | os.PathLike[str]) -> tuple[str, ...]:
    """Names to block, one per line (``#`` comments and blanks ignored).

    Refuses anything but a regular file with mode exactly 0600 so a
    world-readable copy of household names cannot be created by accident.
    Each line is matched as a whole name and as its individual parts of
    ``ROSTER_TOKEN_MIN_LEN`` or more characters; nicknames shorter than that
    or spelled differently need their own line.
    """
    p = Path(path)
    try:
        st = p.lstat()
    except FileNotFoundError as exc:
        raise RosterError(f"roster missing: {p}") from exc
    if not stat.S_ISREG(st.st_mode):
        raise RosterError(f"roster is not a regular file: {p}")
    mode = stat.S_IMODE(st.st_mode)
    if mode != ROSTER_MODE:
        raise RosterError(f"roster mode is {mode:04o}, expected {ROSTER_MODE:04o}: {p}")
    names = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.append(line)
    return tuple(names)


def _word(pattern: str) -> re.Pattern[str]:
    return re.compile(r"(?<![A-Za-z0-9])" + pattern + r"(?![A-Za-z0-9])", re.IGNORECASE)


def _identifier_patterns(username: str, roster: Iterable[str]) -> list[tuple[str, re.Pattern[str]]]:
    if not isinstance(username, str) or not username.strip():
        raise ValueError("username must be a non-empty string; read it from the environment, never hard-code it")
    extra = [("os_username", _word(re.escape(username.strip())))]
    seen: set[str] = set()
    for name in roster:
        tokens = normalise_text(name).split()
        if not tokens:
            continue
        candidates = [r"\s+".join(re.escape(t) for t in tokens)]
        candidates.extend(re.escape(t) for t in tokens if len(t) >= ROSTER_TOKEN_MIN_LEN)
        for candidate in candidates:
            key = candidate.lower()
            if key not in seen:
                seen.add(key)
                extra.append(("roster_name", _word(candidate)))
    return extra


def normalise_text(text: str) -> str:
    """NFKC-normalise and drop Unicode format characters (category Cf)."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text) if unicodedata.category(ch) != "Cf")


def scan_text(text: str, patterns: Iterable[tuple[str, re.Pattern[str]]] = PATTERNS) -> list[tuple[str, int]]:
    """Every (pattern name, match length) hit in one string. Never the text."""
    hits: list[tuple[str, int]] = []
    format_chars = sum(1 for ch in text if unicodedata.category(ch) == "Cf")
    if format_chars:
        hits.append((FORMAT_CHAR, format_chars))
    non_ascii = sum(1 for ch in text if ord(ch) > 0x7F)
    if non_ascii:
        hits.append((NON_ASCII, non_ascii))
    normalised = normalise_text(text)
    for name, regex in patterns:
        for match in regex.finditer(normalised):
            hits.append((name, match.end() - match.start()))
    return hits


def _scan_node(node: Any, path: str, patterns: list, findings: list[Finding]) -> None:
    """Scan every key and every scalar, numbers included; keys get placeholders."""
    if isinstance(node, Mapping):
        for index, (key, value) in enumerate(node.items()):
            key_hits = scan_text(str(key), patterns)
            if key_hits:
                child = f"{path}.<key#{index}>"
                findings.extend(Finding(name, child, length, True) for name, length in key_hits)
            else:
                child = f"{path}.{key}"
            _scan_node(value, child, patterns, findings)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _scan_node(item, f"{path}[{i}]", patterns, findings)
    elif node is None or isinstance(node, bool):
        return
    else:
        text = node if isinstance(node, str) else json.dumps(node)
        findings.extend(Finding(name, path, length) for name, length in scan_text(text, patterns))


def scan_packet(packet: Mapping[str, Any], username: str,
                roster_path: str | os.PathLike[str] | None = None,
                roster: Iterable[str] = ()) -> list[Finding]:
    """Return every finding in the packet (empty list means clean).

    ``roster_path`` is loaded with ``load_roster`` (mode 0600 enforced);
    ``roster`` adds names directly for tests. Keys are scanned as well as
    values so a leaking key name is caught.
    """
    names = list(roster)
    if roster_path is not None:
        names.extend(load_roster(roster_path))
    patterns = list(PATTERNS) + _identifier_patterns(username, names)
    findings: list[Finding] = []
    _scan_node(packet, "$", patterns, findings)
    return findings


def assert_clean(packet: Mapping[str, Any], username: str,
                 roster_path: str | os.PathLike[str] | None = None,
                 roster: Iterable[str] = ()) -> None:
    """Raise LeakError on any finding. The only entry point a sender may use."""
    findings = scan_packet(packet, username, roster_path=roster_path, roster=roster)
    if findings:
        raise LeakError(findings)


PATTERN_NAMES: tuple[str, ...] = tuple(name for name, _ in PATTERNS) + (
    FORMAT_CHAR, NON_ASCII, "os_username", "roster_name")

"""tmpfs options must survive YAML flow-sequence parsing.

``tmpfs: [/tmp:size=16m,mode=1777]`` is a YAML flow sequence, so the unquoted
comma splits it into TWO entries — ``/tmp:size=16m`` and ``mode=1777``. Docker
then rejects the second as a mount path::

    invalid mount path: 'mode=1777' mount path must be absolute

The affected services had never been executed, so nothing caught it. Any tmpfs
entry carrying options must be quoted.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "stack/home-agent-compose.yml"

OPTIONED_TMPFS_SERVICES = (
    "provision-identity-binding-kernel-role",
    "provision-parent-relationship-kernel-role",
)


def _raw() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def test_e5p_no_unquoted_flow_sequence_tmpfs_with_options() -> None:
    """A comma inside an unquoted flow sequence silently splits the entry."""

    offenders = re.findall(r'tmpfs: \[[^\]"\']*,[^\]]*\]', _raw())
    assert offenders == [], offenders


def test_e5p_optioned_tmpfs_entries_parse_as_one_mount() -> None:
    compose = yaml.safe_load(_raw())

    for service in OPTIONED_TMPFS_SERVICES:
        entries = compose["services"][service]["tmpfs"]
        assert len(entries) == 1, (service, entries)
        entry = entries[0]
        assert entry.startswith("/"), (service, entry)
        assert "size=16m" in entry and "mode=1777" in entry, (service, entry)


def test_e5p_every_tmpfs_entry_is_an_absolute_path() -> None:
    """Whatever the style, no entry may be a bare option fragment."""

    compose = yaml.safe_load(_raw())

    for name, service in compose.get("services", {}).items():
        for entry in service.get("tmpfs", []) or []:
            assert entry.startswith("/"), (name, entry)

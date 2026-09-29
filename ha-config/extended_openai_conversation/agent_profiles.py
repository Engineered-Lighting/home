"""Writable profile store for the household the agent authority owns.

The agent authority is the source of truth for who exists and how people are
related. It carries no headshot, no subrole and no notes, and the legacy store
that did carry them was frozen by the identity cutover and can never be written
again.

So profile material lives here instead: a small SQLite table plus an avatar
directory, keyed by the authority's person_id. It was seeded once from the
frozen store, and from then on everyone is equal -- a person added today takes a
headshot exactly the way a person added last year does.

Deliberately separate from identity: nothing here decides who exists or who is
related to whom. Losing this file would cost decoration, not truth.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

PROFILE_ROOT = Path("/config/home_agent_profiles")
PROFILE_DB = PROFILE_ROOT / "profiles.db"
PROFILE_AVATARS = PROFILE_ROOT / "avatars"

# The authority's identifiers are UUIDs. Anything else is refused rather than
# joined into a path, so a person_id can never escape the avatar directory.
PERSON_ID = re.compile(r"^[0-9a-fA-F-]{36}$")

MAX_AVATAR_BYTES = 2 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
  person_id            TEXT PRIMARY KEY,
  display_name         TEXT NOT NULL,
  relationship_type    TEXT,
  relationship_subrole TEXT,
  pronouns             TEXT,
  notes                TEXT,
  avatar_ext           TEXT,
  seeded_from_uuid     TEXT,
  updated_at           TEXT NOT NULL
);
"""

# What a caller may set. relationship_type decides which ring of the graph a
# person sits on; the rest is descriptive.
WRITABLE = ("relationship_type", "relationship_subrole", "pronouns", "notes")

RELATIONSHIP_TYPES = {
    "me", "partner", "family_immediate", "family_extended", "roommate",
    "friend", "neighbor", "service", "guest", "unknown", "do_not_identify",
}


def _connect() -> sqlite3.Connection:
    PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
    PROFILE_AVATARS.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(PROFILE_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def valid_person_id(person_id: str) -> bool:
    return bool(person_id and PERSON_ID.match(person_id))


def avatar_path(person_id: str, ext: str = "jpg") -> Path:
    if not valid_person_id(person_id):
        raise ValueError("invalid person_id")
    return PROFILE_AVATARS / f"{person_id}.{ext}"


def list_profiles() -> dict[str, dict[str, Any]]:
    """Every profile, keyed by person_id, with avatar presence resolved."""

    conn = _connect()
    try:
        out: dict[str, dict[str, Any]] = {}
        for row in conn.execute("SELECT * FROM profiles"):
            record = dict(row)
            ext = record.get("avatar_ext")
            record["avatar_present"] = bool(
                ext and avatar_path(record["person_id"], ext).is_file()
            )
            record.pop("seeded_from_uuid", None)
            out[record["person_id"]] = record
        return out
    finally:
        conn.close()


def upsert_profile(person_id: str, display_name: str, values: dict[str, Any]) -> dict:
    """Set the writable fields. Unknown keys are ignored, not stored."""

    if not valid_person_id(person_id):
        raise ValueError("invalid person_id")

    kind = values.get("relationship_type")
    if kind is not None and kind not in RELATIONSHIP_TYPES:
        # Refused rather than coerced: an unrecognised type would silently drop
        # the person out of the graph, which reads as data loss.
        raise ValueError(f"unknown relationship_type: {kind}")

    fields = {k: values[k] for k in WRITABLE if k in values}
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO profiles (person_id, display_name, updated_at)"
            " VALUES (?, ?, datetime('now'))"
            " ON CONFLICT(person_id) DO UPDATE SET"
            "   display_name = excluded.display_name,"
            "   updated_at = datetime('now')",
            (person_id, display_name or ""),
        )
        for key, value in fields.items():
            conn.execute(
                f"UPDATE profiles SET {key} = ?, updated_at = datetime('now')"
                " WHERE person_id = ?",
                (value, person_id),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM profiles WHERE person_id = ?", (person_id,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def save_avatar(person_id: str, data: bytes) -> None:
    if not valid_person_id(person_id):
        raise ValueError("invalid person_id")
    if not data:
        raise ValueError("empty image")
    if len(data) > MAX_AVATAR_BYTES:
        raise ValueError("image too large")
    # Accept only what the tab produces and the browser can render back.
    if not (data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n")):
        raise ValueError("expected a JPEG or PNG image")
    ext = "jpg" if data.startswith(b"\xff\xd8\xff") else "png"

    PROFILE_AVATARS.mkdir(parents=True, exist_ok=True)
    target = avatar_path(person_id, ext)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.chmod(0o600)
    tmp.replace(target)

    # Drop a stale image in the other format so presence cannot be ambiguous.
    other = avatar_path(person_id, "png" if ext == "jpg" else "jpg")
    if other.is_file():
        other.unlink()

    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO profiles (person_id, display_name, avatar_ext, updated_at)"
            " VALUES (?, '', ?, datetime('now'))"
            " ON CONFLICT(person_id) DO UPDATE SET"
            "   avatar_ext = excluded.avatar_ext,"
            "   updated_at = datetime('now')",
            (person_id, ext),
        )
        conn.commit()
    finally:
        conn.close()


def read_avatar(person_id: str) -> tuple[bytes, str] | None:
    if not valid_person_id(person_id):
        return None
    for ext, mime in (("jpg", "image/jpeg"), ("png", "image/png")):
        path = avatar_path(person_id, ext)
        if path.is_file():
            return path.read_bytes(), mime
    return None


def delete_avatar(person_id: str) -> bool:
    if not valid_person_id(person_id):
        return False
    removed = False
    for ext in ("jpg", "png"):
        path = avatar_path(person_id, ext)
        if path.is_file():
            path.unlink()
            removed = True
    if removed:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE profiles SET avatar_ext = NULL, updated_at = datetime('now')"
                " WHERE person_id = ?",
                (person_id,),
            )
            conn.commit()
        finally:
            conn.close()
    return removed

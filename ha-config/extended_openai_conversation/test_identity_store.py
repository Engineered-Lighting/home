#!/usr/bin/env python3
"""Tests for identity_store.py.

Standalone-runnable like test_world_state.py — no pytest install
needed. Uses an in-memory SQLite DB so tests are hermetic + fast.

Run on workstation: `py -3 test_identity_store.py`
Exit 0 = all green; non-zero = failures.

Coverage (Slice-1 acceptance — per Addendum 14 user-story IDs):

  Schema integrity:
    - Empty DB → schema applied; identities table exists; PRAGMA foreign_keys ON
    - Invalid relationship_type rejected by CHECK constraint
    - Schema version recorded in schema_meta

  Identity CRUD (US-1.07, 1.08, 1.13–1.22, 1.23, 1.29):
    - create_identity returns a stable uuid
    - get_identity by uuid AND by alias
    - list_identities filters archived + ignored + by relationship_type
    - update_identity bumps version + writes audit row
    - update_identity rejects unknown columns
    - update_identity respects optimistic concurrency (expected_version)
    - delete_identity cascades aliases + relationships + preferences
    - delete_identity queues a Frigate-delete in pending_writes (AR-8)

  Aliases (US-1.09):
    - add_alias is idempotent on duplicates
    - resolve_frigate_name finds via alias OR enrollment

  Relationships (US-2.01, 2.02, 2.06, 2.15, 2.16):
    - add_relationship requires both identities exist
    - add_relationship rejects self-loops
    - list_relationships filters by status
    - end_relationship sets status + ended_at

  Preferences (US-4.01–4.05):
    - set_preference upserts (same key + scope updates)
    - list_preferences filters by scope (subset semantics)
    - source enforcement (manual / inferred only)
    - provenance round-trips

  Audit / change_log (AR-10, AR2-7):
    - Every mutation writes a change_log row with conv_id link
    - purge_change_log respects per-kind TTL + skips pinned rows

  Disable knob (rollback):
    - EXTENDED_OPENAI_IDENTITY_STORE=off → all methods are no-ops

  Privacy flags (AR2-6):
    - Each typed flag round-trips correctly (private/silent/ignored/etc.)
    - list_identities default-excludes ignored

  Concurrency (AR-9):
    - BEGIN IMMEDIATE blocks a concurrent transaction (sanity check)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

# Make this script's directory importable so `from identity_store ...` works
# regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Force-enable for tests in case the env was sticky
os.environ.pop("EXTENDED_OPENAI_IDENTITY_STORE", None)
os.environ["EXTENDED_OPENAI_IDENTITY_SEMANTIC_WRITES"] = "legacy_migration_only"

from identity_store import (  # type: ignore[import]
    IdentityStore,
    RELATIONSHIP_TYPES,
    PREFERENCE_SOURCES,
    SCHEMA_VERSION,
    CHANGE_LOG_RETENTION,
    is_disabled,
)


# ── Tiny test runner ──────────────────────────────────────────────────

passes = 0
fails = 0
failures: list[tuple[str, str]] = []

def t(name: str, fn):
    global passes, fails
    try:
        fn()
        passes += 1
        print(f"  PASS  {name}")
    except AssertionError as e:
        fails += 1
        failures.append((name, str(e)))
        print(f"  FAIL  {name}: {e}")
    except Exception as e:
        fails += 1
        failures.append((name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")

def section(name: str):
    print(f"\n[{name}]")


def fresh() -> IdentityStore:
    """In-memory store + setup. Each test gets a clean DB."""
    s = IdentityStore(db_path=":memory:")
    s.setup()
    return s


# ── Schema integrity ──────────────────────────────────────────────────
section("schema integrity")

def _schema_applied():
    s = fresh()
    cur = s._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [r["name"] for r in cur.fetchall()]
    assert "identities" in tables, tables
    assert "relationships" in tables, tables
    assert "preferences" in tables, tables
    assert "change_log" in tables, tables
    assert "pending_writes" in tables, tables
    # Foreign keys enforced
    fk = s._conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1, f"foreign_keys = {fk}"
    # Schema version recorded
    v = s._conn.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()
    assert v is not None and int(v[0]) == SCHEMA_VERSION
t("empty DB → all tables present + FK enforced + version recorded", _schema_applied)

def _bad_rel_type_rejected():
    s = fresh()
    try:
        s.create_identity("Test", relationship_type="bogus")
        raise AssertionError("expected ValueError for bogus relationship_type")
    except ValueError:
        pass
t("invalid relationship_type rejected by Python validation", _bad_rel_type_rejected)


# ── Identity CRUD ─────────────────────────────────────────────────────
section("identity CRUD")

def _create_returns_uuid():
    s = fresh()
    u = s.create_identity("Marcelo", relationship_type="me")
    assert isinstance(u, str) and len(u) == 32, u
    row = s.get_identity(u)
    assert row is not None
    assert row.display_name == "Marcelo"
    assert row.relationship_type == "me"
    # Primary 'name' alias is auto-inserted
    aliases = {a["alias"] for a in row.aliases if a["kind"] == "name"}
    assert "Marcelo" in aliases
t("create_identity returns uuid + creates 'name' alias", _create_returns_uuid)

def _get_by_alias():
    s = fresh()
    u = s.create_identity("Sarah", relationship_type="partner",
                          aliases=["Sar", "S."])
    by_alias = s.get_identity("Sar")
    assert by_alias is not None and by_alias.uuid == u
    # Case-insensitive
    by_alias2 = s.get_identity("sarah")
    assert by_alias2 is not None and by_alias2.uuid == u
t("get_identity resolves by alias + case-insensitive", _get_by_alias)

def _list_filters():
    s = fresh()
    s.create_identity("M", relationship_type="me")
    s.create_identity("P", relationship_type="partner")
    s.create_identity("G", relationship_type="guest")
    s.create_identity("Hidden", relationship_type="friend")
    # Archive one
    hidden = s.get_identity("Hidden")
    s.update_identity(hidden.uuid, {"is_archived": True})
    # Ignore another
    g = s.get_identity("G")
    s.update_identity(g.uuid, {"is_ignored": True})
    all_default = s.list_identities()
    names_default = [i.display_name for i in all_default]
    assert "Hidden" not in names_default, names_default
    assert "G" not in names_default, names_default
    assert {"M", "P"} <= set(names_default)
    # include_archived
    with_arch = s.list_identities(include_archived=True)
    names_arch = [i.display_name for i in with_arch]
    assert "Hidden" in names_arch
    # filter by relationship_type
    partners = s.list_identities(relationship_type="partner")
    assert [i.display_name for i in partners] == ["P"]
t("list_identities filters archived + ignored + by type", _list_filters)

def _update_bumps_version():
    s = fresh()
    u = s.create_identity("Test", relationship_type="friend")
    cur = s.get_identity(u)
    assert cur.version == 1
    ok = s.update_identity(u, {"notes": "hi"})
    assert ok
    after = s.get_identity(u)
    assert after.version == 2
    assert after.notes == "hi"
t("update_identity bumps version + persists patch", _update_bumps_version)

def _update_rejects_unknown_col():
    s = fresh()
    u = s.create_identity("Test")
    try:
        s.update_identity(u, {"some_random_field": "x"})
        raise AssertionError("expected ValueError for unknown column")
    except ValueError:
        pass
t("update_identity rejects unknown columns", _update_rejects_unknown_col)

def _update_optimistic_conflict():
    s = fresh()
    u = s.create_identity("Test")
    s.update_identity(u, {"notes": "first"})  # version → 2
    # Try with stale version=1
    try:
        s.update_identity(u, {"notes": "second"}, expected_version=1)
        raise AssertionError("expected ValueError for version mismatch")
    except ValueError as e:
        assert "version" in str(e).lower()
t("update_identity rejects on stale expected_version", _update_optimistic_conflict)

def _rename_pushes_to_frigate():
    s = fresh()
    u = s.create_identity("david", frigate_person_name="david")
    s.update_identity(u, {"display_name": "David Smith"})
    pending = s.pending_writes(state="pending")
    rename = [p for p in pending if p["op"] == "rename"]
    assert len(rename) == 1, pending
    assert rename[0]["payload"]["old_name"] == "david"
    assert rename[0]["payload"]["new_name"] == "david_smith"
    # Identity-side aliases + enrollments should now reflect new name
    row = s.get_identity(u)
    frigate_aliases = [a["alias"] for a in row.aliases if a["kind"] == "frigate_name"]
    assert frigate_aliases == ["david_smith"], frigate_aliases
t("display_name change queues Frigate rename + updates frigate aliases",
  _rename_pushes_to_frigate)

def _rename_no_queue_when_unchanged():
    s = fresh()
    u = s.create_identity("david", frigate_person_name="david")
    # Patch something else; no rename queued
    s.update_identity(u, {"notes": "hi"})
    pending = s.pending_writes(state="pending")
    assert all(p["op"] != "rename" for p in pending), pending
t("non-display_name update does NOT queue Frigate rename",
  _rename_no_queue_when_unchanged)

def _delete_cascades_and_queues():
    s = fresh()
    u = s.create_identity(
        "Sarah",
        relationship_type="partner",
        frigate_person_name="sarah_frigate",
        aliases=["Sar"],
    )
    # Add a relationship + preference so we can verify cascade
    u2 = s.create_identity("Brother", relationship_type="family_immediate")
    s.add_relationship(u, u2, "sibling")
    s.set_preference(u, "light_temp_pref", 2700)
    s.delete_identity(u)
    # All gone
    assert s.get_identity(u) is None
    assert s.get_identity("Sar") is None  # alias gone
    assert s.list_relationships(u) == []
    assert s.list_preferences(u) == []
    # Frigate-delete queued
    pending = s.pending_writes(state="pending")
    assert any(pw["op"] == "delete" and pw["payload"]["person_name"] == "sarah_frigate"
               for pw in pending), pending
t("delete_identity cascades + queues Frigate delete", _delete_cascades_and_queues)


# ── Aliases ───────────────────────────────────────────────────────────
section("aliases")

def _add_alias_idempotent():
    s = fresh()
    u = s.create_identity("Sarah")
    assert s.add_alias(u, "Sar") is True
    # Second add of the same alias is a no-op return False
    assert s.add_alias(u, "Sar") is False
t("add_alias is idempotent on duplicates", _add_alias_idempotent)

def _resolve_frigate_name():
    s = fresh()
    u = s.create_identity("Sarah", frigate_person_name="sarah_frigate")
    row_via_alias = s.resolve_frigate_name("sarah_frigate")
    assert row_via_alias is not None and row_via_alias.uuid == u
    # Case-insensitive
    row_ci = s.resolve_frigate_name("Sarah_Frigate")
    assert row_ci is not None and row_ci.uuid == u
    # Unknown frigate name returns None
    assert s.resolve_frigate_name("nobody") is None
t("resolve_frigate_name finds via alias + case-insensitive + None on miss",
  _resolve_frigate_name)


# ── Relationships ─────────────────────────────────────────────────────
section("relationships")

def _add_relationship_requires_both():
    s = fresh()
    u = s.create_identity("A")
    try:
        s.add_relationship(u, "nonexistent-uuid", "friend")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
t("add_relationship rejects when target identity missing",
  _add_relationship_requires_both)

def _reject_self_loop():
    s = fresh()
    u = s.create_identity("A")
    try:
        s.add_relationship(u, u, "friend")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
t("add_relationship rejects self-loops", _reject_self_loop)

def _end_relationship_sets_status():
    s = fresh()
    a = s.create_identity("A")
    b = s.create_identity("B")
    edge_id = s.add_relationship(a, b, "friend")
    assert edge_id > 0
    # Default list shows it
    active = s.list_relationships(a)
    assert len(active) == 1
    assert s.end_relationship(edge_id) is True
    active_after = s.list_relationships(a)
    assert active_after == []  # default excludes ended
    with_ended = s.list_relationships(a, include_ended=True)
    assert len(with_ended) == 1
    assert with_ended[0]["status"] == "ended"
    assert with_ended[0]["ended_at"] is not None
t("end_relationship sets status='ended' + ended_at", _end_relationship_sets_status)


# ── Preferences ───────────────────────────────────────────────────────
section("preferences")

def _set_pref_upserts():
    s = fresh()
    u = s.create_identity("Sarah")
    pid = s.set_preference(u, "light_temp_pref", 2700)
    assert pid > 0
    # Set same key + same (empty) scope → update, not duplicate
    pid2 = s.set_preference(u, "light_temp_pref", 3000)
    prefs = s.list_preferences(u)
    assert len(prefs) == 1, prefs
    assert prefs[0]["value"] == 3000
t("set_preference upserts on (key, scope)", _set_pref_upserts)

def _pref_scope_filter():
    s = fresh()
    u = s.create_identity("Sarah")
    s.set_preference(u, "light_temp_pref", 2700,
                     scope={"time_of_day": "evening"})
    s.set_preference(u, "light_temp_pref", 5000,
                     scope={"time_of_day": "morning"})
    s.set_preference(u, "music_genre", "lofi")  # no scope = unscoped
    morning = s.list_preferences(
        u, scope_filter={"time_of_day": "morning"}
    )
    keys_morning = {(p["key"], p["value"]) for p in morning}
    # Should include the morning-scoped pref AND the unscoped one
    assert ("light_temp_pref", 5000) in keys_morning
    assert ("music_genre", "lofi") in keys_morning
    # Should NOT include the evening-scoped one
    assert ("light_temp_pref", 2700) not in keys_morning
t("list_preferences with scope_filter applies subset semantics",
  _pref_scope_filter)

def _pref_source_enforced():
    s = fresh()
    u = s.create_identity("Sarah")
    try:
        s.set_preference(u, "k", "v", source="auto")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
t("set_preference rejects invalid source", _pref_source_enforced)

def _provenance_round_trip():
    s = fresh()
    u = s.create_identity("Sarah")
    prov = {"observations": [1, 2, 3], "rule": "repeated_room_action"}
    s.set_preference(u, "k", 5, source="inferred", confidence=0.7,
                     provenance=prov)
    p = s.list_preferences(u)[0]
    assert p["source"] == "inferred"
    assert p["confidence"] == 0.7
    assert p["provenance"] == prov
t("preference provenance JSON round-trips intact", _provenance_round_trip)


# ── Audit log ─────────────────────────────────────────────────────────
section("audit / change_log")

def _every_mutation_writes_audit_row():
    s = fresh()
    u = s.create_identity("Sarah", relationship_type="partner")
    s.update_identity(u, {"notes": "hi"})
    s.set_preference(u, "k", "v")
    rows = s._conn.execute(
        "SELECT kind, actor FROM change_log WHERE target_uuid = ? ORDER BY id",
        (u,),
    ).fetchall()
    kinds = [r["kind"] for r in rows]
    # 3 mutations: create + update + set_pref
    assert kinds == ["identity_mutation"] * 3, kinds
t("every mutation writes a change_log row", _every_mutation_writes_audit_row)

def _audit_is_content_minimized():
    s = fresh()
    u = s.create_identity("SENSITIVE-NAME-CANARY")
    s.update_identity(u, {"notes": "SENSITIVE-NOTE-CANARY"})
    s.delete_identity(u, queue_frigate_delete=False)
    rows = s._conn.execute(
        "SELECT before_json, after_json, link_conv_id, link_turn_id "
        "FROM change_log ORDER BY id"
    ).fetchall()
    serialized = json.dumps([dict(row) for row in rows])
    assert "SENSITIVE-NAME-CANARY" not in serialized
    assert "SENSITIVE-NOTE-CANARY" not in serialized
    assert all(row["before_json"] is None for row in rows)
    assert all(
        set(json.loads(row["after_json"])) == {"operation_code"}
        for row in rows
    )
    assert all(row["link_conv_id"] is None for row in rows)
    assert all(row["link_turn_id"] is None for row in rows)
t("audit rows never retain identity snapshots or conversation links",
  _audit_is_content_minimized)

def _setup_scrubs_historical_audit_snapshots():
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "identity.db")
        first = IdentityStore(db_path=path)
        first.setup()
        first._conn.execute(
            "INSERT INTO change_log(ts, kind, actor, target_uuid, before_json, "
            "after_json, link_conv_id) VALUES(?, 'identity_mutation', 'user', "
            "?, ?, ?, ?)",
            (
                "2026-07-11T00:00:00Z",
                "opaque-id",
                '{"display_name":"SENSITIVE-HISTORICAL-CANARY"}',
                '{"notes":"SENSITIVE-HISTORICAL-NOTE"}',
                "conversation-canary",
            ),
        )
        first.close()
        reopened = IdentityStore(db_path=path)
        reopened.setup()
        row = reopened._conn.execute(
            "SELECT before_json, after_json, link_conv_id FROM change_log"
        ).fetchone()
        assert row["before_json"] is None
        assert json.loads(row["after_json"]) == {
            "operation_code": "legacy_scrubbed"
        }
        assert row["link_conv_id"] is None
        raw = Path(path).read_bytes()
        assert b"SENSITIVE-HISTORICAL-CANARY" not in raw
        assert b"SENSITIVE-HISTORICAL-NOTE" not in raw
        assert b"conversation-canary" not in raw
        wal = Path(path + "-wal")
        assert not wal.exists() or b"SENSITIVE-HISTORICAL-CANARY" not in wal.read_bytes()
        reopened.close()
t("setup scrubs historical full audit snapshots", _setup_scrubs_historical_audit_snapshots)

def _purge_respects_ttl_and_pinned():
    s = fresh()
    u = s.create_identity("Sarah")
    # Insert old + new + pinned rows manually
    s._conn.execute(
        "INSERT INTO change_log(ts, kind, actor, target_uuid, pinned) "
        "VALUES (?, 'detection', 'system', ?, 0)",
        ("2020-01-01T00:00:00Z", u),  # ancient
    )
    s._conn.execute(
        "INSERT INTO change_log(ts, kind, actor, target_uuid, pinned) "
        "VALUES (?, 'assistant_action', 'llm', ?, 1)",
        ("2020-01-01T00:00:00Z", u),  # ancient but PINNED
    )
    s._conn.execute(
        "INSERT INTO change_log(ts, kind, actor, target_uuid, pinned) "
        "VALUES (?, 'detection', 'system', ?, 0)",
        ("2099-01-01T00:00:00Z", u),  # future = fresh
    )
    deleted = s.purge_change_log(now_ts=time.time())
    assert deleted >= 1, deleted
    remaining = s._conn.execute(
        "SELECT COUNT(*) FROM change_log WHERE target_uuid = ?", (u,)
    ).fetchone()[0]
    # Should keep: pinned ancient + future + the 1 auto-create row
    # Should delete: ancient unpinned detection
    assert remaining >= 2, remaining
    # Pinned ancient row survived
    pinned = s._conn.execute(
        "SELECT COUNT(*) FROM change_log "
        "WHERE pinned = 1 AND target_uuid = ?", (u,)
    ).fetchone()[0]
    assert pinned == 1
t("purge_change_log honors TTL + skips pinned rows",
  _purge_respects_ttl_and_pinned)


# ── Disable knob ──────────────────────────────────────────────────────
section("disable knob")

def _disabled_short_circuits():
    os.environ["EXTENDED_OPENAI_IDENTITY_STORE"] = "off"
    try:
        s = IdentityStore(db_path=":memory:")
        s.setup()  # no-op
        assert s.create_identity("anyone") == ""
        assert s.get_identity("anyone") is None
        assert s.list_identities() == []
        assert s.resolve_frigate_name("x") is None
        assert s.pending_writes() == []
    finally:
        os.environ.pop("EXTENDED_OPENAI_IDENTITY_STORE", None)
t("EXTENDED_OPENAI_IDENTITY_STORE=off → all methods no-op",
  _disabled_short_circuits)


# ── Privacy flags (typed columns per AR-5) ─────────────────────────────
section("semantic cutover freeze")

def _semantic_freeze_is_irreversible_and_recognition_stays_operational():
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "identity.db")
        mutable = IdentityStore(db_path=path)
        mutable.setup()
        assert mutable.semantic_writes_frozen is False
        mutable.create_identity("Reviewed before cutover")
        mutable.close()

        os.environ.pop("EXTENDED_OPENAI_IDENTITY_SEMANTIC_WRITES", None)
        frozen = IdentityStore(db_path=path)
        frozen.setup()
        assert frozen.semantic_writes_frozen is True
        try:
            frozen.create_identity("Must fail")
            raise AssertionError("semantic create unexpectedly succeeded")
        except PermissionError:
            pass
        recognition_uuid = frozen.ensure_recognition_enrollment(
            "new_frigate_cluster", display_label="Unreviewed recognition"
        )
        assert recognition_uuid
        assert frozen.resolve_frigate_name("new_frigate_cluster") is not None
        frozen.close()

        os.environ["EXTENDED_OPENAI_IDENTITY_SEMANTIC_WRITES"] = (
            "legacy_migration_only"
        )
        reopened = IdentityStore(db_path=path)
        reopened.setup()
        assert reopened.semantic_writes_frozen is True
        try:
            reopened.add_alias(recognition_uuid, "Must fail")
            raise AssertionError("semantic alias unexpectedly succeeded")
        except PermissionError:
            pass
        reopened.close()

t(
    "Core cutover marker freezes semantics but preserves recognition enrollment",
    _semantic_freeze_is_irreversible_and_recognition_stays_operational,
)


section("privacy flags")

def _typed_flags_round_trip():
    s = fresh()
    u = s.create_identity("Sarah")
    s.update_identity(u, {
        "is_private": True,
        "is_silent": True,
        "is_ignored": False,
        "do_not_track": True,
    })
    row = s.get_identity(u)
    assert row.is_private is True
    assert row.is_silent is True
    assert row.is_ignored is False
    assert row.do_not_track is True
t("typed privacy flags round-trip", _typed_flags_round_trip)


# ── Concurrency sanity ────────────────────────────────────────────────
section("concurrency")

def _txn_isolation():
    """Two threads racing: only one update should win when both compute
    against version=1. This is sanity-only; expected_version is the
    real safety mechanism."""
    s = fresh()
    u = s.create_identity("Sarah")
    barrier = threading.Barrier(2)
    results: dict[str, bool] = {}
    def worker(key: str, value: str):
        barrier.wait()
        try:
            s.update_identity(u, {"notes": value}, expected_version=1)
            results[key] = True
        except ValueError:
            results[key] = False
    t1 = threading.Thread(target=worker, args=("A", "first"))
    t2 = threading.Thread(target=worker, args=("B", "second"))
    t1.start(); t2.start(); t1.join(); t2.join()
    # Exactly one wins, one fails version check
    wins = sum(1 for v in results.values() if v)
    assert wins == 1, f"expected exactly one winner, got {results}"
t("two threads racing: optimistic concurrency lets only one win",
  _txn_isolation)


# ── Summary ───────────────────────────────────────────────────────────
print(f"\n{passes} pass · {fails} fail")
if fails:
    print("\nFailures:")
    for name, msg in failures:
        print(f"  - {name}: {msg}")
sys.exit(0 if fails == 0 else 1)

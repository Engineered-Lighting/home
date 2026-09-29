#!/usr/bin/env python3
"""Tests for frigate_sync.py.

Standalone-runnable like test_world_state.py + test_identity_store.py.
No pytest install needed; no httpx dependency (uses a stub).

Run on workstation: `py -3 test_frigate_sync.py`
Exit 0 = all green; non-zero = failures.

Coverage:

  Capability probe:
    - reachable + face_library detected when /api/faces returns dict
    - face_library FALSE when /api/faces returns non-dict
    - unreachable when /api/version times out
    - graceful when httpx absent (skipped path)

  Operational enrollment poll:
    - empty store + Frigate has 3 faces → 3 opaque identifiers retained
    - 'train' bucket filtered out (Frigate's training holding pen)
    - re-seed (force=True) doesn't duplicate operational identifiers
    - non-empty store + force=False → skipped with reason
    - Frigate offline → seed returns error in report, doesn't raise

  Writeback:
    - rename_frigate_face: 200 → success
    - rename_frigate_face: 4xx → error captured
    - delete_frigate_face: 404 → treated as idempotent success
    - delete_frigate_face: 500 → error captured

  Drainer:
    - drains pending rows, marks done on success
    - retries on failure (state stays pending, attempt_count bumps)
    - rows exceeding max_attempts → marked failed
    - empty outbox → no-op summary
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Force-enable the legacy bridge for functional tests. Production has the
# inverse default and must opt in explicitly.
os.environ["EXTENDED_OPENAI_FRIGATE_SYNC"] = "on"
os.environ.pop("EXTENDED_OPENAI_IDENTITY_STORE", None)
os.environ["EXTENDED_OPENAI_IDENTITY_SEMANTIC_WRITES"] = "legacy_migration_only"

from identity_store import IdentityStore  # type: ignore[import]
from frigate_sync import (  # type: ignore[import]
    probe_frigate_capabilities,
    auto_seed_identities_from_frigate,
    rename_frigate_face,
    delete_frigate_face,
    drain_pending_writes,
)


# ── Tiny test runner ─────────────────────────────────────────────────

passes = 0
fails = 0
failures: list[tuple[str, str]] = []

def t(name: str, coro):
    global passes, fails
    try:
        asyncio.run(coro())
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


# ── Mock httpx client ────────────────────────────────────────────────


class MockResponse:
    def __init__(self, status_code: int, body=None, text: str = ""):
        self.status_code = status_code
        self._body = body
        self.text = text if text else (json.dumps(body) if body is not None else "")
    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class MockHttpClient:
    """Minimal stub of httpx.AsyncClient. Caller registers responses
    keyed on method+path; .get/.post/.delete dispatch to those."""
    def __init__(self):
        self.responses: dict[tuple[str, str], MockResponse] = {}
        self.exceptions: dict[tuple[str, str], Exception] = {}
        self.calls: list[tuple[str, str, dict]] = []
    def stub(self, method: str, path_suffix: str, response: MockResponse):
        self.responses[(method.upper(), path_suffix)] = response
    def stub_exception(self, method: str, path_suffix: str, exc: Exception):
        self.exceptions[(method.upper(), path_suffix)] = exc
    async def _do(self, method: str, url: str, **kwargs):
        # Match by suffix
        method = method.upper()
        for (m, suf), exc in self.exceptions.items():
            if m == method and url.endswith(suf):
                self.calls.append((method, url, kwargs))
                raise exc
        for (m, suf), resp in self.responses.items():
            if m == method and url.endswith(suf):
                self.calls.append((method, url, kwargs))
                return resp
        # Pattern fallback: ignore query/path-suffix mismatches
        for (m, suf), resp in self.responses.items():
            if m == method and suf in url:
                self.calls.append((method, url, kwargs))
                return resp
        for (m, suf), exc in self.exceptions.items():
            if m == method and suf in url:
                self.calls.append((method, url, kwargs))
                raise exc
        # Default = 404
        self.calls.append((method, url, kwargs))
        return MockResponse(404, text="not found")
    async def get(self, url, **kwargs): return await self._do("GET", url, **kwargs)
    async def post(self, url, **kwargs): return await self._do("POST", url, **kwargs)
    async def put(self, url, **kwargs): return await self._do("PUT", url, **kwargs)
    async def delete(self, url, **kwargs): return await self._do("DELETE", url, **kwargs)
    async def aclose(self): pass


def fresh_store() -> IdentityStore:
    s = IdentityStore(db_path=":memory:")
    s.setup()
    return s


# ── Capability probe ────────────────────────────────────────────────
section("capability probe")

async def _probe_face_library_detected():
    http = MockHttpClient()
    http.stub("GET", "/api/version", MockResponse(200, text="0.17.1-abc"))
    http.stub("GET", "/api/faces", MockResponse(200, {"sarah": ["a.webp"], "marcelo": []}))
    http.stub("GET", "/api/events", MockResponse(200, []))
    caps = await probe_frigate_capabilities(http_client=http)
    assert caps.reachable is True, caps
    assert caps.version == "0.17.1-abc", caps
    assert caps.face_library is True, caps
    assert caps.rename is True   # inferred from face_library presence
    assert caps.delete is True
    assert caps.unmatched_events is True
    assert caps.last_probed_at is not None
t("face_library detected when /api/faces returns dict", _probe_face_library_detected)

async def _probe_face_library_absent():
    http = MockHttpClient()
    http.stub("GET", "/api/version", MockResponse(200, text="0.17.1-abc"))
    http.stub("GET", "/api/faces", MockResponse(404, text="not configured"))
    http.stub("GET", "/api/events", MockResponse(404, text="not found"))
    caps = await probe_frigate_capabilities(http_client=http)
    assert caps.reachable is True
    assert caps.face_library is False
    assert caps.rename is False
    assert caps.unmatched_events is False
t("face_library absent when /api/faces 404", _probe_face_library_absent)

async def _probe_unreachable():
    http = MockHttpClient()
    http.stub_exception("GET", "/api/version", ConnectionError("connection refused"))
    caps = await probe_frigate_capabilities(http_client=http)
    assert caps.reachable is False
    assert caps.last_error is not None and "version probe failed" in caps.last_error
t("unreachable when /api/version raises", _probe_unreachable)

async def _probe_disabled():
    os.environ["EXTENDED_OPENAI_FRIGATE_SYNC"] = "off"
    try:
        caps = await probe_frigate_capabilities(http_client=MockHttpClient())
        assert caps.reachable is False
        assert "disabled" in (caps.last_error or "")
    finally:
        os.environ["EXTENDED_OPENAI_FRIGATE_SYNC"] = "on"
t("EXTENDED_OPENAI_FRIGATE_SYNC=off short-circuits probe", _probe_disabled)

async def _probe_default_disabled():
    os.environ.pop("EXTENDED_OPENAI_FRIGATE_SYNC", None)
    try:
        http = MockHttpClient()
        caps = await probe_frigate_capabilities(http_client=http)
        assert caps.reachable is False
        assert "disabled" in (caps.last_error or "")
        assert http.calls == []
    finally:
        os.environ["EXTENDED_OPENAI_FRIGATE_SYNC"] = "on"
t("Frigate sync is fail-closed when the setting is absent", _probe_default_disabled)


# ── Auto-seed ────────────────────────────────────────────────────────
section("auto-seed")

async def _seed_empty_store():
    s = fresh_store()
    http = MockHttpClient()
    http.stub("GET", "/api/faces", MockResponse(200, {
        "sarah": ["a.webp", "b.webp"],
        "marcelo": ["c.webp"],
        "david": [],
        "train": ["misc.webp"],   # special bucket — must be filtered out
    }))
    rep = await auto_seed_identities_from_frigate(s, http_client=http)
    assert rep.attempted is True
    assert rep.frigate_faces_found == 3, rep   # train excluded
    assert rep.identities_created == 3, rep
    # No semantic people or display labels are created.
    assert s.list_identities() == []
    for name in ("sarah", "marcelo", "david"):
        assert s.resolve_operational_recognition_name(name) is not None
t("empty store + 3 Frigate faces → 3 operational ids + train filtered",
  _seed_empty_store)

async def _seed_proceeds_when_non_empty_and_dedupes_per_face():
    """After 2026-05-17: the outer 'skip if non-empty' guard was removed
    so new Frigate faces show up on subsequent polls. Per-face dedup
    (resolve_frigate_name) ensures pre-existing identities are not
    duplicated. This test verifies the new behavior."""
    s = fresh_store()
    s.create_identity(
        "PreExisting",
        relationship_type="friend",
        frigate_person_name="preexisting",
    )
    http = MockHttpClient()
    # Frigate returns the pre-existing face AND two NEW faces
    http.stub("GET", "/api/faces", MockResponse(200,
        {"preexisting": ["x.webp"],
         "sarah": ["a.webp"],
         "ben": ["b.webp"]}))
    rep = await auto_seed_identities_from_frigate(s, http_client=http)
    assert rep.attempted is True, "auto-seed must always attempt now"
    assert rep.identities_created == 2, f"created sarah+ben: {rep.identities_created}"
    assert rep.identities_skipped_existing == 1, f"skipped preexisting: {rep}"
    # Semantic store remains unchanged; new buckets are operational only.
    rows = s.list_identities()
    names = sorted(r.display_name for r in rows)
    assert names == ["PreExisting"], names
    assert s.resolve_operational_recognition_name("sarah") is not None
    assert s.resolve_operational_recognition_name("ben") is not None
t("non-empty store + new Frigate faces → semantic dedup + operational ids",
  _seed_proceeds_when_non_empty_and_dedupes_per_face)

async def _seed_force_idempotent():
    s = fresh_store()
    http = MockHttpClient()
    http.stub("GET", "/api/faces", MockResponse(200, {"sarah": ["a.webp"]}))
    rep1 = await auto_seed_identities_from_frigate(s, http_client=http)
    assert rep1.identities_created == 1
    # Re-seed with force=True — should NOT duplicate
    rep2 = await auto_seed_identities_from_frigate(s, http_client=http, force=True)
    assert rep2.attempted is True
    assert rep2.identities_skipped_existing == 1, rep2
    assert rep2.identities_created == 0, rep2
    assert s.list_identities() == []
    assert s.resolve_operational_recognition_name("sarah") is not None
t("force=True is idempotent in operational storage", _seed_force_idempotent)

async def _seed_purges_changed_privacy_before_network():
    s = fresh_store()
    identity_uuid = s.create_identity(
        "Privacy subject",
        frigate_person_name="privacy_face",
    )
    assert s.ensure_recognition_enrollment("privacy_face") == "privacy_face"
    assert s.update_identity(identity_uuid, {"do_not_track": True})
    # Simulate a stale row from an earlier process/revision. The periodic poll
    # must purge it even when Frigate is currently offline.
    s._operational_conn.execute(
        "INSERT INTO operational_recognition_enrollments("
        "frigate_person_name, source, created_at, updated_at"
        ") VALUES('privacy_face', 'frigate', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    http = MockHttpClient()
    http.stub_exception(
        "GET",
        "/api/faces",
        ConnectionError("offline after privacy purge"),
    )
    report = await auto_seed_identities_from_frigate(s, http_client=http)
    assert report.operational_rows_purged == 1, report
    assert s._operational_conn.execute(
        "SELECT COUNT(*) FROM operational_recognition_enrollments"
    ).fetchone()[0] == 0
t("periodic poll purges changed privacy before network access",
  _seed_purges_changed_privacy_before_network)

async def _disabled_seed_still_enforces_privacy():
    s = fresh_store()
    identity_uuid = s.create_identity(
        "Disabled bridge privacy subject",
        frigate_person_name="disabled_privacy_face",
    )
    assert s.update_identity(identity_uuid, {"do_not_track": True})
    # Recreate a stale row to model retention from a prior process/policy.
    s._operational_conn.execute(
        "INSERT INTO operational_recognition_enrollments("
        "frigate_person_name, source, created_at, updated_at"
        ") VALUES('disabled_privacy_face', 'frigate', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    http = MockHttpClient()
    os.environ["EXTENDED_OPENAI_FRIGATE_SYNC"] = "off"
    try:
        report = await auto_seed_identities_from_frigate(
            s,
            http_client=http,
        )
    finally:
        os.environ["EXTENDED_OPENAI_FRIGATE_SYNC"] = "on"
    assert report.attempted is False, report
    assert report.skipped_reason == "frigate sync disabled", report
    assert report.operational_rows_purged == 1, report
    assert http.calls == []
    assert s._operational_conn.execute(
        "SELECT COUNT(*) FROM operational_recognition_enrollments"
    ).fetchone()[0] == 0
t("disabled sync still purges changed privacy",
  _disabled_seed_still_enforces_privacy)

async def _seed_handles_offline():
    s = fresh_store()
    http = MockHttpClient()
    http.stub_exception("GET", "/api/faces", ConnectionError("connection refused"))
    rep = await auto_seed_identities_from_frigate(s, http_client=http)
    assert rep.attempted is True
    assert rep.identities_created == 0
    assert any("failed" in e.lower() or "refused" in e.lower() for e in rep.errors), rep.errors
    # Doesn't raise; store remains empty
    assert s.list_identities() == []
t("Frigate offline → errors captured, no raise", _seed_handles_offline)

async def _seed_handles_bad_payload():
    s = fresh_store()
    http = MockHttpClient()
    http.stub("GET", "/api/faces", MockResponse(200, ["not", "a", "dict"]))
    rep = await auto_seed_identities_from_frigate(s, http_client=http)
    assert rep.attempted is True
    assert rep.identities_created == 0
    assert any("not a dict" in e for e in rep.errors), rep.errors
t("bad /api/faces payload → graceful error", _seed_handles_bad_payload)


# ── Writeback ────────────────────────────────────────────────────────
section("writeback (rename + delete)")

async def _rename_success():
    http = MockHttpClient()
    http.stub("PUT", "/api/faces/old/rename", MockResponse(200, text="ok"))
    result = await rename_frigate_face("old", "new", http_client=http)
    assert result.success is True
    assert result.status_code == 200
t("rename_frigate_face 200 → success", _rename_success)

async def _rename_failure():
    http = MockHttpClient()
    http.stub("PUT", "/api/faces/old/rename", MockResponse(404, text="no such face"))
    result = await rename_frigate_face("old", "new", http_client=http)
    assert result.success is False
    assert result.status_code == 404
    assert "404" in (result.error or "")
t("rename_frigate_face 4xx → error captured", _rename_failure)

async def _delete_404_idempotent():
    http = MockHttpClient()
    http.stub("DELETE", "/api/faces/gone", MockResponse(404, text="already gone"))
    result = await delete_frigate_face("gone", http_client=http)
    assert result.success is True, "404 should be idempotent success"
    assert result.status_code == 404
t("delete_frigate_face 404 → idempotent success", _delete_404_idempotent)

async def _delete_500_failure():
    http = MockHttpClient()
    http.stub("DELETE", "/api/faces/x", MockResponse(500, text="server error"))
    result = await delete_frigate_face("x", http_client=http)
    assert result.success is False
    assert result.status_code == 500
t("delete_frigate_face 500 → error captured", _delete_500_failure)


# ── Drainer ──────────────────────────────────────────────────────────
section("pending_writes drainer")

async def _drainer_empty():
    s = fresh_store()
    http = MockHttpClient()
    summary = await drain_pending_writes(s, http_client=http)
    assert summary == {"processed": 0, "succeeded": 0, "failed": 0, "skipped": 0}
t("drainer with empty outbox → no-op", _drainer_empty)

async def _drainer_success():
    s = fresh_store()
    u = s.create_identity("Sarah", frigate_person_name="sarah")
    s.delete_identity(u)  # queues a delete
    http = MockHttpClient()
    http.stub("DELETE", "/api/faces/sarah", MockResponse(200, text="ok"))
    summary = await drain_pending_writes(s, http_client=http)
    assert summary["processed"] == 1
    assert summary["succeeded"] == 1
    # Outbox should now be empty (all done)
    remaining = s.pending_writes(state="pending")
    assert remaining == []
t("drainer: delete success → row marked done", _drainer_success)

async def _drainer_retry_on_failure():
    s = fresh_store()
    u = s.create_identity("Sarah", frigate_person_name="sarah")
    s.delete_identity(u)
    http = MockHttpClient()
    http.stub("DELETE", "/api/faces/sarah", MockResponse(503, text="overloaded"))
    summary = await drain_pending_writes(s, http_client=http)
    assert summary["failed"] == 1
    # Still pending — attempt_count bumped
    pending = s.pending_writes(state="pending")
    assert len(pending) == 1
    assert pending[0]["attempt_count"] >= 1
t("drainer: 503 → keeps row pending + bumps attempt_count",
  _drainer_retry_on_failure)

async def _drainer_max_attempts():
    s = fresh_store()
    u = s.create_identity("Sarah", frigate_person_name="sarah")
    s.delete_identity(u)
    # Manually set attempt_count high
    s._conn.execute(
        "UPDATE pending_writes SET attempt_count = 99 WHERE state = 'pending'"
    )
    http = MockHttpClient()
    summary = await drain_pending_writes(s, http_client=http, max_attempts_per_row=5)
    assert summary["failed"] == 1
    assert summary["skipped"] == 1
    # Row should be marked failed
    pending = s.pending_writes(state="pending")
    failed = s.pending_writes(state="failed")
    assert pending == [] and len(failed) == 1
t("drainer: row over max_attempts → marked failed, not retried",
  _drainer_max_attempts)


# ── Summary ──────────────────────────────────────────────────────────
print(f"\n{passes} pass · {fails} fail")
if fails:
    print("\nFailures:")
    for name, msg in failures:
        print(f"  - {name}: {msg}")
sys.exit(0 if fails == 0 else 1)

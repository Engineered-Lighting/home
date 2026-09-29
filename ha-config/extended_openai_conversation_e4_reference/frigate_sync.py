"""Frigate face-library sync layer for identity_store.

Bridges identity_store.py (HA side) with Frigate's HTTP API. Two
flows:

1. **Operational enrollment poll**: pull Frigate's face library and
   retain only opaque source identifiers in the separate recognition
   database. It never creates semantic people, aliases, relationships,
   display labels, or model context. Legacy privacy flags remain a
   read-only ingress policy.

2. **Capability probe** (Slice 2 + AR2-2): on integration boot,
   one HTTP call to Frigate to verify:
   - Can we reach the API?
   - Does it expose `/api/faces`?
   - Does it expose the optional unmatched-face cluster events
     (US-1.01 — the discovery queue depends on this; if absent,
     the queue is empty after seed)
   Result is cached for the session + surfaced to the people tab
   as a status indicator.

3. **Writeback** (Slice 10, but signatures defined here so the
   pending_writes drainer has a target):
   - `rename_face(old_name, new_name)` → POST /api/faces/<old>/rename
   - `delete_face(name)` → DELETE /api/faces/<name>
   These are called by the drainer (Slice 11) when consuming the
   pending_writes outbox.

Disable
-------
Frigate synchronization is disabled by default. Set
`EXTENDED_OPENAI_FRIGATE_SYNC=on` only for the isolated legacy
recognition/enrollment workflow after an operator review. Any other value
keeps network polling, enrollment, and writeback as no-ops. A scheduled poll
still reapplies current privacy locally so disabling transport cannot retain an
opaque identifier that a newer policy now blocks.

Why a separate file
-------------------
identity_store.py is hermetic — pure SQLite, no HTTP. Frigate
sync is the IO-heavy translation layer. Splitting keeps the store
testable without mocking HTTP, and lets the sync be swapped out
(e.g., if Frigate moves to a different API shape).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

LOGGER = logging.getLogger(__name__)

FRIGATE_SYNC_DISABLED_ENV = "EXTENDED_OPENAI_FRIGATE_SYNC"

# Default Frigate base URL. Overridable per-call for tests + multi-host
# deploys. The HA-side address that the integration actually uses comes
# from this env var so it can be tuned without code changes.
FRIGATE_BASE_URL_ENV = "EXTENDED_OPENAI_FRIGATE_URL"
FRIGATE_BASE_URL_DEFAULT = "http://192.168.0.125:5000"

# How long to wait for the Frigate API. Auto-seed runs at integration
# boot — if Frigate is slow we don't want to block HA startup, so the
# timeout is short. Reconciliation/writeback can tolerate more.
FRIGATE_PROBE_TIMEOUT_S = 3.0
FRIGATE_SEED_TIMEOUT_S = 8.0
FRIGATE_WRITEBACK_TIMEOUT_S = 10.0


def is_disabled() -> bool:
    """Fail closed unless the legacy recognition bridge is explicitly enabled."""

    return os.environ.get(FRIGATE_SYNC_DISABLED_ENV, "off").strip().lower() != "on"


def base_url() -> str:
    return os.environ.get(FRIGATE_BASE_URL_ENV, FRIGATE_BASE_URL_DEFAULT).rstrip("/")


# ── Capability probe ────────────────────────────────────────────────


@dataclass
class FrigateCapabilities:
    """Result of probe_frigate_capabilities. Indicates what surfaces
    the Frigate instance supports. Used by the people tab to decide
    what UI to show + what features to enable."""
    reachable: bool = False
    version: Optional[str] = None
    face_library: bool = False          # /api/faces returns face → crops
    unmatched_events: bool = False      # cluster events for the discovery queue
    rename: bool = False                # /api/faces/<old>/rename supported
    delete: bool = False                # DELETE /api/faces/<name> supported
    last_probed_at: Optional[str] = None
    last_error: Optional[str] = None


async def probe_frigate_capabilities(
    *, http_client=None, frigate_url: Optional[str] = None,
) -> FrigateCapabilities:
    """One-time capability probe at integration boot. Always returns
    a FrigateCapabilities (with `reachable=False` if Frigate is down)
    — never raises. Caller treats absent capabilities as graceful
    degradation."""
    caps = FrigateCapabilities()
    if is_disabled():
        caps.last_error = "frigate sync disabled via env var"
        return caps
    url = (frigate_url or base_url()).rstrip("/")
    # Lazy-import httpx so this module can be imported standalone for
    # pytest without the dependency installed.
    if http_client is None:
        try:
            import httpx  # type: ignore[import]
        except ImportError:
            caps.last_error = "httpx not available"
            return caps
        # FIX (S-2 latent surfaced during scenario testing):
        # httpx.AsyncClient(...) calls load_verify_locations on the event
        # loop (SSL context init). HA's loop-blocking detector flags it.
        # Move client construction off-loop via asyncio.to_thread when
        # available (Python 3.9+); fall back to direct construction for
        # standalone-pytest contexts where there's no running loop.
        try:
            loop = asyncio.get_running_loop()
            http_client = await loop.run_in_executor(
                None, lambda: httpx.AsyncClient(timeout=FRIGATE_PROBE_TIMEOUT_S),
            )
        except RuntimeError:
            http_client = httpx.AsyncClient(timeout=FRIGATE_PROBE_TIMEOUT_S)
        owns_client = True
    else:
        owns_client = False
    try:
        # Version + reachability
        try:
            r = await http_client.get(f"{url}/api/version")
            if r.status_code == 200:
                caps.reachable = True
                caps.version = (r.text or "").strip()
        except Exception as e:
            caps.last_error = f"version probe failed: {e}"
            return caps

        # Face library
        try:
            r = await http_client.get(f"{url}/api/faces")
            if r.status_code == 200:
                payload = r.json()
                if isinstance(payload, dict):
                    caps.face_library = True
                    # If face library exists, we assume rename + delete also
                    # exist in the same API (matches Frigate 0.17+ behavior).
                    # Probing rename/delete without actually performing one
                    # is not straightforward, so we infer from the library
                    # being present. False positives here just mean the
                    # writeback drainer will get 404s on attempted writes,
                    # which it handles gracefully.
                    caps.rename = True
                    caps.delete = True
        except Exception as e:
            LOGGER.debug("face library probe failed: %s", e)

        # Unmatched-face events (the discovery queue surface — AR2-2)
        # Frigate publishes face events on MQTT primarily; the REST API
        # exposes /api/events with filters. A discovery-queue caller can
        # poll /api/events?has_clip=false&label=face for unmatched. We
        # probe by attempting one such call.
        try:
            r = await http_client.get(
                f"{url}/api/events",
                params={"label": "face", "limit": 1, "has_clip": "false"},
            )
            # Some Frigate versions return 200 with [] for no matches, others
            # 404 if the filter isn't supported. We accept any 2xx as "yes".
            if 200 <= r.status_code < 300:
                caps.unmatched_events = True
        except Exception as e:
            LOGGER.debug("unmatched events probe failed: %s", e)

        from datetime import datetime, timezone
        caps.last_probed_at = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        return caps
    finally:
        if owns_client:
            await http_client.aclose()


# ── Auto-seed ────────────────────────────────────────────────────────


@dataclass
class SeedReport:
    """Outcome of a seed run for telemetry + the people-tab onboarding
    flow (AR2-3 cold-start UX)."""
    attempted: bool = False
    skipped_reason: Optional[str] = None
    frigate_faces_found: int = 0
    identities_created: int = 0
    identities_skipped_existing: int = 0
    identities_skipped_policy: int = 0
    operational_rows_purged: int = 0
    errors: list = field(default_factory=list)


async def auto_seed_identities_from_frigate(
    store,                              # IdentityStore (typed loosely to avoid circular import)
    *,
    http_client=None,
    frigate_url: Optional[str] = None,
    force: bool = False,
) -> SeedReport:
    """Pull `/api/faces` into isolated operational recognition storage.

    Idempotent: existing semantic or operational names are skipped. New
    Frigate buckets never become semantic identity rows.

    `force=True` is now a no-op semantically (kept for back-compat
    with tests); the function always proceeds when not disabled.

    Never raises — errors collected in the report for surfacing in
    the people tab.

    Changed in 2026-05-17: dropped the "skip if store non-empty"
    early-return. Previously, identity_store was seeded once at
    first boot then never updated, so new Frigate face buckets
    (e.g., user trains a new person via Frigate's UI) never showed
    up in the home app. The per-face `resolve_frigate_name` check
    already prevents duplicates; the outer guard was an unnecessary
    optimization.
    """
    report = SeedReport()
    if store is None:
        report.skipped_reason = "no store passed"
        return report
    try:
        report.operational_rows_purged = (
            store.purge_blocked_operational_recognition()
        )
    except Exception as exc:  # noqa: BLE001
        # Do not fetch/process new names if current privacy cannot first be
        # enforced against rows left by an earlier poll.
        report.errors.append(
            "operational recognition privacy purge failed: "
            f"{type(exc).__name__}"
        )
        return report
    if is_disabled():
        # Disabling network synchronization must not disable enforcement of a
        # newer privacy decision against identifiers retained by an older run.
        report.skipped_reason = "frigate sync disabled"
        return report
    report.attempted = True

    url = (frigate_url or base_url()).rstrip("/")
    if http_client is None:
        try:
            import httpx  # type: ignore[import]
        except ImportError:
            report.errors.append("httpx not installed")
            return report
        http_client = httpx.AsyncClient(timeout=FRIGATE_SEED_TIMEOUT_S)
        owns_client = True
    else:
        owns_client = False
    try:
        try:
            r = await http_client.get(f"{url}/api/faces")
        except Exception as e:
            report.errors.append(f"GET /api/faces failed: {e}")
            return report
        if r.status_code != 200:
            report.errors.append(f"GET /api/faces returned {r.status_code}")
            return report
        try:
            payload = r.json()
        except Exception as e:
            report.errors.append(f"face library JSON parse failed: {e}")
            return report
        if not isinstance(payload, dict):
            report.errors.append(f"face library payload not a dict (got {type(payload).__name__})")
            return report

        # Filter out the `train` bucket which Frigate uses for training
        # crops not yet assigned to a person.
        face_map = {k: v for k, v in payload.items() if k != "train" and isinstance(v, list)}
        report.frigate_faces_found = len(face_map)

        for frigate_name, crops in face_map.items():
            del crops
            # The fenced semantic store is deliberately absent from
            # WorldState, but its privacy flags still form a read-only ingress
            # policy. In particular, do-not-track/ignored subjects must never
            # be copied into the isolated operational database.
            policy = store.legacy_recognition_persistence_policy(
                frigate_name
            )
            if policy == "blocked":
                report.identities_skipped_policy += 1
                continue
            existing_operational = (
                store.resolve_operational_recognition_name(frigate_name)
            )
            existing_semantic = store.resolve_frigate_name(frigate_name)
            if (
                existing_operational is not None
                or existing_semantic is not None
            ):
                report.identities_skipped_existing += 1
                continue
            try:
                persisted_name = store.ensure_recognition_enrollment(
                    frigate_name,
                    actor="frigate_sync",
                )
                if persisted_name:
                    report.identities_created += 1
                else:
                    report.identities_skipped_policy += 1
            except Exception as e:
                # Do not copy a potentially private face-library label into
                # logs, metrics, or user-facing error payloads.
                report.errors.append(
                    f"operational recognition enrollment failed: "
                    f"{type(e).__name__}"
                )
        return report
    finally:
        if owns_client:
            await http_client.aclose()


# ── Writeback (signatures used by Slices 10 + 11) ───────────────────


@dataclass
class WritebackResult:
    success: bool = False
    status_code: Optional[int] = None
    error: Optional[str] = None


async def rename_frigate_face(
    old_name: str, new_name: str,
    *, http_client=None, frigate_url: Optional[str] = None,
) -> WritebackResult:
    """POST /api/faces/<old_name>/rename — Frigate 0.17+ supports this.
    Called by the pending_writes drainer (Slice 11) when an identity is
    renamed in the people tab and `our store authoritative` (locked
    decision #2) pushes the rename back to Frigate."""
    if is_disabled():
        return WritebackResult(success=False, error="frigate sync disabled")
    url = (frigate_url or base_url()).rstrip("/")
    if http_client is None:
        try:
            import httpx  # type: ignore[import]
        except ImportError:
            return WritebackResult(success=False, error="httpx not installed")
        http_client = httpx.AsyncClient(timeout=FRIGATE_WRITEBACK_TIMEOUT_S)
        owns_client = True
    else:
        owns_client = False
    try:
        try:
            # BUG (S3 found in scenario testing): Frigate's rename endpoint
            # requires PUT, not POST. Sending POST returned 405 Method Not
            # Allowed with `allow: PUT`. The drainer was running, hitting
            # the API, getting 405, marking the pending_write as failed,
            # and the rename never took effect in Frigate. Verified live:
            # `curl -X POST .../api/faces/david/rename?new_name=davidson`
            # → 405; `curl -X PUT` → 200.
            r = await http_client.put(
                f"{url}/api/faces/{old_name}/rename",
                params={"new_name": new_name},
            )
        except Exception as e:
            return WritebackResult(success=False, error=str(e))
        out = WritebackResult(status_code=r.status_code)
        if 200 <= r.status_code < 300:
            out.success = True
        else:
            out.error = f"HTTP {r.status_code}: {r.text[:200]}"
        return out
    finally:
        if owns_client:
            await http_client.aclose()


async def delete_frigate_face(
    name: str,
    *, http_client=None, frigate_url: Optional[str] = None,
) -> WritebackResult:
    """DELETE /api/faces/<name> — removes the person + their crops
    from Frigate. Called when an identity is hard-deleted in our store
    and the cascade (Slice 11) drains the pending_writes outbox."""
    if is_disabled():
        return WritebackResult(success=False, error="frigate sync disabled")
    url = (frigate_url or base_url()).rstrip("/")
    if http_client is None:
        try:
            import httpx  # type: ignore[import]
        except ImportError:
            return WritebackResult(success=False, error="httpx not installed")
        http_client = httpx.AsyncClient(timeout=FRIGATE_WRITEBACK_TIMEOUT_S)
        owns_client = True
    else:
        owns_client = False
    try:
        try:
            r = await http_client.delete(f"{url}/api/faces/{name}")
        except Exception as e:
            return WritebackResult(success=False, error=str(e))
        out = WritebackResult(status_code=r.status_code)
        if 200 <= r.status_code < 300 or r.status_code == 404:
            # 404 = already gone; idempotent success
            out.success = True
        else:
            out.error = f"HTTP {r.status_code}: {r.text[:200]}"
        return out
    finally:
        if owns_client:
            await http_client.aclose()


# ── Pending-writes drainer (used by integration setup in Slice 11) ──


async def drain_pending_writes(
    store,
    *, http_client=None, frigate_url: Optional[str] = None,
    max_attempts_per_row: int = 5,
) -> dict:
    """Single-pass drain of the pending_writes outbox. Marks rows
    in_flight → done or failed. Called periodically by the
    integration (e.g., every 30s). Returns a summary dict for logging.

    Failure handling: rows that have been attempted ≥ max_attempts_per_row
    are left in 'failed' state — surfaced in the people tab as
    "Frigate sync stuck" so the user can intervene (retry / cancel)."""
    summary = {"processed": 0, "succeeded": 0, "failed": 0, "skipped": 0}
    if is_disabled() or store is None:
        return summary
    if getattr(store, "semantic_writes_frozen", True):
        summary["capability_disabled"] = True
        summary["reason"] = getattr(
            store,
            "legacy_authority_state",
            "legacy_frozen",
        )
        return summary
    pending = store.pending_writes(state="pending", limit=50)
    for row in pending:
        if row["attempt_count"] >= max_attempts_per_row:
            store.mark_pending_write(row["id"], state="failed",
                                     error=f"exceeded {max_attempts_per_row} attempts")
            summary["failed"] += 1
            summary["skipped"] += 1
            continue
        store.mark_pending_write(row["id"], state="in_flight")
        op = row["op"]
        payload = row["payload"]
        result: WritebackResult
        try:
            if op == "rename":
                result = await rename_frigate_face(
                    payload["old_name"], payload["new_name"],
                    http_client=http_client, frigate_url=frigate_url,
                )
            elif op == "delete":
                result = await delete_frigate_face(
                    payload["person_name"],
                    http_client=http_client, frigate_url=frigate_url,
                )
            else:
                result = WritebackResult(success=False, error=f"unknown op {op!r}")
        except Exception as e:
            result = WritebackResult(success=False, error=str(e))
        summary["processed"] += 1
        if result.success:
            store.mark_pending_write(row["id"], state="done")
            summary["succeeded"] += 1
        else:
            store.mark_pending_write(row["id"], state="pending", error=result.error)
            summary["failed"] += 1
    return summary

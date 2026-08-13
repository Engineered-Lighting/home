"""Home Assistant lifecycle adapter for the pure edge components."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import secrets
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback

from .const import EVENT_CONVERSATION_FINISHED
from .delivery import DeliveryCoordinator, DeliveryResult
from .model import EdgePolicy
from .outbox import EdgeOutbox
from .transport import MutualTLSTransport

_LOGGER = logging.getLogger(__name__)


class CorePrivacyPolicyUnavailable(RuntimeError):
    """The receiver cannot currently provide its executable privacy policy."""


class EdgeRuntime:
    """Subscribe, normalize, spool, and deliver reviewed HA events."""

    def __init__(
        self,
        hass: HomeAssistant,
        policy: EdgePolicy,
        outbox: EdgeOutbox,
        transport: MutualTLSTransport,
        *,
        batch_size: int,
        privacy_receipt_path: Path | None = None,
    ) -> None:
        self.hass = hass
        self._configured_policy = policy
        self.policy = policy
        self.outbox = outbox
        self.transport = transport
        self.coordinator = DeliveryCoordinator(
            outbox,
            transport,
            batch_size=batch_size,
            run_sync=hass.async_add_executor_job,
        )
        self._unsubscribers: list[Any] = []
        self._delivery_lock = asyncio.Lock()
        self._privacy_policy_lock = asyncio.Lock()
        self._privacy_policy_digest: str | None = None
        self._closed = False
        self._runtime_started = False
        self._privacy_policy_current = False
        self._privacy_receipt_path = privacy_receipt_path
        self.last_delivery_result = DeliveryResult("not_started")

    async def async_start(self) -> None:
        await self.hass.async_add_executor_job(self.outbox.initialize)
        # ssl.create_default_context, load_verify_locations, load_cert_chain,
        # and credential reads all perform blocking filesystem work. HA 2026.8
        # detects these calls when they occur on the event loop.
        await self.hass.async_add_executor_job(self.transport.prepare)
        await self.transport.start()
        if not await self._async_refresh_privacy_policy(log_failure=False):
            raise CorePrivacyPolicyUnavailable(
                "Core privacy policy is temporarily unavailable"
            )

        # Subscribe before taking the startup snapshot.  A transition racing
        # the snapshot remains a distinct, time-stamped envelope; the core is
        # responsible for event-time projection and the snapshot is explicitly
        # labelled snapshot-only.
        self._unsubscribers.append(
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._async_handle_event)
        )
        self._unsubscribers.append(
            self.hass.bus.async_listen(
                EVENT_CONVERSATION_FINISHED, self._async_handle_event
            )
        )

        await self.hass.async_add_executor_job(self.outbox.begin_runtime)
        self._runtime_started = True

        for entity_id in sorted(self.policy.entity_ids):
            if entity_id in self.policy.blocked_entity_ids:
                continue
            state = self.hass.states.get(entity_id)
            if state is not None:
                await self._async_ingest(
                    EVENT_STATE_CHANGED,
                    {
                        "entity_id": entity_id,
                        "old_state": None,
                        "new_state": state,
                        "_snapshot": True,
                    },
                    getattr(state, "context", None),
                    getattr(state, "last_updated", None),
                )
        self.hass.async_create_task(self.async_deliver_once())

    @callback
    def _async_handle_event(self, event: Event) -> None:
        if self._closed:
            return
        self.hass.async_create_task(
            self._async_ingest(
                event.event_type,
                event.data,
                event.context,
                getattr(event, "time_fired", None),
            )
        )

    async def _async_ingest(
        self, event_type: str, data: Any, context: Any, occurred_at: Any
    ) -> None:
        if event_type == EVENT_STATE_CHANGED:
            entity_id = (
                str(data.get("entity_id") or "").strip().lower()
                if isinstance(data, Mapping)
                else ""
            )
            if (
                entity_id not in self._configured_policy.entity_ids
                or entity_id in self._configured_policy.blocked_entity_ids
            ):
                # The HA state bus is global. Reject unrelated entity IDs using
                # only static configuration before any network call or queueing.
                return
        # Keep the accepted policy stable through normalization and durable
        # enqueue. A new block cannot purge and then be bypassed by an event
        # that was normalized under the preceding policy generation.
        async with self._privacy_policy_lock:
            if not await self._async_refresh_privacy_policy_locked():
                # No source content is persisted while the executable privacy
                # policy cannot be verified.
                await self.hass.async_add_executor_job(
                    self.outbox.quarantine,
                    event_type,
                    "privacy_policy_unavailable",
                    {"data_type": type(data).__name__},
                )
                return
            try:
                normalized = self.policy.normalize_event(
                    event_type, data, context, occurred_at=occurred_at
                )
            except Exception as exc:  # malformed reviewed input is quarantined
                await self.hass.async_add_executor_job(
                    self.outbox.quarantine,
                    event_type,
                    f"normalize_{type(exc).__name__}",
                    {"data_type": type(data).__name__},
                )
                return
            if normalized is None:
                return
            try:
                await self.hass.async_add_executor_job(self.outbox.enqueue, normalized)
            except Exception as exc:
                _LOGGER.error(
                    "Home Agent Edge failed to spool %s: %s",
                    event_type,
                    type(exc).__name__,
                )
                await self.hass.async_add_executor_job(
                    self.outbox.quarantine,
                    event_type,
                    f"spool_{type(exc).__name__}",
                    {"normalized_type": normalized.get("source_event_type")},
                )
                return
        self.hass.async_create_task(self.async_deliver_once())

    async def async_deliver_once(self, _now: Any = None) -> DeliveryResult:
        if self._closed or self._delivery_lock.locked():
            return DeliveryResult("busy" if not self._closed else "closed")
        async with self._delivery_lock:
            if not await self._async_refresh_privacy_policy():
                return DeliveryResult(
                    "privacy_policy_unavailable",
                    error_code="privacy_policy_unavailable",
                )
            self.last_delivery_result = await self.hass.async_add_executor_job(
                self._readiness_probe
            )
            if self.last_delivery_result.status != "ready":
                return self.last_delivery_result
            # Transport is async; coordinator does synchronous, short SQLite
            # operations around it.  Those are serialized and bounded to a
            # small batch.
            self.last_delivery_result = await self.coordinator.deliver_once()
            return self.last_delivery_result

    def _readiness_probe(self) -> DeliveryResult:
        try:
            self.outbox.stats()
            return DeliveryResult("ready")
        except Exception as exc:
            return DeliveryResult("spool_unavailable", error_code=type(exc).__name__)

    async def async_maintain(self, _now: Any = None) -> None:
        if not self._closed:
            await self._async_refresh_privacy_policy()
            await self.hass.async_add_executor_job(self.outbox.heartbeat)
            await self.hass.async_add_executor_job(self.outbox.maintain)

    async def _async_refresh_privacy_policy(self, *, log_failure: bool = True) -> bool:
        if self._closed:
            return False
        # Fetch, validate, purge, and assignment are one serialized transition.
        # This prevents a slower older response from rolling back a newer policy.
        async with self._privacy_policy_lock:
            return await self._async_refresh_privacy_policy_locked(
                log_failure=log_failure
            )

    async def _async_refresh_privacy_policy_locked(
        self, *, log_failure: bool = True
    ) -> bool:
        """Refresh while the caller owns ``_privacy_policy_lock``."""

        try:
            document = await self.transport.fetch_privacy_policy()
            updated = self._configured_policy.with_core_privacy_policy(document)
            digest = str(document["policy_digest"])
            if digest != self._privacy_policy_digest:
                purged_payload_count = await self.hass.async_add_executor_job(
                    self.outbox.suppress_privacy_subjects,
                    set(updated.blocked_entity_ids),
                    set(updated.blocked_user_ids),
                )
                if type(purged_payload_count) is not int or purged_payload_count < 0:
                    raise ValueError("edge privacy purge result is invalid")
                self.policy = updated
                self._privacy_policy_digest = digest
                purge_result = "purged_before_accept"
            else:
                purged_payload_count = 0
                purge_result = "unchanged_verified_policy"
            if self._privacy_receipt_path is not None:
                await self.hass.async_add_executor_job(
                    _write_privacy_policy_receipt,
                    self._privacy_receipt_path,
                    digest,
                    len(updated.blocked_entity_ids),
                    len(updated.blocked_user_ids),
                    purged_payload_count,
                    purge_result,
                )
            self._privacy_policy_current = True
            return True
        except Exception as exc:
            self._privacy_policy_current = False
            if log_failure:
                _LOGGER.error(
                    "Home Agent Edge privacy policy refresh failed closed: %s",
                    type(exc).__name__,
                )
            return False

    async def async_close(self) -> None:
        self._closed = True
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        await self.transport.close()
        if self._runtime_started:
            await self.hass.async_add_executor_job(self.outbox.end_runtime)
            self._runtime_started = False
        await self.hass.async_add_executor_job(self.outbox.close)


def _write_privacy_policy_receipt(
    path: Path,
    policy_digest: str,
    blocked_entity_count: int,
    blocked_user_count: int,
    purged_payload_count: int,
    purge_result: str,
) -> None:
    """Atomically persist only content-free proof of the accepted edge policy."""

    if (
        not isinstance(policy_digest, str)
        or len(policy_digest) != 64
        or any(character not in "0123456789abcdef" for character in policy_digest)
        or type(blocked_entity_count) is not int
        or blocked_entity_count < 0
        or type(blocked_user_count) is not int
        or blocked_user_count < 0
        or type(purged_payload_count) is not int
        or purged_payload_count < 0
        or purge_result not in {"purged_before_accept", "unchanged_verified_policy"}
    ):
        raise ValueError("edge privacy receipt input is invalid")
    parent = path.parent
    parent_details = parent.lstat()
    if not parent.is_dir() or parent_details.st_mode & 0o022:
        raise PermissionError("edge privacy receipt directory is unsafe")
    if path.exists() or path.is_symlink():
        details = path.lstat()
        if not path.is_file() or details.st_nlink != 1 or details.st_mode & 0o077:
            raise PermissionError("edge privacy receipt is unsafe")
    value = {
        "contract": "home-agent-edge-privacy-policy-receipt-v1",
        "policy_digest": policy_digest,
        "blocked_entity_count": blocked_entity_count,
        "blocked_user_count": blocked_user_count,
        "purged_payload_count": purged_payload_count,
        "purge_result": purge_result,
        "refreshed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    raw = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.new.{secrets.token_hex(12)}")
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

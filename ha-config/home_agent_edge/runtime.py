"""Home Assistant lifecycle adapter for the pure edge components."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback

from .const import EVENT_CONVERSATION_FINISHED
from .delivery import DeliveryCoordinator, DeliveryResult
from .model import EdgePolicy
from .outbox import EdgeOutbox
from .transport import MutualTLSTransport

_LOGGER = logging.getLogger(__name__)


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
        self._privacy_policy_current = False
        self.last_delivery_result = DeliveryResult("not_started")

    async def async_start(self) -> None:
        await self.hass.async_add_executor_job(self.outbox.initialize)
        await self.transport.start()
        if not await self._async_refresh_privacy_policy():
            raise RuntimeError("Core privacy policy is unavailable")

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

        for entity_id in sorted(self.policy.entity_ids):
            if entity_id in self.policy.blocked_entity_ids:
                continue
            state = self.hass.states.get(entity_id)
            if state is not None:
                await self._async_ingest(
                    EVENT_STATE_CHANGED,
                    {"entity_id": entity_id, "old_state": None, "new_state": state, "_snapshot": True},
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

    async def _async_refresh_privacy_policy(self) -> bool:
        if self._closed:
            return False
        # Fetch, validate, purge, and assignment are one serialized transition.
        # This prevents a slower older response from rolling back a newer policy.
        async with self._privacy_policy_lock:
            return await self._async_refresh_privacy_policy_locked()

    async def _async_refresh_privacy_policy_locked(self) -> bool:
        """Refresh while the caller owns ``_privacy_policy_lock``."""

        try:
            document = await self.transport.fetch_privacy_policy()
            updated = self._configured_policy.with_core_privacy_policy(document)
            digest = str(document["policy_digest"])
            if digest != self._privacy_policy_digest:
                await self.hass.async_add_executor_job(
                    self.outbox.suppress_privacy_subjects,
                    set(updated.blocked_entity_ids),
                    set(updated.blocked_user_ids),
                )
                self.policy = updated
                self._privacy_policy_digest = digest
            self._privacy_policy_current = True
            return True
        except Exception as exc:
            self._privacy_policy_current = False
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
        await self.hass.async_add_executor_job(self.outbox.end_runtime)
        await self.hass.async_add_executor_job(self.outbox.close)

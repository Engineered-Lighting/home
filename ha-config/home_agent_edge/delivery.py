"""Acknowledged delivery contract for Home Agent Edge."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, TypeVar

from .outbox import EdgeOutbox, OutboxBatch


class BatchTransport(Protocol):
    async def send(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


_T = TypeVar("_T")
SyncRunner = Callable[[Callable[..., _T], Any], Awaitable[_T]]


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    status: str
    first_sequence: int | None = None
    last_sequence: int | None = None
    acknowledged_through: int | None = None
    error_code: str | None = None


class DeliveryCoordinator:
    """Deliver one contiguous batch and accept only bounded acknowledgements."""

    def __init__(
        self,
        outbox: EdgeOutbox,
        transport: BatchTransport,
        *,
        batch_size: int = 100,
        run_sync: SyncRunner | None = None,
    ) -> None:
        self.outbox = outbox
        self.transport = transport
        self.batch_size = batch_size
        self._run_sync = run_sync

    async def deliver_once(self) -> DeliveryResult:
        batch = await self._call(self.outbox.read_batch, self.batch_size)
        if batch is None:
            return DeliveryResult("idle")
        try:
            response = await self.transport.send(batch.as_request())
            acknowledged = self._parse_acknowledgement(response, batch)
            await self._call(self.outbox.acknowledge, acknowledged, batch)
            return DeliveryResult(
                "acknowledged",
                batch.first_sequence,
                batch.last_sequence,
                acknowledged,
            )
        except Exception as exc:
            code = type(exc).__name__
            await self._call(self.outbox.mark_failure, batch, code)
            return DeliveryResult(
                "retry_scheduled",
                batch.first_sequence,
                batch.last_sequence,
                error_code=code,
            )

    async def _call(self, function: Callable[..., _T], *args: Any) -> _T:
        if self._run_sync is None:
            return function(*args)
        return await self._run_sync(function, *args)

    @staticmethod
    def _parse_acknowledgement(
        response: Mapping[str, Any], batch: OutboxBatch
    ) -> int:
        if not isinstance(response, Mapping):
            raise ValueError("delivery response must be an object")
        for count_name in ("accepted", "duplicates", "quarantined"):
            raw_count = response.get(count_name)
            if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
                raise ValueError(f"invalid {count_name} count")
        if sum(int(response[name]) for name in ("accepted", "duplicates", "quarantined")) != len(batch.items):
            raise ValueError("receiver result count does not match delivered envelope count")
        acknowledgements = response.get("acknowledgements")
        if not isinstance(acknowledgements, Mapping):
            raise ValueError("missing acknowledgements map")
        raw = acknowledgements.get(batch.acknowledgement_key())
        if isinstance(raw, bool):
            raise ValueError("acknowledged_through must be an integer")
        try:
            acknowledged = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("missing acknowledged_through") from exc
        if not batch.first_sequence <= acknowledged <= batch.last_sequence:
            raise ValueError("acknowledgement falls outside the delivered batch")
        if acknowledged not in {item.sequence_end for item in batch.items}:
            raise ValueError("acknowledgement splits an envelope range")
        return acknowledged

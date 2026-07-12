from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Literal

from sqlalchemy import func, select

from . import schema

DISK_WARN_FREE_PERCENT = 20.0
DISK_STOP_OPTIONAL_FREE_PERCENT = 15.0
DISK_READ_ONLY_FREE_PERCENT = 10.0
INGEST_DAILY_EVENT_ALERT = 1_000
INGEST_WEEKLY_BYTE_ALERT = 100 * 1024 * 1024

_RETIRED_LEGACY_IDENTITY_IMPORT_EXACT_PATHS = frozenset(
    {
        "/v1/people",
        "/v1/people/verify-reviewed",
        "/v1/people/legacy-role-labels",
        "/v1/people/legacy-relationship-candidates",
    }
)
_RETIRED_LEGACY_IDENTITY_IMPORT_PERSON_SUFFIXES = frozenset(
    {"aliases", "recognition-bindings", "privacy-directives", "status-import"}
)


@dataclass(frozen=True, slots=True)
class DiskBudget:
    state: Literal["normal", "warn", "stop_optional", "read_only", "unavailable"]
    free_percent: float | None
    free_bytes: int | None
    total_bytes: int | None

    @property
    def optional_work_allowed(self) -> bool:
        return self.state in {"normal", "warn"}

    @property
    def general_writes_allowed(self) -> bool:
        return self.state in {"normal", "warn", "stop_optional"}

    @property
    def ready(self) -> bool:
        return self.state not in {"read_only", "unavailable"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "free_percent": self.free_percent,
            "free_bytes": self.free_bytes,
            "total_bytes": self.total_bytes,
            "warn_at_percent": DISK_WARN_FREE_PERCENT,
            "stop_optional_at_percent": DISK_STOP_OPTIONAL_FREE_PERCENT,
            "read_only_at_percent": DISK_READ_ONLY_FREE_PERCENT,
            "optional_work_allowed": self.optional_work_allowed,
            "general_writes_allowed": self.general_writes_allowed,
        }


def inspect_disk_budget(
    path: Path, *, disk_usage: Callable[[Path], Any] = shutil.disk_usage
) -> DiskBudget:
    try:
        usage = disk_usage(path)
        total, free = int(usage.total), int(usage.free)
        if total <= 0 or free < 0:
            raise ValueError("invalid disk usage")
        free_percent = free * 100.0 / total
    except (OSError, ValueError, TypeError, AttributeError):
        return DiskBudget("unavailable", None, None, None)
    if free_percent <= DISK_READ_ONLY_FREE_PERCENT:
        state = "read_only"
    elif free_percent <= DISK_STOP_OPTIONAL_FREE_PERCENT:
        state = "stop_optional"
    elif free_percent <= DISK_WARN_FREE_PERCENT:
        state = "warn"
    else:
        state = "normal"
    return DiskBudget(state, round(free_percent, 3), free, total)


async def resource_budget_snapshot(
    database, *, monitor_path: Path, include_ingest_metrics: bool
) -> dict[str, Any]:
    disk = inspect_disk_budget(monitor_path)
    ingest_status, daily_events, weekly_bytes = "not_applicable", 0, 0
    alerts: list[str] = []
    if include_ingest_metrics:
        try:
            now = datetime.now(UTC)
            relevant = schema.envelopes.c.event_type.in_(["location_fix", "snapshot"])
            async with database.transaction() as connection:
                row = (
                    (
                        await connection.execute(
                            select(
                                func.count().filter(
                                    relevant,
                                    schema.envelopes.c.ingested_at
                                    >= now - timedelta(days=1),
                                ).label("daily_events"),
                                func.coalesce(
                                    func.sum(schema.envelopes.c.payload_bytes).filter(
                                        relevant,
                                        schema.envelopes.c.ingested_at
                                        >= now - timedelta(days=7),
                                    ),
                                    0,
                                ).label("weekly_bytes"),
                            )
                        )
                    ).mappings().one()
                )
            daily_events = int(row["daily_events"])
            weekly_bytes = int(row["weekly_bytes"])
            ingest_status = "ok"
            if daily_events > INGEST_DAILY_EVENT_ALERT:
                alerts.append("ingest_daily_events_high")
            if weekly_bytes > INGEST_WEEKLY_BYTE_ALERT:
                alerts.append("ingest_weekly_bytes_high")
        except Exception:
            ingest_status = "unavailable"
            alerts.append("ingest_budget_unavailable")
    if disk.state != "normal":
        alerts.append(f"disk_{disk.state}")
    return {
        "status": "degraded" if alerts else "ok",
        "ready": disk.ready and ingest_status != "unavailable",
        "disk": disk.as_dict(),
        "ingest": {
            "status": ingest_status,
            "location_events_24h": daily_events,
            "location_bytes_7d": weekly_bytes,
            "event_alert_above": INGEST_DAILY_EVENT_ALERT,
            "byte_alert_above": INGEST_WEEKLY_BYTE_ALERT,
        },
        "alerts": alerts,
    }


def is_privacy_essential_write(path: str) -> bool:
    return (
        path.startswith("/v1/erasure-requests/")
        or path.startswith("/v1/preferences/")
        or path == "/v1/principal-binding-request/cancel"
        or path == "/v1/forget-preview"
        or (path.startswith("/v1/facts/") and path.endswith("/forget-preview"))
    )


def is_retired_legacy_identity_import(path: str) -> bool:
    """Identify exact no-write tombstones without widening a middleware bypass."""

    if path in _RETIRED_LEGACY_IDENTITY_IMPORT_EXACT_PATHS:
        return True
    parts = path.split("/")
    return (
        len(parts) == 5
        and parts[:3] == ["", "v1", "people"]
        and bool(parts[3])
        and parts[4] in _RETIRED_LEGACY_IDENTITY_IMPORT_PERSON_SUFFIXES
    )

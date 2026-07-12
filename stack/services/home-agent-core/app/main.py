from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .api import ingest_router, semantic_router
from .config import Settings
from .db import Database
from .errors import DomainError
from .ledger import EncryptedErasureLedger
from .models import HealthView
from .restore import RestoreQuarantineGate, outbox_health
from .resources import (
    inspect_disk_budget,
    is_privacy_essential_write,
    resource_budget_snapshot,
)
from .rollout import RolloutAuthorizationGate
from .spool import DisabledRuntimeSpool, EncryptedRuntimeSpool
from .store import CoreStore
from .worker import DurableWorker


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # fail closed when required secrets are absent
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    database = Database(settings.async_database_url())
    operator_database = (
        Database(settings.async_operator_database_url())
        if settings.operator_database_url is not None
        and settings.role in {"api", "all"}
        else None
    )
    spool = (
        EncryptedRuntimeSpool(
            settings.runtime_spool_path,
            settings.decoded_spool_key(),
            ttl_seconds=settings.runtime_ttl_seconds,
            max_bytes=settings.runtime_max_bytes,
        )
        if settings.role in {"ingest", "worker", "all"}
        and settings.runtime_spool_key is not None
        else DisabledRuntimeSpool()
    )
    store = (
        CoreStore(database, spool, settings)
        if settings.knowledge_encryption_key is not None
        else None
    )
    operator_store = (
        CoreStore(operator_database, spool, settings)
        if operator_database is not None
        and settings.knowledge_encryption_key is not None
        else None
    )
    ledger = (
        EncryptedErasureLedger(
            settings.erasure_ledger_path,
            settings.decoded_erasure_ledger_key(),
            head_path=settings.erasure_ledger_head_path,
        )
        if settings.role in {"worker", "all"}
        else None
    )
    worker = (
        DurableWorker(
            database,
            ledger,
            spool,
            claim_lease_seconds=settings.outbox_claim_lease_seconds,
            poll_seconds=settings.outbox_poll_seconds,
        )
        if ledger is not None
        else None
    )
    restore_gate = RestoreQuarantineGate(
        database,
        settings.erasure_ledger_head_path,
        cache_seconds=settings.restore_gate_cache_seconds,
    )
    rollout_gate = RolloutAuthorizationGate(database, settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        stop = asyncio.Event()
        task: asyncio.Task | None = None
        revision = await application.state.database.migration_revision()
        if revision != settings.readiness_migration:
            try:
                spool.close()
            finally:
                if operator_database is not None:
                    await operator_database.close()
                await database.close()
            raise RuntimeError(
                "database migration mismatch: expected "
                f"{settings.readiness_migration}, received {revision or 'unknown'}"
            )
        if operator_database is not None:
            operator_revision = await operator_database.migration_revision()
            if operator_revision != settings.readiness_migration:
                try:
                    spool.close()
                finally:
                    await operator_database.close()
                    await database.close()
                raise RuntimeError(
                    "operator database migration mismatch: expected "
                    f"{settings.readiness_migration}, received "
                    f"{operator_revision or 'unknown'}"
                )
        rollout_status = await application.state.rollout_gate.status(force=True)
        if not rollout_status.authorized:
            try:
                spool.close()
            finally:
                if operator_database is not None:
                    await operator_database.close()
                await database.close()
            raise RuntimeError(
                "rollout authorization rejected: " + rollout_status.code
            )
        if worker is not None:
            task = asyncio.create_task(worker.run(stop))
        try:
            yield
        finally:
            stop.set()
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            spool.close()
            if operator_database is not None:
                await operator_database.close()
            await database.close()

    application = FastAPI(
        title="Home Agent Core",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.database = database
    application.state.operator_database = operator_database
    application.state.spool = spool
    application.state.store = store
    application.state.operator_store = operator_store
    application.state.ledger = ledger
    application.state.restore_gate = restore_gate
    application.state.rollout_gate = rollout_gate

    @application.middleware("http")
    async def resource_budget_guard(request: Request, call_next):
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.url.path not in {"/healthz", "/readyz"}
            and settings.role in {"api", "ingest", "all"}
        ):
            disk = inspect_disk_budget(settings.storage_monitor_path)
            privacy_write = is_privacy_essential_write(request.url.path)
            if disk.state in {"read_only", "unavailable"} and not privacy_write:
                return JSONResponse(status_code=507, content={"error": {
                    "code": "storage_read_only_degraded",
                    "message": "resource budget permits privacy-essential writes only",
                    "details": {"disk_state": disk.state},
                }})
            if (
                disk.state == "stop_optional"
                and settings.role in {"api", "all"}
                and not privacy_write
            ):
                return JSONResponse(status_code=503, content={"error": {
                    "code": "optional_work_suspended",
                    "message": "optional work is suspended by the resource budget",
                    "details": {"disk_state": disk.state},
                }})
        return await call_next(request)

    @application.middleware("http")
    async def restore_quarantine(request: Request, call_next):
        if request.url.path not in {"/healthz", "/readyz"} and settings.role in {
            "api",
            "ingest",
            "all",
        }:
            gate_status = await request.app.state.restore_gate.status()
            if not gate_status.current:
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": {
                            "code": "restore_quarantine",
                            "message": "erasure ledger replay is required",
                            "details": {
                                "reason_code": gate_status.code,
                                "ledger_epoch": gate_status.ledger_epoch,
                                "database_epoch": gate_status.database_epoch,
                            },
                        }
                    },
                )
        return await call_next(request)

    @application.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
            headers=headers,
        )

    @application.get("/healthz", response_model=HealthView)
    async def health() -> HealthView:
        database_ok = await database.ping()
        revision = await database.migration_revision()
        if operator_database is not None:
            database_ok = database_ok and await operator_database.ping()
            operator_revision = await operator_database.migration_revision()
        else:
            operator_revision = settings.readiness_migration
        gate_status = await application.state.restore_gate.status()
        rollout_status = await application.state.rollout_gate.status()
        outbox_status = await outbox_health(database)
        resources = await resource_budget_snapshot(
            database,
            monitor_path=settings.storage_monitor_path,
            include_ingest_metrics=settings.role in {"api", "ingest", "all"},
        )
        migration_ok = (
            revision == settings.readiness_migration
            and operator_revision == settings.readiness_migration
        )
        return HealthView(
            status=(
                "ok"
                if database_ok
                and migration_ok
                and gate_status.current
                and rollout_status.authorized
                and outbox_status.code == "current"
                and resources["status"] == "ok"
                else "degraded"
            ),
            role=settings.role,
            database="ok" if database_ok else "unavailable",
            migration=revision or "unknown",
            restore_gate=gate_status.code,
            rollout_authorization=rollout_status.code,
            outbox={
                "status": outbox_status.code,
                "incomplete_erasure": outbox_status.incomplete_erasure,
                "failed_erasure": outbox_status.failed_erasure,
                "unsupported": outbox_status.unsupported,
            },
            spool=spool.stats(),
            resources=resources,
            policy_version=settings.policy_version,
            rollout_mode=settings.rollout_mode,
            capabilities={
                "persistent_memory": (
                    "operator_and_confirmation_gated"
                    if settings.rollout_mode == "canary"
                    else settings.rollout_mode
                ),
                "location_visits": "principal_consent_gated",
                "private_initiatives": (
                    "enabled" if settings.rollout_mode == "canary" else "disabled"
                ),
                "physical_actions": "disabled",
                "active_room": "disabled",
                "learning": "disabled",
                "vjepa": "disabled",
            },
        )

    @application.get("/readyz")
    async def ready() -> JSONResponse:
        database_ok = await database.ping()
        revision = await database.migration_revision()
        if operator_database is not None:
            database_ok = database_ok and await operator_database.ping()
            operator_revision = await operator_database.migration_revision()
        else:
            operator_revision = settings.readiness_migration
        gate_status = await application.state.restore_gate.status(force=True)
        rollout_status = await application.state.rollout_gate.status(force=True)
        outbox_status = await outbox_health(database)
        resources = await resource_budget_snapshot(
            database,
            monitor_path=settings.storage_monitor_path,
            include_ingest_metrics=settings.role in {"api", "ingest", "all"},
        )
        ready_state = (
            database_ok
            and revision == settings.readiness_migration
            and operator_revision == settings.readiness_migration
            and gate_status.current
            and rollout_status.authorized
            and outbox_status.ready
            and resources["ready"]
        )
        return JSONResponse(
            status_code=200 if ready_state else 503,
            content={
                "ready": ready_state,
                "database": database_ok,
                "migration": revision,
                "expected_migration": settings.readiness_migration,
                "restore_gate": gate_status.code,
                "rollout_authorization": rollout_status.code,
                "ledger_epoch": gate_status.ledger_epoch,
                "database_epoch": gate_status.database_epoch,
                "outbox": outbox_status.code,
                "resources": resources,
            },
        )

    if settings.role in {"ingest", "all"}:
        application.include_router(ingest_router())
    if settings.role in {"api", "all"}:
        application.include_router(semantic_router())
    return application

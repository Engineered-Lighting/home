from __future__ import annotations

import asyncio
import hmac
import logging
import os
import signal
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.routing import Match

from . import __version__
from .api import ingest_router, semantic_router
from .auth import (
    ATTESTED_NATIVE_CHANNEL,
    native_installation_id_valid,
    require_bootstrap,
    require_native_service_identity,
    require_operator_bearer,
    require_service_identity,
)
from .config import Settings
from .db import (
    Database,
    ParentRelationshipAuthorityDatabase,
    PrincipalBindingCommitDatabase,
)
from .errors import DomainError
from .ledger import EncryptedErasureLedger
from .maintenance import WorkerMaintenanceInspector
from .models import HealthView
from .parent_relationship_adapter import (
    AuthenticatedParentRelationshipAdapter,
)
from .principal_binding_adapter import AuthenticatedPrincipalBindingAdapter
from .restore import RestoreQuarantineGate, outbox_health
from .resources import (
    inspect_disk_budget,
    is_privacy_essential_write,
    is_retired_legacy_identity_import,
    resource_budget_snapshot,
)
from .rollout import RolloutAuthorizationGate
from .spool import DisabledRuntimeSpool, EncryptedRuntimeSpool
from .store import CoreStore
from .worker import DurableWorker


LOGGER = logging.getLogger("home_agent.main")


def terminate_on_unexpected_worker_exit(
    task: asyncio.Task,
    stop: asyncio.Event,
    *,
    terminate=None,
) -> None:
    """Make Docker's restart policy observe a dead maintenance loop."""

    if stop.is_set():
        return
    error_class = "None"
    with suppress(BaseException):
        error = task.exception()
        if error is not None:
            error_class = type(error).__name__
    LOGGER.critical(
        "worker task exited unexpectedly "
        "code=worker_task_exited error_class=%s",
        error_class,
    )
    (terminate or os.kill)(os.getpid(), signal.SIGTERM)


def maintenance_route_auth_contract(request: Request) -> tuple[str, bool] | None:
    """Resolve the matched route's declared authentication dependency graph."""

    for route in request.app.routes:
        match, _child_scope = route.matches(request.scope)
        if match is not Match.FULL:
            continue
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            return None
        calls = set()
        pending = [dependant]
        while pending:
            dependency = pending.pop()
            call = getattr(dependency, "call", None)
            if call is not None:
                calls.add(call)
            pending.extend(getattr(dependency, "dependencies", ()))
        requires_bootstrap = require_bootstrap in calls
        if require_operator_bearer in calls:
            return ("operator", requires_bootstrap)
        if require_native_service_identity in calls:
            return ("native_service", requires_bootstrap)
        if require_service_identity in calls:
            return ("service", requires_bootstrap)
        return None
    return None


def trusted_maintenance_gated_caller(request: Request, settings: Settings) -> bool:
    """Recognize a credential only for the matched route's declared audience."""

    contract = maintenance_route_auth_contract(request)
    if contract is None:
        return False
    audience, requires_bootstrap = contract

    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return False
    supplied = authorization.removeprefix("Bearer ").strip()
    if audience in {"service", "native_service"}:
        if settings.service_token is None or not hmac.compare_digest(
            supplied, settings.service_token.get_secret_value()
        ):
            return False
        ha_user_id = request.headers.get("x-authenticated-ha-user", "")
        if not ha_user_id or len(ha_user_id) > 64:
            return False
        if requires_bootstrap:
            if settings.bootstrap_token is None or not hmac.compare_digest(
                request.headers.get("x-home-agent-bootstrap", ""),
                settings.bootstrap_token.get_secret_value(),
            ):
                return False
        if audience == "native_service":
            if not hmac.compare_digest(
                request.headers.get("x-home-agent-channel", ""),
                ATTESTED_NATIVE_CHANNEL,
            ):
                return False
            if not native_installation_id_valid(
                request.headers.get("x-home-agent-installation")
            ):
                return False
        return True
    if audience != "operator" or settings.operator_token is None:
        return False
    if not hmac.compare_digest(supplied, settings.operator_token.get_secret_value()):
        return False
    if not requires_bootstrap:
        return True
    return settings.bootstrap_token is not None and hmac.compare_digest(
        request.headers.get("x-home-agent-bootstrap", ""),
        settings.bootstrap_token.get_secret_value(),
    )


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
    binding_commit_database = (
        PrincipalBindingCommitDatabase(
            settings.async_binding_commit_database_url()
        )
        if settings.binding_commit_database_url is not None
        and settings.role in {"api", "all"}
        else None
    )
    binding_adapter = (
        AuthenticatedPrincipalBindingAdapter(binding_commit_database)
        if binding_commit_database is not None
        else None
    )
    parent_relationship_database = (
        ParentRelationshipAuthorityDatabase(
            settings.async_binding_commit_database_url()
        )
        if settings.binding_commit_database_url is not None
        and settings.role in {"api", "all"}
        else None
    )
    parent_relationship_adapter = (
        AuthenticatedParentRelationshipAdapter(
            parent_relationship_database
        )
        if parent_relationship_database is not None
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
    maintenance_inspector = WorkerMaintenanceInspector(database)

    async def close_database_clients() -> None:
        if parent_relationship_adapter is not None:
            await parent_relationship_adapter.close()
        if binding_adapter is not None:
            await binding_adapter.close()
        if operator_database is not None:
            await operator_database.close()
        await database.close()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        stop = asyncio.Event()
        task: asyncio.Task | None = None
        revision = await application.state.database.migration_revision()
        if revision != settings.readiness_migration:
            try:
                spool.close()
            finally:
                await close_database_clients()
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
                    await close_database_clients()
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
                await close_database_clients()
            raise RuntimeError(
                "rollout authorization rejected: " + rollout_status.code
            )
        application.state.maintenance_observed_after = await database.current_time()
        if store is not None:
            store.maintenance_observed_after = (
                application.state.maintenance_observed_after
            )
        if operator_store is not None:
            operator_store.maintenance_observed_after = (
                application.state.maintenance_observed_after
            )
        if worker is not None:
            restore_status = await application.state.restore_gate.status(force=True)
            if not restore_status.current:
                try:
                    spool.close()
                finally:
                    await close_database_clients()
                raise RuntimeError(
                    "worker restore quarantine rejected: " + restore_status.code
                )
            task = asyncio.create_task(worker.run(stop))
            task.add_done_callback(
                lambda completed: terminate_on_unexpected_worker_exit(
                    completed, stop
                )
            )
        try:
            yield
        finally:
            stop.set()
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            spool.close()
            await close_database_clients()

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
    application.state.binding_commit_database = binding_commit_database
    application.state.binding_adapter = binding_adapter
    application.state.parent_relationship_database = (
        parent_relationship_database
    )
    application.state.parent_relationship_adapter = (
        parent_relationship_adapter
    )
    application.state.spool = spool
    application.state.store = store
    application.state.operator_store = operator_store
    application.state.ledger = ledger
    application.state.restore_gate = restore_gate
    application.state.rollout_gate = rollout_gate
    application.state.maintenance_inspector = maintenance_inspector
    application.state.maintenance_observed_after = None

    @application.middleware("http")
    async def resource_budget_guard(request: Request, call_next):
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.url.path not in {"/healthz", "/readyz"}
            and not is_retired_legacy_identity_import(request.url.path)
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

    @application.middleware("http")
    async def worker_maintenance_guard(request: Request, call_next):
        """Fail closed for trusted semantic writes after record-only rollout.

        Ingest remains available because the store can durably acknowledge a
        redacted header while suppressing raw-spool persistence.  Privacy
        cancellation, opt-out, forgetting, and erasure writes also remain
        available during worker degradation.
        """

        gated_write = (
            settings.rollout_mode in {"shadow", "canary"}
            and settings.role in {"api", "all"}
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.url.path != "/v1/ingest/envelopes"
            and not is_retired_legacy_identity_import(request.url.path)
            and not is_privacy_essential_write(request.url.path)
        )
        # Preserve endpoint authentication semantics: an invalid or missing
        # bearer must reach the normal dependency and receive its 401/403.
        if gated_write and trusted_maintenance_gated_caller(request, settings):
            maintenance = await request.app.state.maintenance_inspector.inspect(
                observed_after=request.app.state.maintenance_observed_after,
            )
            if not maintenance.ready:
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": {
                            "code": "worker_maintenance_unavailable",
                            "message": (
                                "semantic writes require a current retention worker"
                            ),
                            "details": maintenance.as_public_dict(),
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
        maintenance_status = await application.state.maintenance_inspector.inspect(
            expected_instance_id=(
                worker.maintenance_instance_id if worker is not None else None
            ),
            observed_after=application.state.maintenance_observed_after,
        )
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
                and maintenance_status.ready
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
            worker_maintenance=maintenance_status.as_public_dict(),
            spool=spool.stats(),
            resources=resources,
            policy_version=settings.policy_version,
            rollout_mode=settings.rollout_mode,
            capabilities={
                "persistent_memory": (
                    "operator_and_confirmation_gated"
                    if settings.rollout_mode == "canary"
                    else "disabled"
                ),
                "location_visits": (
                    "principal_consent_gated"
                    if settings.rollout_mode == "canary"
                    else "disabled"
                ),
                "location_retention": (
                    "principal_consent_gated"
                    if settings.rollout_mode == "canary"
                    else "disabled"
                ),
                "preference_opt_in": (
                    "principal_consent_gated"
                    if settings.rollout_mode == "canary"
                    else "disabled"
                ),
                "preference_opt_out": "enabled",
                "private_initiatives": "disabled",
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
        maintenance_status = await application.state.maintenance_inspector.inspect(
            expected_instance_id=(
                worker.maintenance_instance_id if worker is not None else None
            ),
            observed_after=application.state.maintenance_observed_after,
        )
        outbox_status = await outbox_health(database)
        resources = await resource_budget_snapshot(
            database,
            monitor_path=settings.storage_monitor_path,
            include_ingest_metrics=settings.role in {"api", "ingest", "all"},
        )
        maintenance_required = (
            settings.role in {"worker", "all"}
            or settings.rollout_mode in {"shadow", "canary"}
        )
        ready_state = (
            database_ok
            and revision == settings.readiness_migration
            and operator_revision == settings.readiness_migration
            and gate_status.current
            and rollout_status.authorized
            and (maintenance_status.ready or not maintenance_required)
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
                "worker_maintenance": maintenance_status.as_public_dict(),
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

"""Private per-site proof/logout application; no legacy Core routes or listener.

The host runs this application's lifespan and supplies restore/schema admission.
Proof admission may additionally suspend optional work; logout admission must not
depend on optional inference or preference availability. SQL kernels retain their
own authority checks. Database pools belong to this application's lifespan.
"""
import asyncio
import hmac
from contextlib import asynccontextmanager
from inspect import iscoroutinefunction

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.engine import make_url

from .errors import OptionalWorkSuspendedError
from .resources import resource_budget_snapshot
from .store import CoreStore
from .shared_auth_proof import SharedLinkProofDatabase
from .shared_auth_proof_api import ProofIngressBinding, create_proof_ingress
from .shared_link_session_api import SessionRevocationBinding, create_session_revocation_ingress
from .shared_link_session_revocation import SharedLinkSessionRevocationDatabase

REVISION = "0047_personal_pref_authority_v1"


def compose_site_identity_ingress(core_application, *, proof_binding, session_binding,
                                  proof_database_url, session_database_url):
    """Attach live Core admission to dedicated pools; caller runs both lifespans."""
    state = core_application.state
    settings, database, store = state.settings, state.database, state.store
    if (not isinstance(store, CoreStore) or store.database is not database or
            store.settings is not settings or settings.role != "api" or
            settings.rollout_mode not in ("shadow", "canary") or
            settings.readiness_migration != REVISION or
            database.engine.url.username != "home_agent_api"):
        raise ValueError("commissioned Core API admission required")
    target = database.engine.url
    for raw in (proof_database_url, session_database_url):
        parsed = make_url(raw)
        if (parsed.host, parsed.port or 5432, parsed.database) != (
                target.host, target.port or 5432, target.database):
            raise ValueError("identity pools must use the admitted Core database")

    async def admit_revocation():
        if (state.maintenance_observed_after is None or
                await database.migration_revision() != REVISION or
                not (await state.restore_gate.status(force=True)).current):
            raise OptionalWorkSuspendedError("identity restore or schema unavailable")

    async def admit_proof():
        await admit_revocation()
        if not (await state.rollout_gate.status(force=True)).authorized:
            raise OptionalWorkSuspendedError("identity rollout unavailable")
        maintenance = await state.maintenance_inspector.inspect(
            observed_after=state.maintenance_observed_after)
        resources = await resource_budget_snapshot(database,
            monitor_path=settings.storage_monitor_path, include_ingest_metrics=False)
        if not maintenance.ready or not resources["ready"]:
            raise OptionalWorkSuspendedError("identity optional work unavailable")

    return create_site_identity_runtime(proof_binding=proof_binding,
        session_binding=session_binding, proof_database_url=proof_database_url,
        session_database_url=session_database_url, admit_proof=admit_proof,
        admit_revocation=admit_revocation)


def create_site_identity_runtime(*, proof_binding, session_binding,
                                 proof_database_url, session_database_url,
                                 admit_proof, admit_revocation):
    if (type(proof_binding) is not ProofIngressBinding or
            type(session_binding) is not SessionRevocationBinding or
            proof_binding.issuer_id != session_binding.issuer_id or
            proof_binding.credential == session_binding.credential or
            not iscoroutinefunction(admit_proof) or
            not iscoroutinefunction(admit_revocation)):
        raise ValueError("separate issuer-bound credentials and admission required")

    # Constructors enforce exact non-administrator roles and bounded pools. No
    # connection is opened until lifespan startup has passed admission.
    proof = SharedLinkProofDatabase(proof_database_url, issuer_id=proof_binding.issuer_id)
    session = SharedLinkSessionRevocationDatabase(session_database_url,
                                                issuer_id=session_binding.issuer_id)
    ready = False
    active = 0

    @asynccontextmanager
    async def lifespan(_):
        nonlocal ready
        try:
            async with asyncio.timeout(10):
                await admit_revocation()
            ready = True
            yield
        finally:
            ready = False
            # Always attempt both disposals, including failed startup.
            outcomes = await asyncio.gather(proof.close(), session.close(), return_exceptions=True)
            if any(isinstance(value, BaseException) for value in outcomes):
                raise RuntimeError("identity database shutdown incomplete") from None

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    proof_app = create_proof_ingress(binding=proof_binding, database=proof)
    session_app = create_session_revocation_ingress(binding=session_binding, database=session)
    proof_paths = {route.path for route in proof_app.routes}
    session_paths = {route.path for route in session_app.routes}
    app.router.routes.extend(proof_app.routes)
    app.router.routes.extend(session_app.routes)

    @app.middleware("http")
    async def admission(request: Request, call_next):
        nonlocal active
        path = request.url.path
        if path not in proof_paths | session_paths:
            return await call_next(request)
        if not ready:
            return JSONResponse({"error": "identity_runtime_unavailable"}, status_code=503,
                                headers={"Cache-Control": "no-store"})
        expected = proof_binding.credential if path in proof_paths else session_binding.credential
        authorization = request.headers.getlist("authorization")
        if (request.scope.get("scheme") != "https" or "origin" in request.headers or
                "cookie" in request.headers or len(authorization) != 1 or
                not hmac.compare_digest(authorization[0].encode("latin-1"), b"Bearer " + expected)):
            # The existing handler provides its precise transport/auth error.
            # Unauthenticated callers must not consume database admission work.
            return await call_next(request)
        if active >= 2:
            return JSONResponse({"error": "identity_runtime_busy"}, status_code=429,
                                headers={"Cache-Control": "no-store"})
        active += 1
        try:
            try:
                async with asyncio.timeout(10):
                    await (admit_proof() if path in proof_paths else admit_revocation())
            except Exception:
                # No upstream operation was dispatched. Admission never retries
                # work or exposes private diagnostic details.
                return JSONResponse({"error": "identity_admission_unavailable"}, status_code=503,
                                    headers={"Cache-Control": "no-store"})
            return await call_next(request)
        finally:
            active -= 1

    return app

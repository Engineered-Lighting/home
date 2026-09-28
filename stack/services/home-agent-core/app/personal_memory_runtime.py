"""Compose private preference ingress with the existing Core runtime.

The caller must run Core's lifespan and expose this app only through its
separately provisioned private TLS listener. This module starts no listener,
changes no database grants, and never substitutes an operator connection.
"""
from .errors import OptionalWorkSuspendedError
from .personal_memory_api import PersonalMemoryBinding, create_personal_memory_ingress
from .personal_memory_contract import PreferenceReviewCommitment
from .personal_memory_service import PersonalMemoryService
from .personal_memory_storage import PersonalMemoryStorage
from .resources import resource_budget_snapshot
from .restore import outbox_health
from .store import CoreStore

REVISION = "0047_personal_pref_authority_v1"


def compose_personal_memory_ingress(core_application, *, binding, review_key):
    state = core_application.state
    settings, store, database = state.settings, state.store, state.database
    if (type(binding) is not PersonalMemoryBinding or not isinstance(store, CoreStore)
        or store.database is not database or store.settings is not settings
        or database.engine.url.username != "home_agent_api"
        or settings.role != "api" or settings.rollout_mode not in ("shadow", "canary")
        or settings.readiness_migration != REVISION):
        raise ValueError("separately provisioned preference API runtime required")
    storage = PersonalMemoryStorage(PreferenceReviewCommitment(review_key),
        policy_digest=settings.policy_digest, policy_version=settings.policy_version)

    async def admit():
        # Inspect live state on every transaction and again before delivery;
        # startup success is not a permanent restore or rollout authorization.
        if state.maintenance_observed_after is None:
            raise OptionalWorkSuspendedError("Core startup incomplete")
        if await database.migration_revision() != REVISION:
            raise OptionalWorkSuspendedError("preference schema unavailable")
        restore = await state.restore_gate.status(force=True)
        if not restore.current:
            raise OptionalWorkSuspendedError("restore replay required")
        rollout = await state.rollout_gate.status(force=True)
        if not rollout.authorized:
            raise OptionalWorkSuspendedError("rollout authorization required")
        maintenance = await state.maintenance_inspector.inspect(
            observed_after=state.maintenance_observed_after)
        if not maintenance.ready:
            raise OptionalWorkSuspendedError("retention maintenance unavailable")
        outbox = await outbox_health(database)
        resources = await resource_budget_snapshot(database,
            monitor_path=settings.storage_monitor_path, include_ingest_metrics=False)
        if not outbox.ready or not resources["ready"]:
            raise OptionalWorkSuspendedError("preference resources unavailable")

    service = PersonalMemoryService(store=store, storage=storage, admission=admit)
    return create_personal_memory_ingress(binding=binding, service=service)

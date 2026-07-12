from __future__ import annotations


class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"


class ForbiddenError(DomainError):
    status_code = 403
    code = "forbidden"


class AuthenticationError(DomainError):
    status_code = 401
    code = "unauthorized"


class ValidationDomainError(DomainError):
    status_code = 422
    code = "invalid_envelope_batch"


class CapabilityDisabledError(DomainError):
    status_code = 409
    code = "capability_disabled"


class WorkerMaintenanceUnavailableError(DomainError):
    status_code = 503
    code = "worker_maintenance_unavailable"


class OptionalWorkSuspendedError(DomainError):
    status_code = 503
    code = "optional_work_suspended"


class StorageReadOnlyDegradedError(DomainError):
    status_code = 507
    code = "storage_read_only_degraded"

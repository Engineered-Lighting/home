"""Optional, offline Den model sidecar. Importing this package never imports torch."""

from .contracts import ContractError, IntentHint, profile_digest, validate_request

__all__ = ["ContractError", "IntentHint", "profile_digest", "validate_request"]

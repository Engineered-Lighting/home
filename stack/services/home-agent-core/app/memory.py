from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from .crypto import canonical_json


PARENTS_HOUSE_PATTERN = re.compile(
    r"^\s*this\s+is\s+my\s+parents(?:['\u2019])?\s+mountain\s+"
    r"(?:house|home|place|retreat)[.!]?\s*$",
    re.IGNORECASE,
)

PROHIBITED_ASSERTIONS = (
    "ownership",
    "legal title",
    "residence",
    "current presence",
    "generic person/place association",
)


@dataclass(frozen=True, slots=True)
class VerifierResult:
    rule: str
    outcome: str
    reason_code: str

    def as_dict(self) -> dict[str, str]:
        return {
            "rule": self.rule,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class DescriptorDecision:
    state: str
    exact_descriptor: str
    role_expression: str
    resolved_parent_person_ids: tuple[uuid.UUID, ...]
    unresolved_role_expression: bool
    results: tuple[VerifierResult, ...]


def is_typed_parents_mountain_descriptor(exact_text: str) -> bool:
    """Return whether wording stays inside the narrow typed MVP descriptor."""

    return bool(PARENTS_HOUSE_PATTERN.match(exact_text))


def propose_parents_mountain_house(
    *,
    exact_text: str,
    authenticated: bool,
    principal_bound: bool,
    location_memory_opted_in: bool,
    active_place_count: int,
    anchor_supported: bool,
    parent_person_ids: Iterable[uuid.UUID],
    role_expansion_complete: bool = True,
) -> DescriptorDecision:
    parents = tuple(dict.fromkeys(parent_person_ids))
    results = (
        VerifierResult(
            "schema_and_predicate",
            "pass" if is_typed_parents_mountain_descriptor(exact_text) else "fail",
            "recognized_descriptor"
            if is_typed_parents_mountain_descriptor(exact_text)
            else "unsupported_statement",
        ),
        VerifierResult(
            "principal_authentication",
            "pass" if authenticated else "fail",
            "authenticated_principal" if authenticated else "principal_unknown",
        ),
        VerifierResult(
            "principal_binding",
            "pass" if principal_bound else "fail",
            "principal_bound" if principal_bound else "principal_unbound",
        ),
        VerifierResult(
            "privacy_consent",
            "pass" if location_memory_opted_in else "fail",
            "location_memory_opted_in"
            if location_memory_opted_in
            else "location_memory_disabled",
        ),
        VerifierResult(
            "deictic_resolution",
            "pass" if active_place_count == 1 else "fail",
            "unique_current_place"
            if active_place_count == 1
            else "ambiguous_current_place",
        ),
        VerifierResult(
            "location_anchor",
            "pass" if anchor_supported else "fail",
            "supported_anchor" if anchor_supported else "location_unresolved",
        ),
        VerifierResult(
            "parent_resolution",
            "pass" if len(parents) >= 1 and role_expansion_complete else "defer",
            (
                "confirmed_parent_facts"
                if len(parents) >= 1 and role_expansion_complete
                else "role_expression_partially_unresolved"
                if len(parents) >= 1
                else "role_expression_unresolved"
            ),
        ),
        VerifierResult("prohibited_implications", "pass", "descriptor_only"),
    )
    # A supported current locality with an insufficient property anchor is a
    # reviewable-but-unresolved transaction, not poisoned input.  Keeping the
    # transaction in ``needs_confirmation`` keeps the review visible and lets
    # the client request a fresh preview after better fixes arrive, while
    # ``confirm_descriptor`` still refuses this unresolved snapshot. Every
    # other failed gate quarantines.
    hard_fail = any(
        result.outcome == "fail" and result.rule != "location_anchor"
        for result in results
    )
    return DescriptorDecision(
        state="quarantined" if hard_fail else "needs_confirmation",
        exact_descriptor=exact_text,
        role_expression="parents_of(self)",
        resolved_parent_person_ids=parents,
        unresolved_role_expression=not parents or not role_expansion_complete,
        results=results,
    )


def render_travel_arrival(exact_descriptor: str) -> str:
    """Render the one reviewed travel template without invoking a model."""

    if not is_typed_parents_mountain_descriptor(exact_descriptor):
        raise ValueError("descriptor is outside the typed parents-house shape")
    phrase = re.sub(
        r"^\s*this\s+is\s+my\s+",
        "",
        exact_descriptor,
        count=1,
        flags=re.IGNORECASE,
    ).strip()
    phrase = phrase.rstrip(".! ")
    return f"Looks like you\u2019re back at your {phrase}."


def descriptor_preview_payload(
    *,
    transaction_id: uuid.UUID,
    place_id: uuid.UUID | None,
    decision: DescriptorDecision,
    locator_summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "transaction_id": str(transaction_id),
        "place_id": str(place_id) if place_id else None,
        "state": decision.state,
        "exact_descriptor": decision.exact_descriptor,
        "role_expression": decision.role_expression,
        "resolved_parent_person_ids": [
            str(value) for value in decision.resolved_parent_person_ids
        ],
        "unresolved_role_expression": decision.unresolved_role_expression,
        "locator": locator_summary,
        "retained": [
            "specific private place locator",
            "perspective-scoped exact social descriptor",
        ],
        "not_asserted": list(PROHIBITED_ASSERTIONS),
        "verifier_results": [result.as_dict() for result in decision.results],
    }


def preview_digest(payload: dict[str, Any], key: bytes) -> str:
    import hashlib
    import hmac

    return hmac.new(key, canonical_json(payload), hashlib.sha256).hexdigest()


def require_untampered_preview(
    payload: dict[str, Any], supplied_digest: str, key: bytes
) -> None:
    actual = preview_digest(payload, key)
    if not __import__("hmac").compare_digest(actual, supplied_digest):
        raise ValueError("preview digest does not match the reviewed proposal")

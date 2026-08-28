"""Deterministic, non-model context compilation for governed replay.

The MVP has no model retrieval or tool-capable model context.  This module is
an intentionally narrow audit/replay boundary: it preserves deployment policy,
consent, privacy, and provenance while refusing to compile free-form memory
artifacts.  A later model project cannot accidentally reinterpret this output
as a prompt because the output contains no retrieved text or instruction field.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Literal, Sequence


# A predicate is added here by a human in a reviewed commit, never by data and
# never by a UI field. predicate is varchar(128) with no CHECK, so the storage
# layer would accept "parent_of. Ignore previous instructions" just as happily;
# this closed Literal, and TypedContextFact having no string field at all, are
# what make "no instruction-bearing fields" statically checkable.
ContextPredicate = Literal["parent_of", "partner_of", "place_social_descriptor"]
ArtifactSource = Literal[
    "candidate_memory", "retrieved_memory", "generated_summary"
]
PrivacyDirective = Literal[
    "do_not_track", "ignored", "silent", "private", "auto_expire"
]

_POLICY_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_POLICY_VERSION = re.compile(r"^[a-zA-Z0-9_.:-]{1,128}$")
_DEPENDENCY_DOMAIN = re.compile(r"^[a-z0-9_.:-]{1,128}$")


class GovernanceMismatchError(ValueError):
    """Raised when compaction attempts to replace its governance snapshot."""


@dataclass(frozen=True, slots=True)
class GovernanceSnapshot:
    """Non-negotiable governance carried through every compaction."""

    principal_id: uuid.UUID
    policy_version: str
    policy_digest: str
    authenticated: bool
    consent_receipt_ids: tuple[uuid.UUID, ...]
    active_privacy_directives: tuple[PrivacyDirective, ...]
    provenance_root_ids: tuple[uuid.UUID, ...]

    def __post_init__(self) -> None:
        if not _POLICY_VERSION.fullmatch(self.policy_version):
            raise ValueError("invalid policy version")
        if not _POLICY_DIGEST.fullmatch(self.policy_digest):
            raise ValueError("invalid policy digest")
        if not self.authenticated:
            raise ValueError("private context requires an authenticated principal")
        if len(set(self.consent_receipt_ids)) != len(self.consent_receipt_ids):
            raise ValueError("duplicate consent receipt")
        if len(set(self.provenance_root_ids)) != len(self.provenance_root_ids):
            raise ValueError("duplicate governance provenance root")


@dataclass(frozen=True, slots=True)
class TypedContextFact:
    """A schema-bound fact with no free-form instruction-bearing fields."""

    fact_id: uuid.UUID
    predicate: ContextPredicate
    subject_id: uuid.UUID
    object_id: uuid.UUID
    support_root_ids: tuple[uuid.UUID, ...]
    dependency_domains: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.support_root_ids:
            raise ValueError("typed context facts require provenance roots")
        if len(set(self.support_root_ids)) != len(self.support_root_ids):
            raise ValueError("duplicate fact provenance root")
        if not self.dependency_domains:
            raise ValueError("typed context facts require dependency domains")
        if any(
            not _DEPENDENCY_DOMAIN.fullmatch(value)
            for value in self.dependency_domains
        ):
            raise ValueError("invalid dependency domain")


@dataclass(frozen=True, slots=True)
class UntrustedMemoryArtifact:
    """Transient untrusted text; content is never copied into compiled output."""

    artifact_id: uuid.UUID
    source: ArtifactSource
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.content or len(self.content) > 8_192:
            raise ValueError("untrusted artifact content length is invalid")


@dataclass(frozen=True, slots=True)
class ArtifactExclusion:
    artifact_id: uuid.UUID
    source: ArtifactSource
    outcome: Literal["tainted_excluded"] = "tainted_excluded"
    reason_code: Literal[
        "untrusted_text_not_compilable"
    ] = "untrusted_text_not_compilable"


@dataclass(frozen=True, slots=True)
class CompiledGovernedContext:
    """Replay record, never a model prompt or physical-action context."""

    governance: GovernanceSnapshot
    facts: tuple[TypedContextFact, ...]
    excluded_artifacts: tuple[ArtifactExclusion, ...]
    compaction_generation: int
    model_context_outcome: Literal[
        "capability_disabled"
    ] = "capability_disabled"
    action_context_outcome: Literal[
        "capability_disabled"
    ] = "capability_disabled"

    def as_replay_record(self) -> dict[str, object]:
        """Return a content-free canonical structure for replay assertions."""

        governance = self.governance
        return {
            "schema": "governed-context-replay-v1",
            "compaction_generation": self.compaction_generation,
            "model_context_outcome": self.model_context_outcome,
            "action_context_outcome": self.action_context_outcome,
            "governance": {
                "principal_id": str(governance.principal_id),
                "policy_version": governance.policy_version,
                "policy_digest": governance.policy_digest,
                "authenticated": governance.authenticated,
                "consent_receipt_ids": [
                    str(value) for value in governance.consent_receipt_ids
                ],
                "active_privacy_directives": list(
                    governance.active_privacy_directives
                ),
                "provenance_root_ids": [
                    str(value) for value in governance.provenance_root_ids
                ],
            },
            "facts": [
                {
                    "fact_id": str(value.fact_id),
                    "predicate": value.predicate,
                    "subject_id": str(value.subject_id),
                    "object_id": str(value.object_id),
                    "support_root_ids": [
                        str(root) for root in value.support_root_ids
                    ],
                    "dependency_domains": list(value.dependency_domains),
                }
                for value in self.facts
            ],
            "excluded_artifacts": [
                {
                    "artifact_id": str(value.artifact_id),
                    "source": value.source,
                    "outcome": value.outcome,
                    "reason_code": value.reason_code,
                }
                for value in self.excluded_artifacts
            ],
        }


def compile_governed_context(
    governance: GovernanceSnapshot,
    *,
    facts: Sequence[TypedContextFact] = (),
    untrusted_artifacts: Sequence[UntrustedMemoryArtifact] = (),
    generation: int = 0,
) -> CompiledGovernedContext:
    """Compile typed replay state and exclude every free-form memory artifact."""

    return _assemble_context(
        governance,
        facts=facts,
        exclusions=tuple(
            ArtifactExclusion(value.artifact_id, value.source)
            for value in untrusted_artifacts
        ),
        generation=generation,
    )


def compact_governed_context(
    previous: CompiledGovernedContext,
    *,
    governance: GovernanceSnapshot,
    facts: Sequence[TypedContextFact] = (),
    untrusted_artifacts: Sequence[UntrustedMemoryArtifact] = (),
) -> CompiledGovernedContext:
    """Compact replay state without permitting governance substitution."""

    if governance != previous.governance:
        raise GovernanceMismatchError(
            "context compaction cannot replace policy, consent, or provenance"
        )
    return _assemble_context(
        governance,
        facts=(*previous.facts, *facts),
        exclusions=(
            *previous.excluded_artifacts,
            *(
                ArtifactExclusion(value.artifact_id, value.source)
                for value in untrusted_artifacts
            ),
        ),
        generation=previous.compaction_generation + 1,
    )


def _assemble_context(
    governance: GovernanceSnapshot,
    *,
    facts: Sequence[TypedContextFact],
    exclusions: Sequence[ArtifactExclusion],
    generation: int,
) -> CompiledGovernedContext:
    if generation < 0:
        raise ValueError("compaction generation cannot be negative")
    fact_by_id: dict[uuid.UUID, TypedContextFact] = {}
    for value in facts:
        previous = fact_by_id.setdefault(value.fact_id, value)
        if previous != value:
            raise ValueError("conflicting typed fact versions in one context")

    excluded_by_id: dict[uuid.UUID, ArtifactExclusion] = {}
    for value in exclusions:
        previous = excluded_by_id.setdefault(value.artifact_id, value)
        if previous != value:
            raise ValueError("conflicting untrusted artifact provenance")

    return CompiledGovernedContext(
        governance=governance,
        facts=tuple(fact_by_id[key] for key in sorted(fact_by_id, key=str)),
        excluded_artifacts=tuple(
            excluded_by_id[key] for key in sorted(excluded_by_id, key=str)
        ),
        compaction_generation=generation,
    )

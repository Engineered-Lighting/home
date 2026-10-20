"""Trusted user-input sequencing; no model is allowed to construct action intent.

Server routes choose the explicit read/lighting entry point from the user event,
never from retrieved text or model output. No production broker is installed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from .authority import (ActionEnvelope, ActionLedger, ActionPlanner, Denied,
                        LightOperation, Session, SharedSpeaker)
from .memory import AuthorizedRetriever, NormalizedMemory


@dataclass(frozen=True)
class Interaction:
    handle: str
    principal: Session | SharedSpeaker
    question: str
    action: ActionEnvelope | None


class LightingBroker(Protocol):
    """Fixed per-site authenticated typed adapter, not a model-selected endpoint.

    A production broker must independently revalidate the transmitted signed
    capability immediately before HA execution and persist its own outcome.
    The prototype's process-local envelope is not a transport signature.
    """
    site_id: str
    def execute(self, operation: LightOperation, envelope: ActionEnvelope) -> str: ...


class PersonalCoordinator:
    def __init__(self, planner: ActionPlanner, retriever: AuthorizedRetriever, ledger: ActionLedger,
                 brokers: dict[str, LightingBroker] | None = None):
        self.planner, self.retriever, self.ledger = planner, retriever, ledger
        self.brokers = dict(brokers or {})
        if any(site not in ("echo", "victoria") or broker.site_id != site for site, broker in self.brokers.items()):
            raise ValueError("broker must be bound to one canonical home")
        self._interactions: dict[str, Interaction] = {}

    def _save(self, principal, question, action):
        if not isinstance(question, str) or not 0 < len(question) <= 4096:
            raise ValueError("bounded original user input required")
        # Only bounded active interactions are retained; callers close them on
        # completion/home switch/revocation. No silent eviction creates authority.
        if len(self._interactions) >= 32:
            raise Denied("too many active interactions")
        interaction = Interaction(uuid4().hex, principal, question, action)
        self._interactions[interaction.handle] = interaction
        return interaction

    def prepare_read(self, principal: Session | SharedSpeaker, original_question: str) -> Interaction:
        if isinstance(principal, SharedSpeaker):
            self.planner.authority.check_speaker(principal)
        else:
            self.planner.authority.owner(principal)
        return self._save(principal, original_question, None)

    def prepare_lighting(self, principal: Session | SharedSpeaker, original_command: str, *,
                         request_id: str, interaction_site: str | None = None,
                         context_receipt: str | None = None) -> Interaction:
        # The immutable envelope exists BEFORE any source/model work is possible.
        action = self.planner.plan_direct(principal, original_command, request_id=request_id,
                                          interaction_site=interaction_site, context_receipt=context_receipt)
        return self._save(principal, original_command, action)

    def _check(self, interaction: Interaction) -> None:
        if self._interactions.get(interaction.handle) is not interaction:
            raise Denied("interaction is closed or was not issued by the user boundary")
        if interaction.action is not None:
            self.planner.validate(interaction.action)
        elif isinstance(interaction.principal, SharedSpeaker):
            self.planner.authority.check_speaker(interaction.principal)
        else:
            self.planner.authority.owner(interaction.principal)

    def retrieve(self, interaction: Interaction, *, site_id: str, source_instance: str) -> tuple[NormalizedMemory, ...]:
        self._check(interaction)
        if not isinstance(interaction.principal, Session):
            raise Denied("private memory requires an authenticated Home session")
        result = self.retriever.retrieve(interaction.principal, site_id, source_instance, interaction.question)
        self._check(interaction)
        return result

    def execute(self, interaction: Interaction) -> tuple[dict, ...]:
        self._check(interaction)
        if interaction.action is None:
            raise Denied("read-only interaction has no action intent")
        results = []
        for index, operation in enumerate(interaction.action.operations):
            broker = self.brokers.get(operation.site_id)
            if broker is None:
                results.append({"site_id": operation.site_id, "operation_index": index, "status": "unavailable"})
                continue
            reserved = self.ledger.reserve(interaction.action, index)
            if reserved is None:
                results.append({"site_id": operation.site_id, "operation_index": index,
                                "status": self.ledger.status(interaction.action.request_id, index)})
                continue
            try:
                self.planner.validate(interaction.action)
            except Denied:
                self.ledger.complete(interaction.action.request_id, index, "failed")
                raise
            try:
                outcome = broker.execute(reserved, interaction.action)
            except Exception:
                outcome = "unknown"
            if outcome not in {"succeeded", "failed", "unknown"}:
                outcome = "unknown"
            self.ledger.complete(interaction.action.request_id, index, outcome)
            results.append({"site_id": operation.site_id, "operation_index": index, "status": outcome})
        return tuple(results)

    def close(self, interaction: Interaction) -> None:
        self._interactions.pop(interaction.handle, None)

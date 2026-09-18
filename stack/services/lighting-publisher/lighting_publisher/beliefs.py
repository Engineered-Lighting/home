"""The belief seam: where Jev's answers would enter the state machines.

``BeliefSource.current(now)`` returns the latest ``Beliefs`` (per-question
probabilities reduced by ``lighting_beliefs.reduce``) or None when there is
no answer. Every machine in this package treats None as "no belief": the
deterministic rule applies and the journal says so. This milestone ships only
``NoBeliefs``; the Jev client arrives in M6 behind ``lighting_beliefs.egress``
and the leak guard, never here.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Beliefs:
    """One committed set of answers, as probabilities only.

    Score questions carry their per-level distribution; the Noul carries
    ``P(yes)``. ``request_id`` identifies the commit so a machine can count
    consecutive commits; ``at`` is when the answer was committed.
    """

    at: dt.datetime
    request_id: str | None
    p_attention: tuple[float, float, float]
    p_eating: float
    p_food_prep: tuple[float, float, float]
    p_settling: tuple[float, float, float]
    p_rest: tuple[float, float, float]

    @staticmethod
    def _ge(levels: tuple[float, ...], k: int) -> float:
        return min(1.0, sum(levels[k:]))

    def p_attention_ge(self, k: int) -> float:
        """``P(tv_attention >= k)``."""
        return self._ge(self.p_attention, k)

    @property
    def p_attention_eq0(self) -> float:
        return self.p_attention[0]

    @property
    def p_rest_eq2(self) -> float:
        return self.p_rest[2]

    def p_food_prep_ge(self, k: int) -> float:
        return self._ge(self.p_food_prep, k)

    @classmethod
    def from_reduced(cls, reduced: Mapping[str, Any], at: dt.datetime,
                     request_id: str | None = None) -> "Beliefs":
        """Build from ``lighting_beliefs.reduce.reduce_answers`` output.

        Accepts the ``ScoreBelief``/``NoulBelief`` objects or their
        ``as_dict()`` forms. Every question must be present.
        """
        def levels(qid: str) -> tuple[float, float, float]:
            item = reduced[qid]
            raw = item["p_level"] if isinstance(item, Mapping) else item.p_level
            got = tuple(float(v) for v in raw)
            if len(got) != 3:
                raise ValueError(f"{qid} has {len(got)} levels, expected 3")
            return got  # type: ignore[return-value]

        eating = reduced["eating"]
        p_yes = eating["p_yes"] if isinstance(eating, Mapping) else eating.p_yes
        return cls(at=at, request_id=request_id,
                   p_attention=levels("tv_attention"), p_eating=float(p_yes),
                   p_food_prep=levels("food_prep"), p_settling=levels("settling"),
                   p_rest=levels("rest_state"))

    def as_dict(self) -> dict:
        return {
            "at": self.at.isoformat(timespec="seconds"), "request_id": self.request_id,
            "p_attention": list(self.p_attention), "p_eating": self.p_eating,
            "p_food_prep": list(self.p_food_prep), "p_settling": list(self.p_settling),
            "p_rest": list(self.p_rest),
        }


class BeliefSource(ABC):
    """Whatever can answer "what does the house believe right now?"."""

    @abstractmethod
    def current(self, now: dt.datetime) -> Beliefs | None:
        """The latest beliefs, or None when there is no answer to act on."""


class NoBeliefs(BeliefSource):
    """The M4 source: there is never a belief; every rule is deterministic."""

    def current(self, now: dt.datetime) -> Beliefs | None:
        return None

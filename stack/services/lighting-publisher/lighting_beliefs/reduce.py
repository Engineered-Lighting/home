"""Probability-only reduction of System One answers.

Every belief the publisher acts on is a probability derived from the answer's
distribution: ``P(level >= k)`` for a Score, ``P(yes)`` for a Noul. The vendor
``confidence`` field is carried as metadata for the journal and never used to
gate anything; gating on it would let a calibration change upstream move the
lights. All functions are pure and accept the HTTP/JSON answer shape (Score
probabilities keyed by level as strings or ints).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .questions import NOUL, SCORE, QUESTIONS, Question

PROBABILITY_TOLERANCE = 0.02


class AnswerError(ValueError):
    """An answer is missing, malformed or does not match its question."""


@dataclass(frozen=True)
class ScoreBelief:
    """Reduced Score answer: per-level and cumulative probabilities."""

    question_id: str
    p_level: tuple[float, ...]
    p_at_least: tuple[float, ...]
    expected_level: float
    metadata: dict = field(default_factory=dict)

    def p_exactly(self, k: int) -> float:
        return self.p_level[k]

    def p_ge(self, k: int) -> float:
        return self.p_at_least[k]

    def as_dict(self) -> dict:
        return {
            "question_id": self.question_id,
            "primitive": SCORE,
            "p_level": list(self.p_level),
            "p_at_least": list(self.p_at_least),
            "expected_level": self.expected_level,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class NoulBelief:
    """Reduced Noul answer: P(yes)."""

    question_id: str
    p_yes: float
    metadata: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "question_id": self.question_id,
            "primitive": NOUL,
            "p_yes": self.p_yes,
            "metadata": dict(self.metadata),
        }


def level_probabilities(answer: Mapping[str, Any], levels: int) -> tuple[float, ...]:
    """Normalised per-level probabilities from a Score answer.

    Accepts string or int level keys; a missing level counts as 0. Rejects
    booleans, negative values, unknown levels, two keys naming the same level
    (``"0"`` and ``0``) and a total further than ``PROBABILITY_TOLERANCE``
    from 1, then renormalises the small remainder.
    """
    raw = answer.get("probabilities")
    if not isinstance(raw, Mapping) or not raw:
        raise AnswerError("score answer has no probabilities")
    values = [0.0] * levels
    seen: set[int] = set()
    for key, value in raw.items():
        if isinstance(key, bool):
            raise AnswerError(f"bad level key {key!r}")
        try:
            index = int(key)
        except (TypeError, ValueError) as exc:
            raise AnswerError(f"bad level key {key!r}") from exc
        if not 0 <= index < levels:
            raise AnswerError(f"level {index} outside 0..{levels - 1}")
        if index in seen:
            raise AnswerError(f"level {index} given twice")
        seen.add(index)
        if not _is_probability(value):
            raise AnswerError(f"bad probability for level {index}")
        values[index] = float(value)
    total = sum(values)
    if abs(total - 1.0) > PROBABILITY_TOLERANCE:
        raise AnswerError(f"probabilities sum to {total:.3f}")
    return tuple(v / total for v in values)


def _is_probability(value: Any) -> bool:
    """A non-negative finite int or float; booleans are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and 0.0 <= value <= float("inf") and value != float("inf")


def cumulative_at_least(p_level: tuple[float, ...]) -> tuple[float, ...]:
    """``P(level >= k)`` for every k; index 0 is always 1.0."""
    out = []
    running = 0.0
    for p in reversed(p_level):
        running += p
        out.append(min(1.0, running))
    return tuple(reversed(out))


def _metadata(answer: Mapping[str, Any]) -> dict:
    """Vendor fields kept for the journal only. Nothing here gates."""
    meta = {}
    for key in ("confidence", "score", "legend"):
        if key in answer:
            meta["vendor_" + key] = answer[key]
    return meta


def reduce_score(answer: Mapping[str, Any], question: Question) -> ScoreBelief:
    """Reduce one Score answer against its question's level count."""
    if question.primitive != SCORE:
        raise AnswerError(f"{question.id} is not a score question")
    p_level = level_probabilities(answer, question.levels)
    expected = sum(i * p for i, p in enumerate(p_level))
    return ScoreBelief(question.id, p_level, cumulative_at_least(p_level), expected, _metadata(answer))


def reduce_noul(answer: Mapping[str, Any], question: Question) -> NoulBelief:
    """Reduce one Noul answer to P(yes)."""
    if question.primitive != NOUL:
        raise AnswerError(f"{question.id} is not a noul question")
    value = answer.get("noul")
    if not _is_probability(value) or value > 1.0:
        raise AnswerError(f"noul value {value!r} not in [0, 1]")
    return NoulBelief(question.id, float(value), _metadata(answer))


def reduce_answers(answers: Mapping[str, Mapping[str, Any]],
                   questions: tuple[Question, ...] = QUESTIONS) -> dict[str, ScoreBelief | NoulBelief]:
    """Reduce a full ``answers`` map (id -> answer) for the given questions.

    Every question must be answered; extra answers are ignored so a speculative
    fan-out can add questions without touching this function.
    """
    beliefs: dict[str, ScoreBelief | NoulBelief] = {}
    for q in questions:
        answer = answers.get(q.id)
        if not isinstance(answer, Mapping):
            raise AnswerError(f"no answer for {q.id}")
        beliefs[q.id] = reduce_score(answer, q) if q.primitive == SCORE else reduce_noul(answer, q)
    return beliefs

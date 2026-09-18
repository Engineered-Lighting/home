"""The five belief questions the publisher asks Jev, as reviewable data.

Each question is one snap judgment over the ``lighting-beliefs-state/v1``
packet (see ``packet.py``). Score levels describe concrete situations, never
abstract degrees, because System One judges every level in isolation. The
question ids are never sent to the model; they key the answers.

``build_questions()`` returns the frozen list; ``questions_as_dicts()`` gives the
HTTP/JSON shape (``{"type", "instructions", "criteria"}``) keyed by id, and
``questions_as_sdk()`` builds ``typesafe_sdk`` objects when that optional extra
is installed. Thresholds that act on the answers live in ``reduce.py`` callers,
not here; this file is the wording under review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

SCORE = "score"
NOUL = "noul"

# Nested-path hints for the state, written the way the docs reference state.
_PACKET_HINT = (
    "The state is a house-level snapshot built from cameras. `cameras.<name>` holds "
    "`occupancy.zones` (occupied, clear or unknown per zone), `people` (position and "
    "posture per anonymous track), `claims` (short fallible observations), and "
    "`account` (a short fallible narrative). `devices.media` lists screens and "
    "speakers with role, state, source_kind and age_s. `quiet.credible_activity_age_s` "
    "is how many seconds since anyone was credibly active. Judge only what is stated; "
    "an empty or unknown field is not evidence."
)


@dataclass(frozen=True)
class Question:
    """One question as data: id, primitive, instructions and criteria."""

    id: str
    primitive: str
    instructions: str
    criteria: Any = field(default=None)

    @property
    def levels(self) -> int:
        """Number of Score levels (0 for a Noul)."""
        return len(self.criteria) if self.primitive == SCORE else 0

    def as_dict(self) -> dict:
        """The HTTP/JSON question shape used by the v1 systemone endpoint."""
        body: dict = {"type": self.primitive, "instructions": self.instructions}
        if self.criteria is not None:
            body["criteria"] = list(self.criteria) if self.primitive == SCORE else dict(self.criteria)
        return body


TV_ATTENTION = Question(
    id="tv_attention",
    primitive=SCORE,
    instructions=(
        "How much are the people in the living room attending to the television right now? "
        "Use `devices.media` for whether a screen is playing and `cameras.living_room` for "
        "where people are and what they are doing. " + _PACKET_HINT
    ),
    criteria=[
        "Nobody is attending to the television: the living room is empty, or the people in it "
        "are busy elsewhere in the house, standing with their back to the screen, or the "
        "screen is off or idle.",
        "Someone is in the living room with the screen playing but is only partly attending: "
        "glancing between the screen and a phone or laptop, talking, eating at the table, "
        "walking in and out, or tidying.",
        "Someone is seated or reclined on the sofa facing the playing screen and is still, "
        "with no other activity claimed for them.",
    ],
)

EATING = Question(
    id="eating",
    primitive=NOUL,
    instructions=(
        "Is someone in the house eating a meal or a snack right now? Look for a person seated "
        "with a plate, bowl or food in hand in `cameras.<name>.people`, `claims` and "
        "`objects.person_adjacent`. Cooking or setting the table without eating is not eating. "
        + _PACKET_HINT
    ),
    criteria={
        "true": "A person is currently putting food in their mouth, holding food, or seated at "
                "a table or sofa with a plate, bowl or takeout container in front of them.",
        "false": "Nobody is eating: rooms are empty, people are cooking, cleaning, working, "
                 "watching a screen without food, or food is only visible on a counter.",
    },
)

FOOD_PREP = Question(
    id="food_prep",
    primitive=SCORE,
    instructions=(
        "How much food preparation is happening in the kitchen right now? Use "
        "`cameras.kitchen` occupancy, people positions, claims and adjacent objects. "
        + _PACKET_HINT
    ),
    criteria=[
        "No food preparation: the kitchen is empty, or someone is only passing through, "
        "getting a glass of water, or standing at the counter with a phone.",
        "Light preparation: someone is making a drink, opening the fridge, using the "
        "microwave or kettle, plating a snack, or clearing dishes.",
        "Active cooking: someone is at the stove or chopping on the counter, handling pans, "
        "knives or several ingredients, and staying at the island or sink for it.",
    ],
)

SETTLING = Question(
    id="settling",
    primitive=SCORE,
    instructions=(
        "How far has the household settled toward sleep for the night? Judge from where "
        "people are, how they move, screens in `devices.media`, and "
        "`quiet.credible_activity_age_s`. " + _PACKET_HINT
    ),
    criteria=[
        "Not settling: someone is moving between rooms, cooking, exercising, working at a "
        "desk, or a screen is playing with someone attending to it.",
        "Winding down: someone is seated quietly or reclined with the screen off or idle, "
        "moving toward the bathroom or bedroom, or the last credible activity was a few "
        "minutes ago with nobody visibly active.",
        "Settled for the night: no person is visible on any camera or the only person is "
        "lying still, screens are off or idle, and no credible activity for a long stretch.",
    ],
)

REST_STATE = Question(
    id="rest_state",
    primitive=SCORE,
    instructions=(
        "What is the rest state of the person most at rest in the house right now? Use "
        "posture and stillness from `cameras.<name>.people` and `claims`. " + _PACKET_HINT
    ),
    criteria=[
        "Awake and active: sitting upright, standing, walking, gesturing, using a phone or "
        "laptop, talking, or handling objects.",
        "Drowsy or resting: reclined on the sofa or bed with eyes open or closed for a short "
        "while, head propped, occasional small movements, screen possibly still playing.",
        "Asleep: lying down and motionless for a sustained period, eyes closed, under a "
        "blanket or with the head down, not reacting to the room.",
    ],
)

QUESTIONS: tuple[Question, ...] = (TV_ATTENTION, EATING, FOOD_PREP, SETTLING, REST_STATE)
QUESTION_IDS: tuple[str, ...] = tuple(q.id for q in QUESTIONS)


def build_questions() -> list[Question]:
    """Return the five questions in review order."""
    return list(QUESTIONS)


def questions_as_dicts() -> dict[str, dict]:
    """The plain-dict shape: ``{id: {"type", "instructions", "criteria"}}``."""
    return {q.id: q.as_dict() for q in QUESTIONS}


def questions_as_sdk() -> Mapping[str, Any]:
    """The ``typesafe_sdk`` shape (``Score``/``Noul`` objects keyed by id).

    Imports the optional ``cloud`` extra lazily so the scaffold stays
    stdlib-only. Raises ImportError with an install hint when it is absent.
    """
    try:
        from typesafe_sdk import Noul, Score  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised only with the extra
        raise ImportError("typesafe_sdk is not installed; pip install 'lighting_beliefs[cloud]'") from exc
    built: dict[str, Any] = {}
    for q in QUESTIONS:
        if q.primitive == SCORE:
            built[q.id] = Score(instructions=q.instructions, criteria=list(q.criteria))
        else:
            built[q.id] = Noul(instructions=q.instructions, criteria=dict(q.criteria))
    return built


def question_by_id(question_id: str) -> Question:
    """Look a question up by id; KeyError when unknown."""
    for q in QUESTIONS:
        if q.id == question_id:
            return q
    raise KeyError(question_id)

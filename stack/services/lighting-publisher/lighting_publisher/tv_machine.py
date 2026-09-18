"""Story T: the television state machine behind ``tv_watching``.

States: TV_OFF, PROVISIONAL, WATCHING, AWAY_HOLD, UNATTENDED, NAPPING.
``tv_watching`` is on in PROVISIONAL, WATCHING, AWAY_HOLD and NAPPING.

Deterministic rule (beliefs None, the M4 shadow):

- TV on with living-room occupancy: PROVISIONAL; with the sofa's stable
  occupancy on: WATCHING.
- Every occupancy signal off (the sofa empties, no living-room track, no
  zone occupied): AWAY_HOLD, ``tv_watching`` stays on up to ``AWAY_HOLD_S``;
  any return goes back to PROVISIONAL or WATCHING.
- UNATTENDED only when neither the sofa's stable occupancy nor a living-room
  track has been seen for the whole hold. It is unreachable while either is
  on, with or without beliefs.
- NAPPING only from a belief (``P(rest == 2)``); without beliefs it is never
  entered and a machine in NAPPING falls back to PROVISIONAL.
- ``unavailable``/``unknown`` TV state keeps the current state for
  ``TV_UNAVAILABLE_GRACE_S``; an affirmative off resets to TV_OFF.
- ``tv_watching`` never flips within ``MIN_FLIP_INTERVAL_S`` of its last flip;
  a transition that would flip it sooner is deferred to a later tick.

Belief rule (adds to the above): WATCHING on two consecutive commits with
``P(attention >= 2) >= 0.60``; UNATTENDED after ``P(attention == 0) >= 0.80``
sustained 3 min from WATCHING or 5 min from PROVISIONAL, still never while
the sofa or a track is on; NAPPING after ``P(rest == 2) >= 0.60`` for 5 min
with someone seen. Every transition is journaled as a dict.

The caller must tick the machine periodically (every few seconds) as well as
on every input change; time-based transitions happen only inside ``update``.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .beliefs import Beliefs
from .stories import (AWAY_HOLD_S, MIN_FLIP_INTERVAL_S, NAPPING_S, P_NAPPING_REST_EQ2,
                      P_UNATTENDED_ATTENTION_EQ0, P_WATCHING_ATTENTION_GE2, TV_OFF_STATES,
                      TV_ON_STATES, TV_UNAVAILABLE_GRACE_S, UNATTENDED_OCCUPIED_S,
                      UNATTENDED_PROVISIONAL_S, WATCHING_COMMITS, elapsed_at_least, iso,
                      seconds_between)

TV_OFF = "TV_OFF"
PROVISIONAL = "PROVISIONAL"
WATCHING = "WATCHING"
AWAY_HOLD = "AWAY_HOLD"
UNATTENDED = "UNATTENDED"
NAPPING = "NAPPING"
STATES = (TV_OFF, PROVISIONAL, WATCHING, AWAY_HOLD, UNATTENDED, NAPPING)
WATCHING_STATES = frozenset({PROVISIONAL, WATCHING, AWAY_HOLD, NAPPING})

_MAX_CASCADE = 3


def _later(a: dt.datetime | None, b: dt.datetime | None) -> dt.datetime | None:
    """The later of two optional times; None when either is unknown."""
    if a is None or b is None:
        return None
    return max(a, b)


@dataclass(frozen=True)
class TvDecision:
    """What to publish for ``binary_sensor.living_lights_tv_watching``."""

    tv_watching: bool
    state: str
    p_attention: float | None
    since: dt.datetime
    request_id: str | None
    changed: bool

    def as_attributes(self) -> dict:
        return {"state_machine": self.state, "p_attention": self.p_attention,
                "since": iso(self.since), "request_id": self.request_id}

    @property
    def payload(self) -> str:
        return "ON" if self.tv_watching else "OFF"


class TvMachine:
    """The story T machine. Construct once, call ``update`` on every tick."""

    def __init__(self, journal: list[dict] | None = None) -> None:
        self.state = TV_OFF
        self.since: dt.datetime | None = None
        self.tv_watching = False
        self.journal: list[dict] = journal if journal is not None else []
        self._last_flip: dt.datetime | None = None
        self._unavailable_since: dt.datetime | None = None
        self._prev_room_occupied = False
        self._commits = 0
        self._last_commit_key: object = None
        self._att0_since: dt.datetime | None = None
        self._rest2_since: dt.datetime | None = None
        self._last_request_id: str | None = None

    # --- input conditioning -------------------------------------------------

    def _effective_tv(self, now: dt.datetime, tv_state: str | None) -> tuple[str, str]:
        """(``on``/``off``, reason). Unavailable keeps the state within the grace."""
        state = (tv_state or "").strip().lower()
        if state in TV_ON_STATES:
            self._unavailable_since = None
            return "on", "tv_on"
        if state in TV_OFF_STATES:
            self._unavailable_since = None
            return "off", "tv_off"
        if self._unavailable_since is None:
            self._unavailable_since = now
        if self.state != TV_OFF and not elapsed_at_least(self._unavailable_since, now, TV_UNAVAILABLE_GRACE_S):
            return "on", "tv_unavailable_grace"
        return "off", "tv_unavailable_expired" if self.state != TV_OFF else "tv_unavailable"

    def _track_beliefs(self, now: dt.datetime, beliefs: Beliefs | None) -> None:
        """Maintain commit counts and sustain windows from the belief stream."""
        if beliefs is None:
            self._commits = 0
            self._last_commit_key = None
            self._att0_since = None
            self._rest2_since = None
            self._last_request_id = None
            return
        self._last_request_id = beliefs.request_id
        key = (beliefs.request_id, beliefs.at)
        if key != self._last_commit_key:
            self._last_commit_key = key
            if beliefs.p_attention_ge(2) >= P_WATCHING_ATTENTION_GE2:
                self._commits += 1
            else:
                self._commits = 0
        if beliefs.p_attention_eq0 >= P_UNATTENDED_ATTENTION_EQ0:
            self._att0_since = self._att0_since or now
        else:
            self._att0_since = None
        if beliefs.p_rest_eq2 >= P_NAPPING_REST_EQ2:
            self._rest2_since = self._rest2_since or now
        else:
            self._rest2_since = None

    # --- transitions ----------------------------------------------------------

    def _next(self, now: dt.datetime, state: str, since: dt.datetime | None, tv: str,
              room: bool, sofa: bool, track: bool, room_rose: bool,
              beliefs: Beliefs | None) -> tuple[str, str] | None:
        """One transition from ``state`` given the inputs, or None to stay."""
        if tv == "off":
            return (None if state == TV_OFF else (TV_OFF, "tv_off"))
        occupied = room or sofa or track
        seen = sofa or track
        belief = beliefs is not None
        # Sustain windows count from the later of the belief onset and the
        # entry into the current state, so a belief that predates the state
        # cannot fire a transition on the first tick.
        att0_since = _later(self._att0_since, since)
        rest2_since = _later(self._rest2_since, since)
        napping = (belief and seen and elapsed_at_least(rest2_since, now, NAPPING_S))
        committed = belief and self._commits >= WATCHING_COMMITS

        if state == TV_OFF:
            if occupied:
                return PROVISIONAL, "occupied"
            return None
        if state == PROVISIONAL:
            if not occupied:
                return AWAY_HOLD, "room_empty"
            if sofa:
                return WATCHING, "sofa_stable"
            if napping:
                return NAPPING, "belief_rest"
            if committed:
                return WATCHING, "belief_attention_commits"
            if belief and not seen and elapsed_at_least(att0_since, now, UNATTENDED_PROVISIONAL_S):
                return UNATTENDED, "belief_attention_zero"
            return None
        if state == WATCHING:
            if not occupied:
                return AWAY_HOLD, "sofa_empty"
            if napping:
                return NAPPING, "belief_rest"
            if belief and not seen and elapsed_at_least(att0_since, now, UNATTENDED_OCCUPIED_S):
                return UNATTENDED, "belief_attention_zero"
            if not sofa and not committed:
                return PROVISIONAL, "sofa_stable_off"
            return None
        if state == AWAY_HOLD:
            if occupied:
                return PROVISIONAL, "return"
            if elapsed_at_least(since, now, AWAY_HOLD_S):
                return UNATTENDED, "away_hold_expired"
            return None
        if state == UNATTENDED:
            if seen:
                return PROVISIONAL, "person_seen"
            if room_rose:
                return PROVISIONAL, "room_occupied_rising"
            return None
        if state == NAPPING:
            if not occupied:
                return AWAY_HOLD, "room_empty"
            if not belief:
                return PROVISIONAL, "no_belief"
            if not elapsed_at_least(self._rest2_since, now, 0):
                return PROVISIONAL, "belief_rest_ended"
            return None
        raise AssertionError(f"unknown state {state}")

    def update(self, now: dt.datetime, tv_state: str | None, living_room_occupied: bool,
               sofa_stable: bool, living_room_track: bool,
               beliefs: Beliefs | None = None) -> TvDecision:
        """Advance the machine to ``now`` and return what to publish.

        ``living_room_occupied``: any living-room zone occupied per Frigate;
        ``sofa_stable``: the sofa's stable occupancy; ``living_room_track``: the
        observer sees a person track in the living room (False when the
        observer is stale).
        """
        if self.since is None:
            self.since = now
        tv, tv_reason = self._effective_tv(now, tv_state)
        room = bool(living_room_occupied)
        sofa = bool(sofa_stable)
        track = bool(living_room_track)
        room_rose = room and not self._prev_room_occupied
        self._prev_room_occupied = room
        self._track_beliefs(now, beliefs)

        state, since = self.state, self.since
        steps: list[tuple[str, str, str]] = []
        for _ in range(_MAX_CASCADE):
            hop = self._next(now, state, since, tv, room, sofa, track, room_rose, beliefs)
            if hop is None:
                break
            steps.append((state, hop[0], hop[1]))
            state, since = hop[0], now

        target_watching = state in WATCHING_STATES
        changed = False
        if steps:
            flip = target_watching != self.tv_watching
            if flip and self._last_flip is not None and not elapsed_at_least(self._last_flip, now, MIN_FLIP_INTERVAL_S):
                self.journal.append({"t": iso(now), "event": "flip_deferred", "from": self.state,
                                     "to": state, "reason": steps[-1][2],
                                     "since_last_flip_s": seconds_between(self._last_flip, now)})
            else:
                for src, dst, reason in steps:
                    self.journal.append({"t": iso(now), "event": "transition", "from": src, "to": dst,
                                         "reason": reason, "tv": tv_reason, "room": room, "sofa": sofa,
                                         "track": track, "belief": beliefs is not None,
                                         "request_id": self._last_request_id})
                self.state, self.since = state, now
                changed = True
                if flip:
                    self.tv_watching = target_watching
                    self._last_flip = now
        p_attention = beliefs.p_attention_ge(2) if beliefs is not None else None
        return TvDecision(tv_watching=self.tv_watching, state=self.state, p_attention=p_attention,
                          since=self.since, request_id=self._last_request_id, changed=changed)

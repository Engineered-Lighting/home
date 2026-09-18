"""Story S: the asleep estimator behind ``sensor.living_lights_asleep_estimator``.

States: ``likely_asleep``, ``awake``, ``away``.

Enter ``likely_asleep`` when, for ``IDLE_S`` (15 min): the local hour is in
[00:00, 08:00) or the profile is ``overnight``; no credible departure has
been seen; no credible person is on any camera; the TV is off, or with a
belief ``P(attention == 0) >= 0.80`` for 15 min, or without a belief the
legacy rule (the TV state is ignored, evidence ``no_answer_tv_rule =
legacy``); and no explicit brighten command in 15 min.

A credible person is a Frigate person on a camera corroborated by the
observer's typed presence when the observer is fresh, or Frigate alone when
the observer is stale. Raw motion is never an input here. A camera the fresh
observer contradicts still stops the quiet clock at each change of its
Frigate person count: a count that moves is somebody walking through a
camera the observer's slow typed reading missed, and a stuck sensor never
moves, so it stops holding the house awake from its last change on.

The quiet clock therefore runs from the last credible person: the last tick a
camera was credible, or the last person-count change on a discredited one.

Exit ``likely_asleep`` only on ``WAKE_S`` (10 min) of credible occupancy at
any hour, an explicit wake or brighten command, or a credible departure
(``away``). Occupancy counts up to the tick the run ends, so a ten-minute
kitchen trip clears the latch as the cameras go quiet again. ``away`` ends
with a credible arrival or 10 min of credible occupancy. An arrival is the
front door occupied while ``user_at_home`` is on: the door and the phone
edge within ``ARRIVAL_DOOR_WINDOW_S`` of each other, or the door alone when
the phone was already home (somebody walked in while the phone never left,
so there is no departure to pair with). A manual flip of the Home Assistant
latch (writer ``manual``) is followed for ``REARM_S`` (45 min). The decision
carries ``reassert`` when it disagrees with the mirrored latch so the caller
can republish.

Once ``likely_asleep`` is left, for any reason, it cannot be re-entered for
``REARM_S`` (45 min): the legacy automation's unconditional re-arm, which the
publisher shadows so the two paths cannot disagree about a wake-up. A latch
refused only by the re-arm records ``latch_refused = rearm`` in the evidence,
with ``rearm_age_s`` saying how long ago the latch was left; a manual flip is
never refused by it.

``likely_asleep`` is never entered once the household is up for the day:
credible occupancy sustained ``MORNING_UP_S`` inside the morning window (the
legacy ``woke up`` automation's rule). A guest on the sofa who steps off
camera at 07:00 has not just gone to bed, and 15 quiet minutes after that
must not latch the house asleep; the flag clears when the morning window
does.

Known limitation (S7): a stuck Frigate sensor keeps the house awake while
the observer is stale, exactly like the legacy latch; with a fresh observer
that sees nobody, Frigate alone is not credible and the house latches.

Tick the estimator at least once a minute; time-based transitions happen
only inside ``update``.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Mapping

from .stories import (ARRIVAL_DOOR_WINDOW_S, BRIGHTEN_QUIET_S, DEPARTURE_CONFIRM_S,
                      DEPARTURE_DOOR_WINDOW_S, IDLE_S, MANUAL_FLIP_HONORED_S, MANUAL_WRITERS,
                      MORNING_UP_S, OVERNIGHT_PROFILE, P_TV_ATTENTION_EQ0, REARM_S,
                      TV_ATTENTION_QUIET_S, TV_ON_STATES, WAKE_S, elapsed_at_least,
                      in_morning_window, in_night_window, iso, seconds_between)

LIKELY_ASLEEP = "likely_asleep"
AWAKE = "awake"
AWAY = "away"
STATES = (LIKELY_ASLEEP, AWAKE, AWAY)


@dataclass(frozen=True)
class CameraSignal:
    """One camera's person evidence for this tick.

    ``frigate_person``: ``frigate/<camera>/person`` count above zero;
    ``observer_present``: the observer's typed presence for the camera, None
    when the observer has no reading; ``observer_fresh``: the reading is
    younger than ``OBSERVER_FRESH_S``.
    """

    frigate_person: bool
    frigate_changed: dt.datetime | None = None
    observer_present: bool | None = None
    observer_fresh: bool = False

    @property
    def credible(self) -> bool:
        if not self.frigate_person:
            return False
        if self.observer_fresh and self.observer_present is not None:
            return bool(self.observer_present)
        return True


@dataclass(frozen=True)
class EstimatorInputs:
    """Everything the estimator looks at on one tick."""

    local_time: dt.datetime
    profile: str | None = None
    user_at_home: bool | None = None
    user_at_home_changed: dt.datetime | None = None
    front_door_occupied: bool = False
    front_door_changed: dt.datetime | None = None
    cameras: Mapping[str, CameraSignal] = field(default_factory=dict)
    tv_state: str | None = None
    p_attention0: float | None = None
    last_brighten: dt.datetime | None = None
    last_wake: dt.datetime | None = None
    latch_on: bool | None = None
    latch_writer: str | None = None
    latch_changed: dt.datetime | None = None


@dataclass(frozen=True)
class AsleepDecision:
    """What to publish for ``sensor.living_lights_asleep_estimator``."""

    state: str
    since: dt.datetime
    evidence: dict
    reassert: bool
    changed: bool

    @property
    def asleep(self) -> bool:
        return self.state == LIKELY_ASLEEP

    def as_attributes(self) -> dict:
        return {"since": iso(self.since), "reassert": self.reassert, **self.evidence}


class AsleepEstimator:
    """The story S estimator. Construct once, call ``update`` every tick."""

    def __init__(self, journal: list[dict] | None = None, initial: str = AWAKE) -> None:
        if initial not in STATES:
            raise ValueError(initial)
        self.state = initial
        self.since: dt.datetime | None = None
        self.journal: list[dict] = journal if journal is not None else []
        self._quiet_since: dt.datetime | None = None
        self._occupied_since: dt.datetime | None = None
        self._att0_since: dt.datetime | None = None
        self._last_credible_at: dt.datetime | None = None
        self._frigate_edges: dict[str, dt.datetime] = {}
        self._morning_occupied_since: dt.datetime | None = None
        self._up_for_the_day = False
        self._manual_key: tuple | None = None
        self._left_asleep_at: dt.datetime | None = None
        self._departure_at: dt.datetime | None = None
        self._arrival_at: dt.datetime | None = None
        self._last_door_on: dt.datetime | None = None
        self._last_home_on: dt.datetime | None = None
        self._prev_home: bool | None = None
        self._prev_door = False

    # --- evidence -------------------------------------------------------------

    def _credible_cameras(self, inputs: EstimatorInputs) -> list[str]:
        return sorted(name for name, sig in inputs.cameras.items() if sig.credible)

    def _person_edge(self, inputs: EstimatorInputs) -> dt.datetime | None:
        """The newest Frigate person-count change this tick reports first.

        ``frigate/<camera>/person`` is a count: it moves whenever somebody
        appears or leaves, even while the camera stays above zero. The moment
        it moves is the last proof of a person there, and it is all a camera
        the fresh observer contradicts contributes.
        """
        newest: dt.datetime | None = None
        for name, sig in inputs.cameras.items():
            when = sig.frigate_changed
            if when is None or self._frigate_edges.get(name) == when:
                continue
            self._frigate_edges[name] = when
            if newest is None or when > newest:
                newest = when
        return newest

    def _track_occupancy(self, now: dt.datetime, credible: bool,
                         edge: dt.datetime | None) -> dt.datetime | None:
        """Advance the quiet and occupancy clocks; return the credible run.

        The returned run is the one in progress, or the one that ended on
        this very tick, so ``WAKE_S`` of occupancy is still an exit on the
        tick the cameras clear.
        """
        run = self._occupied_since
        if credible:
            self._occupied_since = run or now
            self._quiet_since = None
            self._last_credible_at = now
            return self._occupied_since
        self._occupied_since = None
        quiet_from = now if self._quiet_since is None else self._quiet_since
        if edge is not None and quiet_from < edge <= now:
            quiet_from = edge
        self._quiet_since = quiet_from
        return run

    def _track_morning(self, now: dt.datetime, inputs: EstimatorInputs, credible: bool) -> None:
        """Latch "the household is up for the day" inside the morning window."""
        if not in_morning_window(inputs.local_time):
            self._morning_occupied_since = None
            self._up_for_the_day = False
            return
        if not credible:
            self._morning_occupied_since = None
            return
        self._morning_occupied_since = self._morning_occupied_since or now
        if elapsed_at_least(self._morning_occupied_since, now, MORNING_UP_S):
            self._up_for_the_day = True

    def _track_presence(self, now: dt.datetime, inputs: EstimatorInputs) -> None:
        """Front-door and phone edges for arrival and departure detection."""
        if inputs.front_door_occupied and not self._prev_door:
            self._last_door_on = inputs.front_door_changed or now
        self._prev_door = bool(inputs.front_door_occupied)
        home = inputs.user_at_home
        if home is True and self._prev_home is not True:
            self._last_home_on = inputs.user_at_home_changed or now
        self._prev_home = home

    def _credible_arrival(self, now: dt.datetime, inputs: EstimatorInputs) -> dt.datetime | None:
        """When the phone and the front door last agreed that somebody came in.

        The door turning occupied while ``user_at_home`` is already on is an
        arrival by itself: an arrival need not follow a departure, and the
        phone that never left has no edge to pair with. The other order needs
        the pairing: a phone that comes home long after a door reading is a
        GPS fix catching up, not somebody at the door.
        """
        if inputs.user_at_home is not True or self._last_door_on is None:
            return None
        home_on = self._last_home_on
        if home_on is None or self._last_door_on >= home_on:
            return self._last_door_on
        if (home_on - self._last_door_on).total_seconds() <= ARRIVAL_DOOR_WINDOW_S:
            return home_on
        return None

    def _credible_departure(self, now: dt.datetime, inputs: EstimatorInputs, credible_now: bool) -> str | None:
        """Why the phone-away reading counts as a departure, or None."""
        if inputs.user_at_home is not False or credible_now:
            return None
        left_at = inputs.user_at_home_changed
        if left_at is None:
            return None
        if self._last_credible_at is not None and self._last_credible_at > left_at:
            return None
        if self._last_door_on is not None and 0 <= (left_at - self._last_door_on).total_seconds() <= DEPARTURE_DOOR_WINDOW_S:
            return "door_then_phone_off"
        if elapsed_at_least(left_at, now, DEPARTURE_CONFIRM_S):
            return "phone_off_confirmed"
        return None

    def _tv_quiet(self, now: dt.datetime, inputs: EstimatorInputs) -> tuple[bool, str]:
        """(TV allows sleep, rule name)."""
        tv_on = (inputs.tv_state or "").strip().lower() in TV_ON_STATES
        if not tv_on:
            self._att0_since = None
            return True, "tv_off"
        if inputs.p_attention0 is None:
            self._att0_since = None
            return True, "legacy"
        if inputs.p_attention0 >= P_TV_ATTENTION_EQ0:
            self._att0_since = self._att0_since or now
            return elapsed_at_least(self._att0_since, now, TV_ATTENTION_QUIET_S), "belief"
        self._att0_since = None
        return False, "belief"

    def _manual_hold(self, now: dt.datetime, inputs: EstimatorInputs) -> bool:
        writer = (inputs.latch_writer or "").strip().lower()
        if writer not in MANUAL_WRITERS or inputs.latch_changed is None or inputs.latch_on is None:
            return False
        age = seconds_between(inputs.latch_changed, now)
        return age is not None and 0 <= age < MANUAL_FLIP_HONORED_S

    # --- the tick -------------------------------------------------------------

    def _transition(self, now: dt.datetime, to: str, reason: str, evidence: dict) -> None:
        self.journal.append({"t": iso(now), "event": "transition", "from": self.state, "to": to,
                             "reason": reason, "evidence": dict(evidence)})
        if self.state == LIKELY_ASLEEP and to != LIKELY_ASLEEP:
            # Leaving the latch, by any door, starts the REARM_S guard.
            self._left_asleep_at = now
        self.state, self.since = to, now

    def update(self, now: dt.datetime, inputs: EstimatorInputs) -> AsleepDecision:
        """Advance to ``now`` with this tick's inputs; return what to publish."""
        if self.since is None:
            self.since = now
        credible_cameras = self._credible_cameras(inputs)
        credible_now = bool(credible_cameras)
        occupied_run = self._track_occupancy(now, credible_now, self._person_edge(inputs))
        self._track_morning(now, inputs, credible_now)
        self._track_presence(now, inputs)
        tv_ok, tv_rule = self._tv_quiet(now, inputs)
        brighten_age = seconds_between(inputs.last_brighten, now)
        wake_age = seconds_between(inputs.last_wake, now)
        window = in_night_window(inputs.local_time) or inputs.profile == OVERNIGHT_PROFILE
        departure = self._credible_departure(now, inputs, credible_now)
        arrival = self._credible_arrival(now, inputs)
        manual = self._manual_hold(now, inputs)
        quiet_s = seconds_between(self._quiet_since, now)
        occupied_s = seconds_between(occupied_run, now)
        evidence: dict = {
            "window": window, "profile": inputs.profile, "local_hour": inputs.local_time.hour,
            "credible_cameras": credible_cameras,
            "observer_fresh": sorted(n for n, s in inputs.cameras.items() if s.observer_fresh),
            "quiet_s": quiet_s, "occupied_s": occupied_s,
            "tv_state": inputs.tv_state, "tv_rule": tv_rule, "tv_ok": tv_ok,
            "brighten_age_s": brighten_age, "user_at_home": inputs.user_at_home,
            "departure": departure, "arrival": iso(arrival), "manual_hold": manual,
            "up_for_the_day": self._up_for_the_day,
            "rearm_age_s": seconds_between(self._left_asleep_at, now),
            "latch_on": inputs.latch_on, "latch_writer": inputs.latch_writer,
        }
        if tv_rule == "legacy":
            evidence["no_answer_tv_rule"] = "legacy"

        before = self.state
        if manual:
            key = (inputs.latch_changed, inputs.latch_on)
            wanted = LIKELY_ASLEEP if inputs.latch_on else AWAKE
            if key != self._manual_key:
                self._manual_key = key
                # The manual flip restarts the quiet and occupancy clocks so the
                # rules resume from the flip, not from before it.
                self._quiet_since = inputs.latch_changed if not credible_now else None
                self._occupied_since = inputs.latch_changed if credible_now else None
                if self.state != wanted:
                    self._transition(now, wanted, "manual", evidence)
                else:
                    self.journal.append({"t": iso(now), "event": "manual_hold", "state": wanted})
        else:
            self._manual_key = None
            self._advance(now, inputs, evidence, credible_now, tv_ok, brighten_age, wake_age,
                          window, departure, arrival, occupied_run)

        changed = self.state != before
        expected_latch = self.state == LIKELY_ASLEEP
        reassert = (not manual and inputs.latch_on is not None and inputs.latch_on != expected_latch)
        evidence["reason"] = self.journal[-1].get("reason") if changed and self.journal else None
        return AsleepDecision(state=self.state, since=self.since, evidence=evidence,
                              reassert=reassert, changed=changed)

    def _advance(self, now: dt.datetime, inputs: EstimatorInputs, evidence: dict, credible_now: bool,
                 tv_ok: bool, brighten_age: float | None, wake_age: float | None, window: bool,
                 departure: str | None, arrival: dt.datetime | None,
                 occupied_run: dt.datetime | None) -> None:
        """The rule table when no manual hold applies."""
        since = self.since
        occupied_long = elapsed_at_least(occupied_run, now, WAKE_S)
        wake_cmd = inputs.last_wake is not None and since is not None and inputs.last_wake > since
        brighten_cmd = inputs.last_brighten is not None and since is not None and inputs.last_brighten > since
        arrived = arrival is not None and since is not None and arrival > since

        if self.state == LIKELY_ASLEEP:
            if departure:
                self._transition(now, AWAY, "departure_" + departure, evidence)
            elif arrived:
                self._transition(now, AWAKE, "arrival", evidence)
            elif wake_cmd:
                self._transition(now, AWAKE, "wake_command", evidence)
            elif brighten_cmd:
                self._transition(now, AWAKE, "brighten_command", evidence)
            elif occupied_long:
                self._transition(now, AWAKE, "occupancy", evidence)
            return
        if self.state == AWAY:
            if arrived:
                self._transition(now, AWAKE, "arrival", evidence)
            elif occupied_long:
                self._transition(now, AWAKE, "occupancy", evidence)
            return
        # AWAKE
        if departure:
            self._transition(now, AWAY, "departure_" + departure, evidence)
            return
        quiet_long = elapsed_at_least(self._quiet_since, now, IDLE_S)
        brighten_quiet = brighten_age is None or brighten_age >= BRIGHTEN_QUIET_S
        if (window and not self._up_for_the_day and not credible_now and quiet_long
                and tv_ok and brighten_quiet):
            if self._rearmed(now):
                self._transition(now, LIKELY_ASLEEP, "quiet_window", evidence)
            else:
                # Everything else agrees; only the legacy re-arm says no.
                evidence["latch_refused"] = "rearm"

    def _rearmed(self, now: dt.datetime) -> bool:
        """True when ``REARM_S`` has passed since ``likely_asleep`` was left."""
        return self._left_asleep_at is None or elapsed_at_least(self._left_asleep_at, now, REARM_S)

"""Den spotlight preview. Tracking and encoded commands share one applied state.

The rules here decide what each fixture is for and where it points; direction.py decides how each change is played (the
lighting-direction note): a rise, an exit or a hand-off is a level train of stationary MOVEs on an eased curve, a move is
one MOVE flown on the board's own S-curve, and the room's glow dims under a task light. Published state is always what
the trace has committed: each level step lights only once its row is written."""

import dataclasses
import datetime
import json
import math
import os
import queue
import threading
import time
import uuid
from pathlib import Path

import bulb_plan
import direction
from contracts import PERCEPTION_S
from preview_evidence import Episode, field, finite, head as published_head, pose, seated as published_seated
import preview_trace
from preview_trace import PreviewTrace


POSE_AGE_S = PERCEPTION_S
FRAME_S = .1
LEASE_S = 1.0
# The entry rule (spine decision 4, the owner's ruling): one confident yes lights. A later answer confirms it, starts
# the patience that ends it (phone, screen, closed book), or ends it at once (none or several people in the stills).
ENTRY_ANSWERS = 1
# The keep rule's time hysteresis (contract 10, "For 04"): both sofa sockets sit within 3 degrees of side-on, so a
# fixture lit for someone stays theirs through a facing refusal that hasn't lasted this long.
KEEP_HOLD_S = 1.0
# The gap hold (plan 05 section 3.5, contract 4): the camera's newest frame can go 1.5-2.4 s without changing, about 5
# times a minute. A spot already lit stays lit while its person's newest fresh frame is at most this old, the same 3.0 s
# at which contract 2 ends the camera epoch; keep-lit goes on through the gap. Lighting still needs a pose at most
# POSE_AGE_S old, and evidence on a fresh frame (standing, leaving, a second person) darkens at once.
GAP_HOLD_S = 3.0
FACING_REFUSALS = ("in_front_of_reader", "no_facing")
# The direction's choice hysteresis (lighting-direction note): a dark pre-aim moves at most once a second, and while the
# person is on their feet only for a torso move of more than this.
PRE_AIM_STANDING_M = .3
# The standby: while a spot is lit for a task, the best other admitted fixture is aimed at the same page, dark, so a
# hand-off is a crossfade and never a dark move. It re-aims when the page has moved this far.
STANDBY_M = .1
# Exits a fixture can come back from (its job wasn't refused): it's still the incumbent while it fades.
RECOVERABLE = ("task_released", "task_ending")
CONTROL_KEYS = ("on", "stopped", "stop_counter")
BINDING_KEYS = ("transform_id", "scan_revision", "equipment_revision", "equipment_digest")


class Preview:
    def __init__(self, core, *, binding, fixtures, state, seat_mark=None, clock=time.monotonic,
                 publish=None, append=None, threaded=True, bulb_lights=(), utc_offset_h=None):
        self.core, self.clock = core, clock
        self.binding = {key: binding[key] for key in BINDING_KEYS}
        revision = self.binding["equipment_revision"]
        if (type(revision) is not int or revision < 0 or not fixtures or
                not all(isinstance(self.binding[key], str) and self.binding[key] for key in BINDING_KEYS if key != "equipment_revision")):
            raise ValueError("registered fixture geometry required")
        self.folder = Path(state) / "coordinator"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.settings = {**core.SETTINGS, **direction.SETTINGS, **self._read("settings.json", {})}
        controls = self._read("preview-control.json", {"on": True, "stopped": True, "stop_counter": 0})
        self.on = controls.get("on", True) is True
        self.stopped = controls.get("stopped", False) is not False
        self.stop_counter = controls.get("stop_counter", 0)
        if type(self.stop_counter) is not int or self.stop_counter < 0:
            raise ValueError("invalid persisted Stop counter")
        revision = controls.get("control_revision", 0)
        self.control_revision = revision if type(revision) is int and revision >= 0 else 0
        self.control_lock, self.settings_lock, self.lock = threading.Lock(), threading.Lock(), threading.RLock()
        self.epoch = uuid.uuid4().hex
        self.seq = self.decision_seq = 0
        self.seat_mark = seat_mark
        self.publish = publish or (lambda _snapshot: None)
        self.threaded = threaded
        self.jobs = queue.Queue(maxsize=64)
        self.halt = threading.Event()
        self.rows = {}
        # A timed MOVE goes only where the board advertises it and the pinned codec can encode it (the desk-stage
        # re-pin to firmware 62ea043 adds preview_trace.supports_timed); elsewhere the level train plays each fade.
        timed_codec = bool(getattr(preview_trace, "supports_timed", lambda: False)())
        for name, row in fixtures.items():
            profile = row.get("profile") or core.scurve.FICTIONAL_PROFILE
            origin, reference = row["aim_origin_m"], row["horizontal_world_reference"]
            if (not all(len(point) == 3 and all(map(finite, point)) for point in (origin, reference))
                    or not math.hypot(*reference[:2])):
                raise ValueError("invalid fixture geometry")
            current = tuple(row.get("initial_cdeg", (0, 0)))
            ranges = tuple(tuple(core.joints.to_cdeg(x + inset) for x, inset in
                                 zip(profile["range"][axis], (core.joints.INSET_DEG, -core.joints.INSET_DEG)))
                           for axis in ("pan", "tilt"))
            if (len(current) != 2 or any(type(a) is not int or not lo <= a <= hi
                                        for a, (lo, hi) in zip(current, ranges))):
                raise ValueError("invalid initial fixture aim")
            # glide: the MOVE the board is flying (direction.Glide); ramp: the level curve its train samples;
            # lit_until: the last validity this fixture's light was published with, which an exit never outlasts.
            self.rows[name] = dict(row, profile=profile, ranges=ranges, current=current, level=0.0,
                                   pending=None, motion=None, move=None, token=None, keep_at=0.0,
                                   target=None, basis=None, job=None, reason="waiting_for_tracking", pre_at=None,
                                   eye_hold=None, glide=None, ramp=None, ramp_at=None, goal=0.0, exiting=None,
                                   lit_until=None, ended_at=None, dip=None, arrive_level=None,
                                   lit_subject=None, step_at=None, step_s=direction.level_step_s(profile),
                                   timed=timed_codec and direction.capabilities(profile)["timed_move"],
                                   join=direction.capabilities(profile)["join_flight"])
        self.wall = time.time   # the trace's day files and its run header's wall anchor
        self.trace_day = None   # the UTC day file this run last wrote to: a new one starts with the run header
        self.trace = PreviewTrace(self.epoch, append or self._append, binding=self.binding,
                                  profiles={name: row["profile"] for name, row in self.rows.items()},
                                  clock=self.clock, wall=lambda: self.wall())
        self.scope = None
        self.retired_runs = set()
        self.track_seq = -1
        self.track_binding = self.track = self.track_until = self.episode = self.eligibility = None
        self.hold_until = None   # while lit, the track holds to here through a camera gap (GAP_HOLD_S)
        self.eligible_track = None
        self.captured = None   # the newest frame taken for the track
        self.since = None      # the published seated since_mono the episode was built at
        self.shift = None
        self.target_basis = None
        self.facing = direction.FacingFilter()
        self.ambient = direction.Ambient()
        self.standby = None   # the runner-up for the lit person, aimed dark at their page
        self.admitted = {}    # this evaluation's other admitted fixtures: {name: (eye result, joints)}
        self.latest_decision = self.latest = self.problem = None
        self.context_signature, self.context_since = None, self.clock()
        self.counts = {"snapshots": 0, "refused": 0, "trace_errors": 0}
        self.trace_committed = 0
        self.trace_complete = True
        self.worker = self.ticker = None
        self.hue = None
        self.site = None
        # The bulbs (bulb_plan.py): planned and published always, sent only while this switch is On. A missing or
        # unreadable file is Off, and neither On nor a restart sends anything by itself.
        self.bulbs_on = self._read("bulbs-control.json", {}).get("on") is True
        self.bulb_lights = tuple(bulb_lights or ())
        self.utc_offset_h = (time.localtime().tm_gmtoff / 3600.0) if utc_offset_h is None else float(utc_offset_h)
        self.bulb = self.bulb_since = None
        self._publish(self.clock())

    def _read(self, name, fallback):
        try:
            value = json.loads((self.folder / name).read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else fallback
        except FileNotFoundError:
            return {"on": True, "stopped": False, "stop_counter": 0} if name == "preview-control.json" else fallback
        except (OSError, ValueError):
            return fallback

    def _save(self, name, data):
        path = self.folder / name
        part = path.with_suffix(".new")
        part.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")
        os.replace(part, path)

    def _append(self, row):
        """One fsynced trace row in days/<UTC date>/coordinator-fixture_calls.jsonl. The first row this run writes to a
        day's file follows the run header, so a run that crosses UTC midnight leaves each file whole on its own."""
        day = datetime.datetime.fromtimestamp(self.wall(), datetime.timezone.utc).strftime("%Y-%m-%d")
        folder = self.folder.parent / "days" / day
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "coordinator-fixture_calls.jsonl").open("a", encoding="utf-8") as log:
            if day != self.trace_day:
                log.write(json.dumps(self.trace.header, allow_nan=False, separators=(",", ":")) + "\n")
            log.write(json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n")
            log.flush()
            os.fsync(log.fileno())
        self.trace_day = day

    def connect(self, publish):
        with self.lock:
            self.publish = publish
            self._publish(self.clock())

    def start(self):
        if self.stopped:
            with self.lock:
                for name in self.rows:
                    self._submit(name, "stop", None)
        if self.threaded:
            self.worker = threading.Thread(target=self._write, name="den-preview-trace", daemon=True)
            self.ticker = threading.Thread(target=self._tick, name="den-preview-clock", daemon=True)
            self.worker.start()
            self.ticker.start()
        return self

    def close(self):
        self.halt.set()
        for thread in (self.worker, self.ticker):
            if thread is not None:
                thread.join(1)

    def _tick(self):
        while not self.halt.wait(FRAME_S):
            self.tick()

    def tick(self):
        with self.lock:
            self._evaluate(self.clock())

    def _drop_track(self):
        """The person binding changed (any of its four keys), or a control reset it: the episode, its onset, the need
        state and its requests all go."""
        self.track_binding = self.track = self.track_until = self.hold_until = self.episode = self.eligibility = None
        self.eligible_track = None
        self.captured = self.since = None
        self.shift = self.target_basis = None
        self.facing = direction.FacingFilter()

    def _darken(self):
        """Contract 2's consumer rule: a stale snapshot or an unsafe frame darkens. No light until a current, posed,
        seated frame of the same person; the episode, its onset, the need state and the requests stay."""
        self.track = self.track_until = self.hold_until = None

    def tracking(self, snapshot):
        with self.lock:
            now = self.clock()
            self._expire(now)
            scope = (field(snapshot, "service_run_id"), field(snapshot, "camera_epoch"))
            seq = field(snapshot, "seq")
            if not isinstance(scope[0], str) or type(scope[1]) is not int or scope[0] in self.retired_runs or type(seq) is not int:
                return
            if self.scope is not None and scope[0] == self.scope[0] and scope[1] < self.scope[1]:
                return
            if scope != self.scope:
                if self.scope is not None and scope[0] != self.scope[0]:
                    self.retired_runs.add(self.scope[0])
                self.scope, self.track_seq = scope, -1
                self._drop_track()
            if seq <= self.track_seq:
                return
            self.track_seq = seq
            frame = field(snapshot, "frame")
            captured = field(frame, "capture_mono")
            deadline = captured + POSE_AGE_S if finite(captured) else -math.inf
            tracks = field(snapshot, "tracks", ())
            if field(snapshot, "status") != "fresh" or not finite(captured) or not captured <= now < deadline:
                # No new frame (a camera gap: perception's stale snapshots) or a late one: nothing to learn here.
                # _expire darkens at the light's deadline, or, while a spot is lit, at the gap hold's.
                pass
            elif (field(snapshot, "presence") != "present" or len(tracks) != 1
                    or any(field(frame, k) != self.binding[k] for k in ("transform_id", "scan_revision"))):
                self._darken()
            else:
                eligibility = field(snapshot, "eligibility_revision")
                if self.eligibility is not None and eligibility != self.eligibility:
                    self.episode = self.shift = self.target_basis = None
                self._track(tracks[0], captured, deadline, captured + GAP_HOLD_S, now)
                # The ask names the eligible track only on a frame that may start an ask. A bad frame publishes None
                # with the same eligibility revision, and the person stays eligible for the reply already in flight.
                eligible = field(snapshot, "eligible_track_id")
                if eligible is not None or eligibility != self.eligibility:
                    self.eligible_track = eligible
                self.eligibility = eligibility
            self._evaluate(now)

    offer_snapshot = tracking

    def _track(self, track, captured, deadline, hold, now):
        key = (*self.scope, field(track, "track_id"), field(track, "association_revision"))
        if None in key:
            self._darken()
            return
        if key != self.track_binding:
            self._drop_track()
            self.track_binding = key
        joints = pose(track)
        if (field(track, "state") != "confirmed" or field(track, "association_status") != "confident"
                or field(track, "position_basis") != "stereo" or not field(track, "current_obs_ids") or not joints):
            self._darken()
            return
        if self.captured is not None and captured <= self.captured:
            return
        body = self.core.body
        head = published_head(track) or body.head(joints)   # contract 2's head_point_m, perception's
        if head and not body.head(joints):
            joints["nose"] = [*head, 1.0]
        self.captured = captured
        self.track_until, self.hold_until = deadline, hold
        # Contract 2's seated, published once by perception: the episode starts at its since_mono, the same instant
        # the ask's stills start from. It's rebuilt only when that published since moves, never on episode.started:
        # a none or multiple reply moves started on to the negative, and the episode must keep that.
        seated, since = published_seated(track, captured)
        if seated and (self.episode is None or since != self.since):
            policy = self.core.need_v1.make_policy({"phone_patience_s": self.settings["phone_patience_s"],
                                                    "dwell": {"entry_samples": ENTRY_ANSWERS}})
            self.episode = Episode(policy, key, since, (self.binding["transform_id"], self.binding["scan_revision"]))
            self.since = since
            self.shift = direction.AimFilter(self.settings["shift_m"], self.settings["shift_hold_s"])
        if not seated:
            self.episode = self.shift = self.target_basis = None
        # The direction's facing: seated, the circular mean of the last 3 s, so a side-on fixture isn't flipped in
        # and out of "in front of you" by the shoulders' frame-to-frame swing.
        facing = self.facing.update(captured, body.facing(joints), seated)
        shoulders = body.shoulder_mid(joints) or head
        target, basis = shoulders, "torso"
        if seated:
            # The page, not the wrists: the aim filter's settled median, which a page turn never moves.
            fallback, fallback_basis = body.task_point(joints, facing, self.seat_mark)
            page = direction.page_point(joints, self.settings["page_lift"])
            self.shift.shift_m, self.shift.hold_s = self.settings["shift_m"], self.settings["shift_hold_s"]
            target, basis = self.shift.update(captured, page, fallback, fallback_basis, self._lit_for(key[2]))
            self.target_basis = basis
        self.track = {"track_id": key[2], "head": head, "shoulder_mid": shoulders, "facing": facing,
                      "target": target, "basis": basis, "seated": seated}

    def inference(self, reply):
        with self.lock:
            now = self.clock()
            self._expire(now)
            if (self.episode is not None and self.on and not self.stopped
                    and field(reply, "eligibility_revision") == self.eligibility
                    and field(reply, "track_id") == self.eligible_track
                    and finite(field(reply, "observed_mono")) and field(reply, "observed_mono") <= self.captured):
                self.episode.accept(reply, now, self.settings["phone_patience_s"])
            self._evaluate(now)

    offer_inference = inference

    @staticmethod
    def _lighting(row):
        """The fixture is lit, rising or has a lit command in flight."""
        return (row["token"] is not None or row["level"] > 0 or (row["motion"] and row["motion"]["level"] > 0)
                or (row["pending"] and row["pending"]["level"] > 0) or bool(row["ramp"] and row["ramp"].end > 0))

    def _lit(self):
        return any(self._lighting(row) for row in self.rows.values())

    def _lit_for(self, track_id):
        """A light is on for this person: lit, rising, or leaving in a way it can come back from."""
        return any(row["job"] == track_id and self._lighting(row) and row["exiting"] in (None, *RECOVERABLE)
                   for row in self.rows.values())

    def _expire(self, now):
        if self.track_until is not None and now >= self.track_until and (now >= self.hold_until or not self._lit()):
            self._darken()
        if self.episode is not None:
            self.episode.expire(now, self.settings["phone_patience_s"])

    def task_until(self):
        if self.episode is None:
            return None
        return self.episode.deadline(self.settings["phone_patience_s"])

    def _eye(self, name, row, person, kept):
        """den-core's eye rule for this fixture, with the direction's spatial hysteresis on glare: a fixture lit for this
        person is refused for its beam's centre passing the head only once it's inside GLARE_KEEP_M (admission keeps
        den-core's 0.15 m), so a few centimetres of head jitter can't flip it."""
        eye = self.core.eye
        result = eye.evaluate(name, row, person, person["target"], kept)
        if kept and result.reason == "beam_through_head":
            aim = eye.aim
            reader = aim.Reader(tuple(person["shoulder_mid"]), tuple(person["facing"][:2]) + (0.0,), "shoulders",
                                tuple(person["head"]), "pose", tuple(person["target"]))
            fixture = aim.Fixture(name, tuple(row["aim_origin_m"]), tuple(row["horizontal_world_reference"]),
                                  dict(aim.DEFAULT_LIMITS))
            held = aim.evaluate_fixture(fixture, reader, dataclasses.replace(
                eye._KEEP, head_clearance_m=direction.GLARE_KEEP_M))
            result = held if held.ok else result
        return result

    def _candidate(self, person, now=None):
        core = self.core
        now = self.clock() if now is None else now
        candidates, reasons, joints = {}, {}, {}
        incumbent = pre_aimed = None
        for name, row in self.rows.items():
            # An exiting fixture is never the incumbent: a refused light that's fading out can't win its job back
            # in the middle of the crossfade (that was a hand-off ping-pong).
            kept = row["job"] == person["track_id"] and row["exiting"] in (None, *RECOVERABLE) and (
                row["token"] is not None or self._lighting(row))
            result = self._eye(name, row, person, kept)
            if not kept:
                row["eye_hold"] = None
            elif result.ok:
                row["eye_hold"] = (now, person["facing"])
            elif result.reason in FACING_REFUSALS and row["eye_hold"] and now - row["eye_hold"][0] < KEEP_HOLD_S:
                # Facing jitter: judge the lit fixture by the last facing that kept it, until the refusal has lasted
                # KEEP_HOLD_S.
                held = self._eye(name, row, dict(person, facing=row["eye_hold"][1]), kept)
                result = held if held.ok else result
            if not result.ok:
                reasons[name] = result.reason
                continue
            try:
                target = tuple(map(core.joints.to_cdeg, core.joints.room_to_joints(
                    person["target"], row["aim_origin_m"], row["horizontal_world_reference"])))
            except ValueError:
                reasons[name] = "ambiguous_aim"
                continue
            if any(not lo <= a <= hi for a, (lo, hi) in zip(target, row["ranges"])):
                reasons[name] = "out_of_range"
                continue
            if any(abs(a - b) > 18000 for a, b in zip(target, row["current"])):
                reasons[name] = "route_over_180"
                continue
            candidates[name], joints[name] = result, target
            if kept:
                incumbent = name
            elif row["job"] == person["track_id"] and row["reason"] == "pre_aim":
                pre_aimed = name
        # A lit fixture keeps its job (den-core's rule); with none lit, the one already pre-aimed for this person keeps
        # its pre-aim while it's admitted, so the dark aim doesn't hop between two near-equal sockets.
        mine = incumbent or pre_aimed
        selected = core.share.pick_for_person(candidates, self.rows, person["target"], mine) if candidates else None
        self.admitted = {name: (candidates[name], joints[name]) for name in candidates if name != selected}
        return selected, joints.get(selected), reasons

    def _evaluate(self, now):
        self._expire(now)
        self._context(now)
        person = self.track
        # A light needs a current, posed, seated frame: a reply that lands while the frames are stale, poseless or
        # unsafe (say, an ask sent as the person stood up) is taken into the episode but lights nothing.
        task = self.task_until() is not None and bool(person and person["seated"])
        # In a camera gap (the newest frame older than POSE_AGE_S, held while lit up to GAP_HOLD_S): the fixture lit for
        # this person keeps its job, aim and level, and nothing new is aimed or lit.
        held = person is not None and now >= self.track_until
        directing = self.on and not self.stopped
        for row in self.rows.values():
            self._advance(row, now, task and directing)
        chosen, target, reasons = None, None, {}
        self.admitted = {}
        if held:
            if task:
                chosen = next((name for name, row in self.rows.items() if row["job"] == person["track_id"]
                               and self._lighting(row) and not row["exiting"]), None)
            if chosen is not None:
                row = self.rows[chosen]
                target = row["glide"].target if row["glide"] else row["current"]
        elif person and person["target"] is not None and (task or self.settings["pre_aim"]):
            chosen, target, reasons = self._candidate(person, now)
        diagnostic = {"kind": "task" if task else "pre_aim", "fixture": chosen, "refused": reasons,
                      "target_basis": person and person["basis"], "joints_cdeg": list(target) if target else None,
                      "gap_hold": held}
        subject = self._subject()
        previous = self.latest_decision or {}
        if diagnostic != previous.get("proposal") or subject != previous.get("subject"):
            self.decision_seq += 1
            self.latest_decision = {"version": "decision/v3", "decision_id": f"{self.epoch}-{self.decision_seq}",
                                    "actuation": "shadow", "subject": subject, "proposal": diagnostic}
        # The room's glow follows the task light: it dims a beat after the spot starts and comes back as it ends.
        self.ambient.direct(directing and task and chosen is not None, self.settings, now)
        standby = self._standby(chosen, task, held, person) if directing else None
        for name, row in self.rows.items():
            if not directing:
                continue
            if name != chosen or not task:
                if self._lighting(row) and (person is None or row["job"] != person["track_id"]):
                    # Contract 2's consumer rule, unchanged: a stale or unsafe snapshot, or another person binding,
                    # darkens at once. Only the normal exits below fade.
                    self._release(name, row, now, reasons.get(name, "task_released"))
                elif self._lighting(row) and not row["exiting"]:
                    # Standing up, seen on a fresh frame: the quick exit; a task that ended with the person still
                    # seated (an answer, the patience): the slow one; another fixture taking the job: the crossfade.
                    self._exit(name, row, now, reasons.get(name, "task_released"), handoff=task and chosen is not None,
                               leaving=not person["seated"])
            elif self._ending(row, now):
                # A planned end (the phone patience, or the reply's validity) is played so it's dark at the deadline.
                self._exit(name, row, now, "task_ending", ending=self.task_until())
            if name == chosen and row["pending"] is None:
                self._aim(name, row, person, target, task, now)
            elif name == standby and row["pending"] is None:
                self._stand_by(name, row, person, now)
            self._play(name, row, now, held)
            if row["token"] is not None and not row["exiting"] and now >= row["keep_at"]:
                row["keep_at"] = now + LEASE_S
                self._submit(name, "keep_lit", row["move"]["decision_id"], token=row["token"])
            if (name not in (chosen, standby) and row["glide"] is None and row["pending"] is None
                    and not row["exiting"] and (
                        row["reason"] in ("waiting_for_tracking", "waiting_for_task", "task", "pre_aim", "standby")
                        or name in reasons or name in self.admitted)):
                row["reason"] = reasons.get(name, "waiting_for_task")
        self._publish(now)

    def _goal(self, row, person):
        """The level that's enough for this person's task from this fixture (den-core's light model)."""
        modeled = self.core.share.brightness(row, person["target"])
        return self.core.spot_light.level_for(modeled, self.core.SPOT_TARGET)[0]

    def _ending(self, row, now):
        """A lit fixture whose task ends within one exit fade, unless that end has already been played."""
        until = self.task_until()
        return (until is not None and row["level"] > 0 and not row["exiting"] and self.settings["fade_out_s"] > 0
                and until - now <= self.settings["fade_out_s"] and row["ended_at"] != until)

    def _advance(self, row, now, task):
        """The board flies its MOVE: the joints follow the glide's S-curve, and a dark move's level arrives with it."""
        glide = row["glide"]
        if glide is None:
            return
        row["current"] = glide.at(now)
        if glide.done(now):
            row["current"], row["glide"] = glide.target, None
            if row["arrive_level"] is not None:
                row["level"], row["arrive_level"] = row["arrive_level"], None

    def _aim(self, name, row, person, target, task, now):
        """What the chosen fixture does next: a dark pre-aim, a dark move then a rise, a rise where it already points, or
        one lit glide after the book. Level changes are ramps, which _play turns into the level train."""
        glide = row["glide"]
        effective_target = glide.target if glide else row["current"]
        lit = self._lighting(row) and not row["exiting"]
        if row["exiting"]:
            # The task came back during the exit (a new answer after a planned end, or the episode back on the same
            # page): rise again from where the fade-out has got to. An exit for a refusal never comes back here.
            extended = row["ended_at"] is not None and (self.task_until() or 0) > row["ended_at"]
            if (task and row["job"] == person["track_id"]
                    and (row["exiting"] == "task_released" or (row["exiting"] == "task_ending" and extended))):
                row.update(exiting=None, ended_at=None, reason="task", goal=self._goal(row, person))
                row["ramp"] = direction.Ramp(row["level"], row["goal"], self.settings["fade_in_s"], "ease_out", now,
                                             "fade_in")
            return
        if row["dip"] is not None:
            return   # fading down for a dark move: _play moves it once it's dark
        if not task:
            if lit or glide or target == effective_target:
                return
            if row["pre_at"] is not None and now - row["pre_at"] < 1:
                return
            if (not person["seated"] and row["target"] is not None
                    and math.dist(row["target"], person["target"]) <= PRE_AIM_STANDING_M):
                return
            self._move(name, row, target, 0.0, "dark", now, person, "pre_aim")
            return
        goal = self._goal(row, person)
        if not lit:
            if glide:
                return   # landing first (P3 holds a MOVE that arrives during a move); it rises once it's there
            if target != effective_target and not self._in_beam(row, person["target"]):
                if row["timed"]:
                    # One timed MOVE: dark on the way, blooming from dark on arrival (the board starts the fade then).
                    row.update(goal=goal, ended_at=None)
                    row["ramp"] = direction.Ramp(0.0, goal, self.settings["fade_in_s"], "ease_out", now, "fade_in")
                    row["ramp"].sent = True
                    self._move(name, row, target, direction.level_code(goal) / direction.LEVEL_CODES, "dark", now,
                               person, "task", timed=self._timed(row["ramp"]))
                    return
                self._move(name, row, target, 0.0, "dark", now, person, "task")   # a dark move, then the rise
                return
            # Aimed, or near enough that the page is already in the beam: rise now, where it points. A page that's off
            # centre is settled onto by one lit glide once the rise is done.
            # Aimed: rise. A hand-off (another fixture fading out for this person) rises over the crossfade.
            handoff = any(other is not row and other["exiting"] and other["job"] == person["track_id"]
                          for other in self.rows.values())
            fade = self.settings["crossfade_s" if handoff else "fade_in_s"]
            row.update(goal=goal, job=person["track_id"], target=person["target"], basis=person["basis"],
                       reason="task", ended_at=None)
            curve = "ease_in_out" if handoff else "ease_out"
            # Back from a dip (a dark move while lit) it returns over the crossfade, not the slow first rise.
            back = row["move"] is not None and row["move"].get("requested", {}).get("cue") == "dip_move"
            fade = self.settings["crossfade_s"] if back else fade
            # A rise's first step goes with the yes: the bloom starts one step in, already visible.
            start = now - row["step_s"] if curve == "ease_out" and not back else now
            row["ramp"] = direction.Ramp(row["level"], goal, fade, "ease_in_out" if back else curve, start,
                                         "crossfade_in" if handoff or back else "fade_in")
            return
        if target != effective_target:
            if glide and not row["profile"].get("retarget_mid_move"):
                return
            ramp = row["ramp"]
            if ramp is not None and ramp.end > ramp.start and not ramp.done(now):
                return   # a rise finishes before the glide: one change at a time
            travel = self.settings["move_style"] if row["profile"].get("lit_motion") else "dark"
            if travel != "dark":
                # The owner's path rule (the beam's centre never sweeps through the lit person's head), with the same
                # keep clearance as the glare refusal, so head jitter at the boundary doesn't turn a glide into a dip.
                _, closest = self.core.eye.path_clear(row["aim_origin_m"], row["horizontal_world_reference"],
                    tuple(x / 100 for x in row["current"]), tuple(x / 100 for x in target),
                    math.dist(row["aim_origin_m"], person["target"]), person["head"])
                # The head target inflated by the profile's path_margin (the measured off-path residual) at the head's
                # distance; the board adds its own half-tick corridor.
                margin = math.dist(row["aim_origin_m"], person["head"]) * math.tan(
                    math.radians(row["profile"].get("path_margin_cdeg", 0) / 100))
                if closest < direction.GLARE_KEEP_M + margin:
                    travel = "dark"
            if travel == "lit":
                # One movement, one command: the glide carries the level the train has reached, and a rise in progress
                # waits for it to land.
                timed = {"min_duration_ms": round(self.settings["glide_min_s"] * 1000), "fade_ms": 0,
                         "fade_curve": "linear"} if row["timed"] else None
                self._move(name, row, target, direction.level_code(row["level"]) / direction.LEVEL_CODES, "lit", now,
                           person, "task", timed=timed)
                return
            # Dim or dark travel (or a path past the head): fade down, move, rise again.
            down = min(self.settings["move_dim_level"] if travel == "dim" else 0.0, row["level"])
            row["dip"] = (target, down, person)
            row["ramp"] = direction.Ramp(row["level"], down, self.settings["fade_out_s"], "ease_out", now, "dip")
            return
        if row["ramp"] is None and goal > 0 and abs(goal - row["goal"]) > .1 * goal:
            # The book moved to where this fixture gives it a different amount of light (plan C6's 10 %).
            row["goal"] = goal
            row["ramp"] = direction.Ramp(row["level"], goal, self.settings["crossfade_s"], "ease_in_out", now, "level")
        row.update(job=person["track_id"], target=person["target"], basis=person["basis"])

    def _in_beam(self, row, point):
        """The beam as it points now has `point` well inside it: within 80 % of half its FWHM."""
        joints = self.core.joints
        aim = joints.direction(row["current"][0] / 100, row["current"][1] / 100, row["horizontal_world_reference"])
        to = [point[i] - row["aim_origin_m"][i] for i in range(3)]
        length = math.sqrt(sum(c * c for c in to))
        if length <= 0:
            return False
        cos = sum(aim[i] * to[i] for i in range(3)) / length
        half = row["profile"].get("beam_fwhm_deg", 20.0) / 2
        return math.degrees(math.acos(max(-1.0, min(1.0, cos)))) <= .8 * half

    def _standby(self, chosen, task, held, person):
        """The runner-up for the lit person: the best other admitted fixture (the same pick, keeping the current one)."""
        lit = chosen is not None and task and not held and self._lighting(self.rows[chosen])
        if not lit or not self.admitted:
            self.standby = None
            return None
        options = {name: result for name, (result, _joints) in self.admitted.items()}
        self.standby = self.core.share.pick_for_person(options, self.rows, person["target"], self.standby)
        return self.standby

    def _stand_by(self, name, row, person, now):
        """The standby follows the page dark, at most once a second and only for a move of more than STANDBY_M."""
        joints = self.admitted[name][1]
        if self._lighting(row) or row["glide"] or joints == row["current"]:
            return
        if row["pre_at"] is not None and now - row["pre_at"] < 1:
            return
        if (row["reason"] == "standby" and row["target"] is not None
                and math.dist(row["target"], person["target"]) <= STANDBY_M):
            return
        self._move(name, row, joints, 0.0, "dark", now, person, "standby")

    def _exit(self, name, row, now, reason, *, handoff=False, ending=None, leaving=False):
        """The light leaves: a fade-out on the exit curve, or a crossfade when another fixture takes the job. It's dark
        by the light's last published validity, so an exit never keeps a beam on longer than a snapshot promised. A
        planned end (ending: the task's deadline) starts early enough to be dark at it. Off, Stop, expiry and a zero
        fade stay instant."""
        fade = self.settings["crossfade_s" if handoff else "leave_fade_s" if leaving else "fade_out_s"]
        bound = row["lit_until"] if ending is None else min(row["lit_until"] or ending, ending)
        joins = row["timed"] and row["join"]
        too_short = fade <= 0 if row["timed"] else fade < 2 * row["step_s"]
        if too_short or bound is None or now >= bound or row["level"] <= 0 or (row["glide"] is not None and not joins):
            # A fade too short for this fixture's train, a spent validity, or a fixture in the middle of a glide: one
            # MOVE, dark. The board can't fade during a move (P3: a MOVE that arrives during a move waits for the
            # landing), so a train would stall.
            if ending is None or row["glide"] is not None:
                self._release(name, row, now, reason)
            return
        # The curves: a crossfade eases in and out; an exit lets most of the light go first (ease_out).
        curve = "ease_in_out" if handoff else "ease_out"
        row["ramp"] = direction.Ramp(row["level"], 0.0, min(fade, bound - now), curve, now,
                                     "crossfade_out" if handoff else "fade_out")
        row.update(exiting=reason, reason=reason, dip=None, lit_until=bound, ended_at=ending)

    def _timed(self, ramp):
        """A ramp as a timed MOVE's fade: its time and its curve, as the board plays them."""
        return {"min_duration_ms": 0, "fade_ms": round(ramp.duration * 1000), "fade_curve": ramp.curve}

    def _play(self, name, row, now, held):
        """The level train: the ramp's next step as one stationary MOVE when its 0-254 code changes, one in flight at a
        time. A rise waits through a glide and a camera gap (the gap hold keeps the level as it is); an exit never
        waits. On a board with the timed MOVE the whole ramp is one MOVE, and the light follows the board's curve."""
        ramp, last = row["ramp"], row["ramp_at"]
        row["ramp_at"] = now
        if ramp is None:
            return
        if row["exiting"] and now >= row["lit_until"]:
            self._release(name, row, now, row["exiting"])
            return
        if row["pending"] is not None:
            return
        if row["timed"]:
            self._play_timed(name, row, now, held, ramp)
            return
        if not row["exiting"] and (row["glide"] is not None or held):
            if last is not None:
                ramp.paused(now - last)
            return
        if row["step_at"] is not None and now - row["step_at"] < row["step_s"] - 1e-9:
            return   # the train's cap: at most one level MOVE per step_s (direction.level_step_s)
        code = direction.level_code(ramp.value(now))
        if code != direction.level_code(row["level"]):
            row["step_at"] = now
            gliding = row["glide"] is not None
            self._move(name, row, row["glide"].target if gliding else row["current"], code / direction.LEVEL_CODES,
                       ("lit" if code else "dark") if gliding else "none", now, None, row["reason"],
                       deadline=row["lit_until"] if row["exiting"] else None, step=True, exiting=bool(row["exiting"]),
                       cue=ramp.purpose)
            return
        if not ramp.done(now):
            return
        row["ramp"] = None
        if ramp.purpose == "dip":
            target, down, person = row["dip"]
            row["dip"] = None
            self._move(name, row, target, down, "lit" if down else "dark", now, person, "task", cue="dip_move")
        elif row["exiting"] and code == 0:
            row.update(job=None, target=None, basis=None, exiting=None, lit_until=None, goal=0.0, token=None)

    def _play_timed(self, name, row, now, held, ramp):
        """One timed MOVE per ramp (firmware 62ea043): fade_ms and its curve, from the level the board shows now. While
        the board plays it, the published level is its curve's, so the light shown is still the one committed."""
        if not ramp.sent:
            gliding = row["glide"] is not None
            if not row["exiting"] and (gliding or held):
                return   # a rise waits for the landing and through a gap, as the train does
            ramp.start, ramp.started, ramp.sent = row["level"], now, True
            if row["exiting"]:
                ramp.duration = max(0.0, min(ramp.duration, row["lit_until"] - now))
            target = row["glide"].target if gliding else row["current"]   # mid-glide: joining the flight
            travel = ("lit" if row["level"] > 0 else "dark") if gliding else "none"
            self._move(name, row, target, direction.level_code(ramp.end) / direction.LEVEL_CODES, travel, now, None,
                       row["reason"], deadline=row["lit_until"] if row["exiting"] else None, step=True,
                       exiting=bool(row["exiting"]), cue=ramp.purpose, timed=self._timed(ramp))
            return
        row["level"] = ramp.value(now) if now >= ramp.started else ramp.start
        if not ramp.done(now):
            return
        row["level"] = ramp.end
        row["ramp"] = None
        if ramp.purpose == "dip":
            target, down, person = row["dip"]
            row["dip"] = None
            self._move(name, row, target, down, "lit" if down else "dark", now, person, "task", cue="dip_move")
        elif row["exiting"] and ramp.end == 0:
            row.update(job=None, target=None, basis=None, exiting=None, lit_until=None, goal=0.0, token=None)

    def _release(self, name, row, now, reason, *, trace=True):
        """Dark at once: one level-0 MOVE. During a glide it goes to the target being flown, dark, so the board never
        turns back to a point it has passed. Untraced (Stop), the axes hold where they are."""
        glide = row["glide"]
        changed = row["level"] or row["pending"] or row["motion"] or glide or row["token"] is not None
        row.update(level=0.0, token=None, pending=None, motion=None, job=None, reason=reason, target=None, basis=None,
                   glide=None, ramp=None, goal=0.0, exiting=None, lit_until=None, dip=None, arrive_level=None,
                   ended_at=None)
        if trace and changed:
            if glide is not None:
                row["glide"] = glide
                self._move(name, row, glide.target, 0.0, "dark", now, None, reason)
            else:
                self._move(name, row, row["current"], 0.0, "none", now, None, reason)

    def _move(self, name, row, target, level, travel, now, person, reason, *, deadline=None, step=False,
              exiting=False, cue=None, timed=None):
        """One MOVE ticket. step: a level step of the train, which leaves the fixture's job as it is; exiting: a step of
        an exit, valid to the light's last published validity rather than the task's."""
        end = (deadline if exiting else min(self.task_until(), self.track_until)) if level else None
        details = {"start_cdeg": row["current"], "target_cdeg": target, "range_cdeg": row["ranges"],
                   "level": level, "cct_k": self.core.CCT_K, "travel": travel,
                   "fade_s": self.settings["fade_in_s" if level else "fade_out_s"],
                   "valid_ms": min(1500, max(1, math.floor((end - now) * 1000))) if end is not None else 1500,
                   "lit_motion": row["profile"].get("lit_motion"), "profile_id": row["profile"].get("label"),
                   "deadline": end, "cue": cue or reason}
        if travel == "dim":
            details["dim_level"] = self.settings["move_dim_level"]
        if timed is not None:
            details["timed"] = dict(timed)   # min_duration_ms, fade_ms, fade_curve: flag bit 4 and bits 5-6
        # Contract 5: a lit MOVE names the reply it answers. An exit's step is the tail of the light that reply lit, so
        # it names that reply's subject even once the task (and its reply) has gone.
        subject = self.latest_decision["subject"]
        if level and exiting and row["lit_subject"] is not None:
            subject = row["lit_subject"]
        elif level and not exiting:
            row["lit_subject"] = subject
        ticket = {"level": level, "deadline": end, "episode": self.episode, "person": person, "reason": reason,
                  "details": details, "subject": subject, "step": step, "exiting": exiting}
        row["pending"] = ticket
        self._submit(name, "aim_move", self.latest_decision["decision_id"], ticket=ticket)

    def _submit(self, name, action, decision, *, token=None, ticket=None, receipt=None):
        job = (name, action, decision, token, ticket, receipt)
        if not self.threaded:
            self._record(job)
            return True
        try:
            self.jobs.put_nowait(job)
        except queue.Full:
            self.problem = "trace queue full"
            self.trace_complete = False
            self.counts["trace_errors"] += 1
            if ticket is not None:
                self._release(name, self.rows[name], self.clock(), self.problem, trace=False)
            return False
        return True

    def _write(self):
        while not self.halt.is_set():
            try:
                job = self.jobs.get(timeout=.2)
            except queue.Empty:
                continue
            self._record(job)

    def _record(self, job):
        name, action, decision, token, ticket, receipt = job
        with self.lock:
            row = self.rows[name]
            now = self.clock()
            self._expire(now)
            if receipt and receipt["revision"] != self.control_revision:
                receipt["event"].set()
                return
            if ticket is not None and (row["pending"] is not ticket or (
                    ticket["deadline"] is not None and self.clock() >= ticket["deadline"])):
                if row["pending"] is ticket:
                    self._release(name, row, self.clock(), "expired_before_encode", trace=False)
                return
            if action == "keep_lit" and (row["token"] != token or self.task_until() is None):
                return
            details = ticket and ticket["details"]
            if ticket and ticket["deadline"] is not None:
                remaining = math.floor((ticket["deadline"] - now) * 1000)
                if remaining < 1:
                    self._release(name, row, self.clock(), "expired_before_encode", trace=False)
                    return
                details = {**details, "valid_ms": min(1500, remaining), "remaining_ms": remaining}
        try:
            record = self.trace.emit(name, action, decision_id=decision, move_token=token,
                                     move=details, at=self.clock(), subject=ticket and ticket.get("subject"))
        except (OSError, ValueError):
            record = None
        with self.lock:
            now = self.clock()
            self._expire(now)
            if record is None:
                self.problem = "trace write failed"
                self.counts["trace_errors"] += 1
                self.trace_complete = False
            else:
                self.trace_committed = record["event_index"]
                if record["outcome"] == "refused":
                    self.counts["refused"] += 1
            if receipt:
                receipt["ok"] = bool(record and record["outcome"] == "encoded_only")
                receipt["event"].set()
            if action == "keep_lit" and row["token"] == token and (
                    record is None or record["outcome"] != "encoded_only"):
                self._release(name, row, now, (record or {}).get("reason", "trace write failed"), trace=False)
                self._publish(now)
            if ticket is not None and row["pending"] is ticket:
                row["pending"] = None
                # An exit's step only ever lowers the light: it stays valid to its own deadline, with no task.
                still_valid = not ticket["level"] or (self.on and not self.stopped and now < ticket["deadline"] and (
                    ticket["exiting"] or (self.episode is ticket["episode"] and self.task_until() is not None)))
                if record and record["outcome"] == "encoded_only" and still_valid:
                    self._apply(row, ticket, record, now)
                else:
                    self._release(name, row, now, (record or {}).get("reason", self.problem or "expired"), trace=False)
                self._publish(now)

    def _apply(self, row, ticket, record, now):
        """The committed MOVE becomes what the fixture does, as the board would do it: a move is flown on its S-curve
        (a MOVE to the target already being flown continues that path); lit travel keeps the level on the way, dark
        travel is dark until it lands with the MOVE's level; a stationary MOVE sets the level at once (no fade on the
        board). A level step leaves the fixture's job as it is."""
        effective = record["effective"]
        details = ticket["details"]
        start, target = tuple(details["start_cdeg"]), tuple(effective["target_cdeg"])
        level, moving = effective["level"], tuple(details["start_cdeg"]) != tuple(effective["target_cdeg"])
        timed = details.get("timed")
        ramp = row["ramp"]
        fading = bool(timed and timed["fade_ms"] > 0 and ramp is not None and ramp.sent)
        if moving:
            glide = row["glide"]
            if glide is None or glide.target != target:   # a MOVE to the target being flown joins that flight
                floor = timed["min_duration_ms"] / 1000 if timed else 0.0
                row["glide"] = direction.Glide(start, target, row["profile"]["axes"], now, floor)
                row["current"] = start
        row["arrive_level"] = None
        if fading:
            # The board fades from what it shows to the MOVE's level, on the ramp's curve. Dark on the way, it blooms
            # from dark when it lands.
            if moving and effective["travel"] != "lit":
                ramp.start, ramp.started = 0.0, row["glide"].started + row["glide"].duration
                row["level"] = 0.0
            else:
                row["level"] = ramp.value(now)
        elif moving and effective["travel"] != "lit":
            row["level"], row["arrive_level"] = 0.0, (level or None)
        else:
            row["level"] = level
        lit = level > 0 or row["level"] > 0   # a fade to 0 holds the lease until it ends
        row.update(move=record, token=record["sequence"] if lit else None, keep_at=now + LEASE_S)
        if not ticket["step"]:
            person = ticket["person"]
            row.update(target=person and person["target"], basis=person and person["basis"],
                       job=person and person["track_id"], reason=ticket["reason"])
            if ticket["reason"] in ("pre_aim", "standby"):
                row["pre_at"] = now
            if ticket["reason"] == "standby":
                row["reason"] = "standby"
        self._advance(row, now, self.task_until() is not None)

    def _plan_bulbs(self, now, ambient=None):
        """The bulb plan for this moment (bulb_plan.py), from the same situation as the spots. Off and Stop release
        the bulbs to den_lighting's own rules. A change wakes the overlay, which sends it only while Bulbs is On."""
        inputs = self.hue.inputs() if self.hue is not None else None
        context = self._context(now)
        key, learnable, source = bulb_plan.situation(self.core, context, inputs and inputs["attrs"], self.wall(),
                                                     self.utc_offset_h)
        if self.stopped or not self.on:
            plan = bulb_plan.release(key, "stopped" if self.stopped else "off")
        else:
            lights = tuple((inputs and inputs["lights"]) or self.bulb_lights)
            learned = self.hue.scenes(key, lights) if self.hue is not None and lights else {}
            plan = bulb_plan.desired(key, people=context["people"], ambient=ambient,
                                     learned=learned, lights=lights, warmest=inputs and inputs["warmest"],
                                     house=inputs and inputs["house"])
        current = {"key": (key, learnable, source), "plan": plan}
        if current != self.bulb:
            if self.bulb is None or plan != self.bulb["plan"]:
                self.bulb_since = now
            self.bulb = current
            if self.hue is not None:
                self.hue.wake()

    def bulb_desired(self):
        """For the overlay's worker: the plan as the preview last published it."""
        with self.lock:
            return self.bulb

    def bulbs_switch(self):
        return self.bulbs_on

    def _bulb_record(self):
        if self.bulb is None:
            return None
        readback = self.hue.readback() if self.hue is not None else {}
        sending = self.hue is not None and self.bulbs_on and readback.get("active") is True
        return bulb_plan.record(self.bulb["plan"], mode="sending" if sending else "shadow", since_mono=self.bulb_since,
                                source=self.bulb["key"][2], sent_generation=readback.get("sent_generation") if sending else None,
                                problem=readback.get("problem"))

    def _publish(self, now):
        self.seq += 1
        fixtures = {}
        directing = self.on and not self.stopped
        limit = self.task_until()
        until = now + POSE_AGE_S
        for name, row in self.rows.items():
            move = row["move"] or {}
            level = row["level"] if directing else 0.0
            if level > 0:
                # Each lit fixture's validity: a task light's is the task's and the hold's; an exit's is fixed when it
                # starts, and the exit is dark by then.
                if not row["exiting"]:
                    row["lit_until"] = min(now + POSE_AGE_S, limit or now, self.hold_until or now)
                until = min(until, row["lit_until"] if row["lit_until"] is not None else now)
            # Additive fields (contract 0: the schema id stays): the glide the board is flying, for Home to draw on the
            # same S-curve, and the ramp its level train is sampling.
            fixtures[name] = {"on": level > 0, "level": level, "cct_k": self.core.CCT_K,
                              "pan_deg": -row["current"][0] / 100, "tilt_deg": -row["current"][1] / 100,
                              "target_m": row["target"], "target_basis": row["basis"],
                              "reason": "stopped" if self.stopped else "off" if not self.on else row["reason"],
                              "motion_phase": "moving" if row["glide"] else "settled",
                              "last_move_trace_id": move.get("trace_id"), "move_sequence": move.get("sequence"),
                              "move_decision_id": move.get("decision_id"),
                              "keep_lit_token": row["token"] if level > 0 else None,
                              "glide": row["glide"].published() if row["glide"] and directing else None,
                              "fade": row["ramp"].published(now) if row["ramp"] and directing else None}
        # The room's glow (additive): what Home draws on every fixture's glow zones and downlight, and later the scale
        # den_lighting would apply to the room's bulbs. None while the coordinator is Off.
        ambient = self.ambient.published(self.settings, now) if self.on else None
        self._plan_bulbs(now, ambient)   # the bulbs follow the same ambient record Home draws (contract 7)
        self.latest = {"schema": "den-virtual-spots-1", "home": "den", "site": self.site, "epoch": self.epoch, "seq": self.seq,
                       "binding": self.binding, "subject": self._subject(), "sampled_mono": now, "valid_until_mono": until,
                       "on": self.on, "stopped": self.stopped, "stop_counter": self.stop_counter, "fixtures": fixtures,
                       "ambient": ambient, "bulbs": self._bulb_record()}
        self.counts["snapshots"] += 1
        self.publish(self.latest)

    def _subject(self):
        """Contract 4's subject: who the light decision is for. The person binding's track, camera epoch and
        association revision, and the newest reply the episode took while it supports a task (else None)."""
        if self.track_binding is None:
            return None
        support = self.episode.need.support if self.episode is not None else None
        request = support.get("request_id") if isinstance(support, dict) and self.task_until() is not None else None
        _run, epoch, track_id, revision = self.track_binding
        return {"track_id": track_id, "camera_epoch": epoch, "association_revision": revision, "request_id": request}

    def lighting_context(self):
        with self.lock:
            now = self.clock()
            self._expire(now)
            return self._context(now)

    def _context(self, now):
        episode = self.episode
        task = self.task_until() is not None
        known = bool(episode and episode.valid_until is not None)
        signature = self.track_binding, episode, task, known
        if signature != self.context_signature:
            self.context_signature, self.context_since = signature, now
        return {"people": int(self.track is not None), "task": task, "seated": episode is not None,
                "activity_known": known, "since_mono": self.context_since}

    def _controls(self):
        return {"on": self.on, "stopped": self.stopped, "stop_counter": self.stop_counter}

    def _persisted(self):
        return {**self._controls(), "control_revision": self.control_revision}

    def routes(self):
        routes = {("GET", "/coordinator"): self.state, ("POST", "/coordinator/switch"): self.switch,
                ("POST", "/coordinator/stop"): self.stop, ("POST", "/coordinator/resume"): self.resume,
                ("POST", "/coordinator/settings"): self.configure, ("POST", "/coordinator/bulbs"): self.bulbs}
        if self.hue is not None:
            routes[("POST", "/coordinator/learned/clear")] = self.hue.request_clear
        return {**routes, **{(method, path.replace("/coordinator", "/coordinator/virtual", 1)): handler
                            for (method, path), handler in routes.items()}}

    def state(self, _body=None):
        with self.lock:
            current = {**self._controls(), "snapshot": self.latest, "decision": self.latest_decision,
                       "server_mono": self.clock(), "settings": dict(self.settings), "problem": self.problem,
                       "trace_event_index": self.trace_committed, "trace_complete": self.trace_complete,
                       "bulbs_on": self.bulbs_on, "bulb_plan": self._bulb_record(),
                       "situation": self.bulb and self.bulb["key"][0]}
        if self.hue is not None:
            readback = self.hue.readback()
            current.update({**readback, "situation": readback.get("situation") or current["situation"],
                            "problem": current["problem"] or readback.get("problem")})
        return 200, current

    def bulbs(self, data):
        """The Bulbs switch (contract 6's fourth control). Off applies before the disk write, as Stop does; On only
        once it's saved. Either way the overlay hears at once: Off releases a plan of ours, On may send one."""
        if not isinstance(data, dict) or type(data.get("on")) is not bool:
            return 400, {"ok": False, "error": "expected on"}
        with self.control_lock:
            if not data["on"]:
                with self.lock:
                    self.bulbs_on = False
                    self._publish(self.clock())
            try:
                self._save("bulbs-control.json", {"on": data["on"]})
            except OSError:
                return 503, {"ok": False, "error": "bulb switch write failed", "on": self.bulbs_on}
            with self.lock:
                self.bulbs_on = data["on"]
                self._publish(self.clock())
        if self.hue is not None:
            self.hue.wake()
        return 200, {"ok": True, "on": data["on"]}

    def switch(self, data):
        if not isinstance(data, dict) or type(data.get("on")) is not bool:
            return 400, {"ok": False, "error": "expected on"}
        return self._control("on" if data["on"] else "off")

    def stop(self, _data):
        return self._control("stop")

    def resume(self, data):
        if not isinstance(data, dict) or type(data.get("stop_counter")) is not int:
            return 400, {"ok": False, "error": "expected stop_counter"}
        return self._control("resume", data["stop_counter"])

    def _control(self, action, counter=None):
        with self.lock:
            if action == "resume" and counter != self.stop_counter:
                return 409, {"ok": False, **self._controls()}
            if action == "resume" and not self.stopped:
                return 200, {"ok": True, **self._controls()}
            self.control_revision += 1
            revision = self.control_revision
            if action == "stop":
                self.stopped = True
                self.stop_counter += 1
            elif action in ("on", "off"):
                self.on = action == "on"
            now = self.clock()
            self._drop_track()
            receipts = []
            for name, row in self.rows.items():
                self._release(name, row, now, action, trace=action == "off")
                if action == "stop":
                    self._submit(name, "stop", None)
                if action == "resume":
                    receipt = {"event": threading.Event(), "revision": revision, "ok": False}
                    if not self._submit(name, "resume", None, receipt=receipt):
                        receipt["event"].set()
                    receipts.append(receipt)
            self._publish(now)
        deadline = time.monotonic() + 2.0
        if any(not (receipt["event"].wait(max(0, deadline - time.monotonic())) and receipt["ok"]) for receipt in receipts):
            return 503, {"ok": False, "error": "Resume trace did not commit"}
        # Every control writes the controls as they are at the write, under both locks, so the file never trails
        # memory once a control has answered. On, Off and Stop always apply: a newer control isn't a conflict for
        # them (no spurious 409). Resume alone must still be the newest control: its check, the write of
        # stopped: false and the un-stop are one step, so no Stop can fall between them, and a failed write leaves
        # it stopped.
        with self.control_lock, self.lock:
            if action == "resume" and revision != self.control_revision:
                return 409, {"ok": False, **self._controls()}
            state = {**self._persisted(), **({"stopped": False} if action == "resume" else {})}
            try:
                self._save("preview-control.json", state)
            except OSError:
                self.problem = "control state write failed"
                return 503, {"ok": False, "error": self.problem}
            if action == "resume":
                self.stopped = False
                self._drop_track()
                self._publish(self.clock())
            return 200, {"ok": True, **{key: state[key] for key in CONTROL_KEYS}}

    def configure(self, data):
        if not isinstance(data, dict):
            return 400, {"ok": False, "error": "expected settings"}
        for name, value in data.items():
            default = self.core.SETTINGS.get(name, direction.SETTINGS.get(name))
            low, high = direction.LIMITS.get(name, (0, math.inf))
            valid = (type(value) is bool if type(default) is bool else
                     value in self.core.MOVE_STYLES if name == "move_style" else
                     finite(value) and low <= value <= high
                     and (name not in ("move_dim_level", "accent_level") or value <= 1))
            if default is None or not valid:
                return 400, {"ok": False, "error": "invalid setting"}
        with self.settings_lock:
            with self.lock:
                settings = {**self.settings, **data}
            try:
                self._save("settings.json", settings)
            except OSError:
                return 503, {"ok": False, "error": "settings write failed"}
            with self.lock:
                self.settings = settings
                self._evaluate(self.clock())
        return 200, {"ok": True, "settings": settings}

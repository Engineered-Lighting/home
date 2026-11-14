"""Den spotlight preview. Tracking and encoded commands share one applied state."""

import datetime
import json
import math
import os
import queue
import threading
import time
import uuid
from pathlib import Path

from contracts import PERCEPTION_S
from preview_evidence import Episode, field, finite, pose, seated as published_seated
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
FACING_REFUSALS = ("in_front_of_reader", "no_facing")
CONTROL_KEYS = ("on", "stopped", "stop_counter")
BINDING_KEYS = ("transform_id", "scan_revision", "equipment_revision", "equipment_digest")


class Preview:
    def __init__(self, core, *, binding, fixtures, state, seat_mark=None, clock=time.monotonic,
                 publish=None, append=None, threaded=True):
        self.core, self.clock = core, clock
        self.binding = {key: binding[key] for key in BINDING_KEYS}
        revision = self.binding["equipment_revision"]
        if (type(revision) is not int or revision < 0 or not fixtures or
                not all(isinstance(self.binding[key], str) and self.binding[key] for key in BINDING_KEYS if key != "equipment_revision")):
            raise ValueError("registered fixture geometry required")
        self.folder = Path(state) / "coordinator"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.settings = dict(core.SETTINGS, **self._read("settings.json", {}))
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
            self.rows[name] = dict(row, profile=profile, ranges=ranges, current=current, level=0.0,
                                   pending=None, motion=None, move=None, token=None, keep_at=0.0,
                                   target=None, basis=None, job=None, reason="waiting_for_tracking", pre_at=None,
                                   eye_hold=None)
        self.trace = PreviewTrace(self.epoch, append or self._append, binding=self.binding,
                                  profiles={name: row["profile"] for name, row in self.rows.items()})
        self.scope = None
        self.retired_runs = set()
        self.track_seq = -1
        self.track_binding = self.track = self.track_until = self.episode = self.eligibility = None
        self.eligible_track = None
        self.captured = None   # the newest frame taken for the track
        self.since = None      # the published seated since_mono the episode was built at
        self.shift = None
        self.target_basis = None
        self.latest_decision = self.latest = self.problem = None
        self.context_signature, self.context_since = None, self.clock()
        self.counts = {"snapshots": 0, "refused": 0, "trace_errors": 0}
        self.trace_committed = 0
        self.trace_complete = True
        self.worker = self.ticker = None
        self.hue = None
        self.site = None
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
        day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        folder = self.folder.parent / "days" / day
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "coordinator-fixture_calls.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n")
            log.flush()
            os.fsync(log.fileno())

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
        self.track_binding = self.track = self.track_until = self.episode = self.eligibility = None
        self.eligible_track = None
        self.captured = self.since = None
        self.shift = self.target_basis = None

    def _darken(self):
        """Contract 2's consumer rule: a stale snapshot or an unsafe frame darkens. No light until a current, posed,
        seated frame of the same person; the episode, its onset, the need state and the requests stay."""
        self.track = self.track_until = None

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
            earliest = field(frame, "capture_earliest_mono")
            deadline = min(captured, earliest) + POSE_AGE_S if finite(captured) and finite(earliest) else (
                captured + POSE_AGE_S if finite(captured) else -math.inf)
            tracks = field(snapshot, "tracks", ())
            if (field(snapshot, "status") != "fresh" or field(snapshot, "presence") != "present"
                    or not finite(captured) or not captured <= now < deadline or len(tracks) != 1
                    or any(field(frame, k) != self.binding[k] for k in ("transform_id", "scan_revision"))):
                self._darken()
            else:
                eligibility = field(snapshot, "eligibility_revision")
                if self.eligibility is not None and eligibility != self.eligibility:
                    self.episode = self.shift = self.target_basis = None
                self._track(tracks[0], captured, deadline, now)
                self.eligibility = eligibility
                self.eligible_track = field(snapshot, "eligible_track_id")
            self._evaluate(now)

    offer_snapshot = tracking

    def _track(self, track, captured, deadline, now):
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
        head = field(track, "coarse_head_room_m") or body.head(joints)
        if head and not body.head(joints):
            joints["nose"] = [*head, 1.0]
        self.captured = captured
        self.track_until = deadline
        # Contract 2's seated, published once by perception: the episode starts at its since_mono, the same instant
        # the ask's stills start from. It's rebuilt only when that published since moves, never on episode.started:
        # a none or multiple reply moves started on to the negative, and the episode must keep that.
        seated, since = published_seated(track, captured)
        if seated and (self.episode is None or since != self.since):
            policy = self.core.need_v1.make_policy({"phone_patience_s": self.settings["phone_patience_s"],
                                                    "dwell": {"entry_samples": ENTRY_ANSWERS}})
            self.episode = Episode(policy, key, since, (self.binding["transform_id"], self.binding["scan_revision"]))
            self.since = since
            self.shift = body.ShiftFilter(self.settings["shift_m"], self.settings["shift_hold_s"])
        if not seated:
            self.episode = self.shift = self.target_basis = None
        facing, shoulders = body.facing(joints), body.shoulder_mid(joints) or head
        target, basis = shoulders, "torso"
        if seated:
            target, basis = body.task_point(joints, facing, self.seat_mark)
            if target is not None:
                self.shift.shift_m, self.shift.hold_s = self.settings["shift_m"], self.settings["shift_hold_s"]
                if self.shift.update(captured, target) is not None:
                    self.target_basis = basis
                target, basis = self.shift.aim, self.target_basis
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

    def _expire(self, now):
        if self.track_until is not None and now >= self.track_until:
            self._darken()
        if self.episode is not None:
            self.episode.expire(now, self.settings["phone_patience_s"])

    def task_until(self):
        if self.episode is None:
            return None
        return self.episode.deadline(self.settings["phone_patience_s"])

    def _candidate(self, person, now=None):
        core = self.core
        now = self.clock() if now is None else now
        candidates, reasons, joints = {}, {}, {}
        incumbent = None
        for name, row in self.rows.items():
            kept = row["token"] is not None and row["job"] == person["track_id"]
            result = core.eye.evaluate(name, row, person, person["target"], kept)
            if not kept:
                row["eye_hold"] = None
            elif result.ok:
                row["eye_hold"] = (now, person["facing"])
            elif result.reason in FACING_REFUSALS and row["eye_hold"] and now - row["eye_hold"][0] < KEEP_HOLD_S:
                # Facing jitter: judge the lit fixture by the last facing that kept it, until the refusal has lasted
                # KEEP_HOLD_S.
                held = core.eye.evaluate(name, row, dict(person, facing=row["eye_hold"][1]), person["target"], kept)
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
        selected = core.share.pick_for_person(candidates, self.rows, person["target"], incumbent) if candidates else None
        return selected, joints.get(selected), reasons

    def _evaluate(self, now):
        self._expire(now)
        self._context(now)
        person = self.track
        # A light needs a current, posed, seated frame: a reply that lands while the frames are stale, poseless or
        # unsafe (say, an ask sent as the person stood up) is taken into the episode but lights nothing.
        task = self.task_until() is not None and bool(person and person["seated"])
        for row in self.rows.values():
            self._advance(row, now, task and self.on and not self.stopped)
        chosen, target, reasons = None, None, {}
        if person and person["target"] is not None and (task or self.settings["pre_aim"]):
            chosen, target, reasons = self._candidate(person, now)
        diagnostic = {"kind": "task" if task else "pre_aim", "fixture": chosen, "refused": reasons,
                      "target_basis": person and person["basis"], "joints_cdeg": list(target) if target else None}
        subject = self._subject()
        previous = self.latest_decision or {}
        if diagnostic != previous.get("proposal") or subject != previous.get("subject"):
            self.decision_seq += 1
            self.latest_decision = {"version": "decision/v3", "decision_id": f"{self.epoch}-{self.decision_seq}",
                                    "actuation": "shadow", "subject": subject, "proposal": diagnostic}
        for name, row in self.rows.items():
            if self.stopped or not self.on:
                continue
            if name != chosen or not task:
                if row["token"] is not None or row["motion"] and row["motion"]["level"] > 0 or (
                        row["pending"] and row["pending"]["level"] > 0):
                    self._release(name, row, now, reasons.get(name, "task_released"))
            if name == chosen and row["pending"] is None:
                self._aim(name, row, person, target, task, now)
            if row["token"] is not None and now >= row["keep_at"]:
                row["keep_at"] = now + LEASE_S
                self._submit(name, "keep_lit", row["move"]["decision_id"], token=row["token"])
            if name != chosen and row["motion"] is None and row["pending"] is None and (
                    row["reason"] in ("waiting_for_tracking", "waiting_for_task", "task", "pre_aim") or name in reasons):
                row["reason"] = reasons.get(name, "waiting_for_task")
        self._publish(now)

    def _advance(self, row, now, task):
        move = row["motion"]
        if move is None:
            return
        elapsed = now - move["started"]
        fraction = min(1, elapsed / move["duration"]) if move["duration"] else 1
        blend = fraction ** 3 * (10 + fraction * (-15 + 6 * fraction))
        row["current"] = tuple(round(a + (b - a) * blend) for a, b in zip(move["from"], move["target_cdeg"]))
        if move["level"] > 0 and not task:
            return  # retain the trace until evaluation records the level-zero release
        if fraction < 1 and move["travel"] == "dark":
            row["level"] = 0.0
            return
        if move["level"] > 0 and task:
            fade_elapsed = elapsed if move["travel"] == "lit" else elapsed - move["duration"]
            fade = min(1, max(0, fade_elapsed) / move["fade_s"]) if move["fade_s"] else 1
            row["level"] = move["initial_level"] + (move["level"] - move["initial_level"]) * fade
            row["token"] = row["move"]["sequence"] if row["level"] > 0 else None
        if fraction == 1 and (not move["level"] or fade == 1):
            row["motion"] = None

    def _aim(self, name, row, person, target, task, now):
        current = row["motion"]
        effective_target = tuple(current["target_cdeg"]) if current else row["current"]
        had_task = row["token"] is not None or bool(current and current["level"] > 0)
        if target == effective_target and task == had_task and row["job"] == person["track_id"]:
            return
        if not task and row["pre_at"] is not None and row["target"] is not None and (
                now - row["pre_at"] < 1 or math.dist(row["target"], person["target"]) <= .3):
            return
        if current and target != effective_target and not row["profile"].get("retarget_mid_move"):
            return
        level = 0.0
        if task:
            modeled = self.core.share.brightness(row, person["target"])
            level = self.core.spot_light.level_for(modeled, self.core.SPOT_TARGET)[0]
        travel = self.settings["move_style"] if task and had_task else "dark"
        if target == row["current"]:
            travel = "none"
        elif travel != "dark":
            clear, _ = self.core.eye.path_clear(row["aim_origin_m"], row["horizontal_world_reference"],
                tuple(x / 100 for x in row["current"]), tuple(x / 100 for x in target),
                math.dist(row["aim_origin_m"], person["target"]), person["head"])
            if not clear:
                travel = "dark"
        self._move(name, row, target, level, travel, now, person, "task" if task else "pre_aim")

    def _release(self, name, row, now, reason, *, trace=True):
        changed = row["level"] or row["pending"] or row["motion"] or row["token"] is not None
        row.update(level=0.0, token=None, pending=None, motion=None, job=None, reason=reason, target=None, basis=None)
        if trace and changed:
            self._move(name, row, row["current"], 0.0, "none", now, None, reason)

    def _move(self, name, row, target, level, travel, now, person, reason):
        end = min(self.task_until(), self.track_until) if level else None
        details = {"start_cdeg": row["current"], "target_cdeg": target, "range_cdeg": row["ranges"],
                   "level": level, "cct_k": self.core.CCT_K, "travel": travel,
                   "fade_s": self.settings["fade_in_s" if level else "fade_out_s"],
                   "valid_ms": min(1500, max(1, math.floor((end - now) * 1000))) if end is not None else 1500,
                   "lit_motion": row["profile"].get("lit_motion"), "profile_id": row["profile"].get("label"),
                   "deadline": end}
        if travel == "dim":
            details["dim_level"] = self.settings["move_dim_level"]
        ticket = {"level": level, "deadline": end, "episode": self.episode, "person": person, "reason": reason,
                  "details": details, "subject": self.latest_decision["subject"]}
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
                                     move=details, at=self.clock())
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
                still_valid = not ticket["level"] or (self.on and not self.stopped and self.episode is ticket["episode"]
                    and self.task_until() is not None and now < ticket["deadline"])
                if record and record["outcome"] == "encoded_only" and still_valid:
                    self._apply(row, ticket, record, now)
                else:
                    self._release(name, row, now, (record or {}).get("reason", self.problem or "expired"), trace=False)
                self._publish(now)

    def _apply(self, row, ticket, record, now):
        effective = record["effective"]
        person, details = ticket["person"], ticket["details"]
        travel = effective["travel"]
        start = details["start_cdeg"]
        duration = self.core.scurve.move_time(tuple((b - a) / 100 for a, b in zip(start, effective["target_cdeg"])),
                                             row["profile"]["axes"])
        previous = row["level"] if travel in ("lit", "none") and effective["level"] else 0.0
        row.update(move=record, current=start, level=previous, token=None, keep_at=now + LEASE_S,
                   target=person and person["target"], basis=person and person["basis"],
                   job=person and person["track_id"], reason=ticket["reason"],
                   motion=dict(effective, started=now, duration=duration, initial_level=previous,
                               fade_s=details["fade_s"], **{"from": start}))
        if ticket["reason"] == "pre_aim":
            row["pre_at"] = now
        self._advance(row, now, self.task_until() is not None)

    def _publish(self, now):
        self.seq += 1
        fixtures = {}
        for name, row in self.rows.items():
            move = row["move"] or {}
            level = row["level"] if self.on and not self.stopped else 0.0
            fixtures[name] = {"on": level > 0, "level": level, "cct_k": self.core.CCT_K,
                              "pan_deg": -row["current"][0] / 100, "tilt_deg": -row["current"][1] / 100,
                              "target_m": row["target"], "target_basis": row["basis"],
                              "reason": "stopped" if self.stopped else "off" if not self.on else row["reason"],
                              "motion_phase": "moving" if row["motion"] else "settled",
                              "last_move_trace_id": move.get("trace_id"), "move_sequence": move.get("sequence"),
                              "move_decision_id": move.get("decision_id"), "keep_lit_token": row["token"]}
        limit = self.task_until()
        if any(row["on"] for row in fixtures.values()):
            until = min(now + POSE_AGE_S, limit or now, self.track_until or now)
        else:
            until = now + POSE_AGE_S
        self.latest = {"schema": "den-virtual-spots-1", "home": "den", "site": self.site, "epoch": self.epoch, "seq": self.seq,
                       "binding": self.binding, "subject": self._subject(), "sampled_mono": now, "valid_until_mono": until,
                       "on": self.on, "stopped": self.stopped, "stop_counter": self.stop_counter, "fixtures": fixtures}
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
                ("POST", "/coordinator/settings"): self.configure}
        if self.hue is not None:
            routes[("POST", "/coordinator/learned/clear")] = self.hue.request_clear
        return {**routes, **{(method, path.replace("/coordinator", "/coordinator/virtual", 1)): handler
                            for (method, path), handler in routes.items()}}

    def state(self, _body=None):
        with self.lock:
            current = {**self._controls(), "snapshot": self.latest, "decision": self.latest_decision,
                       "server_mono": self.clock(), "settings": dict(self.settings), "problem": self.problem,
                       "trace_event_index": self.trace_committed, "trace_complete": self.trace_complete}
        if self.hue is not None:
            current.update(self.hue.readback())
        return 200, current

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
            default = self.core.SETTINGS.get(name)
            valid = (type(value) is bool if type(default) is bool else
                     value in self.core.MOVE_STYLES if name == "move_style" else
                     finite(value) and 0 <= value and (name not in ("move_dim_level", "accent_level") or value <= 1))
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

"""Den spotlight preview. Tracking and encoded commands share one applied state.

The rules here decide what each fixture is for and where it points; direction.py decides how each change is played (the
lighting-direction note): a rise, an exit or a hand-off is a level train of stationary MOVEs on an eased curve, a move is
one MOVE flown on the board's own S-curve, and the room's glow dims under a task light. Published state is always what
the trace has committed: each level step lights only once its row is written."""

import copy
import dataclasses
import datetime
import json
import math
import os
import queue
import threading
import time
import uuid
from collections import deque
from pathlib import Path

import bulb_plan
import direction
import fixture_learning
import manual
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
# The gap hold (plan 05 section 3.5, contract 4): the camera's newest frame can go 1.5-2.4 s without changing, about 5
# times a minute. A spot already lit stays lit while its person's newest fresh frame is at most this old, the same 3.0 s
# at which contract 2 ends the camera epoch; keep-lit goes on through the gap. Lighting still needs a pose at most
# POSE_AGE_S old, and evidence on a fresh frame (standing, leaving, a second person) darkens at once.
GAP_HOLD_S = 3.0
FACING_REFUSALS = ("in_front_of_reader", "no_facing")
# A light on someone refused for these is in, or can't be checked against, their head or eyes: it's cut at once (one
# level-0 MOVE), never eased out. Easing is for ordinary hand-offs and exits.
EYE_REFUSALS = ("beam_through_head", "no_head")
# The clearance is to each eye as well as to den-core's head point (the face joints' median, ears included, about 3 cm
# behind the eyes): the eye keypoints, or without them the head point this far along the facing.
EYES_AHEAD_M = 0.03
# After a cut for the head or the eyes, the fixture isn't admitted again for this long: no cut-and-relight cycles.
CUT_HOLD_S = 1.5
# A lit glide's path is checked against the head and the eyes this often, in joint space (0.25 degrees: about 7 mm at
# 1.7 m, well inside the 3 cm between the keep clearance and admission's).
SWEEP_STEP_CDEG = 25
# The direction's choice hysteresis (lighting-direction note): a dark pre-aim moves at most once a second, and while the
# person is on their feet only for a torso move of more than this.
PRE_AIM_STANDING_M = .3
# The standby: while a spot is lit for a task, the best other admitted fixture is aimed at the same page, dark, so a
# hand-off is a crossfade and never a dark move. It re-aims when the page has moved this far.
STANDBY_M = .1
# Exits a fixture can come back from (its job wasn't refused): it's still the incumbent while it fades.
RECOVERABLE = ("task_released", "task_ending")
# Several people (plan 04, step 3): the person the light is for keeps it while others are in the room, and every other
# person's head is kept out of every beam by the same clearance as the reader's own (den-core eye.HEAD_CLEARANCE_M).
THROUGH_ANOTHER_HEAD = "beam_through_another_head"   # den-core eye's own is beam_through_head
# The owner's own changes (plan 04b, manual.py): Home reports each settled change and names the snapshot it was drawing.
# den-live reads the change's context from its own record of the snapshots it published: the last RING of them (a
# minute at the tick's ten a second), learned from only while MANUAL_RECENT_S old. Home's `before` may trail what was
# published by this much (it eases what it draws) before the change counts as made against something else.
RING = 600
MANUAL_RECENT_S = 30.0
BEFORE_LEVEL_SLACK = 0.15
BEFORE_ANGLE_SLACK_DEG = 5.0
# The hand hold ends when nobody has been seen for this long (the den is empty), as well as at its own next reason.
VACANT_S = 30.0
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
        self.manual_control_revisions = controls.get("manual_revisions", {})
        self.control_lock, self.settings_lock, self.lock = threading.Lock(), threading.Lock(), threading.RLock()
        self.epoch = uuid.uuid4().hex
        self.seq = self.decision_seq = 0
        self.seat_mark = seat_mark
        self.publish = publish or (lambda _snapshot: None)
        self.threaded = threaded
        self.jobs = queue.Queue(maxsize=64)
        self.halt = threading.Event()
        self.rows = {}
        # A timed MOVE goes only where the board advertises it and the pinned codec can encode it (c12_fc13.TIMED, flag
        # bit 4, from the re-pin to firmware 62ea043); elsewhere the level train plays each fade.
        timed_codec = getattr(preview_trace.wire, "TIMED", None) is not None
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
                                   pending=None, motion=None, move=None, token=None, keep_at=0.0, sends=[], cut_at=None, leased_at=None,
                                   target=None, basis=None, job=None, reason="waiting_for_tracking", pre_at=None,
                                   glide=None, ramp=None, ramp_at=None, goal=0.0, exiting=None,
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
        self.others = ()       # everyone else on the newest fresh frame: {track_id, head}, kept out of every beam
        self.captured = None   # the newest frame taken for the track
        self.since = None      # the published seated since_mono the episode was built at
        self.shift = None
        self.target_basis = None
        self.facing = direction.FacingFilter()
        self.ambient = direction.Ambient()
        self.standby = None   # the runner-up for the lit person, aimed dark at their page
        self.admitted = {}    # this evaluation's other admitted fixtures: {name: (eye result, joints)}
        self.cut = {}         # this evaluation's lights in (or unchecked against) a head or eyes: {name: reason}
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
        self.utc_offset_set = utc_offset_h is not None
        self.bulb = self.bulb_since = None
        # The owner's own changes (plan 04b). ring: the snapshots a change can name; hands: {(fixture, part): hold};
        # spot_applied: (episode, what the spot learned) fixed when the task starts; room_cue: the room glow's cue.
        self.ring = deque(maxlen=RING)
        self.hands = {}
        # A hold's learning, per fixture and group ("spot", or "glow" for its zones and downlight): where the hold
        # began (the snapshot its first change named) and what its changes add up to, so the hold teaches once.
        self.hold_learning = {}
        # Since when fresh frames have shown the den empty (perception's presence "empty"). Only that is evidence:
        # a camera gap, a dark room or a stale snapshot says nothing about who's there.
        self.vacant_since = None
        self.spot_applied = self.room_cue = None
        self.ambient_flag = False
        self.manual_log = manual.Log(self.folder)
        lines = self.manual_log.read()
        self.learning = fixture_learning.Learning.replay(lines)
        self.manual_meta = {name: {"revision": 0, "upgraded": False, "hold_id": None, "state": {}}
                            for name in self.rows}
        self.manual_fingerprints, self.manual_replies, self.manual_resumes = {}, {}, {}
        self.manual_dirty = self.manual_transaction = False
        self.manual_authority_unavailable = False
        self._restore_manual(lines)
        if isinstance(self.manual_control_revisions, dict):
            for name, revision in self.manual_control_revisions.items():
                if name in self.manual_meta and type(revision) is int and revision > self.manual_meta[name]["revision"]:
                    self.manual_meta[name]["revision"] = revision
                    self.manual_meta[name]["hold_id"] = None
                    self.hands = {key: hand for key, hand in self.hands.items() if key[0] != name}
                    self.manual_dirty = True
        if self.manual_log.unreadable:
            self.problem = "manual authority journal is unreadable"
            self.stopped = True
            self.manual_authority_unavailable = True
        for line in lines:
            event = line.get("event")
            if event is not None:
                try:
                    checked = manual.parse(event)
                    if checked["fixture_id"] not in self.rows:
                        raise ValueError("unknown fixture receipt")
                    self.manual_fingerprints[checked["event_id"]] = manual.fingerprint(checked)
                    self.manual_replies[checked["event_id"]] = self._replayed(line)
                except (KeyError, TypeError, ValueError):
                    self.manual_log.skipped += 1
            if "resume" in line:
                try:
                    request, reply = line["resume"], line["reply"]
                    if (not isinstance(request.get("request_id"), str) or
                            not manual.EVENT_ID.fullmatch(request["request_id"]) or
                            request.get("fixture_id") not in self.rows or not isinstance(reply, dict)):
                        raise ValueError("invalid Resume receipt")
                    self.manual_resumes[request["request_id"]] = {
                        "fingerprint": manual.fingerprint(request), "reply": reply}
                except (KeyError, TypeError, ValueError, AttributeError):
                    self.problem = "manual authority Resume receipt is invalid"
                    self.stopped = self.manual_authority_unavailable = True
                    self.manual_log.skipped += 1
        for event_id, reply in list(self.manual_replies.items()):
            if not isinstance(reply, dict):
                self.manual_replies.pop(event_id)
                self.problem = "manual authority receipt is invalid"
                self.stopped = self.manual_authority_unavailable = True
                continue
            name = reply.get("fixture_id")
            reply.update(epoch=self.epoch, from_seq=0)
            if name in self.rows:
                reply.update(hand=self._hand_record(name), manual_revision=self.manual_meta[name]["revision"])
        self._publish(self.clock())

    def _manual_checkpoint(self, *, history=False):
        # Runtime episode objects and monotonic timestamps never cross a process boundary.
        hands = [{"fixture": name, "part": part,
                  "hold": {key: value for key, value in hand.items()
                           if key not in ("episode", "binding", "since_mono")}}
                 for (name, part), hand in self.hands.items()]
        checkpoint = {"schema": "den-manual-checkpoint-1", "authority": {
            "binding": self.binding, "control_revision": self.control_revision,
            "fixtures": self.manual_meta, "hands": hands,
            "learning": [{"fixture": name, "group": group, "record": record}
                         for (name, group), record in self.hold_learning.items()]}}
        if history:
            # Clear carries receipts once. Ordinary event/checkpoint records contain only current ownership.
            checkpoint["dedupe"] = {"fingerprints": self.manual_fingerprints, "replies": self.manual_replies,
                                    "resumes": self.manual_resumes}
        return checkpoint

    def _restore_manual(self, lines):
        for line in lines:
            saved = line.get("authority")
            if not isinstance(saved, dict):
                continue
            try:
                fixtures = saved["fixtures"]
                for name, meta in fixtures.items():
                    if (type(meta["revision"]) is not int or not 0 <= meta["revision"] <= 9007199254740991 or
                            type(meta["upgraded"]) is not bool):
                        raise ValueError("invalid manual revision")
                    if meta.get("hold_id") is not None:
                        if not isinstance(meta["hold_id"], str) or not manual.EVENT_ID.fullmatch(meta["hold_id"]):
                            raise ValueError("invalid saved hold id")
                        if meta["upgraded"] and (not isinstance(meta.get("client_id"), str) or
                                                 not manual.EVENT_ID.fullmatch(meta["client_id"]) or
                                                 type(meta.get("client_seq")) is not int or meta["client_seq"] < 1):
                            raise ValueError("invalid saved ordering")
                    if meta.get("state"):
                        manual._state(meta["state"], "saved state")
                hands = {}
                for item in saved["hands"]:
                    name, part, hand = item["fixture"], item["part"], dict(item["hold"])
                    if name not in self.rows or part not in ("spot", "radials", "downward"):
                        continue
                    if not all(key in hand for key in ("event_id", "task_light", "task", "task_seen", "ends", "lit")):
                        raise ValueError("invalid saved hold")
                    if (any(type(hand[key]) is not bool for key in ("task_light", "task", "task_seen", "lit")) or
                            hand["ends"] not in ("next_cue", "task_end", "task_start", "episode_end")):
                        raise ValueError("invalid saved hold policy")
                    if (not fixtures.get(name, {}).get("hold_id") or not isinstance(hand["event_id"], str) or
                            not manual.EVENT_ID.fullmatch(hand["event_id"])):
                        raise ValueError("saved hold has no owner")
                    hand.update(episode=None, binding=None, since_mono=self.clock(), recovering=True)
                    if hand.get("cue") is not None:
                        hand["cue"] = tuple(hand["cue"])
                        hand["settled"] = False
                    hands[(name, part)] = hand
                self.manual_meta.update({name: copy.deepcopy(meta) for name, meta in fixtures.items() if name in self.rows})
                if "dedupe" in line:
                    self.manual_fingerprints = dict(line["dedupe"].get("fingerprints", {}))
                    self.manual_replies = dict(line["dedupe"].get("replies", {}))
                    self.manual_resumes = dict(line["dedupe"].get("resumes", {}))
                    if any(not isinstance(item, dict) or not isinstance(item.get("reply"), dict) or
                           not isinstance(item.get("fingerprint"), str) for item in self.manual_resumes.values()):
                        raise ValueError("invalid saved Resume receipt")
                matches = saved.get("binding") == self.binding and saved.get("control_revision") == self.control_revision
                self.hands = hands if matches and self.on and not self.stopped else {}
                learning = {}
                for item in saved.get("learning", []):
                    group, record = item["group"], item["record"]
                    required = ({"task_light", "unlit", "page", "target_m", "lux_before", "key", "origin", "learn", "last"}
                                if group == "spot" else {"ambient", "key", "origin", "learn", "zones"})
                    if group not in ("spot", "glow") or not isinstance(record, dict) or not required <= set(record):
                        raise ValueError("invalid saved correction")
                    if item["fixture"] in self.rows:
                        learning[(item["fixture"], group)] = record
                self.hold_learning = learning if self.hands else {}
                for name, meta in self.manual_meta.items():
                    if not any(fixture == name for fixture, _part in self.hands):
                        meta["hold_id"] = None
                    elif meta.get("state"):
                        self._adopt_aim(name, meta["state"])
                        self.rows[name]["reason"] = "hand"
            except (KeyError, TypeError, ValueError, AttributeError):
                self.problem = "manual authority journal is invalid"
                self.stopped = True
                self.manual_authority_unavailable = True

    def _persist_manual(self):
        if self.manual_authority_unavailable:
            return False
        if not self.manual_dirty or self.manual_transaction:
            return True
        try:
            self.manual_log.append(self._manual_checkpoint())
        except OSError:
            self.problem = "manual authority journal write failed"
            return False
        self.manual_dirty = False
        return True

    def _manual_conflict(self, name, error="manual_revision_conflict"):
        return 409, {"ok": False, "error": error, "manual_revision": self.manual_meta[name]["revision"],
                     "hand": self._hand_record(name), "epoch": self.epoch, "from_seq": self.seq}

    def _manual_order(self, event):
        meta = self.manual_meta[event["fixture_id"]]
        if "client_id" not in event:
            return "manual_version_required" if meta["upgraded"] else None
        same = (meta.get("hold_id") == event["hold_id"] and meta.get("client_id") == event["client_id"])
        if same:
            if event["expected_revision"] > meta["revision"]:
                return "manual_revision_conflict"
            return "manual_sequence_conflict" if event["client_seq"] <= meta.get("client_seq", 0) else None
        if event["expected_revision"] != meta["revision"]:
            return "manual_revision_conflict"
        return None

    def _manual_state(self, event):
        name = event["fixture_id"]
        meta, row = self.manual_meta[name], self.latest["fixtures"][name]
        state = meta.get("state") if meta.get("hold_id") else None
        if not state:
            ambient = self.latest.get("ambient") or {"level": 0, "cct_k": self.core.CCT_K}
            light = {"on": ambient["level"] > 0, "brightness": ambient["level"], "color_temp_kelvin": ambient["cct_k"]}
            state = {"pan_deg": row["pan_deg"], "tilt_deg": row["tilt_deg"],
                     "spotlight": {"on": row["on"], "brightness": row["level"], "color_temp_kelvin": row["cct_k"]},
                     "radials": [{"number": n, **light} for n in range(1, 7)], "downward": light}
        state = manual.merge_state(state, event.get("held_state", {}))
        return manual.merge_state(state, event["after"])

    def _manual_before_transaction(self, name):
        return (dict(self.rows[name]), {key: dict(hand) for key, hand in self.hands.items()},
                copy.deepcopy(self.hold_learning), copy.deepcopy(self.manual_meta), self.manual_dirty)

    def _manual_rollback(self, name, saved):
        row, self.hands, self.hold_learning, self.manual_meta, self.manual_dirty = saved
        self.rows[name].clear()
        self.rows[name].update(row)

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
        self.others = ()
        self.captured = self.since = None
        self.shift = self.target_basis = None
        self.facing = direction.FacingFilter()

    def _darken(self):
        """Contract 2's consumer rule: a stale snapshot or an unsafe frame darkens. No light until a current, posed,
        seated frame of the same person; the episode, its onset, the need state and the requests stay."""
        self.track = self.track_until = self.hold_until = None
        self.others = ()

    def _subject_of(self, tracks):
        """(the track the light is for, everyone else). One track is the subject, as it always was. Among several, the
        subject is the one the current binding names (track id and association revision), and only that one: a person
        who walks in never becomes the subject while others are there, and with no binding nobody is lit."""
        if len(tracks) == 1:
            return tracks[0], ()
        bound = self.track_binding and tuple(self.track_binding[2:])
        for track in tracks:
            if bound and (field(track, "track_id"), field(track, "association_revision")) == bound:
                return track, tuple(other for other in tracks if field(other, "track_id") != bound[0])
        return None, tuple(tracks)

    def _other(self, track):
        joints = pose(track)
        head = published_head(track) or self.core.body.head(joints)   # contract 2's head_point_m, as for the reader
        if not (isinstance(head, (list, tuple)) and len(head) == 3 and all(map(finite, head))):
            return None
        return {"track_id": field(track, "track_id"), "head": tuple(head)}

    def _clear_of_others(self, origin, point, kept=False):
        """Whether the beam from `origin` to `point` keeps every other person's head out by the reader's clearance: den-core's
        to be admitted, direction's keep clearance for a fixture already lit (the same hysteresis as the reader's glare)."""
        clearance = direction.GLARE_KEEP_M if kept else self.core.eye.HEAD_CLEARANCE_M
        return all(self.core.eye.centre_clear(origin, point, other["head"]) >= clearance for other in self.others)

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
            elif (field(snapshot, "presence") != "present" or not tracks
                    or any(field(frame, k) != self.binding[k] for k in ("transform_id", "scan_revision"))):
                if (field(snapshot, "presence") == "empty" and not tracks
                        and all(field(frame, k) == self.binding[k] for k in ("transform_id", "scan_revision"))):
                    self.vacant_since = self.vacant_since if self.vacant_since is not None else now
                self._darken()
            elif self._subject_of(tracks)[0] is None:
                self.vacant_since = None
                self._darken()   # several people and none of them the one the light is for
            else:
                self.vacant_since = None
                subject, others = self._subject_of(tracks)
                eligibility = field(snapshot, "eligibility_revision")
                if self.eligibility is not None and eligibility != self.eligibility:
                    self.episode = self.shift = self.target_basis = None
                self._track(subject, captured, deadline, captured + GAP_HOLD_S, now)
                self.others = tuple(row for row in map(self._other, others) if row is not None)
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
        eyes = [list(joints[name][:3]) for name in ("left_eye", "right_eye")
                if joints.get(name) and len(joints[name]) >= 4 and joints[name][3] >= 0.5]
        if not eyes and head and facing:
            eyes = [[head[0] + EYES_AHEAD_M * facing[0], head[1] + EYES_AHEAD_M * facing[1], head[2]]]
        target, basis = shoulders, "torso"
        if seated:
            # The page, not the wrists: the aim filter's settled median, which a page turn never moves.
            fallback, fallback_basis = body.task_point(joints, facing, self.seat_mark)
            page = direction.page_point(joints, self.settings["page_up_m"])
            self.shift.shift_m, self.shift.hold_s = self.settings["shift_m"], self.settings["shift_hold_s"]
            target, basis = self.shift.update(captured, page, fallback, fallback_basis, self._lit_for(key[2]))
            self.target_basis = basis
        self.track = {"track_id": key[2], "head": head, "shoulder_mid": shoulders, "facing": facing,
                      "target": target, "basis": basis, "seated": seated, "eyes": eyes}

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
        """den-core's eye rule for this fixture. Admission is den-core's: nothing new lights in front of the reader. A
        fixture already lit for this person keeps its job however they turn (the owner, 2026-11-14: turning to face a
        light that's on is fine), and is refused for its beam's centre passing the head only once that's inside
        GLARE_KEEP_M (admission keeps den-core's 0.15 m), so head jitter can't flip it. The head and the eyes are also
        checked on the beam as it points (_beam_clear), whichever way the reader faces."""
        eye = self.core.eye
        result = eye.evaluate(name, row, person, person["target"], kept)
        if kept and result.reason in ("beam_through_head", "in_front_of_reader", "no_facing") and person.get("head"):
            aim = eye.aim
            facing = person.get("facing")
            if not facing:   # facing doesn't matter to a lit light: any horizontal direction will do for den-core
                facing = (1.0, 0.0)
            reader = aim.Reader(tuple(person["shoulder_mid"]), tuple(facing[:2]) + (0.0,), "shoulders",
                                tuple(person["head"]), "pose", tuple(person["target"]))
            fixture = aim.Fixture(name, tuple(row["aim_origin_m"]), tuple(row["horizontal_world_reference"]),
                                  dict(aim.DEFAULT_LIMITS))
            held = aim.evaluate_fixture(fixture, reader, dataclasses.replace(
                eye._KEEP, head_clearance_m=direction.GLARE_KEEP_M, min_behind_cos=-1.0))
            # A facing refusal (in front, or no facing) never stands for a lit fixture: it gives way to what the check
            # without facing finds, so a beam through the head is still beam_through_head, and cut.
            result = held if held.ok or result.reason in FACING_REFUSALS else result
        return result

    def _candidate(self, person, now=None):
        core = self.core
        now = self.clock() if now is None else now
        candidates, reasons, joints = {}, {}, {}
        incumbent = pre_aimed = None
        self.cut = {}
        for name, row in self.rows.items():
            if (name, "spot") in self.hands:
                # The owner has it by hand (plan 04b): never a candidate, a standby or a pre-aim, and never cut by
                # den-live either. Manual wins; Home warns if its beam crosses a head.
                reasons[name] = "hand"
                continue
            # An exiting fixture is never the incumbent: a refused light that's fading out can't win its job back
            # in the middle of the crossfade (that was a hand-off ping-pong).
            kept = row["job"] == person["track_id"] and row["exiting"] in (None, *RECOVERABLE) and (
                row["token"] is not None or self._lighting(row))
            result = self._eye(name, row, person, kept)   # a lit fixture is never refused for facing (_eye)
            if row["job"] == person["track_id"] and self._lighting(row):
                # Any light on this person (rising, lit, holding, crossfading or leaving) is judged by the keep rule's
                # head and eye margins, and by the head's clearance on the beam as it points now. den-core checks the
                # line to the page, but a rise in place, the hold without lit travel and a standby's hand-over all
                # light a beam pointing up to 8 degrees off that line. Failing is a cut at once (_evaluate).
                safe = result if kept else self._eye(name, row, person, True)
                if not self._beam_clear(row, person):   # whatever den-core refused it for first
                    safe = self.core.eye.aim.Candidate(name, False, "beam_through_head")
                if not safe.ok and safe.reason in EYE_REFUSALS:
                    self.cut[name] = reasons[name] = safe.reason
                    continue
            if not result.ok:
                reasons[name] = result.reason
                continue
            try:
                target = tuple(map(core.joints.to_cdeg, core.joints.room_to_joints(
                    person["target"], row["aim_origin_m"], row["horizontal_world_reference"])))
            except ValueError:
                reasons[name] = "ambiguous_aim"
                continue
            if not self._clear_of_others(row["aim_origin_m"], person["target"], kept):
                reasons[name] = THROUGH_ANOTHER_HEAD
                continue
            if any(not lo <= a <= hi for a, (lo, hi) in zip(target, row["ranges"])):
                reasons[name] = "out_of_range"
                continue
            if any(abs(a - b) > 18000 for a, b in zip(target, row["current"])):
                reasons[name] = "route_over_180"
                continue
            if not kept:
                # Admission takes keep's test (the head and each eye, along their path), on the beam as it will be
                # aimed and at admission's 15 cm, so nothing is admitted that keep would cut at once. A fixture cut for
                # the head or the eyes waits CUT_HOLD_S before it's admitted again.
                if row["cut_at"] is not None and now - row["cut_at"] < CUT_HOLD_S:
                    reasons[name] = "cut_hold"
                    continue
                if not self._clear(row, person, self.core.eye.HEAD_CLEARANCE_M, target):
                    reasons[name] = "beam_through_head"
                    continue
            candidates[name], joints[name] = result, target
            if kept:
                incumbent = name
            elif row["job"] == person["track_id"] and row["reason"] == "pre_aim":
                pre_aimed = name
        # A lit fixture keeps its job (den-core's rule); with none lit, the one already pre-aimed for this person keeps
        # its pre-aim while it's admitted, so the dark aim doesn't hop between two near-equal sockets.
        mine = incumbent or pre_aimed
        pool = candidates
        if incumbent is not None and self._outgrown(self.rows[incumbent], person):
            # The page has left a lit fixture that can't follow it lit: a dark one already on it takes the job.
            ready = {name: c for name, c in candidates.items()
                     if name != incumbent and self._ready(self.rows[name], person)}
            if ready:
                pool, mine = ready, None
                reasons[incumbent] = "handed_on"
        selected = core.share.pick_for_person(pool, self.rows, person["target"], mine) if pool else None
        self.admitted = {name: (candidates[name], joints[name]) for name in candidates if name != selected}
        return selected, joints.get(selected), reasons

    def _evaluate(self, now):
        self._expire(now)
        self._context(now)
        person = self.track
        # A light needs a current, posed, seated frame: a reply that lands while the frames are stale, poseless or
        # unsafe (say, an ask sent as the person stood up) is taken into the episode but lights nothing.
        # Gaming is dark (the owner's default, plan 04): while den_lighting's gaming signal reads playing, no spot
        # lights a task. The bulbs play den_lighting's gaming scene instead.
        gaming = self._gaming()
        task_on = self.task_until() is not None
        self._end_hands(now, task_on)
        # What the owner taught the spot for this situation (plan 04b), fixed when the task starts. Two offs in a row
        # taught "no spot": the task goes on, unlit.
        learned = self._spot_learned(task_on and not gaming, now)
        unlit = bool(learned and learned["off"])
        task = task_on and bool(person and person["seated"]) and not gaming and not unlit
        # A task whose light the owner has by hand is served: den-live lights nothing else for it while they hold it.
        served, served_lit = self._served_by_hand()
        # In a camera gap (the newest frame older than POSE_AGE_S, held while lit up to GAP_HOLD_S): the fixture lit for
        # this person keeps its job, aim and level, and nothing new is aimed or lit.
        held = person is not None and now >= self.track_until
        directing = self.on and not self.stopped
        for row in self.rows.values():
            self._advance(row, now, task and directing)
        chosen, target, reasons = None, None, {}
        self.admitted, self.cut = {}, {}
        # The learned aim: the page plus the offset the owner taught. Every check runs on it; if no fixture admits it,
        # the page itself is used.
        shifted = self._shifted(person, learned) if task and not served else None
        fallback = False
        if held:
            person = shifted or person
            if task:
                chosen = next((name for name, row in self.rows.items() if row["job"] == person["track_id"]
                               and self._lighting(row) and not row["exiting"]), None)
            if chosen is not None:
                row = self.rows[chosen]
                target = row["glide"].target if row["glide"] else row["current"]
        elif task and served:
            pass
        elif person and person["target"] is not None and (task or self.settings["pre_aim"]):
            if shifted is not None:
                chosen, target, reasons = self._candidate(shifted, now)
                if chosen is None:
                    fallback = True
                    chosen, target, reasons = self._candidate(person, now)
                else:
                    person = shifted
            else:
                chosen, target, reasons = self._candidate(person, now)
        diagnostic = {"kind": "task" if task else "pre_aim", "fixture": chosen, "refused": reasons,
                      "target_basis": person and person["basis"], "joints_cdeg": list(target) if target else None,
                      "gap_hold": held, "gaming": gaming}
        if learned or served:
            diagnostic["learned"] = {"applied": bool(learned), "aim": shifted is not None and not fallback,
                                     "page_fallback": fallback, "unlit": unlit, "served_by_hand": served}
        subject = self._subject()
        previous = self.latest_decision or {}
        if diagnostic != previous.get("proposal") or subject != previous.get("subject"):
            self.decision_seq += 1
            self.latest_decision = {"version": "decision/v3", "decision_id": f"{self.epoch}-{self.decision_seq}",
                                    "actuation": "shadow", "subject": subject, "proposal": diagnostic}
        if person is not None and directing:
            # Every beam the board emits, whatever den-core says and whether or not a candidate was chosen (a task's
            # fade-out with pre-aim off, the leave fade), clears the head and the eyes, or is cut at once. Through a
            # camera gap it's judged from the last frame.
            for name, row in self.rows.items():
                if name not in self.cut and self._lighting(row) and not self._beam_clear(row, person):
                    self.cut[name] = "beam_through_head"
                elif name not in self.cut and self._lighting(row) and not self._others_clear(row, person["target"]):
                    self.cut[name] = THROUGH_ANOTHER_HEAD   # someone else's head in the beam as it points
        # The room's glow follows the task light: it dims a beat after the spot starts and comes back as it ends. A
        # task light the owner holds, lit, counts as the task light.
        self._cue_room(directing and ((task and chosen is not None) or (served_lit and task_on and not gaming)), now)
        standby = self._standby(chosen, task, held, person) if directing else None
        for name, row in self.rows.items():
            if not directing:
                continue
            if (name, "spot") in self.hands:
                row["reason"] = "hand"   # the owner's until its hold ends: den-live sends it nothing
                continue
            if name in self.cut and self._lighting(row):
                # In (or unchecked against) the reader's head or eyes: dark at once, whatever it's doing. Never eased.
                self._release(name, row, now, self.cut[name])
                row["cut_at"] = now
                self._play(name, row, now, held)
                continue
            if name != chosen or not task:
                if self._lighting(row) and (person is None or row["job"] != person["track_id"]
                                            or reasons.get(name) == THROUGH_ANOTHER_HEAD):
                    # Contract 2's consumer rule, unchanged: a stale or unsafe snapshot, or another person binding,
                    # darkens at once. So does a lit beam someone else's head has walked into (plan 04): never a
                    # fade in their eyes. Only the normal exits below fade.
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
            if row["token"] is not None and not row["exiting"] and now >= row["keep_at"] and self._can_send(row, now):
                row["keep_at"], row["leased_at"] = now + LEASE_S, now
                self._submit(name, "keep_lit", row["move"]["decision_id"], token=row["token"])
            if (name not in (chosen, standby) and row["glide"] is None and row["pending"] is None
                    and not row["exiting"] and (
                        row["reason"] in ("waiting_for_tracking", "waiting_for_task", "task", "pre_aim", "standby")
                        or name in reasons or name in self.admitted)):
                row["reason"] = reasons.get(name, "waiting_for_task")
        self._publish(now)

    def _goal(self, row, person):
        """The level that's enough for this person's task from this fixture (den-core's light model), at the light the
        owner taught for this situation (a scale on SPOT_TARGET)."""
        modeled = self.core.share.brightness(row, person["target"])
        return self.core.spot_light.level_for(modeled, self.core.SPOT_TARGET * self._lux_scale())[0]

    # ---- The owner's own changes (plan 04b): what's learned, applied; and the hand hold. -----------------------------

    def _part_of_day(self, wall):
        """den-core's part of the day, in the configured offset, or else in this machine's own timezone (LA's, with its
        daylight saving: the spine, 2026-11-14); "any" if that can't be read."""
        if self.utc_offset_set:
            return self.core.situation.part_of_day(wall, self.utc_offset_h)
        try:
            return self.core.situation.part_of_day(wall, time.localtime(wall).tm_gmtoff / 3600.0)
        except (AttributeError, OverflowError, OSError, TypeError, ValueError):
            return "any"

    def _situation(self, now):
        """(activity, key): den-core's situation as den-live is acting on it, for the fixtures. Someone tracked is
        occupied; nobody is unknown (they can be out of view); gaming is den_lighting's signal (the bulbs keep
        den_lighting's own situation, bulb_plan.situation)."""
        context = self._context(now)
        occupied = True if context["people"] > 0 else None
        kind = self.core.situation.activity(occupied, self._gaming(), int(context["task"]), int(context["seated"]))
        return kind, self.core.situation.key(kind, self._part_of_day(self.wall()))

    def _spot_learned(self, task, now):
        """The task spot's learned values for this situation, fixed when the episode's task starts and kept for the
        whole episode, so learning never moves a light under someone while they read. None without any."""
        if self.episode is None:
            self.spot_applied = None
            return None
        if self.spot_applied is None or self.spot_applied[0] is not self.episode:
            kind, key = self._situation(now)
            if not task or kind != "task":
                # Not yet: a yes that lands in a camera gap (nobody tracked, so the situation reads unknown) waits for
                # the next frame, rather than fixing "nothing learned" for the whole session.
                return None
            self.spot_applied = (self.episode, self.learning.spot(key))
        return self.spot_applied[1]

    def _unlit(self):
        """This episode's task is on, unlit: the owner taught "no spot" for its situation."""
        applied = self.spot_applied
        return bool(applied is not None and applied[0] is self.episode and (applied[1] or {}).get("off")
                    and self.task_until() is not None)

    def _lux_scale(self):
        applied = self.spot_applied
        learned = applied[1] if applied is not None and applied[0] is self.episode else None
        return (learned or {}).get("lux_scale") or 1.0

    def _shifted(self, person, learned):
        """The person with the learned aim offset added to their page, or None without one."""
        offset = learned and learned.get("offset_m")
        if not offset or person is None or person["target"] is None or not person["seated"]:
            return None
        return dict(person, target=tuple(person["target"][i] + offset[i] for i in range(3)))

    def _cue_room(self, flag, now):
        """The room's glow: a new cue whenever the task light comes or goes, or den-live's situation changes to another
        it can name. The glow the owner taught for that situation is fixed for the cue.

        A hand hold on glow zones or a downlight ends at its next reason to act: once the room's situation, as den-live
        sees it on a fresh frame, isn't the one the owner set it in (the task light came or went, they stood or sat, a
        new part of the day). Nothing seen, nothing ends: a camera gap that darkens den-live's own light is no reason."""
        kind, key = self._situation(now)
        learnable = kind in self.core.situation.LEARNABLE
        previous = self.room_cue
        cue = previous is None or previous["flag"] != flag or (learnable and key != previous["key"])
        if cue:
            directing = self.on and not self.stopped
            learned = self.learning.room(key) if directing and learnable else None
            level = self.settings["ambient_level"]
            goal = min(learned["level"], 1.0) / level if learned and level > 0 else None
            self.room_cue = {"flag": flag, "key": key, "goal": goal, "cct_k": learned and learned["cct_k"],
                             "learned": learned}
        if self.track is not None and learnable:
            standing = not self.track["seated"]
            for hold in [hold for hold in self.hands if hold[1] != "spot"]:
                hand = self.hands[hold]
                if hand["cue"] is None:
                    hand["cue"] = (flag, key)   # the situation it was set in, as soon as den-live can name it
                    continue
                if hand["episode"] is not None and self.episode is not hand["episode"] and not standing:
                    if hand.get("vacated"):
                        self._end_hand(hold)   # the den was seen empty between the episodes
                        continue
                    if self.episode is None:
                        continue
                    # A camera gap or a re-identified track: the hold carries on with the new episode, and waits for
                    # the situation it was set in to come back (a task's next yes) before it can end.
                    hand.update(episode=self.episode, settled=False)
                if not hand.get("settled", True):
                    if hand["cue"] == (flag, key):
                        hand["settled"] = True
                    continue
                if hand["cue"] != (flag, key):
                    self._end_hand(hold)
        self.ambient_flag = flag
        self.ambient.direct(flag, self.settings, now, goal=self.room_cue["goal"], cct_k=self.room_cue["cct_k"])

    def _served_by_hand(self):
        """(served, lit): a hold on the spot that was this episode's task light, and whether the owner left it on."""
        holds = [hand for (name, part), hand in self.hands.items()
                 if part == "spot" and hand["task_light"] and hand["episode"] is self.episode]
        return bool(holds), any(hand["lit"] for hand in holds)

    def _end_hands(self, now, task):
        """The next reason to act ends a spot's hold (plan 04b, the spine's rulings of 2026-11-14): for the task's own
        light, the task ending, a new episode (standing up, or another subject); for a light the owner put on for
        themselves, a task starting; for a light they put on while the coordinator, as taught, left the task unlit, a
        new episode only (it's their reading light through a phone break). Every hold ends when fresh frames have shown
        the den empty for VACANT_S, on Off or Stop, and on Home's Resume. A reply that only renews the task, a new part
        of the day, a camera gap or a dark room ends nothing."""
        standing = self.track is not None and not self.track["seated"]
        for hold, hand in list(self.hands.items()):
            if self.vacant_since is not None:
                hand["vacated"] = True   # fresh frames have shown the den empty since this hold began
            elif self.track is not None and self.episode is hand["episode"]:
                hand["vacated"] = False
            if (self.track is None and self.vacant_since is not None
                    and now - max(self.vacant_since, hand["since_mono"]) >= VACANT_S):
                why = "vacant"
            elif hold[1] != "spot":
                continue   # the glow's parts end at the next change of the room's situation (_cue_room)
            else:
                why = self._spot_hold_end(hand, task, standing)
            if why:
                self._end_hand(hold)

    def _spot_hold_end(self, hand, task, standing):
        """Why a spot's hold ends now, or None. Manual always wins, so a camera gap never ends one (the spine,
        2026-11-14): a new camera epoch or a re-identified track drops den-live's episode, and the hold waits for the
        person and carries on with their next episode. The episode ending ends the task's light only on evidence:
        they're seen standing, fresh frames showed the den empty, or the task had already ended when it was lost."""
        if hand.get("recovering"):
            if self.track is None:
                return None
            hand["recovering"] = False
        if self.episode is not hand["episode"]:
            if hand["task_light"] and standing:
                return "stood_up"
            if hand["task_light"] and (hand.get("vacated") or (hand["task_seen"] and not hand["task"])):
                return "new_episode"
            if self.episode is None:
                return None   # nobody's episode for now (a gap, or on their feet): it waits
            hand.update(episode=self.episode, binding=self.track_binding, task_seen=False)
        if not hand["task_seen"]:
            # Back with a new episode: the task they were at (if any) carries on, so its next yes isn't a new start.
            if task:
                hand["task_seen"], hand["task"] = True, True
            return None
        if hand["task_light"]:
            why = None if task or hand["ends"] == "episode_end" else "task_ended"
        else:
            why = "task_started" if task and not hand["task"] else None
        hand["task"] = task
        return why

    def _end_hand(self, hold):
        saved = self._manual_before_transaction(hold[0]) if not self.manual_transaction else None
        del self.hands[hold]
        name = hold[0]
        if hasattr(self, "manual_meta"):
            self.manual_meta[name]["revision"] += 1
            if not any(fixture == name for fixture, _part in self.hands):
                self.manual_meta[name]["hold_id"] = None
            self.manual_dirty = True
        if hold[1] == "spot" and self.rows[hold[0]]["reason"] == "hand":
            self.rows[hold[0]]["reason"] = "waiting_for_task"
        group = self._group(hold[1])
        if not any(name == hold[0] and self._group(part) == group for name, part in self.hands):
            self.hold_learning.pop((hold[0], group), None)
        if saved is not None and not self._persist_manual():
            self._manual_rollback(hold[0], saved)

    @staticmethod
    def _group(part):
        return "spot" if part == "spot" else "glow"

    def _hold(self, event, now, plan=None, recorded=True):
        """The owner's change takes the parts it touched from den-live at once, recorded or not: a spot is released with
        no command (den-live stops directing it; nothing is sent), and the glow's parts are Home's until the next cue.
        Off or Stopped, den-live directs nothing, so there's nothing to hold.

        A spot is the task's light when it was this person's lit task light at the snapshot the change named, or when
        the owner lights it during a task the coordinator, as taught, left unlit (their reading light: it lasts the
        episode). plan: the hold's learning records from _learning_inputs, kept once the change is recorded."""
        if not (self.on and not self.stopped):
            return None
        name, row, person = event["fixture_id"], self.rows[event["fixture_id"]], self.track
        light = event["after"].get("spotlight") or {}
        plan = plan or {}
        for part in manual.parts(event["after"]):
            previous = self.hands.get((name, part))
            hand = {**(previous or {}), "event_id": event["event_id"], "since_mono": previous["since_mono"] if previous else now,
                    "episode": previous["episode"] if previous else self.episode,
                    "binding": previous["binding"] if previous else self.track_binding,
                    "task": previous["task"] if previous else self.task_until() is not None,
                    "task_seen": previous["task_seen"] if previous else True,
                    "vacated": bool(previous and previous.get("vacated")),
                    # A glow part's situation: the room's (task light, situation key) once den-live has seen it.
                    "cue": previous.get("cue") if previous else None,
                    "task_light": False, "lit": False, "ends": "next_cue"}
            if part == "spot":
                lighting = self._lighting(row)
                base = plan.get("spot")
                if previous:
                    task_light, ends = previous["task_light"], previous["ends"]
                elif base is not None:
                    task_light = base["task_light"]
                    ends = "episode_end" if base["unlit"] else "task_end" if task_light else "task_start"
                else:
                    task_light = bool(person is not None and row["job"] == person["track_id"] and lighting
                                      and hand["task"])
                    ends = "task_end" if task_light else "task_start"
                lit = (light["on"] and light.get("brightness", 1) > 0 if "on" in light
                       else light["brightness"] > 0 if "brightness" in light
                       else previous["lit"] if previous else lighting)
                hand.update(task_light=task_light, lit=lit, ends=ends)
                if lighting or row["glide"] is not None or row["pending"] is not None:
                    self._release(name, row, now, "hand", trace=False)
                row["reason"] = "hand"
            self.hands[(name, part)] = hand
        if recorded:
            for group, record in plan.items():
                self.hold_learning[(name, group)] = record
        return self._hand_record(name)

    def _adopt_aim(self, name, after):
        """den-live's books follow the hand: where the owner left the fixture (Home's pan and tilt, or else the surface
        point it was aimed at) becomes its aim, so when the hold ends it's dark where the hand left it, and its next MOVE
        starts from there. Nothing is sent. A lit row is never re-aimed here (_hold has released it first)."""
        row = self.rows[name]
        if self._lighting(row):
            return
        if "pan_deg" in after or "tilt_deg" in after:
            joints = (round(-after.get("pan_deg", -row["current"][0] / 100) * 100),
                      round(-after.get("tilt_deg", -row["current"][1] / 100) * 100))   # published pan_deg is -cdeg/100
        elif (after.get("target") or {}).get("point") is not None:
            try:
                joints = tuple(map(self.core.joints.to_cdeg, self.core.joints.room_to_joints(
                    after["target"]["point"], row["aim_origin_m"], row["horizontal_world_reference"])))
            except ValueError:
                return
        else:
            return
        row.update(current=tuple(min(hi, max(lo, int(a))) for a, (lo, hi) in zip(joints, row["ranges"])), glide=None,
                   pre_at=None)

    def _hand_record(self, name):
        holds = {part: hand for (fixture, part), hand in self.hands.items() if fixture == name}
        if not holds:
            return None
        newest = max(holds.values(), key=lambda hand: hand["since_mono"])
        meta = self.manual_meta.get(name, {})
        return {"event_id": meta.get("event_id", newest["event_id"]), "parts": sorted(holds),
                "hold_id": meta.get("hold_id"), "revision": meta.get("revision", 0),
                "client_id": meta.get("client_id"), "client_seq": meta.get("client_seq"),
                "state": copy.deepcopy(meta.get("state", {})),
                "recovering": any(hand.get("recovering", False) for hand in holds.values()),
                "since_mono": min(hand["since_mono"] for hand in holds.values()),
                "ends": {part: hand["ends"] for part, hand in sorted(holds.items())},
                "warning": self._hand_warning(name)}

    def _hand_warning(self, name):
        """For Home's warning on a spot the owner holds lit: its beam, where the hand left it, passing the reader's head
        or eyes (beam_through_head, at the direction's keep clearance) or someone else's head
        (beam_through_another_head). den-live never cuts it: manual wins (the spine, 2026-11-14). None when it's clear,
        off, or there's nobody to check it against."""
        hand = self.hands.get((name, "spot"))
        if hand is None or not hand["lit"]:
            return None
        row, person = self.rows[name], self.track
        if person is not None and person.get("head") and not self._clear(row, person, direction.AIM_KEEP_M):
            return "beam_through_head"
        if any(not self._clear(row, {"head": other["head"]}, direction.GLARE_KEEP_M) for other in self.others):
            return THROUGH_ANOTHER_HEAD
        return None

    def _snapshot_entry(self, now, fixtures, ambient):
        """What a change naming this snapshot is read against: den-live's own context, not Home's echo of it."""
        kind, key = self._situation(now)
        person = self.track
        task = self.task_until() is not None and bool(person and person["seated"]) and not self._gaming()
        self.ring.append({"seq": self.seq, "mono": now, "directing": self.on and not self.stopped,
                          "people": self._context(now)["people"], "kind": kind, "key": key,
                          "track_id": person and person["track_id"], "task": task,
                          # The coordinator's own light for the task: unlit as taught, and the scale it lights at (what
                          # a change of brightness is a correction of; never what a fade happened to show).
                          "unlit": task and self._unlit(), "lux_applied": self._lux_scale(),
                          "page": person and person["target"] and list(person["target"]),
                          "fixtures": {name: {"level": row["level"], "pan_deg": row["pan_deg"], "tilt_deg": row["tilt_deg"],
                                              "target_m": row["target_m"], "job": self.rows[name]["job"]}
                                       for name, row in fixtures.items()},
                          "ambient": ambient and {"level": ambient["level"], "cct_k": ambient["cct_k"]}})

    def _lux(self, name, target, level):
        """The light at `target` from this fixture at `level`, as a scale on SPOT_TARGET (den-core's light model)."""
        modeled = self.core.share.brightness(self.rows[name], target)
        return modeled * self.core.spot_light.dimming_fraction(level) / self.core.SPOT_TARGET

    @staticmethod
    def _turns_on(light):
        return (light.get("on") is True and light.get("brightness", 1) != 0) or (
            light.get("on") is not False and light.get("brightness", 0) > 0)

    def _hold_base(self, group, name, entry, after):
        """Where a hold begins, from the snapshot its first change named: for the spot, whether it's the task's light
        (this person's lit task light then, or one the owner lights during a task left unlit as taught), the page,
        the aim and the scale the coordinator was lighting at; for the glow, the room's glow then."""
        if group == "glow":
            return {"ambient": entry["ambient"], "key": entry["key"], "origin": None, "learn": None, "zones": {}}
        fixture = entry["fixtures"][name]
        lit_task = bool(entry["task"] and entry["track_id"] is not None and fixture["job"] == entry["track_id"]
                        and fixture["level"] > 0 and fixture["target_m"] is not None)
        unlit = bool(not lit_task and entry["task"] and entry.get("unlit") and entry["track_id"] is not None
                     and self._turns_on(after.get("spotlight") or {}))
        return {"task_light": lit_task or unlit, "unlit": unlit, "page": entry["page"],
                "target_m": fixture["target_m"] if lit_task else None, "lux_before": entry.get("lux_applied", 1.0),
                "key": entry["key"], "origin": None, "learn": None, "last": {}}

    @staticmethod
    def _carry(group, record, after):
        """The hold's record follows the hand: its last spot state, or every zone and downlight it has set."""
        if group == "spot":
            last = record["last"]
            for key in ("pan_deg", "tilt_deg", "target"):
                if key in after:
                    last[key] = after[key]
            if after.get("spotlight"):
                last["spotlight"] = {**(last.get("spotlight") or {}), **after["spotlight"]}
        else:
            zones = record["zones"]
            for zone in after.get("radials") or []:
                key = "radials:%d" % zone["number"]
                zones[key] = {**zones.get(key, {}), **zone}
            if after.get("downward"):
                zones["downward"] = {**zones.get("downward", {}), **after["downward"]}
        return record

    def _learning_inputs(self, event, now):
        """(context, learning inputs or None, why not, extra): den-core learn's gates, in its order, on the snapshot the
        change names; then what the change says about the task spot and the room's glow.

        A change to a fixture already held is read against the hold, not den-live's released row: Home's `before` is
        the hand's own last state, and the hold's changes add up to one correction, learned from where the hold began
        (extra["supersedes"]: the hold's first learned change, per part). extra["plan"]: the hold's records this
        change leaves; extra["keys"]: each part's situation key."""
        snapshot = event["snapshot"]
        name, after = event["fixture_id"], event["after"]
        extra = {"plan": {}, "keys": {}, "supersedes": {}}
        groups = sorted({self._group(part) for part in manual.parts(after)})
        held = {group: self.hold_learning[(name, group)] for group in groups if (name, group) in self.hold_learning}
        entry = None
        if snapshot["epoch"] == self.epoch:
            entry = next((row for row in reversed(self.ring) if row["seq"] == snapshot["seq"]), None)
        if entry is None:
            extra["plan"] = {group: self._carry(group, copy.deepcopy(record), after) for group, record in held.items()}
            return {"seq": snapshot["seq"]}, None, "unknown_snapshot", extra
        plan = {group: copy.deepcopy(held[group]) if group in held else self._hold_base(group, name, entry, after)
                for group in groups}
        extra["plan"] = plan
        context = {key: entry[key] for key in ("seq", "key", "kind", "people", "track_id", "task", "directing")}
        context["age_s"] = round(now - entry["mono"], 3)
        why = ((None if entry["directing"] else "coordinator_off")
               or (None if entry["people"] else "vacant")
               or (None if entry["people"] == 1 else "people_%d" % entry["people"])
               or (None if entry["kind"] in self.core.situation.LEARNABLE else "situation_%s" % entry["kind"])
               or (None if now - entry["mono"] <= MANUAL_RECENT_S else "stale_snapshot")
               or self._mismatch(event, entry, held))
        for group, record in plan.items():
            self._carry(group, record, after)
        if why:
            return context, None, why, extra
        learn = {}
        spot = plan.get("spot")
        if spot is not None and not spot["task_light"]:
            context["spot"] = "not_the_task_light"   # a light for the owner's own use teaches the task nothing
        elif spot is not None:
            light = after.get("spotlight") or {}
            inputs = {"lux_scale": None, "offset_m": None, "off": None}
            point, near = (after.get("target") or {}).get("point"), None
            if point is not None:
                inputs["offset_m"] = fixture_learning.offset(point, spot["page"])
                if inputs["offset_m"] is None:
                    context["aim"] = "pointed_elsewhere"
                else:
                    near = point
            if light.get("on") is False or light.get("brightness") == 0:
                inputs["off"] = True
            elif "brightness" in light or light.get("on") is True:
                inputs["off"] = False
                where = near or spot["target_m"] or spot["page"]
                if "brightness" in light and where is not None:
                    inputs["lux_scale"] = self._lux(name, where, light["brightness"])
            if any(value is not None for value in inputs.values()):
                merged = dict(spot["learn"] or {"lux_before": spot["lux_before"], "lux_scale": None, "offset_m": None,
                                                "off": None})
                merged.update({key: value for key, value in inputs.items() if value is not None})
                learn["spot"] = spot["learn"] = merged
                self._origin(spot, "spot", event, entry, extra)
        glow = plan.get("glow")
        if glow is not None and glow["ambient"] is not None and ((after.get("radials") or []) or after.get("downward")):
            zones = list(glow["zones"].values())
            levels = [0.0 if zone.get("on") is False else zone["brightness"] for zone in zones
                      if zone.get("on") is False or "brightness" in zone]
            kelvins = [zone["color_temp_kelvin"] for zone in zones
                       if "color_temp_kelvin" in zone and zone.get("on") is not False]
            if levels or kelvins:
                learn["room"] = glow["learn"] = {
                    "level": sum(levels) / len(levels) if levels else None, "level_before": glow["ambient"]["level"],
                    "cct_k": round(sum(kelvins) / len(kelvins)) if kelvins else None,
                    "cct_before": glow["ambient"]["cct_k"]}
                self._origin(glow, "room", event, entry, extra)
        if not learn:
            return context, None, context.get("spot") or context.get("aim") or "nothing_to_learn", extra
        return context, learn, None, extra

    @staticmethod
    def _origin(record, part, event, entry, extra):
        """A hold's first learned change is its origin, in the situation it named; later ones supersede it."""
        if record["origin"] is None:
            record["origin"], record["key"] = event["event_id"], entry["key"]
        else:
            extra["supersedes"][part] = record["origin"]
        extra["keys"][part] = record["key"]

    def _mismatch(self, event, entry, held=None):
        """"before_mismatch" when Home's `before` isn't what it should have been drawing: what den-live published at
        that snapshot or, for a part already held, the hand's own last state. Else None."""
        held = held or {}
        before, fixture = event["before"], entry["fixtures"][event["fixture_id"]]
        light = before.get("spotlight") or {}
        spot = held.get("spot")
        if spot is None:
            if "brightness" in light and abs(light["brightness"] - fixture["level"]) > BEFORE_LEVEL_SLACK:
                return "before_mismatch"
            for key in ("pan_deg", "tilt_deg"):
                if key in before and abs(before[key] - fixture[key]) > BEFORE_ANGLE_SLACK_DEG:
                    return "before_mismatch"
        else:
            last = spot["last"]
            last_light = last.get("spotlight") or {}
            if ("brightness" in light and "brightness" in last_light
                    and abs(light["brightness"] - last_light["brightness"]) > BEFORE_LEVEL_SLACK):
                return "before_mismatch"
            for key in ("pan_deg", "tilt_deg"):
                if (key in before and last.get(key) is not None
                        and abs(before[key] - last[key]) > BEFORE_ANGLE_SLACK_DEG):
                    return "before_mismatch"
        zones = [("radials:%d" % zone["number"], zone) for zone in before.get("radials") or []] + (
            [("downward", before["downward"])] if before.get("downward") else [])
        glow, ambient = held.get("glow"), entry["ambient"]
        for key, zone in zones:
            if "brightness" not in zone:
                continue
            known = (glow["zones"].get(key) or {}).get("brightness") if glow is not None else None
            reference = known if glow is not None and known is not None else (
                ambient["level"] if glow is None and ambient is not None else None)
            if reference is not None and abs(zone["brightness"] - reference) > BEFORE_LEVEL_SLACK:
                return "before_mismatch"
        return None

    def _replayed(self, line):
        """An event keeps its receipt while ownership is reconciled against the recovered current run."""
        return {"ok": True, "event_id": line["event"]["event_id"], "recorded": True, "learned": bool(line.get("learn")),
                "fixture_id": line["event"]["fixture_id"],
                "why_not": line.get("why_not"), "hand": self._hand_record(line["event"]["fixture_id"]),
                "manual_revision": self.manual_meta.get(line["event"]["fixture_id"], {}).get("revision", 0),
                "epoch": self.epoch, "from_seq": 0}

    def manual_change(self, data):
        """POST /coordinator/manual (plan 04b): one settled change the owner made to a fixture in Home. It's recorded
        in the durable log (fsynced before the reply says so), learned from when den-core's gates allow, and the parts
        it touched are the owner's at once (the hand hold), recorded or not: manual always wins. A retry with the same
        event_id gets the same reply."""
        try:
            event = manual.parse(data)
        except ValueError as error:
            return 400, {"ok": False, "error": str(error)}
        with self.lock:
            if self.manual_authority_unavailable:
                return 503, {"ok": False, "error": "manual_authority_unavailable"}
            if event["fixture_id"] not in self.rows:
                return 400, {"ok": False, "error": "unknown fixture"}
            known = self.manual_replies.get(event["event_id"])
            if known is not None:
                if self.manual_fingerprints.get(event["event_id"]) != manual.fingerprint(event):
                    return self._manual_conflict(event["fixture_id"], "event_id_conflict")
                return 200, known
            name = event["fixture_id"]
            conflict = self._manual_order(event)
            if conflict:
                return self._manual_conflict(name, conflict)
            now = self.clock()
            self._expire(now)
            context, learn, why, extra = self._learning_inputs(event, now)
            line = {"schema": "den-manual-event-1", "event": event, "received_mono": now,
                    "received_wall_ms": round(self.wall() * 1000), "context": context, "key": context.get("key"),
                    "learn": learn, "why_not": why, "keys": extra["keys"] or None,
                    "supersedes": extra["supersedes"] or None}
            state = self._manual_state(event)
            saved = self._manual_before_transaction(name)
            self.manual_transaction = True
            hand = self._hold(event, now, extra["plan"], True)
            meta = self.manual_meta[name]
            meta.update(revision=meta["revision"] + 1, state=state, event_id=event["event_id"])
            if "client_id" in event:
                meta.update(upgraded=True, client_id=event["client_id"], client_seq=event["client_seq"])
            meta["hold_id"] = event.get("hold_id", event["event_id"]) if hand else None
            self._adopt_aim(name, event["after"])
            state["pan_deg"], state["tilt_deg"] = (-value / 100 for value in self.rows[name]["current"])
            reply = {"ok": True, "event_id": event["event_id"], "recorded": True, "learned": bool(learn),
                     "fixture_id": name,
                     "why_not": why, "hand": self._hand_record(name), "manual_revision": meta["revision"],
                     "epoch": self.epoch, "from_seq": self.seq + 1}
            line["authority"] = self._manual_checkpoint()["authority"]
            try:
                self.manual_log.append(line)
                recorded = True
            except OSError:
                self.problem, recorded = "manual log write failed", False
                self._manual_rollback(name, saved)
            finally:
                self.manual_transaction = False
            if recorded and learn:
                self.learning.apply(context["key"], learn, event["at_ms"], event["event_id"], extra["supersedes"],
                                    extra["keys"])
            if recorded:
                self.manual_dirty = False
                self.manual_fingerprints[event["event_id"]] = manual.fingerprint(event)
                self.manual_replies[event["event_id"]] = reply
                self._evaluate(now)
            else:
                reply = {**reply, "ok": False, "recorded": False, "learned": False, "why_not": "log_write_failed"}
        return (200 if recorded else 503), reply

    def manual_resume(self, data):
        """POST /coordinator/manual/resume {fixture_id}: Home's Resume, or its own hold's timeout. The fixture is
        den-live's again from the next evaluation."""
        if not isinstance(data, dict) or not isinstance(data.get("fixture_id"), str):
            return 400, {"ok": False, "error": "expected fixture_id"}
        with self.lock:
            if self.manual_authority_unavailable:
                return 503, {"ok": False, "error": "manual_authority_unavailable"}
            if data["fixture_id"] not in self.rows:
                return 400, {"ok": False, "error": "unknown fixture"}
            name = data["fixture_id"]
            meta = self.manual_meta[name]
            request_id = data.get("request_id")
            if request_id is not None:
                if (set(data) != {"request_id", "fixture_id", "hold_id", "expected_revision"} or
                        not isinstance(request_id, str) or not manual.EVENT_ID.fullmatch(request_id) or
                        not isinstance(data.get("hold_id"), str) or not manual.EVENT_ID.fullmatch(data["hold_id"]) or
                        type(data.get("expected_revision")) is not int or data["expected_revision"] < 0):
                    return 400, {"ok": False, "error": "invalid manual Resume guard"}
                known = self.manual_resumes.get(request_id)
                if known:
                    if known["fingerprint"] != manual.fingerprint(data):
                        return self._manual_conflict(name, "request_id_conflict")
                    return 200, {**known["reply"], "epoch": self.epoch, "from_seq": self.seq}
                if meta.get("hold_id") != data["hold_id"] or meta["revision"] != data["expected_revision"]:
                    return self._manual_conflict(name)
            elif meta["upgraded"]:
                return self._manual_conflict(name, "manual_version_required")
            elif set(data) != {"fixture_id"}:
                return 400, {"ok": False, "error": "invalid manual Resume guard"}
            saved = self._manual_before_transaction(name)
            self.manual_transaction = True
            released = sorted(part for name, part in self.hands if name == data["fixture_id"])
            for part in released:
                self._end_hand((data["fixture_id"], part))
            # Even an already empty fixture advances its fence for the acknowledged request.
            if not released:
                meta["revision"] += 1
            reply = {"ok": True, "released": released, "epoch": self.epoch, "from_seq": self.seq + 1,
                     "manual_revision": meta["revision"], "request_id": request_id, "hold_id": data.get("hold_id")}
            self.manual_transaction = False
            checkpoint = self._manual_checkpoint()
            if request_id is not None:
                checkpoint.update(resume=data, reply=reply)
            try:
                self.manual_log.append(checkpoint)
            except OSError:
                self._manual_rollback(name, saved)
                return 503, {"ok": False, "error": "manual authority journal write failed"}
            if request_id is not None:
                self.manual_resumes[request_id] = {"fingerprint": manual.fingerprint(data), "reply": reply}
            self.manual_dirty = False
            self._evaluate(self.clock())
            return 200, reply

    def learned_clear(self, data=None):
        """POST /coordinator/learned/clear: forget what the owner taught. The fixtures' log is kept as
        manual-events-<stamp>.jsonl and a new one starts; the bulbs' learning clears through the overlay, when there is
        one. The light in use goes back to the page and the house glow at once."""
        stamp = datetime.datetime.fromtimestamp(self.wall(), datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with self.lock:
            if self.manual_authority_unavailable:
                return 503, {"ok": False, "error": "manual_authority_unavailable"}
            previous_learning = self.hold_learning
            self.hold_learning = {}
            try:
                self.manual_log.reset(stamp, self._manual_checkpoint(history=True))
            except OSError:
                self.hold_learning = previous_learning
                return 503, {"ok": False, "error": "learned clear failed"}
            self.learning = fixture_learning.Learning()
            self.hold_learning = {}   # a hold's first change is gone with the log: nothing left for it to supersede
            self.spot_applied = self.room_cue = None
            self._evaluate(self.clock())
        if self.hue is not None:
            status, body = self.hue.request_clear(data)
            return status, {**body, "fixtures": "cleared"}
        return 200, {"ok": True, "fixtures": "cleared"}

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
            if not self._can_send(row, now):
                return
            self._move(name, row, target, 0.0, "dark", now, person, "pre_aim")
            return
        goal = self._goal(row, person)
        if not lit:
            if glide:
                return   # landing first (P3 holds a MOVE that arrives during a move); it rises once it's there
            if target != effective_target and not (self._in_beam(row, person["target"]) and self._clear(
                    row, person, self.core.eye.HEAD_CLEARANCE_M) and self._others_clear(
                    row, person["target"], row["current"], self.core.eye.HEAD_CLEARANCE_M)):
                # Pointing elsewhere, or near the page but with the beam as it points too close to the reader's head or
                # anyone else's (admission measured the line to the page): a dark move to the target den-core's eye
                # rule cleared, then the rise.
                if not self._can_send(row, now):
                    return
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
            # (Any other fixture lit for this person is leaving: it isn't chosen, whichever the loop reaches first.)
            handoff = any(other is not row and other["job"] == person["track_id"] and self._lighting(other)
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
            # A hand-off keeps the page's light whole with the other fixture's fall: a train eases the light itself;
            # a timed board rises on its own ease_out against a fall on ease_in_out twice as long (direction.CROSSFADE).
            space = "light" if handoff and not back and not row["timed"] else "level"
            if handoff and not back and row["timed"]:
                # Half the old fixture's fall, which can't outlast the validity its light was published with.
                curve, fall = direction.CROSSFADE["in"], self._fall(row, person, now)
                fade = min(fade, fall / direction.CROSSFADE["out_x"])
            row["ramp"] = direction.Ramp(row["level"], goal, fade, "ease_in_out" if back else curve, start,
                                         "crossfade_in" if handoff or back else "fade_in", space)
            return
        if target != effective_target:
            if glide and not row["profile"].get("retarget_mid_move"):
                return
            ramp = row["ramp"]
            if ramp is not None and ramp.end > ramp.start and not ramp.done(now):
                return   # a rise finishes before the glide: one change at a time
            if row["profile"].get("lit_motion") and self._in_beam(row, person["target"], direction.FRAME_HOLD):
                return   # the frame holds: the page is still well inside the beam, so nothing moves while they read
            if not row["profile"].get("lit_motion"):
                # A board that can't move lit makes every re-aim a blink. So it holds its aim while the page is still
                # well inside the beam; once the page has gone, a standby on its way there takes the job when it lands
                # (_candidate), as a crossfade. Only with no standby does it fade down, move and rise.
                if self._in_beam(row, person["target"], direction.DARK_HOLD):
                    return
                standby = self.rows.get(self.standby) if self.standby not in (None, name) else None
                if standby is not None and standby["glide"] is not None and not self._lighting(standby):
                    return
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
                # Everyone else's head too (plan 04, step 3), at the admission clearance plus the same margin.
                for other in self.others:
                    _, near = self.core.eye.path_clear(row["aim_origin_m"], row["horizontal_world_reference"],
                        tuple(x / 100 for x in row["current"]), tuple(x / 100 for x in target),
                        math.dist(row["aim_origin_m"], person["target"]), other["head"])
                    other_margin = math.dist(row["aim_origin_m"], other["head"]) * math.tan(
                        math.radians(row["profile"].get("path_margin_cdeg", 0) / 100))
                    if near < self.core.eye.HEAD_CLEARANCE_M + other_margin:
                        travel = "dark"
                if not self._swept_clear(row, person, target):
                    travel = "dark"   # the head or an eye inside the floor's clearance on the way, or where it lands
            if travel == "lit":
                if not self._can_send(row, now):
                    return
                # One movement, one command: the glide carries the level the train has reached, and a rise in progress
                # waits for it to land.
                # Its pace is a camera move's: a small reframe creeps, a big one is a dolly (direction.glide_seconds).
                # A board with the timed MOVE stretches its S-curve to it; so does a virtual one, which Home draws.
                moved = math.dist(row["target"], person["target"]) if row["target"] is not None else 0.0
                pace = direction.glide_seconds(moved, self.settings)
                timed = {"min_duration_ms": round(pace * 1000), "fade_ms": 0, "fade_curve": "linear"} if (
                    row["timed"] or row["profile"].get("fictional")) else None
                self._move(name, row, target, direction.level_code(row["level"]) / direction.LEVEL_CODES, "lit", now,
                           person, "task", timed=timed)
                return
            # Dim or dark travel (or a path past the head): fade down, move, rise again.
            down = min(self.settings["move_dim_level"] if travel == "dim" else 0.0, row["level"])
            if not down and not self._admits(name, row, person, target):
                # Dark, it's no longer kept, and it lands where admission (stricter than keep) refuses it: it would never
                # relight there. So the frame holds, lit; the floor still checks the beam as it points every tick.
                return
            row["dip"] = (target, down, person)
            row["ramp"] = direction.Ramp(row["level"], down, self.settings["fade_out_s"], "ease_out", now, "dip")
            return
        if row["ramp"] is None and goal > 0 and abs(goal - row["goal"]) > .1 * goal:
            # The book moved to where this fixture gives it a different amount of light (plan C6's 10 %).
            row["goal"] = goal
            row["ramp"] = direction.Ramp(row["level"], goal, self.settings["crossfade_s"], "ease_in_out", now, "level")
        row.update(job=person["track_id"], target=person["target"], basis=person["basis"])

    def _fall(self, row, person, now):
        """How long the other fixture lit for this person can take to fall in a hand-off on a timed board: twice the
        crossfade, within EXIT_MAX_S and its light's published validity (or what's left of a fall it's already in)."""
        fall = min(direction.EXIT_MAX_S, direction.CROSSFADE["out_x"] * self.settings["crossfade_s"])
        for other in self.rows.values():
            if other is row or other["job"] != person["track_id"] or not self._lighting(other):
                continue
            ramp = other["ramp"]
            if other["exiting"] and ramp is not None:
                fall = min(fall, max(0.0, ramp.started + ramp.duration - now))
            elif other["lit_until"] is not None:
                fall = min(fall, max(0.0, other["lit_until"] - now))
        return fall

    def _in_beam(self, row, point, fraction=.8):
        """The beam as it points now has `point` well inside it: within `fraction` of half its FWHM."""
        joints = self.core.joints
        aim = joints.direction(row["current"][0] / 100, row["current"][1] / 100, row["horizontal_world_reference"])
        to = [point[i] - row["aim_origin_m"][i] for i in range(3)]
        length = math.sqrt(sum(c * c for c in to))
        if length <= 0:
            return False
        cos = sum(aim[i] * to[i] for i in range(3)) / length
        half = row["profile"].get("beam_fwhm_deg", 20.0) / 2
        return math.degrees(math.acos(max(-1.0, min(1.0, cos)))) <= fraction * half

    def _outgrown(self, row, person):
        """A lit fixture that can't move lit, whose page has left the middle of its beam."""
        return (not row["profile"].get("lit_motion") and self._lighting(row) and not row["exiting"]
                and row["glide"] is None and not self._in_beam(row, person["target"], direction.DARK_HOLD))

    def _beam_clear(self, row, person):
        """Every beam the board emits clears the head and the eyes by AIM_KEEP_M, on its aim now. A MOVE still flying
        dark is judged where it will light, at its landing aim. A dark fixture with nothing in flight emits nothing."""
        glide = row["glide"]
        if row["level"] > 0:
            return self._clear(row, person, direction.AIM_KEEP_M)
        landing = glide is not None and (row["arrive_level"] or (row["ramp"] is not None and row["ramp"].sent
                                                                 and row["ramp"].end > 0))
        if landing:
            return self._clear(row, person, direction.AIM_KEEP_M, glide.target)
        return True

    def _others_clear(self, row, page, joints=None, clearance=None):
        """Every other person's head is out of the beam the board emits, on its aim now (or a dark flight's landing, or
        `joints`), by `clearance` (the direction's keep clearance unless given). Admission checks their heads on the line
        to the page (_clear_of_others), and here they're checked on the beam as it actually points, measured the same
        way: to the beam's centre from the fixture as far as the page, so a head past the book is as far as it is from
        the page, as admission found it (a head the cut and admission disagree on was a cut-and-relight blink)."""
        if not self.others:
            return True
        if joints is None:
            if row["level"] > 0:
                joints = row["current"]
            elif row["glide"] is not None and (row["arrive_level"] or (row["ramp"] is not None and row["ramp"].sent
                                                                        and row["ramp"].end > 0)):
                joints = row["glide"].target
            else:
                return True
        clearance = direction.GLARE_KEEP_M if clearance is None else clearance
        origin = row["aim_origin_m"]
        reach = math.dist(origin, page) if page is not None else math.inf   # no page: the whole beam
        aim = self.core.joints.direction(joints[0] / 100, joints[1] / 100, row["horizontal_world_reference"])
        for other in self.others:
            to = [other["head"][i] - origin[i] for i in range(3)]
            along = max(0.0, min(reach, sum(aim[i] * to[i] for i in range(3))))   # den-core's centre_clear's clamp
            if math.sqrt(sum((to[i] - along * aim[i]) ** 2 for i in range(3))) < clearance:
                return False
        return True

    def _swept_clear(self, row, person, target):
        """A lit glide from the beam's aim now to `target` keeps the reader's head and each eye out by the floor's own
        clearance (AIM_KEEP_M, _beam_clear's) at every point on the way, landing included: the board flies a straight
        line in joint space (direction.Glide), so the floor never has to cut a glide the direction approved."""
        start = row["current"]
        steps = max(1, math.ceil(max(abs(b - a) for a, b in zip(start, target)) / SWEEP_STEP_CDEG))
        return all(self._clear(row, person, direction.AIM_KEEP_M,
                               tuple(round(a + (b - a) * k / steps) for a, b in zip(start, target)))
                   for k in range(1, steps + 1))

    def _admits(self, name, row, person, joints):
        """A dark fixture aimed at `joints` would be admitted for this person, as _candidate admits one: den-core's eye
        rule, the reader's head and eyes on the beam at admission's clearance, and everyone else's on the line to the
        page."""
        return (self._eye(name, row, person, False).ok
                and self._clear(row, person, self.core.eye.HEAD_CLEARANCE_M, joints)
                and self._clear_of_others(row["aim_origin_m"], person["target"]))

    def _ready(self, row, person):
        """A dark fixture already pointing at the page (a standby that has landed), its beam as it points clearing the
        head by admission's margin: it can take the job as a crossfade."""
        return (not self._lighting(row) and row["glide"] is None and row["pending"] is None
                and self._in_beam(row, person["target"], direction.DARK_HOLD)
                and self._clear(row, person, self.core.eye.HEAD_CLEARANCE_M))

    def _clear(self, row, person, clearance, joints=None):
        """The beam's centre as the fixture points now (its joints mid-glide, or `joints`) passes den-core's head point
        and each eye with at least `clearance`, as the newest frame saw them. False without a head."""
        head = person.get("head")
        if not head:
            return False
        joints = row["current"] if joints is None else joints
        aim = self.core.joints.direction(joints[0] / 100, joints[1] / 100, row["horizontal_world_reference"])
        heads = [head] + list(person.get("eyes") or [])
        for point in heads:
            to = [point[i] - row["aim_origin_m"][i] for i in range(3)]
            along = sum(aim[i] * to[i] for i in range(3))
            gap = (math.sqrt(sum(c * c for c in to)) if along <= 0
                   else math.sqrt(sum((to[i] - along * aim[i]) ** 2 for i in range(3))))
            if gap < clearance:
                return False
        return True

    @staticmethod
    def _can_send(row, now):
        """The firmware's radio rule for a real fixture (not a fictional one): at most RADIO_PER_S commands in any
        second, Stop and keep-lit included. The direction's own commands wait their turn and take one slot fewer, so a
        Stop, a release or a cut (dark at once) never has to be a third command in a second.
        Those, and an exit's fade (the light going: dark within a second of standing), never wait, but they count. A
        hand-off's fall waits like the rise it's paired with."""
        if row["profile"].get("fictional"):
            return True
        return sum(1 for t in row["sends"] if t > now - 1.0 + 1e-9) < direction.RADIO_PER_S - 1

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
        if not self._can_send(row, now):
            return
        self._move(name, row, joints, 0.0, "dark", now, person, "standby")

    def _exit(self, name, row, now, reason, *, handoff=False, ending=None, leaving=False):
        """The light leaves: a fade-out on the exit curve, or a crossfade when another fixture takes the job. It's dark
        by the light's last published validity, so an exit never keeps a beam on longer than a snapshot promised. A
        planned end (ending: the task's deadline) starts early enough to be dark at it. Off, Stop, expiry and a zero
        fade stay instant."""
        # Leaving, a real board takes leave_fade_board_s: its send path uses up bar C's second (plan 05).
        leave = "leave_fade_s" if row["profile"].get("fictional") else "leave_fade_board_s"
        fade = min(direction.EXIT_MAX_S, self.settings["crossfade_s" if handoff else leave if leaving else "fade_out_s"]
                   * (direction.CROSSFADE["out_x"] if handoff and row["timed"] else 1))
        if row["leased_at"] is not None:
            # No keep-lit goes in an exit, so it ends inside the board's lease from the last thing that renewed it.
            fade = min(fade, row["leased_at"] + direction.EXIT_MAX_S - now)
        bound = row["lit_until"] if ending is None else min(row["lit_until"] or ending, ending)
        # A board that joins a flight fades during a glide as it does at rest, and so does a virtual fixture: its level
        # steps join the glide (_apply), so an exit mid-creep is a fade, not a cut, and a planned end is played out.
        joins = (row["timed"] and row["join"]) or bool(row["profile"].get("fictional"))
        too_short = fade <= 0 if row["timed"] else fade < 2 * row["step_s"]
        if too_short or bound is None or now >= bound or row["level"] <= 0 or (row["glide"] is not None and not joins):
            # A fade too short for this fixture's train, a spent validity, or a fixture in the middle of a glide: one
            # MOVE, dark. The board can't fade during a move (P3: a MOVE that arrives during a move waits for the
            # landing), so a train would stall.
            if ending is None or row["glide"] is not None:
                self._release(name, row, now, reason)
            return
        # The curves: a crossfade eases in and out (in light on a train; on a timed board its own ease_in_out over
        # twice the rise); an exit lets most of the light go first (ease_out).
        curve = "ease_in_out" if handoff else "ease_out"
        row["ramp"] = direction.Ramp(row["level"], 0.0, min(fade, bound - now), curve, now,
                                     "crossfade_out" if handoff else "fade_out",
                                     "light" if handoff and not row["timed"] else "level")
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
        if row["timed"] and ramp.space == "level":
            self._play_timed(name, row, now, held, ramp)
            return   # a crossfade eases the light, which the board's curves can't: it's a train even on a timed board
        if not row["exiting"] and (row["glide"] is not None or held):
            if last is not None:
                ramp.paused(now - last)
            return
        if row["step_at"] is not None and now - row["step_at"] < row["step_s"] - 1e-9:
            return   # the train's cap: at most one level MOVE per step_s (direction.level_step_s)
        if not self._can_send(row, now):
            return   # the radio rule: every step waits its turn, an exit's included, so a cut has its slot
        code = direction.level_code(ramp.value(now))
        if code != direction.level_code(row["level"]):
            gliding = row["glide"] is not None
            if (code and row["level"] <= 0 and self.track is not None
                    and not self._clear(row, self.track, direction.AIM_KEEP_M,
                                        row["glide"].target if gliding else row["current"])):
                self._release(name, row, now, "beam_through_head")   # its first step would light into the eyes
                row["cut_at"] = now
                return
            row["step_at"] = now
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
            if not self._can_send(row, now):
                return
            if (ramp.end > 0 and row["level"] <= 0 and self.track is not None
                    and not self._clear(row, self.track, direction.AIM_KEEP_M,
                                        row["glide"].target if gliding else row["current"])):
                self._release(name, row, now, "beam_through_head")   # it would light into the head or the eyes
                row["cut_at"] = now
                return
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
        row, now = self.rows[name], self.clock()
        row["sends"] = [t for t in row["sends"] if t > now - 1.0] + [now]   # the radio rule's window (_can_send)
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
                # A real board arrives after its settle (its predicted_ms counts it); a virtual one has no head to settle.
                settle = 0.0 if row["profile"].get("fictional") else row["profile"].get("settle_s", 0.0)
                row["glide"] = direction.Glide(start, target, row["profile"]["axes"], now, floor, settle)
                row["current"] = start
        row["arrive_level"] = None
        if fading:
            # The board fades from what it shows to the MOVE's level, on the ramp's curve. Dark on the way, it blooms
            # from dark when it lands.
            if moving and effective["travel"] != "lit":
                ramp.start, ramp.started = 0.0, row["glide"].arrival
                row["level"] = 0.0
            else:
                row["level"] = ramp.value(now)
        elif moving and effective["travel"] != "lit":
            row["level"], row["arrive_level"] = 0.0, (level or None)
        else:
            row["level"] = level
        lit = level > 0 or row["level"] > 0   # a fade to 0 holds the lease until it ends
        row.update(move=record, token=record["sequence"] if lit else None, keep_at=now + LEASE_S)
        if lit:
            row["leased_at"] = now
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

    def _gaming(self):
        inputs = self.hue.inputs() if self.hue is not None else None
        return bool(inputs) and inputs["attrs"].get("gaming_signal") == "playing"

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
        self._persist_manual()
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
                              "manual_revision": self.manual_meta[name]["revision"],
                              "pan_deg": -row["current"][0] / 100, "tilt_deg": -row["current"][1] / 100,
                              "target_m": row["target"], "target_basis": row["basis"],
                              "reason": "stopped" if self.stopped else "off" if not self.on else row["reason"],
                              "motion_phase": "moving" if row["glide"] else "settled",
                              "last_move_trace_id": move.get("trace_id"), "move_sequence": move.get("sequence"),
                              "move_decision_id": move.get("decision_id"),
                              "keep_lit_token": row["token"] if level > 0 else None,
                              "glide": row["glide"].published() if row["glide"] and directing else None,
                              "fade": row["ramp"].published(now) if row["ramp"] and directing else None,
                              # Plan 04b, additive: the owner's hold on this fixture, and what was learned in its light.
                              "hand": self._hand_record(name) if directing else None,
                              "learned": self._learned_record(row) if directing and level > 0 else None}
        # The room's glow (additive): what Home draws on every fixture's glow zones and downlight, and later the scale
        # den_lighting would apply to the room's bulbs. None while the coordinator is Off.
        ambient = self.ambient.published(self.settings, now) if self.on else None
        if ambient is not None:
            learned = self.room_cue and self.room_cue["learned"]
            ambient["learned"] = (learned and {"level": round(learned["level"], 4), "cct_k": learned["cct_k"],
                                               "n": learned["n"]}) if directing else None
        self._snapshot_entry(now, fixtures, ambient)
        self._plan_bulbs(now, ambient)   # the bulbs follow the same ambient record Home draws (contract 7)
        self.latest = {"schema": "den-virtual-spots-1", "home": "den", "site": self.site, "epoch": self.epoch, "seq": self.seq,
                       "manual_protocol": 2,
                       "binding": self.binding, "subject": self._subject(), "sampled_mono": now, "valid_until_mono": until,
                       "on": self.on, "stopped": self.stopped, "stop_counter": self.stop_counter, "fixtures": fixtures,
                       "ambient": ambient, "bulbs": self._bulb_record()}
        self.counts["snapshots"] += 1
        self.publish(self.latest)

    def _learned_record(self, row):
        """What the task light carries from the owner's teaching, when it's lit for this episode's task."""
        applied = self.spot_applied
        learned = applied[1] if applied is not None and applied[0] is self.episode else None
        if not learned or self.track is None or row["job"] != self.track["track_id"]:
            return None
        return {"lux_scale": None if learned.get("lux_scale") is None else round(learned["lux_scale"], 3),
                "offset_m": None if learned.get("offset_m") is None else [round(x, 3) for x in learned["offset_m"]],
                "n": learned["n"]}

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
        return {"people": int(self.track is not None) + len(self.others), "task": task, "seated": episode is not None,
                "activity_known": known, "since_mono": self.context_since}

    def _controls(self):
        return {"on": self.on, "stopped": self.stopped, "stop_counter": self.stop_counter}

    def _persisted(self):
        return {**self._controls(), "control_revision": self.control_revision,
                "manual_revisions": {name: meta["revision"] for name, meta in self.manual_meta.items()}}

    def routes(self):
        routes = {("GET", "/coordinator"): self.state, ("POST", "/coordinator/switch"): self.switch,
                ("POST", "/coordinator/stop"): self.stop, ("POST", "/coordinator/resume"): self.resume,
                ("POST", "/coordinator/settings"): self.configure, ("POST", "/coordinator/bulbs"): self.bulbs,
                ("POST", "/coordinator/manual"): self.manual_change,
                ("POST", "/coordinator/manual/resume"): self.manual_resume,
                ("POST", "/coordinator/learned/clear"): self.learned_clear}
        return {**routes, **{(method, path.replace("/coordinator", "/coordinator/virtual", 1)): handler
                            for (method, path), handler in routes.items()}}

    def state(self, _body=None):
        with self.lock:
            current = {**self._controls(), "snapshot": self.latest, "decision": self.latest_decision,
                       "manual_protocol": 2,
                       "server_mono": self.clock(), "settings": dict(self.settings), "problem": self.problem,
                       "trace_event_index": self.trace_committed, "trace_complete": self.trace_complete,
                       "bulbs_on": self.bulbs_on, "bulb_plan": self._bulb_record(),
                       "situation": self.bulb and self.bulb["key"][0],
                       # Plan 04b: the panel's "learned from you" (the bulbs' own learning stays under `learned`), the
                       # holds, and how many changes the log holds.
                       "learned_from_you": self.learning.public(),
                       "hands": {name: self._hand_record(name) for name in self.rows if self._hand_record(name)},
                       "fixture_situation": self._situation(self.clock())[1],
                       # Lines the change log couldn't use at start (torn, corrupt): skipped, never fatal.
                       "manual_log": {"skipped": self.manual_log.skipped + self.learning.skipped}}
        if self.hue is not None:
            readback = self.hue.readback()
            current.update({**readback, "situation": readback.get("situation") or current["situation"],
                            "problem": current["problem"] or readback.get("problem")})
        # `learned` stays the bulbs' alone (the overlay's, "light.*"): Home's panel (#444, 2fbc5bd0) lists it as bulbs and
        # reads the spot and the room glow from learned_from_you beside it.
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
            if action == "resume" and self.manual_authority_unavailable:
                return 503, {"ok": False, "error": "manual_authority_unavailable", **self._controls()}
            if action == "on" and self.on:
                return 200, {"ok": True, **self._controls()}
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
            self.hands.clear()   # Off and Stop end every hand hold: den-live directs nothing, so there's nothing to hold
            for meta in self.manual_meta.values():
                if meta.get("hold_id") is not None or meta["upgraded"]:
                    meta["revision"] += 1
                    meta["hold_id"] = None
                    self.manual_dirty = True
            self.hold_learning.clear()
            self.room_cue = None
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

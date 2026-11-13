"""The den lighting coordinator, live in den-live on LA (coordinator plan, step 3). Standard library.

den-core's coordinator (tools/den-core/den_coordinator.py, the pinned "coordinator" tree) decides; this file feeds it
and carries out what it says:
- People: a Loop observer (perceive.Loop's observe list) turns each processed frame's confirmed tracks and their room
  joints ({name: [xyz, supported|uncertain]}, perceive.track_poses) into a `people` event.
- Home Assistant (ha_link.Poller, about once a second): den_lighting 1.1's status sensor (occupancy, gaming, the owner's
  latest correction) and the "Den lighting coordinator" switch. On means the switch is on and den_lighting is automatic.
- PersonState: person_state(record), for the end-to-end plan's person state (§3.0).
- A tick every second.
Out:
- bulb plans: den_lighting.coordinator_plan, through Home Assistant;
- fixture calls: to a real fixture's Home Assistant services when its row names them, else written down only
  (fixture_calls.jsonl, `sent: false`), for the end-to-end plan's Computed stop;
- Decisions, plans, corrections and kept marks: one day file each under <state>/days/<UTC date>/, kept 14 days like
  den-live's other words; learned.json and settings.json under <state>/coordinator/.
Routes (stream.py, behind the gateway: GET look-only, POST signed in only):
    GET  .../coordinator                 the switch, the situation, the latest Decision and plan, what it learned, settings
    POST .../coordinator/switch          {"on": true|false}: flips the switch in Home Assistant
    POST .../coordinator/settings        the owner's settings (move_style, fades, pre_aim, ...)
    POST .../coordinator/learned/clear   keeps learned.json as learned-<stamp>.json and starts empty
    POST .../coordinator/desk            {"on": true|false}: the desk test window, 3 hours
"""
import datetime
import importlib
import json
import math
import os
import struct
import sys
import threading
import time
import uuid
from pathlib import Path

import c12_computed
import ha_link

STATUS_ENTITY = "sensor.den_lighting_control"
SWITCH_ENTITY = "switch.den_lighting_coordinator"
CONFIDENCE = {"supported": 1.0, "uncertain": 0.5}
DESK_WINDOW_S = 3 * 3600
TICK_S = 1.0
MAX_LINE = 16384
HA_STALE_S = 10.0


CORE_MODULES = ("den_coordinator", "body", "bulbs", "eye", "joints", "learn", "scurve", "share", "situation", "spot_light",
                "spot_rule", "need", "need_v1")
LOG_ONLY = ("predicted_s", "profile_sha256", "pose_age_s", "activity_valid_until")   # in fixture_calls.jsonl, never sent


def load_core(tree):
    """den_coordinator and learn from the pinned coordinator tree (git archive of tools/den-core and the brain), loaded
    apart from den-live's own modules: den-live has a body.py of its own (the live 3D body), and den-core's `import
    body` must never get it. The core's modules are kept as den_core.<name>, and whatever den-live had under the same
    names is put back."""
    folder = str(Path(tree) / "tools" / "den-core")
    saved = {name: sys.modules.pop(name) for name in CORE_MODULES if name in sys.modules}
    sys.path.insert(0, folder)
    try:
        dc, learn = importlib.import_module("den_coordinator"), importlib.import_module("learn")
        dc.need_v1 = importlib.import_module("need_v1")
        for name in CORE_MODULES:
            if name in sys.modules:
                sys.modules["den_core." + name] = sys.modules.pop(name)
    finally:
        sys.path.remove(folder)
        sys.modules.update(saved)
    return dc, learn


def joints3d(pose):
    """den-live's room joints to den-core's {name: [x, y, z, confidence]}."""
    out = {}
    for name, value in (pose or {}).items():
        if isinstance(value, (list, tuple)) and len(value) == 2 and value[1] in CONFIDENCE and len(value[0]) == 3:
            out[name] = [float(value[0][0]), float(value[0][1]), float(value[0][2]), CONFIDENCE[value[1]]]
    return out


def ha_event(snapshot, status_entity=STATUS_ENTITY, switch_entity=SWITCH_ENTITY):
    """den_lighting 1.1's status and the coordinator switch, as the coordinator's `ha` event."""
    status, switch = snapshot.get(status_entity), snapshot.get(switch_entity)
    attrs = (status or {}).get("attributes") or {}
    occupancy = attrs.get("occupancy")
    # Gaming is den_lighting's own game session: an unread PC sensor (the PC is off, say) is no game, as den_lighting
    # treats it, so it never pauses learning by itself.
    data = {"on": bool(switch and switch.get("state") == "on" and attrs.get("automatic") is True),
            "occupied": {"on": True, "off": False}.get(occupancy), "game_running": bool(attrs.get("gaming_session"))}
    if isinstance(attrs.get("last_correction"), dict):
        data["last_correction"] = attrs["last_correction"]
    return data


class CoordinatorService:
    def __init__(self, core, *, state_dir, lights, fixtures=None, ha=None, utc_offset_h=0.0, seat_mark=None,
                 status_entity=STATUS_ENTITY, switch_entity=SWITCH_ENTITY, clock=time.time, virtual_binding=None):
        self.dc, self.learn = core
        self.state = Path(state_dir)
        self.folder = self.state / "coordinator"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.settings_path, self.learned_path = self.folder / "settings.json", self.folder / "learned.json"
        self.ha, self.clock = ha, clock
        self.virtual_binding = virtual_binding
        self.virtual = virtual_binding is not None
        self.epoch = uuid.uuid4().hex
        self.control_path = self.folder / "virtual-control.json"
        self.control = self._load_control() if self.virtual else None
        self.trace = c12_computed.ComputedTrace(self.state / "days", self.epoch) if self.virtual else None
        self.output, self.spot_seq, self.site = None, 0, None
        self.scope_matches = False
        self.spot_reasons = {}
        self.labels_revision = None
        self.need = sys.modules["den_core.need_v1"].make_policy() if self.virtual and "den_core.need_v1" in sys.modules else None
        self.status_entity, self.switch_entity = status_entity, switch_entity
        self.lock = threading.RLock()
        self.coordinator = self.dc.make_policy({
            "fixtures": fixtures or {}, "lights": list(lights), "utc_offset_h": utc_offset_h, "seat_mark": seat_mark,
            "settings": self._read_json(self.settings_path) or {}, "learner": self.learn.load(str(self.learned_path))})
        if self.virtual:
            self.coordinator.ha["on"] = self.control["enabled"]
            self.coordinator.stopped = self.control["stopped"]
        self.latest_decision = self.latest_plan = None
        self.ha_seen_at, self.problem = None, None
        self.counts = {"decisions": 0, "plans_sent": 0, "send_failed": 0, "fixture_calls": 0, "learned": 0, "lines_too_long": 0}
        self.halt, self.ticker, self.poller = threading.Event(), None, None

    # ---- in ---------------------------------------------------------------------------------------------------------

    def handle(self, kind, data=None, t=None):
        t = self.clock() if t is None else t
        with self.lock:
            if kind == "tick" and self.need:
                self._need_event(kind, None, t)
            out = self.coordinator.handle({"kind": kind, "t": t, "data": data or {}})
            for record in out:
                self._dispatch(record, t)
            self._publish_spots(t)
        return out

    def observe(self, event, frame):
        """perceive.Loop's observer: each processed frame's confirmed tracks, with their joints, to the coordinator."""
        if frame is None:
            return event
        poses = frame.get("poses") or {}
        tracks = [{"track_id": t["track_id"], "confirmed": True, "joints3d": joints3d(poses.get(t["track_id"])),
                   "capture_ts": frame.get("capture_ts") or self.clock()}
                  for t in event.get("tracks") or [] if t.get("state") == "confirmed"]
        if self.virtual and self.output is not None and getattr(self.output, "labels", None) is not None:
            labels = self.output.labels.get()
            with self.lock:
                revision = labels.get("revision")
                if revision != self.labels_revision:
                    self.labels_revision = revision
                    current = labels if labels.get("content_revision") == self.virtual_binding["binding"]["content_revision"] else {}
                    self.handle("labels", current)
        self.handle("people", {"tracks": tracks})
        return event

    def on_ha(self, snapshot):
        self.ha_seen_at = self.clock()
        data = ha_event(snapshot, self.status_entity, self.switch_entity)
        if self.virtual:
            data.pop("on")
            data.pop("last_correction", None)
        self.handle("ha", data)

    def person_state(self, record):
        self.handle("person_state", record)

    def attach_stream(self, output, revision, site):
        with self.lock:
            self.output, self.site = output, site
            self.scope_matches = bool(self.virtual and self.virtual_binding["binding"]["content_revision"] == revision)
            self._publish_spots(self.clock())

    def on_ask_reply(self, result):
        if self.need and result.get("fact"):
            with self.lock:
                self._need_event("facts", result["fact"], self.clock())

    def _need_event(self, kind, data, t):
        self.need.exit_idle_s = self.coordinator.settings["phone_patience_s"]
        for record in self.need.handle({"kind": kind, "data": data, "t": t}):
            self.person_state(record["data"])

    def _publish_spots(self, t):
        if not self.scope_matches or self.output is None:
            return
        self.spot_seq += 1
        fixtures = []
        enabled, stopped = self.control["enabled"], self.control["stopped"]
        for fixture, spot in sorted(self.coordinator.spots.items()):
            pan, tilt = spot["joints"] or (0, 0)
            trace_id = self.trace.aims.get(fixture) if spot["on"] else self.trace.latest.get(fixture)
            job = spot.get("job") or {}
            person = (self.coordinator.tracks.get(job.get("who")) or {})
            capture = person.get("capture_ts")
            fresh = job.get("kind") != "task" or (
                isinstance(capture, (float, int)) and 0 <= t - capture <= self.dc.POSE_MAX_AGE_S)
            on = enabled and not stopped and bool(spot["on"] and trace_id) and fresh
            why = ("Stopped" if stopped else "Off" if not enabled else
                   self.spot_reasons.get(fixture, "Waiting for a lighting decision"))
            fixtures.append({"fixture_id": fixture, "pan_deg": -pan, "tilt_deg": -tilt, "on": on,
                             "level": spot["level"] if on else 0, "cct_k": self.dc.CCT_K,
                             "why": why, "trace_id": trace_id, "target_m": spot.get("target")})
        sampled = round(t * 1000)
        self.output.publish_spot({"schema": "den-virtual-spots-1", "home": "den", "site": self.site,
                                  "epoch": self.epoch, "seq": self.spot_seq, "sampled_at_ms": sampled,
                                  "expires_at_ms": sampled + 1800, **self.control, **self.virtual_binding,
                                  "fixtures": fixtures})

    def _load_control(self):
        try:
            data = json.loads(self.control_path.read_text(encoding="utf-8"))
            if (type(data) is not dict or set(data) != {"enabled", "stopped", "control_revision"}
                    or type(data["enabled"]) is not bool or type(data["stopped"]) is not bool
                    or type(data["control_revision"]) is not int or data["control_revision"] < 0):
                raise ValueError("invalid virtual control")
            return data
        except FileNotFoundError:
            return {"enabled": False, "stopped": False, "control_revision": 0}

    def _virtual_control(self, action, body):
        if type(body) is not dict:
            return 400, {"ok": False, "error": "invalid_control"}
        expected = body.get("expected_control_revision")
        if action in ("on", "resume") and type(expected) is not int:
            return 400, {"ok": False, "error": "control_revision_required"}
        with self.lock:
            current = self.control
            if action in ("on", "resume") and expected != current["control_revision"]:
                return 409, {"ok": False, "error": "control_revision_changed", **current}
            if action == "resume" and not current["stopped"]:
                return 409, {"ok": False, "error": "not_stopped", **current}
            if action == "on" and not self.scope_matches:
                return 409, {"ok": False, "error": "virtual_fixture_binding_unavailable", **current}
            updated = {**current, "control_revision": current["control_revision"] + 1}
            if action in ("off", "on"):
                updated["enabled"] = action == "on"
            else:
                updated["stopped"] = action == "stop"
            part = self.control_path.with_suffix(".new")
            with part.open("w", encoding="utf-8") as f:
                json.dump(updated, f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(part, self.control_path)
            self.control = updated
            # The computed commands follow the same ordering as the virtual latch.
            t = self.clock()
            trace_action = ("stop" if action == "off" else "resume"
                            if (action == "resume" and current["enabled"]
                                or action == "on" and not current["enabled"] and not current["stopped"]) else None)
            if trace_action:
                for fixture in self.coordinator.fixtures:
                    self._fixture_call({"action": trace_action, "fixture_id": fixture}, t)
            if action in ("off", "on"):
                self.handle("ha", {"on": updated["enabled"]}, t)
            else:
                self.handle(action, t=t)
            return 200, {"ok": True, **updated}

    def route_virtual_switch(self, body):
        if type(body) is not dict or type(body.get("on")) is not bool:
            return 400, {"ok": False, "error": "invalid_control"}
        return self._virtual_control("on" if body["on"] else "off", body)

    # ---- out --------------------------------------------------------------------------------------------------------

    def _dispatch(self, record, t):
        kind, data = record["kind"], record["data"]
        if kind == "decision":
            self.latest_decision = data
            for fixture, row in data.get("spots", {}).items():
                self.spot_reasons[fixture] = (row.get("job") or {}).get("kind", "No current job")
            self.counts["decisions"] += 1
            self._log("decisions", data, t)
        elif kind == "bulb_plan":
            self.latest_plan = data
            self._log("plans", data, t)
            if not self.virtual:
                self._send(*data["service"].split(".", 1), data["data"])
        elif kind == "intent":
            self._fixture_call(data, t)
        elif kind == "learned":
            self._log("corrections", data, t)
            if data.get("learned"):
                self.counts["learned"] += 1
                self.learn.save(str(self.learned_path), self.coordinator.learner)
        elif kind == "kept":
            self._log("kept", data, t)

    def _send(self, domain, service, data):
        if self.ha is None:
            self.problem = "no Home Assistant link: the settings file has no asks block with ha_url"
            return False
        try:
            self.ha.call(domain, service, data)
        except ha_link.HaError as exc:
            self.counts["send_failed"] += 1
            self.problem = "%s.%s: %s" % (domain, service, exc)
            return False
        if domain == "den_lighting":
            self.counts["plans_sent"] += 1
        return True

    def _fixture_call(self, intent, t):
        """A real fixture's call goes to the Home Assistant service its row names for the action; every call is written
        down, sent or not (the end-to-end plan's fixture_calls.jsonl)."""
        row = self.coordinator.fixtures.get(intent.get("fixture_id")) or {}
        service = (row.get("services") or {}).get(intent.get("action")) if row.get("real") else None
        sent = False
        if self.virtual:
            day = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date().isoformat()
            if intent["action"] == "aim_move":
                self.trace.aims.pop(intent["fixture_id"], None)
                self.trace.latest.pop(intent["fixture_id"], None)
            try:
                self.trace.record(intent, day)
            except (KeyError, ValueError, OSError, OverflowError, struct.error):
                self.trace.aims.pop(intent["fixture_id"], None)
                self.trace.latest.pop(intent["fixture_id"], None)
                self.problem = "computed C12 trace unavailable"
        elif service and "." in service:   # the wire format (coordinator plan C9): the log-only fields stay in the log
            data = {k: v for k, v in intent.items() if k not in ("action", "fixture_id", *LOG_ONLY)}
            data.update(row.get("service_target") or {})
            sent = self._send(*service.split(".", 1), data)
        self.counts["fixture_calls"] += 1
        self._log("fixture_calls", dict(intent, at=round(t, 3), sent=sent), t)

    def _log(self, name, data, t):
        line = json.dumps(data, separators=(",", ":"), default=str)
        if len(line) > MAX_LINE:
            self.counts["lines_too_long"] += 1
            line = json.dumps({"too_long": True, "kind": name, "at": t, "decision_id": data.get("decision_id")})
        day = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d")
        folder = self.state / "days" / day
        try:
            folder.mkdir(parents=True, exist_ok=True)
            with open(folder / ("coordinator-%s.jsonl" % name), "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            self.problem = "a day file didn't write"

    @staticmethod
    def _read_json(path):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # ---- routes -----------------------------------------------------------------------------------------------------

    def routes(self):
        if self.virtual:
            base = "/coordinator/virtual"
            return {("GET", base): self.route_state,
                    ("POST", base + "/switch"): self.route_virtual_switch,
                    ("POST", base + "/stop"): lambda body: self._virtual_control("stop", body),
                    ("POST", base + "/resume"): lambda body: self._virtual_control("resume", body),
                    ("POST", base + "/settings"): self.route_settings,
                    ("POST", base + "/learned/clear"): self.route_clear}
        return {("GET", "/coordinator"): self.route_state,
                ("POST", "/coordinator/switch"): self.route_switch,
                ("POST", "/coordinator/settings"): self.route_settings,
                ("POST", "/coordinator/learned/clear"): self.route_clear,
                ("POST", "/coordinator/desk"): self.route_desk}

    def route_state(self, _body=None):
        with self.lock:
            c = self.coordinator
            age = None if self.ha_seen_at is None else round(self.clock() - self.ha_seen_at, 1)
            return 200, {"on": c.ha["on"], "stopped": c.stopped, "desk_until": c.desk_until,
                         "situation": (self.latest_decision or {}).get("situation"), "decision": self.latest_decision,
                         "plan": self.latest_plan, "learned": c.learner.entries, "settings": c.settings,
                         "home_assistant": {"age_s": age, "fresh": age is not None and age <= HA_STALE_S,
                                            "problem": self.problem or (self.poller.problem if self.poller else None)},
                         "counts": dict(self.counts),
                         **({**self.control, "epoch": self.epoch, "server_time_ms": round(self.clock() * 1000)}
                            if self.virtual else {})}

    def route_switch(self, body):
        if not isinstance(body, dict) or type(body.get("on")) is not bool:
            return 400, {"ok": False, "error": "send {\"on\": true} or {\"on\": false}"}
        ok = self._send("switch", "turn_on" if body["on"] else "turn_off", {"entity_id": self.switch_entity})
        return (200, {"ok": True}) if ok else (502, {"ok": False, "error": self.problem})

    def route_settings(self, body):
        if not isinstance(body, dict):
            return 400, {"ok": False, "error": "send the settings as an object"}
        for key, value in body.items():   # checked before anything changes
            default = self.dc.SETTINGS.get(key)
            if key not in self.dc.SETTINGS:
                return 400, {"ok": False, "error": "%s isn't a setting" % key}
            if key == "move_style" and value not in self.dc.MOVE_STYLES:
                return 400, {"ok": False, "error": "move_style must be one of %s" % ", ".join(self.dc.MOVE_STYLES)}
            if isinstance(default, bool) != isinstance(value, bool) or (
                    isinstance(default, (int, float)) and not isinstance(default, bool)
                    and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0)):
                return 400, {"ok": False, "error": "%s has the wrong kind of value" % key}
        with self.lock:
            try:
                self.handle("settings", body)
            except ValueError as exc:
                return 400, {"ok": False, "error": str(exc)}
            settings = dict(self.coordinator.settings)
            part = self.settings_path.with_suffix(".new")
            part.write_text(json.dumps(settings, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(part, self.settings_path)
        return 200, {"ok": True, "settings": settings}

    def route_clear(self, _body=None):
        stamp = datetime.datetime.fromtimestamp(self.clock(), datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with self.lock:
            fresh = self.learn.clear(str(self.learned_path), stamp)
            self.coordinator.learner = fresh
            self.coordinator.planner.last_scenes = {"cleared": True}   # differs from any plan: an explicit empty plan goes next
        return 200, {"ok": True, "kept_as": "learned-%s.json" % stamp}

    def route_desk(self, body):
        if not isinstance(body, dict) or type(body.get("on")) is not bool:
            return 400, {"ok": False, "error": "send {\"on\": true} or {\"on\": false}"}
        t = self.clock()
        self.handle("desk", {"on": body["on"], "until": t + DESK_WINDOW_S}, t)
        return 200, {"ok": True, "until": t + DESK_WINDOW_S if body["on"] else None}

    # ---- running ----------------------------------------------------------------------------------------------------

    def start(self, poll_entities=True):
        if self.ha is not None and poll_entities:
            self.poller = ha_link.Poller(self.ha, [self.status_entity, self.switch_entity], self.on_ha)
            self.poller.start()
        self.ticker = threading.Thread(target=self._tick, daemon=True, name="den-live-coordinator-tick")
        self.ticker.start()

    def _tick(self):
        while not self.halt.wait(TICK_S):
            try:
                self.handle("tick")
            except Exception:   # the coordinator's failure never stops the markers
                self.problem = "a tick failed"

    def close(self):
        self.halt.set()
        if self.poller is not None:
            self.poller.halt.set()

"""Optional Phase 2 publisher. All HA I/O runs on its own ordered worker."""

import json
import os
import threading
import time
import uuid
from pathlib import Path

import ha_link


RENEW_S = 10.0
LEASE_S = 60
STATUS_ENTITY = "sensor.den_lighting_control"
CORRECTION_AGE_S = 15


class HueOverlay:
    def __init__(self, core, learner, ha, context, state, *, utc_offset_h=0.0,
                 clock=time.monotonic, wall=time.time, status_entity=STATUS_ENTITY):
        self.core, self.learn, self.ha, self.context = core, learner, ha, context
        self.clock, self.wall, self.utc_offset_h = clock, wall, utc_offset_h
        self.status_entity = status_entity
        self.folder = Path(state) / "coordinator"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "hue-overlay.json"
        self.publisher = uuid.uuid4().hex
        self.halt, self.wake = threading.Event(), threading.Event()
        self.clear_requested = threading.Event()
        self.clear_attempt = None
        self.lock = threading.Lock()
        self.worker = None
        self.state = self._read()
        self.learner = learner.Learner.from_json(self.state.get("learned"))
        self.epoch, self.claimed_epoch = None, None
        self.accepted, self.pending, self.last_sent = None, None, None
        self.problem = None
        self.latest = {"active": False, "situation": None, "learned": self.learner.entries}

    def _read(self):
        try:
            with self.path.open(encoding="utf-8") as source:
                return json.load(source)
        except FileNotFoundError:
            return {}

    def _save(self):
        part = self.path.with_suffix(".new")
        self.state["learned"] = self.learner.to_json()
        part.write_text(json.dumps(self.state, allow_nan=False), encoding="utf-8")
        os.replace(part, self.path)

    def start(self):
        self.worker = threading.Thread(target=self._run, name="den-hue-overlay", daemon=True)
        self.worker.start()
        return self

    def close(self):
        self.halt.set()
        self.wake.set()
        if self.worker is not None:
            self.worker.join(1)

    def request_clear(self, _body=None):
        self.clear_requested.set()
        self.wake.set()
        return 202, {"ok": True, "pending": True}

    def readback(self):
        with self.lock:
            return {**self.latest, "problem": self.problem, "clear_pending": self.clear_requested.is_set()}

    def _run(self):
        while not self.halt.is_set():
            try:
                self.step()
                self.problem = None
            except (ha_link.HaError, OSError, ValueError, KeyError, TypeError):
                self.problem = "Hue state or delivery unavailable"
            self.wake.wait(1.0)
            self.wake.clear()

    def _call(self, service, data):
        return self.ha.call("den_lighting", service, data, response=True)

    def _situation(self, attrs):
        context = self.context()
        signal = {"playing": True, "idle": False}.get(attrs.get("gaming_signal"))
        occupied = {"on": True, "off": False}.get(attrs.get("occupancy"))
        kind = self.core.situation.activity(occupied, signal, context["task"], context["seated"], context["activity_known"])
        part = self.core.situation.part_of_day(self.wall(), self.utc_offset_h)
        return self.core.situation.key(kind, part), context

    def _desired(self, authority, key):
        kind = key.split("/", 1)[0]
        if kind in ("unknown", "vacant"):
            return {"situation": key, "scenes": {}, "release": True}
        scenes = self.core.bulbs.BulbPlanner(authority["lights"]).scenes(key, self.learner)
        if kind == "gaming":
            for light in authority["lights"]:
                base = {"on": True, "brightness": 38, "kelvin": authority["warmest"][light]}
                learned = {k: v for k, v in scenes.get(light, {}).items() if v is not None}
                scenes[light] = {**base, **learned}
        return {"situation": key, "scenes": scenes}

    def _learn(self, attrs, key, context, poll_started):
        stream = attrs.get("correction_stream_id")
        record = attrs.get("last_correction")
        if not isinstance(stream, str) or not isinstance(record, dict) or type(record.get("seq")) is not int:
            return
        marker = self.state.get("correction", {})
        if marker.get("stream_id") == stream and record["seq"] <= marker.get("seq", 0):
            return
        age = record.get("age_s")
        if (record.get("actor") == "owner" and record.get("authority_epoch") == self.epoch
                and record.get("situation") == key and record.get("on") is True and record.get("occupied") is True
                and context["people"] == 1 and type(age) in (float, int) and 0 <= age <= CORRECTION_AGE_S
                and poll_started - age >= context.get("since_mono", -float("inf"))
                and isinstance(record.get("before"), dict) and isinstance(record.get("after"), dict)):
            self.learner.apply({**record, "seq": f"{stream}:{record['seq']}"}, key, 1, True, True)
            self.learner.seen.clear()  # the durable per-stream cursor handles duplicates
        self.state["correction"] = {"stream_id": stream, "seq": record["seq"]}
        self._save()

    def _clear(self, attrs, authority):
        if self.clear_attempt is not None or authority.get("active"):
            if self.clear_attempt is None:
                self.clear_attempt = {"epoch": self.epoch, "publisher": self.publisher}
            cleared = self._call("coordinator_clear", self.clear_attempt)
            stream, watermark = cleared["stream_id"], cleared["watermark"]
        else:
            stream, watermark = attrs["correction_stream_id"], attrs["correction_watermark"]
        self.learner = self.learn.Learner()
        self.state["correction"] = {"stream_id": stream, "seq": watermark}
        self._save()
        self.accepted = self.pending = self.last_sent = None
        self.clear_attempt = None
        self.clear_requested.clear()

    def step(self):
        poll_started = self.clock()
        snapshot = self.ha.state(self.status_entity)
        attrs = (snapshot or {}).get("attributes") or {}
        authority = attrs.get("coordinator") or {}
        epoch = authority.get("epoch")
        if not isinstance(epoch, str):
            raise ValueError("HA coordinator authority unavailable")
        if epoch != self.epoch:
            self.epoch = epoch
            self.accepted = self.pending = self.last_sent = None
        if self.clear_attempt is not None:
            self._clear(attrs, authority)
            return
        active = authority.get("active") is True
        if active and authority.get("publisher") != self.publisher:
            if self.claimed_epoch == epoch:
                raise ValueError("Hue publisher was superseded")
            authority = self._call("coordinator_claim", {"epoch": epoch, "publisher": self.publisher,
                                                         "claim_revision": authority["claim_revision"]})
        if active:
            self.claimed_epoch = epoch
        if self.clear_requested.is_set():
            self._clear(attrs, authority)
            return
        key, context = self._situation(attrs)
        if active:
            self._learn(attrs, key, context, poll_started)
            self._publish(authority, self._desired(authority, key))
        with self.lock:
            self.latest = {"active": active, "situation": key, "learned": self.learner.to_json()["entries"]}

    def _publish(self, authority, desired):
        now = self.clock()
        renew = not desired.get("release") and self.last_sent is not None and now - self.last_sent >= RENEW_S
        if self.pending is not None and self.pending[0] != desired:
            self.pending = None
        if self.pending is None:
            if desired == self.accepted and not renew:
                return
            generation = max(authority["generation"], self.state.get("generation", 0)) + 1
            self.state["generation"] = generation
            self._save()
            self.pending = desired, {**desired, "epoch": self.epoch, "publisher": self.publisher,
                                     "plan_id": f"plan-{generation}", "generation": generation,
                                     "valid_for_s": LEASE_S, "transition_s": 2}
        _, payload = self.pending
        result = self._call("coordinator_plan", payload)
        if (result.get("epoch"), result.get("publisher"), result.get("generation")) != (
                self.epoch, self.publisher, payload["generation"]):
            self.pending = None
            raise ValueError("Hue plan was superseded")
        self.accepted = desired if desired.get("release") or result.get("fresh") is True else None
        self.last_sent, self.pending = now, None

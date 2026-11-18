"""The bulbs' link to den_lighting, behind the Bulbs switch. All HA I/O runs on its own ordered worker.

The preview decides the bulb plan (bulb_plan.py) and publishes it whether or not anything is sent. This worker:
- reads den_lighting's status (`sensor.den_lighting_control`) and, while no plan of ours holds the bulbs, each bulb's own
  state, and hands them to the preview (`inputs()`), so the plan names den_lighting's own lights at their own levels;
- **sends nothing while the Bulbs switch is Off** (`sending()`), or while den_lighting's own switches are off: no
  claim, no plan, no clear. Turning the switch Off releases a plan of ours at once, instead of letting it lapse;
- while it's On: claims den_lighting's authority, sends the preview's plan (the payload is built in bulb_plan only),
  renews it every RENEW_S with a new generation, and learns from the owner's corrections in the plan's situation;
- wakes on every change of the plan (`wake()`), so a plan leaves within one Home Assistant round trip.

It never writes `light.*`: den_lighting is the only writer of the room lights.
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

import bulb_plan
import ha_link


RENEW_S = bulb_plan.RENEW_S
LEASE_S = bulb_plan.LEASE_S
STATUS_ENTITY = "sensor.den_lighting_control"
CORRECTION_AGE_S = 15
HOUSE_S = 5.0     # how often each bulb's own state is read while no plan of ours holds it
POLL_S = 1.0


class ReadOnly:
    """Home Assistant for reading only: the overlay's state reads go through this, so no read can become a write."""

    def __init__(self, ha):
        self._ha = ha

    def state(self, entity):
        return self._ha.state(entity)


class BulbsOff(ValueError):
    """A den_lighting call was attempted while Bulbs is Off. It never reaches Home Assistant."""


def scene_of(state):
    """A light's Home Assistant state as den_lighting's scene, or None when it can't be read."""
    if not isinstance(state, dict) or state.get("state") not in ("on", "off"):
        return None
    attrs = state.get("attributes") or {}
    brightness, kelvin = attrs.get("brightness"), attrs.get("color_temp_kelvin")
    return {"on": state["state"] == "on", "brightness": brightness if type(brightness) is int else None,
            "kelvin": kelvin if type(kelvin) is int else None}


class HueOverlay:
    def __init__(self, core, learner, ha, context, state, *, sending=lambda: False, desired=None, writer=None,
                 clock=time.monotonic, wall=time.time, status_entity=STATUS_ENTITY):
        self.core, self.learn, self.ha, self.context = core, learner, ha, context
        self.writer = ha if writer is None else writer   # den_lighting's services, called only through _call
        self.sending, self.desired = sending, desired or (lambda: None)
        self.clock, self.wall = clock, wall
        self.status_entity = status_entity
        self.folder = Path(state) / "coordinator"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "hue-overlay.json"
        self.publisher = uuid.uuid4().hex
        self.halt, self.wake_event = threading.Event(), threading.Event()
        self.clear_requested = threading.Event()
        self.clear_attempt = None
        self.lock = threading.Lock()
        self.worker = None
        self.state = self._read()
        self.learner = learner.Learner.from_json(self.state.get("learned"))
        self.epoch, self.claimed_epoch = None, None
        self.accepted, self.pending, self.last_sent = None, None, None
        self.live = False          # a plan of ours may be holding bulbs in this epoch: Off must release it
        self.problem = None
        self.status = None         # den_lighting's status, as last read: {"attrs", "lights", "warmest", "active"}
        self.house, self.house_at = None, None
        self.latest = {"active": False, "situation": None, "learned": self.learner.entries, "plan": None}

    def _read(self):
        try:
            with self.path.open(encoding="utf-8") as source:
                return json.load(source)
        except FileNotFoundError:
            return {}

    def _save(self):
        part = self.path.with_suffix(".new")
        with self.lock:
            self.state["learned"] = self.learner.to_json()
            text = json.dumps(self.state, allow_nan=False)
        part.write_text(text, encoding="utf-8")
        os.replace(part, self.path)

    def start(self):
        self.worker = threading.Thread(target=self._run, name="den-hue-overlay", daemon=True)
        self.worker.start()
        return self

    def close(self):
        self.halt.set()
        self.wake_event.set()
        if self.worker is not None:
            self.worker.join(1)

    def wake(self):
        """The preview's plan changed: step now, not on the next poll."""
        self.wake_event.set()

    def request_clear(self, _body=None):
        self.clear_requested.set()
        self.wake_event.set()
        return 202, {"ok": True, "pending": True}

    def readback(self):
        with self.lock:
            return {**self.latest, "problem": self.problem, "clear_pending": self.clear_requested.is_set()}

    def inputs(self):
        """For the preview's plan: den_lighting's status and the bulbs' own state, or None before the first read."""
        with self.lock:
            if self.status is None:
                return None
            return {**self.status, "house": self.house}

    def scenes(self, key, lights):
        """What was learned for this situation, light by light."""
        with self.lock:
            rows = {light: self.learner.scene(key, light) for light in lights}
        return {light: scene for light, scene in rows.items() if scene}

    def _run(self):
        while not self.halt.is_set():
            try:
                self.step()
                self.problem = None
            except (ha_link.HaError, OSError, ValueError, KeyError, TypeError):
                self.problem = "Hue state or delivery unavailable"
            self.wake_event.wait(POLL_S)
            self.wake_event.clear()

    def _call(self, service, data):
        """Every den_lighting call goes through here. While Bulbs is Off the only one allowed is the release of a plan
        of ours, which hands the bulbs back."""
        handing_back = service == "coordinator_plan" and data.get("release") is True and not data.get("scenes")
        if self.sending() is not True and not handing_back:
            raise BulbsOff("Bulbs is Off: nothing is sent to den_lighting")
        return self.writer.call("den_lighting", service, data, response=True)

    def _learn(self, attrs, key, learnable, context, poll_started):
        stream = attrs.get("correction_stream_id")
        record = attrs.get("last_correction")
        if not isinstance(stream, str) or not isinstance(record, dict) or type(record.get("seq")) is not int:
            return
        marker = self.state.get("correction", {})
        if marker.get("stream_id") == stream and record["seq"] <= marker.get("seq", 0):
            return
        age = record.get("age_s")
        if (learnable and record.get("actor") == "owner" and record.get("authority_epoch") == self.epoch
                and record.get("situation") == key and record.get("on") is True and record.get("occupied") is True
                and context["people"] == 1 and type(age) in (float, int) and 0 <= age <= CORRECTION_AGE_S
                and poll_started - age >= context.get("since_mono", -float("inf"))
                and isinstance(record.get("before"), dict) and isinstance(record.get("after"), dict)):
            with self.lock:
                self.learner.apply({**record, "seq": f"{stream}:{record['seq']}"}, key, 1, True, True)
                self.learner.seen.clear()  # the durable per-stream cursor handles duplicates
        self.state["correction"] = {"stream_id": stream, "seq": record["seq"]}
        self._save()

    def _clear(self, attrs, authority, sending):
        """Home's Clear: forget what was learned. While sending, den_lighting also drops our overlay and reports its
        watermark; while not, the watermark comes from its status and nothing is called."""
        if not sending:
            self.clear_attempt = None   # nothing of ours to clear at den_lighting: its status gives the watermark
        if sending and (self.clear_attempt is not None or authority.get("active")):
            if self.clear_attempt is None:
                self.clear_attempt = {"epoch": self.epoch, "publisher": self.publisher}
            try:
                cleared = self._call("coordinator_clear", self.clear_attempt)
            except ha_link.HaError:
                # A lost response can still be replayed while HA remembers the request.
                # After an authority change, a rejected replay must let the next step claim again.
                if self.clear_attempt["epoch"] != self.epoch:
                    self.clear_attempt = None
                raise
            stream, watermark = cleared["stream_id"], cleared["watermark"]
            self.live = False
        else:
            stream, watermark = attrs["correction_stream_id"], attrs["correction_watermark"]
        with self.lock:
            self.learner = self.learn.Learner()
        self.state["correction"] = {"stream_id": stream, "seq": watermark}
        self._save()
        self.accepted = self.pending = self.last_sent = None
        self.clear_attempt = None
        with self.lock:
            self.latest = {**self.latest, "learned": {}}
            self.clear_requested.clear()

    def _read_house(self, lights, now):
        """Each bulb's own state, read only while no plan of ours holds it: that's its scene before we touched it."""
        if self.live or (self.house_at is not None and now - self.house_at < HOUSE_S):
            return
        house = {}
        for light in lights:
            scene = scene_of(self.ha.state(light))
            if scene is not None:
                house[light] = scene
        with self.lock:
            self.house, self.house_at = house, now

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
            self.live = False   # a new authority already dropped any plan of ours
        active = authority.get("active") is True
        lights = [light for light in authority.get("lights") or () if isinstance(light, str)]
        warmest = {light: kelvin for light, kelvin in (authority.get("warmest") or {}).items() if type(kelvin) is int}
        with self.lock:
            self.status = {"attrs": {"occupancy": attrs.get("occupancy"), "gaming_signal": attrs.get("gaming_signal")},
                           "lights": lights, "warmest": warmest, "active": active}
        self._read_house(lights, poll_started)
        sending = self.sending() is True
        if self.clear_attempt is not None or self.clear_requested.is_set():
            if active and sending and authority.get("publisher") != self.publisher and self.clear_attempt is None:
                authority = self._claim(authority, epoch)
            self._clear(attrs, authority, sending)
            return
        plan = self.desired()
        if not (sending and active):
            if self.live and active and authority.get("publisher") == self.publisher:
                self._release(authority)
            self._show(active, plan, None)
            return
        if authority.get("publisher") != self.publisher:
            authority = self._claim(authority, epoch)
        self.claimed_epoch = epoch
        if plan is None or (not plan["plan"]["release"] and bulb_plan.ALL in plan["plan"]["scenes"]):
            return   # the preview hasn't planned with den_lighting's own lights yet: it will on its next tick
        key, learnable, _source = plan["key"]
        self._learn(attrs, key, learnable, self.context(), poll_started)
        self._publish(authority, plan["plan"])
        self._show(active, plan, self.last_sent and self.state.get("generation"))

    def _claim(self, authority, epoch):
        if self.claimed_epoch == epoch:
            raise ValueError("Hue publisher was superseded")
        self.live = False
        claimed = self._call("coordinator_claim", {"epoch": epoch, "publisher": self.publisher,
                                                   "claim_revision": authority["claim_revision"]})
        self.claimed_epoch = epoch
        return claimed

    def _show(self, active, plan, generation):
        with self.lock:
            self.latest = {"active": active, "situation": plan and plan["plan"]["situation"],
                           "learned": self.learner.to_json()["entries"], "plan": self.latest.get("plan"),
                           "sent_generation": generation}

    def _generation(self, authority):
        generation = max(authority.get("generation", 0), self.state.get("generation", 0)) + 1
        self.state["generation"] = generation
        self._save()
        return generation

    def _release(self, authority):
        """The Bulbs switch went Off with a plan of ours live: hand the bulbs back now, not in LEASE_S."""
        plan = bulb_plan.release("", "bulbs_off")
        payload = bulb_plan.payload(plan, epoch=self.epoch, publisher=self.publisher,
                                    generation=self._generation(authority))
        self._call("coordinator_plan", payload)
        self.live = False
        self.accepted = self.pending = self.last_sent = None
        with self.lock:
            self.latest = {**self.latest, "plan": {"service": "den_lighting.coordinator_plan", "data": payload}}

    def _publish(self, authority, desired):
        now = self.clock()
        renew = not desired.get("release") and self.last_sent is not None and now - self.last_sent >= RENEW_S
        if self.pending is not None and self.pending[0] != desired:
            self.pending = None
        if self.pending is None:
            if desired == self.accepted and not renew:
                return
            if desired.get("release") and not self.live:
                self.accepted = desired   # nothing of ours holds the bulbs: den_lighting's rules already apply
                return
            generation = self._generation(authority)
            self.pending = desired, bulb_plan.payload(desired, epoch=self.epoch, publisher=self.publisher,
                                                      generation=generation)
        _, payload = self.pending
        result = self._call("coordinator_plan", payload)
        if (result.get("epoch"), result.get("publisher"), result.get("generation")) != (
                self.epoch, self.publisher, payload["generation"]):
            self.pending = None
            raise ValueError("Hue plan was superseded")
        self.accepted = desired if desired.get("release") or result.get("fresh") is True else None
        self.live = not desired.get("release")
        self.last_sent, self.pending = now, None
        with self.lock:
            self.latest = {**self.latest, "plan": {"service": "den_lighting.coordinator_plan", "data": payload}}

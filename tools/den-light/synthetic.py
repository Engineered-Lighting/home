#!/usr/bin/env python3
"""Rung 8 Stage 1's synthetic den, fake camera and fake Home Assistant. Tests only.

Everything binds 127.0.0.1 and nothing here contacts another host.

- den_lighting 1.0.4 runs inside the fake HA: its policy.py loaded unchanged by
  path (as den-core's incumbent.py loads it), its __init__.py's pure helpers
  (scene_from_state, command_scene) executed from their own source text, and
  copies of service_called()'s learning loop, state_changed()'s light branch,
  resume() and the command cycle. Both files are pinned by sha256, so a new
  den_lighting version fails loudly. This checkout's copy lives in
  tools/fixtures/den_lighting-1.0.4/ (byte-identical to claude/den-core); a
  checkout that also holds ha-config/custom_components/den_lighting/ is checked
  against the same pins.
- SyntheticDen: known per-bulb bases, two bulb types, linear-in-mired mixing,
  drifting daylight and noise.
- FakeCamera: the meter build's HTTP surface, rendering the den under the held
  settings through a known tone curve.
"""
import ast
import hashlib
import importlib.util
import io
import json
import math
import random
import struct
import sys
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import guard
import meter

HERE = Path(__file__).resolve().parent
FIXTURE_DIR = HERE.parent / "fixtures" / "den_lighting-1.0.4"
LIVE_RELATIVE = Path("ha-config") / "custom_components" / "den_lighting"
POLICY_SHA256 = "271cc1700d3132b34e88a53eb1c377973964b5887813ac3139b7f769ccde7f23"
INIT_SHA256 = "d42469f3efc37ccd30ba185594389e27a67d13a5303251cc711c69c038efc7b0"
HELPERS = ("scene_from_state", "command_scene")
SWITCH = "switch.den_lighting_automatic"
GAMING_SWITCH = "switch.den_lighting_gaming"
SENSOR = "sensor.den_lighting_control"
GAMING_ENTITY = "sensor.den_pc_gaming"
KELVIN_RANGE = {"light.den_front_left": (2000, 6535), "light.front_right": (2000, 6535),
                "light.den_rear_left": (2202, 6535), "light.den_rear_right": (2202, 6535)}
HA_EPOCH = 1_800_000_000.0  # the fake HA's clock starts here (a fixed, synthetic time)


# ---- den_lighting 1.0.4, unchanged ------------------------------------------------

def den_lighting_folders():
    """The vendored 1.0.4 copy, and the live integration if this checkout holds one."""
    folders = [FIXTURE_DIR]
    for folder in (HERE, *HERE.parents):
        if (folder / LIVE_RELATIVE / "policy.py").is_file():
            folders.append(folder / LIVE_RELATIVE)
            break
    return folders


def check_pins(folder):
    for name, pin in (("policy.py", POLICY_SHA256), ("__init__.py", INIT_SHA256)):
        digest = hashlib.sha256((folder / name).read_bytes()).hexdigest()
        if digest != pin:
            raise RuntimeError(f"den_lighting changed: {folder / name} hashes {digest[:12]}..., not the 1.0.4 "
                               "pin. Re-read the plan's probe safety section against the new code first.")


class _State:
    """The two attributes of an HA State that scene_from_state() reads."""

    def __init__(self, state):
        self.state = state["state"]
        self.attributes = state.get("attributes") or {}


def load_den_lighting():
    """(policy module, {helper name: function}) from den_lighting 1.0.4, after the pin checks."""
    for folder in den_lighting_folders():
        check_pins(folder)
    spec = importlib.util.spec_from_file_location("den_lighting_policy_under_test", FIXTURE_DIR / "policy.py")
    policy = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = policy  # its dataclasses look their module up while being built
    spec.loader.exec_module(policy)
    tree = ast.parse((FIXTURE_DIR / "__init__.py").read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in HELPERS]
    namespace = {"Scene": policy.Scene, "math": math}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(FIXTURE_DIR / "__init__.py"), "exec"), namespace)
    return policy, {name: namespace[name] for name in HELPERS}


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def from_iso(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


# ---- the fake Home Assistant ------------------------------------------------------------

class HAError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class FakeHA:
    """HA's REST API on 127.0.0.1 with den_lighting 1.0.4 inside. speed scales the HA clock, so
    den_lighting's 120 s vacancy can pass in a fraction of a real second."""

    def __init__(self, *, token, store=None, speed=1.0, occupancy="off", lights=None):
        self.policy, self.helpers = load_den_lighting()
        self.token, self.speed = token, speed
        self.m0 = time.perf_counter()  # not time.monotonic(): it steps every 15.6 ms on Windows, and last_updated would tie
        self.lock = threading.RLock()
        self.controller = self.policy.Controller.restore((store or {}).get("data"))
        self.lights = {e: dict(on=False, brightness=None, kelvin=None, available=True) for e in guard.LIGHTS}
        for e, value in (lights or {}).items():
            self.lights[e].update(value)
        self.states, self.history, self.logbook, self.calls = {}, [], [], []
        self._own, self._external, self._provisional = {}, {}, {}
        self.pending = []          # delayed light reports: [due, entity, context]
        self.report_delay = {}     # entity -> HA seconds before its next report lands
        self.incumbent = "off"
        self.down = False          # True: HA unreachable (connections dropped)
        self.refuse_writes = 0     # the next n light writes answer 500, as a failed Zigbee delivery does
        self.error = None
        self.on_light_change = None
        now = self.now()
        self._set(GAMING_ENTITY, "unknown", {"friendly_name": "Den PC gaming"}, None)
        self._set(guard.INCUMBENT, self.incumbent, {"friendly_name": "Den lights follow person occupancy"}, None)
        self._set(guard.OCCUPANCY, occupancy, {"friendly_name": "Den OAK person occupancy"}, None)
        self.controller.set_occupancy(occupancy if occupancy in ("on", "off") else "unknown", now)
        for e in guard.LIGHTS:
            self._report(e, None)
        self._refresh()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        self._stop = threading.Event()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self._ticker = threading.Thread(target=self._tick_loop, daemon=True)
        self._ticker.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        self._stop.set()
        self.server.shutdown()
        self.server.server_close()

    def now(self):
        return HA_EPOCH + (time.perf_counter() - self.m0) * self.speed

    # states
    def _set(self, entity_id, state, attributes, context):
        now = self.now()
        old = self.states.get(entity_id)
        if old and old["state"] == state and old["attributes"] == attributes:
            return old
        changed = iso(now) if not old or old["state"] != state else old["last_changed"]
        new = {"entity_id": entity_id, "state": state, "attributes": attributes, "last_changed": changed,
               "last_updated": iso(now), "context": {"id": context or uuid.uuid4().hex, "parent_id": None,
                                                     "user_id": None}}
        self.states[entity_id] = new
        self.history.append((now, json.loads(json.dumps(new))))
        return new

    def _light_attributes(self, e):
        light, (low, high) = self.lights[e], KELVIN_RANGE[e]
        attributes = {"friendly_name": e.split(".", 1)[1].replace("_", " ").title(),
                      "min_color_temp_kelvin": low, "max_color_temp_kelvin": high,
                      "supported_color_modes": ["color_temp"]}
        if light["on"]:
            attributes.update(brightness=light["brightness"], color_temp_kelvin=light["kelvin"], color_mode="color_temp")
        else:
            attributes.update(brightness=None, color_temp_kelvin=None, color_mode=None)
        return attributes

    def _report(self, e, context):
        """The light's state as HA shows it now; den_lighting's state_changed() sees it."""
        old = self.states.get(e)
        light = self.lights[e]
        state = ("on" if light["on"] else "off") if light["available"] else "unavailable"
        new = self._set(e, state, self._light_attributes(e) if light["available"] else
                        {"friendly_name": e}, context)
        if old is None:
            self._observe(e, new)
        elif new is not old:
            self._den_state_changed(e, old, new, context)
            if self.on_light_change:
                self.on_light_change(e)
        return new

    def _apply(self, e, on, brightness=None, kelvin=None):
        """What the bulb does: Zigbee caps brightness at 254 and reports kelvin through mireds."""
        light = self.lights[e]
        if not on:
            light.update(on=False)
            return
        low, high = KELVIN_RANGE[e]
        b = light["brightness"] if brightness is None else brightness
        k = light["kelvin"] if kelvin is None else kelvin
        b = 254 if b is None else max(1, min(254, int(b)))
        k = 4000 if k is None else max(low, min(high, int(k)))
        light.update(on=True, brightness=b, kelvin=round(1e6 / round(1e6 / k)))

    def set_light(self, e, *, on, brightness=None, kelvin=None, context=None):
        """A physical switch or another person: a change den_lighting learns from the state."""
        with self.lock:
            self._apply(e, on, brightness, kelvin)
            state = self._report(e, context)
            self._refresh()
            return state

    def set_available(self, e, available):
        with self.lock:
            self.lights[e]["available"] = available
            self._report(e, None)
            self._refresh()

    def set_occupancy(self, value):
        """Frigate's count (on or off) or a lost signal (unknown, unavailable)."""
        with self.lock:
            self._set(guard.OCCUPANCY, value, self.states[guard.OCCUPANCY]["attributes"], None)
            self.controller.set_occupancy(value if value in ("on", "off") else "unknown", self.now())
            self._cycle()
            self._refresh()

    def start_game(self):
        with self.lock:
            self.controller.gaming = True
            self.controller._changed()
            self._refresh()

    def set_incumbent(self, state):
        with self.lock:
            self.incumbent = state
            self._set(guard.INCUMBENT, state, self.states[guard.INCUMBENT]["attributes"], None)
            self._refresh()

    def ha_restart(self):
        with self.lock:
            self.logbook.append({"when": iso(self.now()), "name": "Home Assistant", "message": "started",
                                 "domain": "homeassistant"})

    def store(self):
        with self.lock:
            return {"version": 1, "minor_version": 1, "key": "den_lighting.state", "data": self.controller.dump()}

    def status(self):
        with self.lock:
            return {**self.controller.status(self.now()), "error": self.error, "gaming_entity": GAMING_ENTITY,
                    "occupancy_source": "raw_person_count", "pending_fixtures": [],
                    "provisional_fixtures": list(self._provisional)}

    def _refresh(self):
        s = self.status()
        if not s["automatic"]:
            word = "disabled"
        elif s["error"]:
            word = "error"
        elif s["occupancy"] == "unknown" or s["gaming_enabled"] and s["gaming_signal"] == "unknown":
            word = "signal_unknown"
        elif s["holds"]:
            word = "manual_hold"
        else:
            word = "gaming" if s["gaming_session"] else "occupancy"
        self._set(SENSOR, word, {**s, "friendly_name": guard.SENSOR_NAME, "icon": "mdi:lightbulb-auto-outline"}, None)
        self._set(SWITCH, "on" if self.controller.enabled else "off",
                  {"friendly_name": guard.SWITCH_NAME, "icon": "mdi:lightbulb-auto"}, None)
        self._set(GAMING_SWITCH, "on" if self.controller.gaming_enabled else "off",
                  {"friendly_name": "Den lighting gaming", "icon": "mdi:gamepad-variant"}, None)

    # den_lighting's runtime: copies of __init__.py's learning paths -----------------------
    def _observe(self, e, state):
        found = self.helpers["scene_from_state"](_State(state))
        if found:
            self.controller.observe(e, found, self.now())
            minimum = state["attributes"].get("min_color_temp_kelvin")
            if isinstance(minimum, (int, float)):
                self.controller.update_warmest(e, int(minimum))
        return found

    def _own_record(self, context, now):
        record = self._own.get(context)
        return record if record and record[2] >= now else None

    def _den_service_called(self, domain, service, data, context):
        """service_called()'s learning loop for light calls."""
        now = self.now()
        if self._own_record(context, now):
            return
        if domain not in {"light", "homeassistant", "scene"} or service not in {"turn_on", "turn_off", "toggle"}:
            return
        ids = data.get("entity_id")
        entities = (set(guard.LIGHTS) if ids == "all" else set([ids] if isinstance(ids, str) else ids or [])) & set(guard.LIGHTS)
        for e in entities:
            current = self.controller.observed.get(e)
            if current is None:
                continue
            target = self.helpers["command_scene"](service, data, current)
            self._provisional.pop(e, None)
            self.controller.manual(e, target, now)
            self._external[e] = (context, now + float(data.get("transition", 2)) + 3, True)

    def _den_state_changed(self, e, old_state, new_state, context):
        """state_changed()'s light branch."""
        now = self.now()
        found = self._observe(e, new_state)
        old = self.helpers["scene_from_state"](_State(old_state))
        if found is None or old is None or found == old:
            return
        own = self._own_record(context, now)
        if own is None:
            own = next((r for r in reversed(tuple(self._own.values()))
                        if r[0] == e and r[4] >= now and r[1].matches(found)
                        and (not found.on or r[1].brightness is None or r[1].brightness == found.brightness)), None)
        external = self._external.get(e)
        if own:
            return
        if external and now <= external[1]:
            if not external[2]:
                self.controller.manual(e, found, now)
            return
        phase = next((r for r in reversed(tuple(self._own.values())) if r[0] == e and r[4] >= now), None)
        if phase:
            if e not in self.controller.holds or e in self._provisional:
                self.controller.manual(e, found, now, update_normal=False)
                self._provisional[e] = self.controller.holds[e]
        else:
            self.controller.manual(e, found, now)

    def _cycle(self):
        """The command cycle: den_lighting's own writes carry its own context, which it does not learn."""
        c = self.controller
        if c.enabled and self.incumbent != "off":
            self.error = "incumbent_automation_not_disabled"
            return
        now = self.now()
        for cmd in c.reconcile(now):
            if (not c.enabled or cmd.revision != c.revision or c.desired(cmd.entity_id, now) != cmd.target
                    or not self.lights[cmd.entity_id]["available"]):
                c.command_failed(cmd.entity_id)
                continue
            context = uuid.uuid4().hex
            self._own[context] = (cmd.entity_id, cmd.target, now + cmd.transition + 30, cmd.revision,
                                  now + cmd.transition + 3)
            self.calls.append((now, "light", "turn_on" if cmd.target.on else "turn_off",
                               {"entity_id": cmd.entity_id}, context, "den_lighting"))
            self._apply(cmd.entity_id, cmd.target.on, cmd.target.brightness, cmd.target.kelvin)
            self._report(cmd.entity_id, context)

    def _resume(self, entity_ids):
        """resume(): clear the listed holds (readback checks are not modelled)."""
        for e in entity_ids if entity_ids is not None else guard.LIGHTS:
            self._provisional.pop(e, None)
        self.controller.resume(entity_ids, self.now())
        self._cycle()

    def _tick_loop(self):
        while not self._stop.wait(0.01):
            with self.lock:
                now = self.now()
                due = [p for p in self.pending if p[0] <= now]
                self.pending = [p for p in self.pending if p[0] > now]
                for _, e, context in due:
                    self._report(e, context)
                self._own = {k: v for k, v in self._own.items() if v[2] >= now}
                self._external = {k: v for k, v in self._external.items() if v[1] >= now}
                self._cycle()
                self._refresh()

    # services
    def call(self, domain, service, data):
        with self.lock:
            context = uuid.uuid4().hex
            now = self.now()
            self.calls.append((now, domain, service, dict(data), context, "rest"))
            changed = []
            if domain == "light" and service in ("turn_on", "turn_off"):
                if self.refuse_writes:
                    self.refuse_writes -= 1
                    raise HAError("Failed to send request: device did not respond", 500)
                ids = data.get("entity_id")
                ids = [ids] if isinstance(ids, str) else list(ids or [])
                if not ids or not set(ids) <= set(self.lights):
                    raise HAError("unknown light")
                self._den_service_called(domain, service, data, context)
                for e in ids:
                    if not self.lights[e]["available"]:
                        continue
                    self._apply(e, service == "turn_on", data.get("brightness"), data.get("color_temp_kelvin"))
                    delay = self.report_delay.pop(e, 0)
                    if delay:
                        self.pending.append([now + delay, e, context])
                    else:
                        changed.append(self._report(e, context))
            elif domain == "switch" and service in ("turn_on", "turn_off") and data.get("entity_id") == SWITCH:
                if service == "turn_on" and self.incumbent != "off":
                    raise HAError("Disable the incumbent Den occupancy automation before enabling this controller")
                self.controller.set_enabled(service == "turn_on", now)
            elif domain == "den_lighting" and service == "resume_automatic":
                ids = data.get("entity_id")
                ids = [ids] if isinstance(ids, str) else ids
                if ids is not None and not set(ids) <= set(guard.LIGHTS):
                    raise HAError("Only the four Victoria Den fixtures can be resumed")
                self._resume(ids)
            else:
                raise HAError(f"service {domain}.{service} is not in this fake")
            self._cycle()
            self._refresh()
            return json.loads(json.dumps(changed))

    def history_since(self, start, entity_ids, end=None):
        """/api/history/period: per entity, the state at the start (dated the start) and every change after."""
        with self.lock:
            end = self.now() if end is None else end
            out = []
            for e in entity_ids:
                before = [s for t, s in self.history if s["entity_id"] == e and t <= start]
                entries = []
                if before:
                    first = dict(before[-1], last_changed=iso(start), last_updated=iso(start))
                    entries.append(first)
                entries += [s for t, s in self.history if s["entity_id"] == e and start < t <= end]
                out.append(entries)
            return out

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, value):
                body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def ok(self):
                if fake.down:
                    self.close_connection = True
                    return False
                if self.headers.get("Authorization") != "Bearer " + fake.token:
                    self.reply(401, {"message": "Unauthorized"})
                    return False
                return True

            def do_GET(self):
                if not self.ok():
                    return None
                parts = urlsplit(self.path)
                query = parse_qs(parts.query)
                path = unquote(parts.path)
                with fake.lock:
                    if path == "/api/":
                        return self.reply(200, {"message": "API running."})
                    if path == "/api/states":
                        return self.reply(200, list(fake.states.values()))
                    if path.startswith("/api/states/"):
                        state = fake.states.get(path[len("/api/states/"):])
                        return self.reply(200, state) if state else self.reply(404, {"message": "Entity not found."})
                    if path.startswith("/api/history/period/"):
                        start = from_iso(path[len("/api/history/period/"):])
                        ids = query.get("filter_entity_id", [""])[0].split(",")
                        end = from_iso(query["end_time"][0]) if "end_time" in query else None
                        return self.reply(200, fake.history_since(start, ids, end))
                    if path.startswith("/api/logbook/"):
                        start = from_iso(path[len("/api/logbook/"):])
                        return self.reply(200, [x for x in fake.logbook if from_iso(x["when"]) >= start])
                return self.reply(404, {"message": "Not found"})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if not self.ok():
                    return None
                path = urlsplit(self.path).path
                if not path.startswith("/api/services/"):
                    return self.reply(404, {"message": "Not found"})
                domain, _, service = path[len("/api/services/"):].partition("/")
                try:
                    data = json.loads(raw or b"{}")
                    return self.reply(200, fake.call(domain, service, data))
                except HAError as exc:
                    if exc.status != 500:
                        return self.reply(exc.status, {"message": str(exc)})
                    body = b"500 Internal Server Error\n\nServer got itself in trouble"  # HA's page: text, not JSON
                    self.send_response(500)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return None

        return Handler


# ---- the synthetic den ------------------------------------------------------------------

def s_curve(y, k=1.3):
    """The fake camera's tone curve: code for relative light y (code 128 at y = 1)."""
    return 255.0 * y ** k / (y ** k + 1.0) if y > 0 else 0.0


def s_inverse_table(k=1.3):
    """meter-norm/v1's measured inverse response for that curve, f(128) = 1."""
    table = []
    for z in range(256):
        q = min(z, 254.5) / 255.0
        table.append((q / (1.0 - q)) ** (1.0 / k))
    return [v / table[128] for v in table]


def meter_norm_for(method0, k=1.0):
    """Stage 0's meter-norm/v1 parameters as a passing response.json would carry them."""
    table = s_inverse_table(k)
    return meter.meter_norm_parameters(method0, {c: table for c in meter.CHANNELS})


WARM_RGB, COOL_RGB = (1.0, 0.72, 0.42), (0.78, 0.9, 1.0)
TYPES = {"A": lambda u: (u / 254.0) ** 2.0, "B": lambda u: 0.01 * (u / 254.0) + 0.99 * (u / 254.0) ** 2.4}
BULB_TYPES = dict(zip(guard.LIGHTS, "AABB"))
SHARED_KELVIN = (max(lo for lo, _ in KELVIN_RANGE.values()), min(hi for _, hi in KELVIN_RANGE.values()))


class SyntheticDen:
    """Known bases B_c(b, k), linear in mired between the warm and cool ends; two bulb types;
    drifting daylight. Values are relative linear light per block and channel."""

    def __init__(self, block_px=40, *, seed=3, daylight=0.004, drift=0.2, noise=0.0, curves=None,
                 saturate=None, temperature_curve=False):
        self.block_px, self.cols, self.rows = block_px, 640 // block_px, 360 // block_px
        self.blocks = self.cols * self.rows
        rng = random.Random(seed)
        spots = {"light.den_front_left": (0.15, 0.35), "light.front_right": (0.85, 0.35),
                 "light.den_rear_left": (0.25, 0.85), "light.den_rear_right": (0.75, 0.85)}
        self.spatial = {}
        for e, (sx, sy) in spots.items():
            peak, radius = rng.uniform(1.5, 2.5), rng.uniform(0.25, 0.4)
            self.spatial[e] = [peak / (1 + (((b % self.cols + 0.5) / self.cols - sx) ** 2 +
                                            ((b // self.cols + 0.5) / self.rows - sy) ** 2) / radius ** 2)
                               + 0.08 for b in range(self.blocks)]
        self.day = [rng.uniform(0.5, 1.5) for _ in range(self.blocks)]
        self.daylight, self.drift, self.noise, self.rng = daylight, drift, noise, rng
        self.curves = curves or {e: TYPES[BULB_TYPES[e]] for e in guard.LIGHTS}
        self.saturate, self.temperature_curve = saturate, temperature_curve
        self.warm, self.cool = SHARED_KELVIN

    def colour(self, kelvin):
        w = (1e6 / self.warm - 1e6 / kelvin) / (1e6 / self.warm - 1e6 / self.cool)
        return tuple(a + (b - a) * w for a, b in zip(WARM_RGB, COOL_RGB))

    def level(self, e, u, kelvin):
        g = self.curves[e](u)
        if self.temperature_curve:  # a curve that depends on temperature: dims faster when warm
            w = (1e6 / self.warm - 1e6 / kelvin) / (1e6 / self.warm - 1e6 / self.cool)
            g = g ** (1.25 - 0.25 * w)
        return g

    def block_rgb(self, scene, t):
        """(R, G, B) relative linear light per block for a scene {entity: {on, brightness, kelvin}} at time t (s)."""
        out = []
        day = self.daylight * (1 + self.drift * t / 600.0)
        for b in range(self.blocks):
            rgb = [day * self.day[b]] * 3
            for e, s in scene.items():
                if s and s.get("on") and s.get("brightness"):
                    g = self.level(e, s["brightness"], s["kelvin"])
                    col = self.colour(s["kelvin"])
                    rgb = [v + g * self.spatial[e][b] * c for v, c in zip(rgb, col)]
            if self.saturate:
                rgb = [self.saturate * (1 - math.exp(-v / self.saturate)) for v in rgb]
            out.append(tuple(rgb))
        return out

    def reading(self, scene, t, weights=(0.2126, 0.7152, 0.0722)):
        """Each block's meter-norm/v1 reading for the scene (what a perfect camera reduces to), plus noise."""
        out = []
        for rgb in self.block_rgb(scene, t):
            value = sum(w * v for w, v in zip(weights, rgb))
            out.append(value + (self.rng.gauss(0, self.noise) if self.noise else 0.0))
        return out


# ---- the fake camera (the meter build's HTTP surface) ---------------------------------------

def _npy(descr, shape, data):
    header = "{'descr': '%s', 'fortran_order': False, 'shape': %s, }" % (
        descr, "(" + ", ".join(str(s) for s in shape) + ("," if len(shape) == 1 else "") + ")")
    header += " " * (63 - (10 + len(header)) % 64) + "\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header.encode("latin-1") + data


def _yuv(r, g, b):
    clip = lambda v: min(255, max(0, v))
    return (clip(((66 * r + 129 * g + 25 * b + 128) >> 8) + 16),
            clip(((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128),
            clip(((112 * r - 94 * g - 18 * b + 128) >> 8) + 128))


class FakeCamera:
    """Renders the synthetic den, lit as the fake HA's bulbs are, under auto or held settings."""

    def __init__(self, den, ha, *, token, step=10, latency=2, seed=7, code_noise=0.4, curve_k=1.0):
        self.den, self.ha, self.token, self.step, self.latency = den, ha, token, step, latency
        self.curve_k = curve_k
        self.latency_s = 0.0         # a bulb's light lands this long after HA shows its target (ZHA reports at once)
        self.landing_at = 0.0
        self.frozen = False          # no new frames: the same sequence again and again
        self.rng = random.Random(seed)
        self.code_noise = code_noise
        self.points = meter.sample_points(step)
        bp = den.block_px
        self.block_of = [(y // bp) * den.cols + (x // bp) for x, y in self.points]
        self.texture = [0.75 + 0.5 * random.Random(i * 7919 + seed).random() for i in range(len(self.points))]
        self.session, self.sequence, self.generation = "session-1", 0, 0
        self.auto = {"exposure_us": 20000, "iso": 400, "kelvin": 4000}
        self.held, self.hold = False, None
        self.shown, self.next_scene, self.wait = None, None, 0
        self.t0 = time.monotonic()
        self.lock = threading.Lock()
        self.fail = None             # 503 or 'drop' for every GET /frame.npz from now on
        self.person = 0.0            # a person in view: extra light on a few blocks
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def scene_now(self):
        with self.ha.lock:
            return {e: dict(on=l["on"] and l["available"], brightness=l["brightness"], kelvin=l["kelvin"])
                    for e, l in self.ha.lights.items()}

    def frame(self):
        self.sequence += 0 if self.frozen else 1
        scene = self.scene_now()
        if scene != self.next_scene:  # a bulb takes `latency` frames and latency_s to show a change
            self.next_scene, self.wait = scene, self.latency
            self.landing_at = time.perf_counter() + self.latency_s
        if self.wait:
            self.wait -= 1
        if (not self.wait and time.perf_counter() >= self.landing_at) or self.shown is None:
            self.shown = self.next_scene
        settings = self.hold if self.held else self.auto
        x = settings["exposure_us"] / 1000 * settings["iso"] / 100
        blocks = self.den.block_rgb(self.shown, time.monotonic() - self.t0)
        if self.person:
            blocks = [tuple(v + self.person for v in rgb) if b % 7 == 0 else rgb for b, rgb in enumerate(blocks)]
        nv = bytearray(meter.NV12_BYTES)
        for i, (px, py) in enumerate(self.points):
            rgb = blocks[self.block_of[i]]
            codes = [min(255, max(0, round(s_curve(x / 8.0 * v * self.texture[i], self.curve_k)
                                           + self.rng.gauss(0, self.code_noise)))) for v in rgb]
            yy, u, v = _yuv(*codes)
            nv[py * 640 + px] = yy
            at = 640 * 360 + (py // 2) * 640 + (px // 2) * 2
            nv[at], nv[at + 1] = u, v
        rgb_meta = {"sequence": self.sequence, "host_monotonic_timestamp_seconds": 100 + self.sequence / 5,
                    "device_timestamp_seconds": 50 + self.sequence / 5, "width": 640, "height": 360,
                    "type": "Type.NV12", "exposure_duration_seconds": (settings["exposure_us"] + 20) / 1e6,
                    "sensitivity_iso": settings["iso"], "color_temperature_kelvin": settings["kelvin"],
                    "lens_position": 120, "meter": {"held": self.held, "generation": self.generation}}
        other = {"sequence": self.sequence, "width": 640, "height": 400}
        meta = {"schema": 1, "session": self.session, "captured_unix_ns": 1,
                "frames": {"rgb": rgb_meta, "depth": dict(other), "left": dict(other), "right": dict(other)}}
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            archive.writestr("rgb_nv12.npy", _npy("|u1", (540, 640), bytes(nv)))
            raw = json.dumps(meta).encode()
            archive.writestr("metadata_json.npy", _npy("|u1", (len(raw),), raw))
        return out.getvalue()

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body, kind="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer " + fake.token:
                    return self.reply(401, b"{}")
                with fake.lock:
                    if self.path == "/health":
                        healthy = fake.fail is None
                        return self.reply(200 if healthy else 503,
                                          json.dumps({"healthy": healthy, "session": fake.session}).encode())
                    if self.path != "/frame.npz":
                        return self.reply(404, b"{}")
                    if fake.fail == 503:
                        return self.reply(503, b"Unavailable\n", "text/plain")
                    if fake.fail == "drop":
                        self.close_connection = True
                        return None
                    body = fake.frame()
                return self.reply(200, body, "application/octet-stream")

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.headers.get("Authorization") != "Bearer " + fake.token:
                    return self.reply(401, b"{}")
                with fake.lock:
                    if self.path == "/meter/release":
                        if fake.held:
                            fake.held, fake.generation = False, fake.generation + 1
                        return self.reply(200, json.dumps({"held": False, "generation": fake.generation}).encode())
                    body = json.loads(raw)
                    hold = {"exposure_us": body["exposure_us"], "iso": body["iso"], "kelvin": body["white_balance_kelvin"]}
                    if not fake.held or hold != fake.hold:
                        fake.held, fake.hold, fake.generation = True, hold, fake.generation + 1
                    return self.reply(200, json.dumps({"held": True, "generation": fake.generation,
                                                       "ttl_s": body["ttl_s"]}).encode())

        return Handler

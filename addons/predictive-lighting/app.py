#!/usr/bin/env python3
"""app.py — predictive-lighting add-on.

Two responsibilities, sharing the same `frigate/events` MQTT subscription:

1. **Zone-transition logger** (legacy / cold storage). Each Frigate event
   feeds zonelog.ZoneTracker, which emits committed zone transitions to
   /share/predictive-lighting/transitions.jsonl. Used as ground-truth for
   validating the anticipator; not used to train anything anymore.

2. **Kinematic anticipator** (the new primary). Each person event is fed
   to anticipate.Anticipator, which derives velocity from Frigate's
   path_data trajectory, ray-casts against the home's hand-drawn polygons
   (/config/spatial_model.json), and emits per-room "anticipated" booleans
   over MQTT. The Living Lights system pre-warms on these booleans.

MQTT lifecycle is crash-safe: LWT on `predictive-lighting/availability`
flips offline on unclean disconnect (HA marks the anticipated_* sensors
unavailable). Startup-clear publishes OFF for every retained room topic
so a previous-process phantom ON doesn't linger. A watchdog sweeps stale
tracks (no event in N seconds) so a track that ended without a clean
`end` event still releases its prediction.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

import paho.mqtt.client as mqtt

import zonelog
import anticipate
import eval_logger

SHARE_DIR = "/share/predictive-lighting"
TRANSITIONS = os.path.join(SHARE_DIR, "transitions.jsonl")
RAW_SAMPLE = os.path.join(SHARE_DIR, "raw-sample.jsonl")
RAW_SAMPLE_LIMIT = 12
# spatial_model lives at /config on HAOS, but the addon's `config` map
# directive proved fragile across HA versions (the supervisor's manifest
# cache here is stuck and won't pick up new map directives). Workaround:
# read from /share/predictive-lighting/spatial_model.json, which IS
# mapped, and have the user / a small HA automation copy /config →
# /share on changes. Acceptable because spatial_model changes are rare
# (~once per drawer-edit session).
SPATIAL_MODEL_PATH = os.path.join(SHARE_DIR, "spatial_model.json")
TOPIC = "frigate/events"
HEARTBEAT_S = 300

# Anticipator MQTT topics.
AVAIL_TOPIC = "predictive-lighting/availability"
ANTICIPATED_BASE = "predictive-lighting/anticipated"
DISCOVERY_BASE = "homeassistant/binary_sensor/anticipated_"
# Phase 2 eval harness — Component 0.
# Per-event debug stream so the eval can compute predictability ceiling and
# offline param-sweep. NOT retained (ephemeral diagnostic).
DEBUG_TOPIC_BASE = "predictive-lighting/debug/track"
# Heartbeat so the harness watchdog can detect a stalled / crashed addon.
HEARTBEAT_TOPIC = "predictive-lighting/eval/heartbeat"
HEARTBEAT_INTERVAL_S = 60

WATCHDOG_INTERVAL_S = 5         # how often to call sweep_stale
STALE_AFTER_S = 5               # tracks with no events for this long -> clear
SPATIAL_RELOAD_S = 60           # re-check spatial_model.json mtime this often


def log(msg):
    print(f"[predictive-lighting] {msg}", flush=True)


def mqtt_credentials():
    """Broker host/port/user/pass from the Supervisor services API — the
    add-on declares `services: [mqtt:need]`, so the Supervisor provisions
    and exposes the broker connection here. No hand-managed secrets."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN missing — not an add-on context?")
    req = urllib.request.Request(
        "http://supervisor/services/mqtt",
        headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    d = data.get("data", {})
    return {
        "host": d.get("host", "core-mosquitto"),
        "port": int(d.get("port", 1883)),
        "username": d.get("username", "") or "",
        "password": d.get("password", "") or "",
        "ssl": bool(d.get("ssl", False)),
    }


class LoggerApp:
    def __init__(self):
        self.tracker = zonelog.ZoneTracker()
        self.raw_count = 0
        self.event_count = 0
        self.transition_count = 0
        self.anticipated_publish_count = 0
        os.makedirs(SHARE_DIR, exist_ok=True)
        self.client = None
        self.spatial_model = self._load_spatial_model()
        self.spatial_mtime = self._spatial_mtime()
        self.anticipator = anticipate.Anticipator(self.spatial_model)
        self.last_spatial_check = time.time()
        self.last_watchdog = time.time()
        self.last_heartbeat = 0.0   # publish first heartbeat on first event
        # Phase 2 eval harness — Component 1.
        self.eval = eval_logger.EvalLogger()
        log(f"anticipator rooms registered: {self.anticipator.rooms}")

    # ── spatial model i/o (from /share mirror of /config) ────────────────

    def _spatial_mtime(self) -> float:
        try:
            return os.path.getmtime(SPATIAL_MODEL_PATH)
        except OSError:
            return 0.0

    def _load_spatial_model(self) -> dict:
        try:
            with open(SPATIAL_MODEL_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            log(f"spatial_model loaded from {SPATIAL_MODEL_PATH}: "
                f"{len(d.get('cameras', {}))} cameras, "
                f"{len(d.get('zone_to_room', {}))} zone mappings, "
                f"{len(d.get('camera_edges', {}))} edge maps")
            return d
        except FileNotFoundError:
            log(f"WARN: {SPATIAL_MODEL_PATH} not present — "
                f"copy /config/spatial_model.json there to enable the anticipator")
            return {"cameras": {}, "camera_edges": {}, "zone_to_room": {}}
        except Exception as e:                                # noqa: BLE001
            log(f"WARN: spatial_model load failed: {e} — "
                f"anticipator will register no rooms (no-op)")
            return {"cameras": {}, "camera_edges": {}, "zone_to_room": {}}

    def _maybe_reload_spatial_model(self, now: float):
        """Re-read the file periodically if its mtime changed."""
        if now - self.last_spatial_check < SPATIAL_RELOAD_S:
            return
        self.last_spatial_check = now
        new_mtime = self._spatial_mtime()
        if new_mtime > self.spatial_mtime:
            log(f"spatial_model mtime changed ({self.spatial_mtime} -> {new_mtime}) — reloading")
            self.spatial_model = self._load_spatial_model()
            self.spatial_mtime = new_mtime
            self.anticipator = anticipate.Anticipator(self.spatial_model)
            self._publish_discovery()
            self._publish_startup_clear()

    # ── anticipator MQTT publishes ───────────────────────────────────────

    def _publish_change(self, change: dict):
        room = change["room"]
        state = change["state"].upper()        # 'ON' or 'OFF'
        topic = f"{ANTICIPATED_BASE}/{room}"
        self.client.publish(topic, state, qos=0, retain=True)
        self.anticipated_publish_count += 1
        log(f"anticipated: {room}={state} (track {str(change['source_track'])[:18]})")

    def _publish_discovery(self):
        """HA MQTT discovery — one binary_sensor per room. Idempotent;
        called on connect and on spatial_model reload."""
        for room in self.anticipator.rooms:
            payload = {
                "name": f"Anticipated {room.replace('_', ' ').title()}",
                "unique_id": f"anticipator_{room}",
                "object_id": f"anticipated_{room}",
                "state_topic": f"{ANTICIPATED_BASE}/{room}",
                "payload_on": "ON",
                "payload_off": "OFF",
                "device_class": "motion",
                "availability_topic": AVAIL_TOPIC,
                "payload_available": "online",
                "payload_not_available": "offline",
            }
            self.client.publish(
                f"{DISCOVERY_BASE}{room}/config",
                json.dumps(payload), qos=0, retain=True)
        log(f"HA MQTT discovery published for {len(self.anticipator.rooms)} rooms")

    def _publish_startup_clear(self):
        """On every connect, explicitly OFF every retained room topic so
        no phantom ON from a previous crashed process lingers."""
        for room in self.anticipator.rooms:
            self.client.publish(
                f"{ANTICIPATED_BASE}/{room}", "OFF", qos=0, retain=True)
        log(f"startup-cleared {len(self.anticipator.rooms)} anticipated topics")

    def _publish_shutdown(self):
        """Graceful shutdown — set availability=offline and clear all
        anticipated room topics. (LWT covers UNclean disconnect.)"""
        try:
            for room in self.anticipator.rooms:
                self.client.publish(
                    f"{ANTICIPATED_BASE}/{room}", "OFF", qos=0, retain=True)
            self.client.publish(AVAIL_TOPIC, "offline", qos=0, retain=True)
            log("graceful shutdown publishes sent")
        except Exception as e:                                # noqa: BLE001
            log(f"shutdown publish failed: {e}")

    # ── MQTT callbacks ───────────────────────────────────────────────────

    def on_connect(self, client, userdata, flags, reason_code,
                   properties=None):
        log(f"on_connect: {reason_code} — subscribing to {TOPIC}")
        client.publish(AVAIL_TOPIC, "online", qos=0, retain=True)
        self._publish_discovery()
        self._publish_startup_clear()
        client.subscribe(TOPIC, qos=0)
        # Phase 2 eval harness — Component 1 subscribes to its own publishes
        # (debug stream + anticipated state). Single paho client, multiplexed
        # by topic in on_message.
        client.subscribe(f"{DEBUG_TOPIC_BASE}/+", qos=0)
        client.subscribe(f"{ANTICIPATED_BASE}/+", qos=0)
        # Start the HA WS thread now that the broker connection is up.
        self.eval.start_ws_thread()

    def on_disconnect(self, client, userdata, flags, reason_code,
                      properties=None):
        log(f"on_disconnect: {reason_code} — paho will auto-reconnect")

    def on_message(self, client, userdata, msg):
        # Multiplex by topic. Frigate events go through the existing tracker
        # + anticipator path. Eval-relevant topics get routed to the logger.
        if msg.topic != TOPIC:
            if (msg.topic.startswith(f"{DEBUG_TOPIC_BASE}/")
                    or msg.topic.startswith(f"{ANTICIPATED_BASE}/")):
                self.eval.note_mqtt(msg.topic, msg.payload)
            return
        # frigate/events branch:
        self.event_count += 1
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as e:                                # noqa: BLE001
            log(f"bad payload: {e}")
            return

        # Raw-sample dump (first 12 events) for post-deploy schema inspection.
        if self.raw_count < RAW_SAMPLE_LIMIT:
            self.raw_count += 1
            after = payload.get("after") or {}
            log(f"raw sample {self.raw_count}: type={payload.get('type')} "
                f"camera={after.get('camera')} label={after.get('label')} "
                f"zones={after.get('current_zones')} "
                f"box={after.get('box')}")
            try:
                with open(RAW_SAMPLE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(payload) + "\n")
            except Exception as e:                            # noqa: BLE001
                log(f"raw-sample write failed: {e}")

        # ── Legacy: zone-transition logger ──────────────────────────────
        try:
            records = self.tracker.process(payload, time.time())
        except Exception as e:                                # noqa: BLE001
            log(f"tracker error: {e}")
            records = []
        for rec in records:
            self._append(rec)

        # ── New: kinematic anticipator ──────────────────────────────────
        now = time.time()
        self._maybe_reload_spatial_model(now)
        # Watchdog: clear predictions for tracks that haven't been seen.
        if now - self.last_watchdog > WATCHDOG_INTERVAL_S:
            self.last_watchdog = now
            try:
                for change in self.anticipator.sweep_stale(now, STALE_AFTER_S):
                    self._publish_change(change)
            except Exception as e:                            # noqa: BLE001
                log(f"anticipator sweep error: {e}")
        # Process the live event.
        try:
            changes, debug = self.anticipator.process(payload)
            for change in changes:
                self._publish_change(change)
            if debug:
                self._publish_debug(debug)
        except Exception as e:                                # noqa: BLE001
            log(f"anticipator process error: {e}")

        # Heartbeat — published from the message loop (rather than the main
        # thread) so it naturally pauses if the event flow itself stalls.
        # That way the harness detects "addon hung" as well as "addon dead."
        if now - self.last_heartbeat > HEARTBEAT_INTERVAL_S:
            self.last_heartbeat = now
            try:
                self.client.publish(
                    HEARTBEAT_TOPIC,
                    json.dumps({
                        "ts": now,
                        "events_seen": self.event_count,
                        "transitions_logged": self.transition_count,
                        "anticipated_publishes": self.anticipated_publish_count,
                    }),
                    qos=0, retain=True)
            except Exception as e:                            # noqa: BLE001
                log(f"heartbeat publish failed: {e}")

    def _publish_debug(self, debug: dict):
        """Per-event diagnostic publish for the Phase 2 eval harness. Not
        retained — these are ephemeral. Topic includes the (short) track id
        so the eval subscriber can grep by track without parsing payloads."""
        try:
            tid = str(debug.get("track_id") or "unknown")[:24]
            self.client.publish(
                f"{DEBUG_TOPIC_BASE}/{tid}",
                json.dumps(debug),
                qos=0, retain=False)
        except Exception as e:                                # noqa: BLE001
            # Never let debug publish errors break the main flow.
            log(f"debug publish failed: {e}")

    # ── Transition logger output ─────────────────────────────────────────

    def _append(self, rec):
        try:
            with open(TRANSITIONS, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            self.transition_count += 1
            log(f"transition #{self.transition_count}: "
                f"{rec['from_zone']} -> {rec['to_zone']} "
                f"(dwell {rec['from_dwell_s']}s"
                f"{', SUSPECT' if rec['dwell_suspect'] else ''})")
        except Exception as e:                                # noqa: BLE001
            log(f"transition write failed: {e}")


def main():
    log("starting — kinematic anticipator + zone-transition logger")
    app = LoggerApp()
    try:
        creds = mqtt_credentials()
    except Exception as e:                                    # noqa: BLE001
        log(f"FATAL: could not get MQTT credentials: {e}")
        return 1
    log(f"broker {creds['host']}:{creds['port']} ssl={creds['ssl']} "
        f"user={creds['username'] or '(none)'}")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="predictive-lighting-anticipator")
    app.client = client
    client.on_connect = app.on_connect
    client.on_disconnect = app.on_disconnect
    client.on_message = app.on_message
    # LWT: broker publishes 'offline' on unclean disconnect; HA marks all
    # binary_sensor.anticipated_* as unavailable in response.
    client.will_set(AVAIL_TOPIC, "offline", qos=0, retain=True)
    if creds["username"]:
        client.username_pw_set(creds["username"], creds["password"])
    if creds["ssl"]:
        client.tls_set()
    client.reconnect_delay_set(min_delay=1, max_delay=60)

    while True:
        try:
            client.connect(creds["host"], creds["port"], keepalive=60)
            break
        except Exception as e:                                # noqa: BLE001
            log(f"connect failed ({e}); retry in 10s")
            time.sleep(10)

    client.loop_start()
    log("MQTT loop running — anticipating + logging transitions")
    try:
        while True:
            time.sleep(HEARTBEAT_S)
            log(f"heartbeat: {app.event_count} events seen, "
                f"{app.transition_count} transitions logged, "
                f"{app.anticipated_publish_count} anticipated publishes, "
                f"{app.tracker.active_tracks()} active tracks")
    except KeyboardInterrupt:
        pass
    finally:
        app._publish_shutdown()
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())

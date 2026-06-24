"""eval_logger.py — Phase 2 eval harness, Component 1.

Persists every state change relevant to the anticipator-eval to a single
append-only JSONL file at /share/predictive-lighting/eval-anticipator.jsonl.

Sources:
1. HA WebSocket state_changed events — filtered to:
     binary_sensor.anticipated_*          (our predictions)
     binary_sensor.<zone>_person_occupancy + _stable (arrivals)
     input_datetime.living_lights_zone_<zone>_last_manual_at (cooldown gates)
2. MQTT debug stream from anticipate.py — `predictive-lighting/debug/track/#`
3. MQTT anticipated stream — `predictive-lighting/anticipated/+` (redundant with
   #1 but lower-latency; useful for cross-checking HA's mirror)

The JSONL is the input to the offline analyzer (tools/eval-anticipator.py).

Auth: reads a long-lived HA token from /share/predictive-lighting/ha_token.txt.
The user copies it there as one-time setup. (Direct LAN address instead of
supervisor proxy because the supervisor's manifest cache won't accept the
homeassistant_api: true flag in this install — same workaround we used for
spatial_model.json.)
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

# Third-party
import websocket  # websocket-client (added in Dockerfile)


HA_TOKEN_PATH = "/share/predictive-lighting/ha_token.txt"
HA_WS_URL = "ws://192.168.0.125:8123/api/websocket"  # LAN; no supervisor proxy
EVAL_JSONL = "/share/predictive-lighting/eval-anticipator.jsonl"

# Entity-prefix filters — only these state_changed events are persisted.
ENTITY_PREFIXES_OF_INTEREST = (
    "binary_sensor.anticipated_",
    "input_datetime.living_lights_zone_",
)
# Substring filters for occupancy sensors (which are bare-slug, no consistent
# prefix). _person_occupancy and _person_occupancy_stable both qualify.
ENTITY_SUBSTRINGS_OF_INTEREST = (
    "_person_occupancy",
)

WS_RECONNECT_BACKOFF_MIN_S = 1.0
WS_RECONNECT_BACKOFF_MAX_S = 60.0


def _log(msg: str):
    print(f"[eval-logger] {msg}", flush=True)


class EvalLogger:
    """Writes one JSON line per relevant state-change event to EVAL_JSONL.
    Two parallel sources feed in:
      - WebSocket thread, started by start_ws_thread()
      - MQTT — caller routes relevant on_message payloads via note_mqtt()
    Writer uses a Lock; multiple threads can call note_*() safely.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._ws_thread: Optional[threading.Thread] = None
        self._ws_stop = threading.Event()
        os.makedirs(os.path.dirname(EVAL_JSONL), exist_ok=True)
        self.write_count = 0
        # Reconnect-marker for analyzer (knows there's a gap if it sees one).
        self._ws_reconnect_count = 0

    # -- public surface ---------------------------------------------------

    def note_mqtt(self, topic: str, payload: bytes):
        """Called by app.py from its paho on_message for relevant topics.
        Handled topics:
          predictive-lighting/debug/track/<id>
          predictive-lighting/anticipated/<room>
        """
        try:
            if topic.startswith("predictive-lighting/debug/track/"):
                body = json.loads(payload.decode("utf-8"))
                self._append({
                    "ts": body.get("ts") or time.time(),
                    "source": "mqtt_debug",
                    "topic": topic,
                    "body": body,
                })
            elif topic.startswith("predictive-lighting/anticipated/"):
                room = topic.rsplit("/", 1)[-1]
                state = payload.decode("utf-8").strip()
                self._append({
                    "ts": time.time(),
                    "source": "mqtt_anticipated",
                    "room": room,
                    "state": state,
                })
        except Exception as e:                                # noqa: BLE001
            _log(f"note_mqtt error topic={topic!r}: {e}")

    def start_ws_thread(self):
        if self._ws_thread and self._ws_thread.is_alive():
            return
        self._ws_thread = threading.Thread(
            target=self._ws_loop, name="eval-ws", daemon=True)
        self._ws_thread.start()
        _log("HA WS thread started")

    def stop(self):
        self._ws_stop.set()

    # -- internals --------------------------------------------------------

    def _append(self, record: dict):
        with self._lock:
            try:
                with open(EVAL_JSONL, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                self.write_count += 1
            except Exception as e:                            # noqa: BLE001
                _log(f"append error: {e}")

    def _load_token(self) -> Optional[str]:
        try:
            with open(HA_TOKEN_PATH, "r", encoding="utf-8") as f:
                return f.read().strip()
        except FileNotFoundError:
            _log(f"WARN: {HA_TOKEN_PATH} missing — HA WS disabled. "
                 f"Copy your long-lived token there to enable.")
            return None
        except Exception as e:                                # noqa: BLE001
            _log(f"token read error: {e}")
            return None

    def _filter_entity(self, entity_id: str) -> bool:
        if any(entity_id.startswith(p) for p in ENTITY_PREFIXES_OF_INTEREST):
            return True
        if any(s in entity_id for s in ENTITY_SUBSTRINGS_OF_INTEREST):
            return True
        return False

    def _ws_loop(self):
        backoff = WS_RECONNECT_BACKOFF_MIN_S
        while not self._ws_stop.is_set():
            token = self._load_token()
            if not token:
                # No token; sleep and retry — maybe user will drop it in.
                time.sleep(30)
                continue
            try:
                self._run_one_ws_session(token)
                # Clean close — reset backoff for next attempt.
                backoff = WS_RECONNECT_BACKOFF_MIN_S
            except Exception as e:                            # noqa: BLE001
                _log(f"WS session ended: {e}; reconnecting in {backoff:.1f}s")
                self._ws_reconnect_count += 1
                self._append({
                    "ts": time.time(),
                    "source": "eval_logger",
                    "event": "ws_reconnect_attempt",
                    "reconnect_count": self._ws_reconnect_count,
                    "backoff_s": backoff,
                    "error": str(e),
                })
                time.sleep(backoff)
                backoff = min(backoff * 2, WS_RECONNECT_BACKOFF_MAX_S)

    def _run_one_ws_session(self, token: str):
        ws = websocket.create_connection(HA_WS_URL, timeout=30)
        try:
            # 1. auth_required
            msg = json.loads(ws.recv())
            if msg.get("type") != "auth_required":
                raise RuntimeError(f"unexpected first msg: {msg}")
            # 2. send auth
            ws.send(json.dumps({"type": "auth", "access_token": token}))
            msg = json.loads(ws.recv())
            if msg.get("type") != "auth_ok":
                raise RuntimeError(f"auth failed: {msg}")
            _log(f"HA WS authed — ha {msg.get('ha_version')}")
            self._append({
                "ts": time.time(),
                "source": "eval_logger",
                "event": "ws_connected",
                "ha_version": msg.get("ha_version"),
            })
            # 3. subscribe_events for state_changed
            ws.send(json.dumps({
                "id": 1, "type": "subscribe_events",
                "event_type": "state_changed",
            }))
            msg = json.loads(ws.recv())
            if msg.get("type") != "result" or not msg.get("success"):
                raise RuntimeError(f"subscribe failed: {msg}")
            _log("subscribed to state_changed; entering receive loop")
            # 4. receive loop
            ws.settimeout(90)  # WS server keepalive ~60s; allow margin
            while not self._ws_stop.is_set():
                raw = ws.recv()
                if not raw:
                    raise RuntimeError("ws.recv returned empty (closed)")
                evt = json.loads(raw)
                if evt.get("type") != "event":
                    continue
                data = (evt.get("event") or {}).get("data") or {}
                entity_id = data.get("entity_id") or ""
                if not self._filter_entity(entity_id):
                    continue
                new_state = (data.get("new_state") or {})
                old_state = (data.get("old_state") or {})
                self._append({
                    "ts": time.time(),
                    "source": "ha_ws",
                    "entity_id": entity_id,
                    "old": old_state.get("state") if old_state else None,
                    "new": new_state.get("state") if new_state else None,
                    "attrs": new_state.get("attributes") or {},
                })
        finally:
            try:
                ws.close()
            except Exception:
                pass

"""Provisioned Victoria MQTT transport; callbacks never mutate the adapter.

Each start creates a new clean-session client. Reconnection is explicit, so
Paho's retained outgoing packets cannot replay on a later connection. QoS 0
provides best-effort live context, not delivery acknowledgement or an event log.
Broker-side max_packet_size is still required: Paho decodes a packet before the
bounded on_message callback sees it. No broker or credentials are discovered.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import importlib.metadata
import json
import re
import socket
import ssl
import threading
import time
from types import MappingProxyType
from typing import Mapping

from .adapter import VictoriaAdapter

SUBSCRIPTIONS = VictoriaAdapter().subscriptions
AVAILABILITY = "home/shared/v1/victoria/den_oak/availability"
OBSERVATION = "home/shared/v1/victoria/den_oak/observation"
PUBLISH_TOPICS = frozenset(("frigate/onConnect", AVAILABILITY, OBSERVATION))
MAX_PAYLOAD = 64 * 1024


class MqttUnavailable(RuntimeError):
    """No publish was accepted for the current connection."""


class MqttBusy(MqttUnavailable):
    """The previous worker or packet still holds the transport permit."""


@dataclass(frozen=True)
class MqttConfig:
    host: str
    ca_file: str
    username: str
    password: str = field(repr=False)
    client_id: str = "victoria-den-adapter"
    event_capacity: int = 128

    def __post_init__(self):
        # Provisioning is trusted configuration, never a request-supplied URL.
        if (not isinstance(self.host, str) or len(self.host) > 253
                or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", self.host)):
            raise ValueError("MQTT broker must be a provisioned DNS hostname or IPv4 address")
        for value in (self.ca_file, self.username, self.password, self.client_id):
            if not isinstance(value, str) or not 1 <= len(value) <= 4096 or any(ord(c) < 32 for c in value):
                raise ValueError("invalid MQTT provisioned configuration")
        if type(self.event_capacity) is not int or not 4 <= self.event_capacity <= 1024:
            raise ValueError("MQTT event capacity must be between 4 and 1024")


@dataclass(frozen=True)
class MqttEvent:
    kind: str
    payload: Mapping


def _paho():
    if importlib.metadata.version("paho-mqtt") != "2.1.0":
        raise RuntimeError("MQTT transport requires pinned paho-mqtt 2.1.0")
    import paho.mqtt.client as mqtt
    return mqtt


class MqttTransport:
    """Lifecycle methods belong to one runtime thread; callbacks only enqueue.

    Drain at least once a second. On disconnect/overflow, pending input is
    replaced by an invalidation event. The runtime must also discard work from
    an older event connection_generation. start() requires the previous worker
    to have stopped, and never retries a failed connection automatically.
    """

    def __init__(self, config: MqttConfig, *, will: tuple[str, str, bool],
                 clock=time.monotonic, mqtt_module=None, tls_context_factory=None):
        if not isinstance(config, MqttConfig):
            raise TypeError("a provisioned MqttConfig is required")
        topic, payload, retain = will
        body = self._payload(topic, payload, retain)
        value = json.loads(body)
        if (topic != AVAILABILITY or value.get("online") is not False
                or not isinstance(value.get("adapter_session"), str)
                or not 1 <= len(value["adapter_session"]) <= 128
                or type(value.get("occupancy_generation")) is not int
                or value["occupancy_generation"] < 0):
            raise ValueError("MQTT will must be offline Victoria availability")
        self._adapter_session = value["adapter_session"]
        self.config, self._will, self._clock = config, body, clock
        self._mqtt_module = mqtt_module  # Explicit test seam; production lazily imports the pin.
        self._tls_factory = tls_context_factory or self._tls_context
        self._lock = threading.RLock()
        self._events = deque()
        self._client = self._thread = None
        self._generation = 0
        self._connected = self._subscribed = self._invalid = False
        self._mid = None
        self._outgoing = None

    @staticmethod
    def _tls_context(config):
        # Avoid create_default_context: it honors ambient SSLKEYLOGFILE. Trust
        # only the provisioned CA, require SAN hostname validation, and never
        # enable TLS session-key logging through inherited environment state.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.hostname_checks_common_name = False
        context.load_verify_locations(cafile=config.ca_file)
        return context

    @staticmethod
    def _payload(topic, payload, retain):
        if topic not in PUBLISH_TOPICS or retain is not False:
            raise ValueError("MQTT publication is outside the fixed non-retained ACL")
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        if not isinstance(payload, bytes) or len(payload) > MAX_PAYLOAD:
            raise ValueError("MQTT payload exceeds limit")
        if topic == "frigate/onConnect":
            if payload != b"":
                raise ValueError("Frigate reconciliation accepts only an empty payload")
        else:
            try:
                value = json.loads(payload)
            except (ValueError, UnicodeError) as exc:
                raise ValueError("MQTT output must be JSON") from exc
            if (not isinstance(value, dict) or value.get("site_id") != "victoria"
                    or value.get("camera_id") != "den_oak"):
                raise ValueError("MQTT output must name the provisioned Victoria camera")
        return payload

    @property
    def adapter_session(self):
        return self._adapter_session

    @property
    def connection_generation(self):
        with self._lock:
            return self._generation

    def _event(self, kind, **payload):
        payload.update(generation=self._generation, connection_generation=self._generation,
                       received_mono=self._clock())
        return MqttEvent(kind, MappingProxyType(payload))

    def _invalidate(self, kind, reason):
        # Called under the lock. Late callbacks cannot repopulate this queue.
        if self._invalid:
            return
        self._invalid = True
        self._connected = self._subscribed = False
        self._events.clear()
        self._events.append(self._event(kind, reason=reason))
        if self._client is not None:
            self._abort(self._client)

    @staticmethod
    def _abort(client):
        # A graceful MQTT DISCONNECT suppresses the will and may first flush an
        # unsent packet. Shut down the public socket before asking Paho to stop;
        # the broker observes a lost connection and can publish the offline LWT.
        # No wait for DNS, remote acknowledgement or the network worker belongs
        # on the runtime's one-second state/expiry loop.
        stream = client.socket()
        if stream is not None:
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # It may already be closed by the network thread.
            try:
                stream.close()
            except OSError:
                pass
        try:
            client.disconnect()
        except (OSError, ValueError):
            pass  # Closing a concurrent TLS/socket operation is expected.

    def _enqueue(self, kind, **payload):
        if len(self._events) >= self.config.event_capacity:
            self._invalidate("overflow", "callback_queue_full")
            return False
        self._events.append(self._event(kind, **payload))
        return True

    def _current(self, client):
        return client is self._client and not self._invalid

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise MqttBusy("previous MQTT worker has not stopped")
            mqtt = self._mqtt_module or _paho()
            context = self._tls_factory(self.config)
            if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
                raise ValueError("MQTT TLS must verify certificate and hostname")
            client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                                 client_id=self.config.client_id, clean_session=True,
                                 protocol=mqtt.MQTTv311, transport="tcp", reconnect_on_failure=False)
            client.tls_set_context(context)
            client.username_pw_set(self.config.username, self.config.password)
            client.max_inflight_messages_set(1)
            client.max_queued_messages_set(1)
            client.connect_timeout = 5
            client.will_set(AVAILABILITY, self._will, qos=0, retain=False)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_subscribe = self._on_subscribe
            client.on_message = self._on_message
            self._generation += 1
            self._client = client
            self._events.clear()
            self._connected = self._subscribed = self._invalid = False
            self._mid = self._outgoing = None
            self._thread = threading.Thread(target=self._run, args=(client,), daemon=True,
                                            name="victoria-mqtt")
            self._thread.start()

    def _run(self, client):
        try:
            with self._lock:
                if not self._current(client):
                    return
            if client.connect(self.config.host, port=8883, keepalive=15) != 0:
                raise MqttUnavailable("connection failed")
            with self._lock:
                if not self._current(client):
                    self._abort(client)
                    return
            # Unlike loop_start(), this never retries the first connection.
            client.loop_forever(retry_first_connection=False)
        except Exception:
            with self._lock:
                if self._current(client):
                    self._invalidate("disconnected", "connection_failed")
        finally:
            with self._lock:
                if self._current(client):
                    self._invalidate("disconnected", "network_loop_ended")

    def stop(self):
        """Invalidate immediately; an unresolved worker retains its permit.

        This does not wait for DNS/socket work. A later start raises MqttBusy
        until the worker has actually terminated. Established connections are
        aborted for offline LWT, never gracefully disconnected while online.
        """
        with self._lock:
            self._invalidate("disconnected", "stopped")

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        with self._lock:
            if not self._current(client):
                return
            if self._connected or reason_code != 0 or getattr(flags, "session_present", False):
                self._invalidate("disconnected", "connection_refused_or_nonclean")
                return
            self._connected = True
            if not self._enqueue("connected", clean_session=True):
                return
            rc, self._mid = client.subscribe([(topic, 0) for topic in SUBSCRIPTIONS])
            if rc != 0:
                self._invalidate("disconnected", "subscribe_failed")

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties):
        with self._lock:
            if not self._current(client) or mid != self._mid or not self._connected:
                return
            # Every requested QoS is zero; any different grant fails closed.
            success = len(reason_codes) == len(SUBSCRIPTIONS) and all(code == 0 for code in reason_codes)
            self._subscribed = success
            if not success:
                self._invalidate("disconnected", "subscription_rejected")
                self._enqueue("subscribed", topics=SUBSCRIPTIONS, success=False)
            else:
                self._enqueue("subscribed", topics=SUBSCRIPTIONS, success=True)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        with self._lock:
            if self._current(client):
                self._invalidate("disconnected", "broker_disconnected")

    def _on_message(self, client, userdata, message):
        with self._lock:
            if not self._current(client) or not self._subscribed:
                return
            if message.topic not in SUBSCRIPTIONS or len(message.payload) > MAX_PAYLOAD:
                self._invalidate("overflow", "invalid_or_oversized_input")
                return
            self._enqueue("message", topic=message.topic, payload=bytes(message.payload),
                          retained=bool(message.retain), duplicate=bool(message.dup))

    def _check_outgoing(self):
        if self._outgoing is not None:
            info, submitted = self._outgoing
            if info.is_published():
                self._outgoing = None
            elif self._clock() - submitted >= 1:
                self._invalidate("disconnected", "publish_not_sent")

    def drain(self, max_events=64):
        if type(max_events) is not int or not 1 <= max_events <= 1024:
            raise ValueError("invalid MQTT drain limit")
        with self._lock:
            self._check_outgoing()
            return [self._events.popleft() for _ in range(min(max_events, len(self._events)))]

    def publish(self, topic, payload, retain=False):
        body = self._payload(topic, payload, retain)
        with self._lock:
            self._check_outgoing()
            if self._invalid or not self._subscribed or not self._client.is_connected():
                raise MqttUnavailable("MQTT subscription is not live")
            if self._outgoing is not None:
                raise MqttBusy("previous MQTT packet remains unsent")
            info = self._client.publish(topic, body, qos=0, retain=False)
            if info.rc != 0:
                self._invalidate("disconnected", "publish_failed")
                raise MqttUnavailable("MQTT publication failed; delivery is unknown")
            self._outgoing = (info, self._clock())
            return info.mid  # Packet identity, never proof of subscriber delivery.

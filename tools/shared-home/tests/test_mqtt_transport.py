"""Socket-free Paho callback/lifecycle tests; no broker is contacted."""
import json
import os
import ssl
import socket
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from home_shared.adapter import VictoriaAdapter
from home_shared.mqtt_transport import (AVAILABILITY, MAX_PAYLOAD, OBSERVATION,
                                        SUBSCRIPTIONS, MqttBusy, MqttConfig,
                                        MqttTransport, MqttUnavailable)
from home_shared.protocol import FrigateWire


class FakeClient:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.done = threading.Event()
        self.loop_entered = threading.Event()
        self.live = False
        self.sent = []
        self.publish_complete = True
        self.publish_rc = 0
        self.socket_calls = []
        self.stream = SimpleNamespace(shutdown=lambda how: self.socket_calls.append(("shutdown", how)),
                                      close=lambda: self.socket_calls.append(("close",)))

    def tls_set_context(self, context): self.context = context
    def username_pw_set(self, username, password): self.credentials = (username, password)
    def max_inflight_messages_set(self, limit): self.inflight = limit
    def max_queued_messages_set(self, limit): self.queued = limit
    def will_set(self, topic, body, **kwargs): self.will = (topic, body, kwargs)
    def connect(self, host, **kwargs):
        self.endpoint = (host, kwargs)
        return 0

    def loop_forever(self, **kwargs):
        self.loop_options = kwargs
        self.loop_entered.set()
        self.done.wait(5)

    def disconnect(self):
        self.socket_calls.append(("disconnect",))
        self.live = False
        self.done.set()
        return 0

    def is_connected(self): return self.live
    def socket(self): return self.stream
    def subscribe(self, topics):
        self.topics = topics
        return (0, 17)

    def publish(self, topic, payload, **kwargs):
        self.sent.append((topic, payload, kwargs))
        return SimpleNamespace(mid=len(self.sent), rc=self.publish_rc,
                               is_published=lambda: self.publish_complete)

    def connack(self, reason=0, session_present=False):
        self.live = reason == 0
        self.on_connect(self, None, SimpleNamespace(session_present=session_present), reason, None)

    def suback(self, reasons=None, mid=17):
        self.on_subscribe(self, None, mid, reasons if reasons is not None else [0] * 4, None)

    def message(self, topic="frigate/camera_activity", payload=b"{}", retain=False, dup=False):
        self.on_message(self, None, SimpleNamespace(topic=topic, payload=payload, retain=retain, dup=dup))


class FakePaho:
    CallbackAPIVersion = SimpleNamespace(VERSION2=2)
    MQTTv311 = 4

    def __init__(self): self.clients = []
    def Client(self, **kwargs):
        client = FakeClient(**kwargs)
        self.clients.append(client)
        return client


class MqttTransportTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.mqtt = FakePaho()
        self.config = MqttConfig("broker.example.test", "fixture-ca.pem", "adapter", "secret", event_capacity=4)
        self.will = FrigateWire(VictoriaAdapter()).last_will()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.transport = MqttTransport(self.config, will=self.will, clock=lambda: self.now,
                                       mqtt_module=self.mqtt, tls_context_factory=lambda _: context)
        self.addCleanup(self.cleanup_transport)

    def cleanup_transport(self):
        self.transport.stop()
        if self.transport._thread is not None:
            self.transport._thread.join(timeout=1)

    def start(self, subscribe=True):
        self.transport.start()
        client = self.mqtt.clients[-1]
        self.assertTrue(client.loop_entered.wait(1))
        client.connack()
        if subscribe:
            client.suback()
        return client

    def test_tls_clean_session_fixed_topics_and_no_automatic_reconnect(self):
        client = self.start()
        self.assertEqual(client.endpoint, ("broker.example.test", {"port": 8883, "keepalive": 15}))
        self.assertEqual(client.options, {"callback_api_version": 2, "client_id": "victoria-den-adapter",
                                         "clean_session": True, "protocol": 4, "transport": "tcp",
                                         "reconnect_on_failure": False})
        self.assertTrue(client.context.check_hostname)
        self.assertEqual(client.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(client.credentials, ("adapter", "secret"))
        self.assertNotIn("secret", repr(self.config))
        self.assertEqual(client.inflight, 1)
        self.assertEqual(client.queued, 1)
        self.assertEqual(client.topics, [(topic, 0) for topic in SUBSCRIPTIONS])
        self.assertEqual(client.loop_options, {"retry_first_connection": False})
        self.assertFalse(client.will[2]["retain"])
        events = self.transport.drain()
        self.assertEqual([e.kind for e in events], ["connected", "subscribed"])
        self.assertTrue(events[0].payload["clean_session"])
        self.assertEqual(events[1].payload["topics"], SUBSCRIPTIONS)
        self.assertTrue(events[1].payload["success"])
        self.assertEqual(events[1].payload["generation"], 1)

    def test_publish_requires_full_matching_suback(self):
        client = self.start(subscribe=False)
        client.suback(mid=99)
        with self.assertRaises(MqttUnavailable): self.transport.publish("frigate/onConnect", "")
        client.suback([0, 0, 128, 0])
        events = self.transport.drain()
        self.assertIn("disconnected", [e.kind for e in events])
        self.assertFalse(next(e for e in events if e.kind == "subscribed").payload["success"])
        with self.assertRaises(MqttUnavailable): self.transport.publish("frigate/onConnect", "")

    def test_short_suback_rejected(self):
        client = self.start(subscribe=False)
        client.suback([0])
        self.assertFalse(self.transport.drain()[-1].payload["success"])

    def test_messages_keep_flags_receipt_time_and_generation(self):
        client = self.start()
        self.transport.drain()
        client.message(payload=b"[]", retain=True, dup=True)
        event, = self.transport.drain()
        self.assertEqual(event.kind, "message")
        self.assertEqual(event.payload["payload"], b"[]")
        self.assertTrue(event.payload["retained"])
        self.assertTrue(event.payload["duplicate"])
        self.assertEqual(event.payload["received_mono"], 100)
        self.assertEqual(event.payload["connection_generation"], 1)

    def test_messages_before_suback_are_not_delivered(self):
        client = self.start(subscribe=False)
        client.message()
        self.assertEqual([e.kind for e in self.transport.drain()], ["connected"])

    def test_overflow_discards_stale_input_and_disconnects(self):
        client = self.start()
        self.transport.drain()
        for _ in range(5): client.message()
        client.message()
        event, = self.transport.drain()
        self.assertEqual(event.kind, "overflow")
        self.assertFalse(client.live)
        self.assertEqual(self.transport.drain(), [])
        with self.assertRaises(MqttUnavailable): self.transport.publish("frigate/onConnect", "")

    def test_wrong_topic_and_oversize_fail_closed(self):
        client = self.start()
        self.transport.drain()
        client.message(payload=b"x" * (MAX_PAYLOAD + 1))
        self.assertEqual(self.transport.drain()[0].kind, "overflow")

    def test_unsubscribed_topic_invalidates_connection(self):
        client = self.start()
        client.message(topic="frigate/another_camera/person")
        event, = self.transport.drain()
        self.assertEqual(event.kind, "overflow")
        self.assertEqual(event.payload["reason"], "invalid_or_oversized_input")

    def test_exact_publish_acl_and_non_retained_payload(self):
        client = self.start()
        for topic, payload, retain in [("ha/lights/set", "on", False),
                                        ("frigate/onConnect", "", True),
                                        ("frigate/onConnect", "query", False),
                                        (OBSERVATION, '{"site_id":"echo","camera_id":"den_oak"}', False)]:
            with self.assertRaises(ValueError): self.transport.publish(topic, payload, retain)
        self.transport.publish("frigate/onConnect", "")
        self.transport.publish(*self.will)
        self.assertEqual(client.sent[0], ("frigate/onConnect", b"", {"qos": 0, "retain": False}))
        self.assertEqual(client.sent[1][0], AVAILABILITY)

    def test_one_unsent_packet_and_timeout_closes_connection(self):
        client = self.start()
        self.transport.drain()
        client.publish_complete = False
        self.transport.publish("frigate/onConnect", "")
        with self.assertRaises(MqttBusy): self.transport.publish(*self.will)
        self.assertEqual(len(client.sent), 1)
        self.now += 1
        event, = self.transport.drain()
        self.assertEqual(event.kind, "disconnected")
        self.assertEqual(event.payload["reason"], "publish_not_sent")

    def test_reconnect_uses_new_client_discards_old_events_and_packets(self):
        old = self.start()
        old.message()
        old.publish_complete = False
        self.transport.publish(*self.will)
        self.transport.stop()
        self.transport._thread.join(timeout=1)
        new = self.start()
        old.message()
        old.suback()
        events = self.transport.drain()
        self.assertEqual([e.kind for e in events], ["connected", "subscribed"])
        self.assertTrue(all(e.payload["generation"] == 2 for e in events))
        self.assertEqual(new.sent, [])

    def test_broker_disconnect_replaces_queued_messages(self):
        client = self.start()
        client.message()
        client.on_disconnect(client, None, None, 128, None)
        event, = self.transport.drain()
        self.assertEqual(event.kind, "disconnected")

    def test_refused_or_nonclean_connack_invalidates(self):
        self.transport.start()
        client = self.mqtt.clients[-1]
        self.assertTrue(client.loop_entered.wait(1))
        client.connack(session_present=True)
        self.assertEqual(self.transport.drain()[0].kind, "disconnected")

    def test_refused_connack_does_not_subscribe(self):
        self.transport.start()
        client = self.mqtt.clients[-1]
        self.assertTrue(client.loop_entered.wait(1))
        client.connack(reason=135)
        self.assertFalse(hasattr(client, "topics"))
        self.assertEqual(self.transport.drain()[0].kind, "disconnected")

    def test_second_start_cannot_create_concurrent_worker(self):
        self.start()
        with self.assertRaises(MqttBusy): self.transport.start()
        self.assertEqual(len(self.mqtt.clients), 1)

    def test_invalidation_aborts_socket_before_disconnect_for_lwt(self):
        client = self.start()
        client.publish_complete = False
        self.transport.publish(*self.will)
        self.now += 1
        self.transport.drain()
        self.assertEqual(client.socket_calls[:3], [("shutdown", socket.SHUT_RDWR),
                                                    ("close",), ("disconnect",)])
        self.assertEqual(len(client.sent), 1)

    def test_normal_stop_also_aborts_socket_for_offline_lwt(self):
        client = self.start()
        self.transport.stop()
        self.assertEqual(client.socket_calls[:3], [("shutdown", socket.SHUT_RDWR),
                                                    ("close",), ("disconnect",)])

    def test_stop_does_not_wait_for_blocked_dns_and_keeps_worker_permit(self):
        entered, release = threading.Event(), threading.Event()
        original = self.mqtt.Client
        def blocked_client(**kwargs):
            client = original(**kwargs)
            client.stream = None
            def connect(*args, **kw):
                entered.set()
                release.wait(5)
                return 0
            client.connect = connect
            return client
        self.mqtt.Client = blocked_client
        self.transport.start()
        try:
            self.assertTrue(entered.wait(1))
            before = time.monotonic()
            self.transport.stop()
            self.assertLess(time.monotonic() - before, .1)
            self.assertTrue(self.transport._thread.is_alive())
            with self.assertRaises(MqttBusy): self.transport.start()
            self.assertEqual(len(self.mqtt.clients), 1)
        finally:
            release.set()
            self.transport._thread.join(timeout=1)
        self.assertFalse(self.transport._thread.is_alive())
        self.assertFalse(self.mqtt.clients[0].loop_entered.is_set())

    def test_connection_exception_is_sanitized_and_not_retried(self):
        original = self.mqtt.Client
        def failing_client(**kwargs):
            client = original(**kwargs)
            def connect(*args, **kw): raise OSError("credential secret must not escape")
            client.connect = connect
            return client
        self.mqtt.Client = failing_client
        self.transport.start()
        self.transport._thread.join(timeout=1)
        event, = self.transport.drain()
        self.assertEqual(event.kind, "disconnected")
        self.assertEqual(event.payload["reason"], "connection_failed")
        self.assertNotIn("secret", repr(event))
        self.assertEqual(len(self.mqtt.clients), 1)

    def test_invalid_tls_and_will_rejected_before_connection(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        self.transport._tls_factory = lambda _: context
        with self.assertRaises(ValueError): self.transport.start()
        body = json.loads(self.will[1])
        body["online"] = True
        with self.assertRaises(ValueError):
            MqttTransport(self.config, will=(AVAILABILITY, json.dumps(body), False))
        self.assertEqual(self.mqtt.clients, [])

    def test_production_tls_ignores_ambient_keylog_and_requires_san(self):
        # Intercept only CA-file loading; exercise the real SSLContext setup.
        # No network or filesystem secret material is involved.
        with patch.dict(os.environ, {"SSLKEYLOGFILE": "untrusted-tls-keys.log"}), \
                patch.object(ssl.SSLContext, "load_verify_locations") as load:
            context = MqttTransport._tls_context(self.config)
        self.assertIsNone(context.keylog_filename)
        self.assertTrue(context.check_hostname)
        self.assertFalse(context.hostname_checks_common_name)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        load.assert_called_once_with(cafile="fixture-ca.pem")
        self.assertEqual(context.cert_store_stats()["x509_ca"], 0)

    def test_unprovisioned_url_and_empty_credentials_rejected(self):
        for host in ("https://broker/", "broker:1883", "broker/#", ""):
            with self.assertRaises(ValueError): MqttConfig(host, "ca", "user", "secret")
        with self.assertRaises(ValueError): MqttConfig("broker", "ca", "user", "")

    def test_will_requires_session_and_nonnegative_generation(self):
        body = json.loads(self.will[1])
        self.assertEqual(self.transport.adapter_session, body["adapter_session"])
        with self.assertRaises(AttributeError): self.transport.adapter_session = "other"
        for field, invalid in [("adapter_session", ""), ("adapter_session", 3),
                                ("adapter_session", "x" * 129),
                                ("occupancy_generation", -1), ("occupancy_generation", True)]:
            value = dict(body, **{field: invalid})
            with self.assertRaises(ValueError):
                MqttTransport(self.config, will=(AVAILABILITY, json.dumps(value), False))

    def test_failed_publish_invalidates_without_retry(self):
        client = self.start()
        client.publish_rc = 4
        with self.assertRaises(MqttUnavailable): self.transport.publish(*self.will)
        self.assertEqual(len(client.sent), 1)
        self.assertEqual(self.transport.drain()[0].kind, "disconnected")


if __name__ == "__main__":
    unittest.main()

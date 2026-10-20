"""Actual pinned Paho against an ephemeral, loopback-only TLS MQTT fixture.

The fixture implements only the packet subset under test. It is not a broker
commissioning receipt, and its checked-in keys are public test credentials.
"""
import importlib.metadata
import json
from pathlib import Path
import socket
import ssl
import threading
import time
from types import SimpleNamespace
import unittest

from home_shared.adapter import VictoriaAdapter
from home_shared.mqtt_transport import (AVAILABILITY, SUBSCRIPTIONS, MqttConfig,
                                        MqttTransport, MqttUnavailable)
from home_shared.protocol import FrigateWire

try:
    import paho.mqtt.client as paho
    PAHO_AVAILABLE = importlib.metadata.version("paho-mqtt") == "2.1.0"
except (ImportError, importlib.metadata.PackageNotFoundError):
    PAHO_AVAILABLE = False

FIXTURES = Path(__file__).parent / "fixtures" / "gateway_client"


def field(value):
    value = value.encode() if isinstance(value, str) else value
    return len(value).to_bytes(2, "big") + value


def packet(header, payload):
    length = len(payload)
    encoded = bytearray((header,))
    while True:
        digit, length = length % 128, length // 128
        encoded.append(digit | (128 if length else 0))
        if not length:
            break
    return bytes(encoded) + payload


def take_field(data, offset):
    length = int.from_bytes(data[offset:offset + 2], "big")
    end = offset + 2 + length
    if end > len(data):
        raise AssertionError("truncated MQTT field")
    return data[offset + 2:end], end


class LocalMqttTLS:
    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(2)
        self.listener.settimeout(.1)
        self.port = self.listener.getsockname()[1]
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(str(FIXTURES / "server.pem"), str(FIXTURES / "server-test-only.key"))
        self.stop = threading.Event()
        self.records = []
        self.errors = []
        self.thread = threading.Thread(target=self.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.listener.close()
        self.thread.join(2)
        if self.thread.is_alive():
            raise AssertionError("local MQTT fixture did not stop")
        if self.errors:
            raise AssertionError(self.errors)

    def receive(self, stream, count):
        result = b""
        while len(result) < count and not self.stop.is_set():
            try:
                chunk = stream.recv(count - len(result))
            except socket.timeout:
                continue
            if not chunk:
                raise EOFError
            result += chunk
        if len(result) != count:
            raise EOFError
        return result

    def read_packet(self, stream):
        header = self.receive(stream, 1)[0]
        length, multiplier = 0, 1
        for _ in range(4):
            byte = self.receive(stream, 1)[0]
            length += (byte & 127) * multiplier
            if not byte & 128:
                if length > 65536:
                    raise AssertionError("fixture packet oversized")
                return header, self.receive(stream, length)
            multiplier *= 128
        raise AssertionError("invalid MQTT length")

    def run(self):
        generation = 0
        while not self.stop.is_set():
            try:
                raw, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            generation += 1
            try:
                raw.settimeout(3)
                with raw, self.context.wrap_socket(raw, server_side=True) as stream:
                    stream.settimeout(.1)
                    while not self.stop.is_set():
                        header, body = self.read_packet(stream)
                        self.records.append((generation, header, body))
                        if header == 0x10:
                            stream.sendall(b"\x20\x02\x00\x00")
                        elif header == 0x82:
                            stream.sendall(packet(0x90, body[:2] + b"\x00" * len(SUBSCRIPTIONS)))
                            stream.sendall(packet(0x39, field("frigate/camera_activity") + b'{"den_oak":{"objects":[]}}'))
                        elif header == 0xC0:
                            stream.sendall(b"\xD0\x00")
                        elif header == 0xE0:
                            break
            except (EOFError, OSError, ssl.SSLError):
                # Abrupt close is expected on stop; bad trust aborts handshake.
                self.records.append((generation, "closed", b""))
            except Exception as exc:
                self.errors.append(repr(exc))


@unittest.skipUnless(PAHO_AVAILABLE, "requires isolated hash-pinned paho-mqtt 2.1.0")
class ActualPahoTests(unittest.TestCase):
    def transport(self, server, *, ca="ca.pem"):
        class LocalClient(paho.Client):
            def connect(client, host, port=1883, keepalive=60, **kwargs):
                # Only test port remapping. TLS still validates 'localhost'
                # against the production trust context and real certificate.
                self.assertEqual((host, port), ("localhost", 8883))
                return super().connect(host, server.port, keepalive, **kwargs)
        module = SimpleNamespace(Client=LocalClient, CallbackAPIVersion=paho.CallbackAPIVersion,
                                 MQTTv311=paho.MQTTv311)
        config = MqttConfig("localhost", str(FIXTURES / ca), "fixture-user", "fixture-password")
        adapter = VictoriaAdapter()
        transport = MqttTransport(config, will=FrigateWire(adapter).last_will(), mqtt_module=module)
        self.addCleanup(self.close_transport, transport)
        return transport

    @staticmethod
    def close_transport(transport):
        transport.stop()
        if transport._thread:
            transport._thread.join(2)

    def wait_for(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail("local MQTT fixture condition timed out")

    def ready(self, transport):
        events = []
        def subscribed():
            events.extend(transport.drain())
            return any(e.kind == "subscribed" and e.payload["success"] for e in events)
        self.wait_for(subscribed)
        return events

    def test_real_connect_subscribe_incoming_flags_and_publication(self):
        with LocalMqttTLS() as server:
            transport = self.transport(server)
            transport.start()
            events = self.ready(transport)
            def received():
                events.extend(transport.drain())
                return any(e.kind == "message" for e in events)
            self.wait_for(received)
            message = next(e for e in events if e.kind == "message")
            self.assertTrue(message.payload["retained"])
            self.assertTrue(message.payload["duplicate"])
            self.assertEqual(message.payload["generation"], 1)
            self.assertEqual(message.payload["payload"], b'{"den_oak":{"objects":[]}}')
            connect = next(body for _, header, body in server.records if header == 0x10)
            protocol, cursor = take_field(connect, 0)
            self.assertEqual(protocol, b"MQTT")
            self.assertEqual(connect[cursor], 4)
            flags = connect[cursor + 1]
            self.assertEqual(flags, 0xC6)  # username/password, will QoS0, clean session; no will retain.
            cursor += 4
            values = []
            for _ in range(5):
                value, cursor = take_field(connect, cursor)
                values.append(value)
            self.assertEqual(values[0], b"victoria-den-adapter")
            self.assertEqual(values[1], AVAILABILITY.encode())
            self.assertFalse(json.loads(values[2])["online"])
            self.assertEqual(values[3:], [b"fixture-user", b"fixture-password"])
            subscribe = next(body for _, header, body in server.records if header == 0x82)
            cursor, topics = 2, []
            while cursor < len(subscribe):
                topic, cursor = take_field(subscribe, cursor)
                self.assertEqual(subscribe[cursor], 0)
                cursor += 1
                topics.append(topic.decode())
            self.assertEqual(tuple(topics), SUBSCRIPTIONS)
            transport.publish("frigate/onConnect", "")
            self.wait_for(lambda: any(header == 0x30 for _, header, _ in server.records))
            output = next(body for _, header, body in server.records if header == 0x30)
            topic, cursor = take_field(output, 0)
            self.assertEqual((topic, output[cursor:]), (b"frigate/onConnect", b""))
            self.close_transport(transport)
            self.wait_for(lambda: any(header == "closed" for _, header, _ in server.records))
            self.assertFalse(any(header == 0xE0 for _, header, _ in server.records))

    def test_real_restart_discards_old_connection_and_offline_publications(self):
        with LocalMqttTLS() as server:
            transport = self.transport(server)
            transport.start()
            self.ready(transport)
            transport.publish("frigate/onConnect", "")
            self.wait_for(lambda: any(header == 0x30 for _, header, _ in server.records))
            old_client = transport._client
            self.close_transport(transport)
            with self.assertRaises(MqttUnavailable): transport.publish("frigate/onConnect", "")
            transport.start()
            events = self.ready(transport)
            self.assertIsNot(old_client, transport._client)
            self.assertTrue(all(e.payload["generation"] == 2 for e in events))
            # Roundtrip marker establishes that the new connection is processing.
            body = json.dumps({"site_id": "victoria", "camera_id": "den_oak", "online": False})
            transport.publish(AVAILABILITY, body)
            self.wait_for(lambda: any(gen == 2 and header == 0x30 for gen, header, _ in server.records))
            topics = [take_field(body, 0)[0] for gen, header, body in server.records if gen == 2 and header == 0x30]
            self.assertEqual(topics, [AVAILABILITY.encode()])
            self.close_transport(transport)

    def test_real_tls_rejects_untrusted_server(self):
        with LocalMqttTLS() as server:
            transport = self.transport(server, ca="client.pem")
            transport.start()
            events = []
            def failed():
                events.extend(transport.drain())
                return any(e.kind == "disconnected" for e in events)
            self.wait_for(failed)
            self.assertFalse(any(e.kind == "connected" for e in events))
            self.assertFalse(any(header == 0x10 for _, header, _ in server.records))
            self.close_transport(transport)

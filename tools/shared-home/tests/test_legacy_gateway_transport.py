"""Two real loopback HTTP hops, actual admission/transport, synthetic authorities."""
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from home_shared.admission import AdmissionController, AdmissionLedger
from home_shared.gateway import Gateway, GatewayHTTPServer
from home_shared.legacy_chat import OpenAIChatBackend
from test_admission import FakeVerifier
from test_gateway import binding_fixture
from test_legacy_chat import POLICY, manifest, payload, sse
from test_openai_backend import model_server, SyntheticInputAuthority, SyntheticAttestor, response


class IntegratedChatTests(unittest.TestCase):
    def run_case(self, upstream, stream, expected_status, expected_body):
        with model_server(upstream) as (port, calls), tempfile.TemporaryDirectory() as directory:
            with AdmissionLedger(Path(directory)/'ledger.sqlite') as ledger:
                subject = manifest(port)
                controller = AdmissionController(ledger, subject, FakeVerifier())
                controller.enable('fixture-epoch')
                backend = OpenAIChatBackend(controller, 'fixture-epoch', policy=POLICY,
                                           attestor=SyntheticAttestor(subject),
                                           input_authority=SyntheticInputAuthority(subject))
                gateway = Gateway(controller, legacy_backend=backend)
                with GatewayHTTPServer(('127.0.0.1', 0), gateway,
                                       {hashlib.sha256(b'fixture-token').hexdigest(): binding_fixture('echo')}) as server:
                    worker = threading.Thread(target=server.serve_forever, daemon=True)
                    worker.start()
                    try:
                        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=4)
                        try:
                            client.request('POST', '/v1/chat/completions', json.dumps(payload(stream)),
                                           {'Authorization': 'Bearer fixture-token', 'Idempotency-Key': 'joined-1'})
                            result = client.getresponse()
                            self.assertEqual(result.status, expected_status)
                            self.assertEqual(result.read(), expected_body)
                        finally:
                            client.close()
                    finally:
                        server.shutdown()
                        worker.join(timeout=2)
                        self.assertFalse(worker.is_alive())
                self.assertEqual(len(calls), 1)
                rows = ledger.db.execute('SELECT state,reservation FROM requests').fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(tuple(rows[0]), ('terminal', '{}'))

    def test_exact_json_gateway_to_actual_transport(self):
        body = b'{"choices": [{"message":{"content":"hello"}}]}'
        upstream = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body
        self.run_case(upstream, False, 200, body)

    def test_chunked_tool_stream_across_both_http_hops(self):
        events = [b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"light"}}]}}]}\n\n',
                  b'data: [DONE]\n\n']
        self.run_case(sse(events, True), True, 200, b''.join(events))

    def test_stream_request_preserves_attested_json_error(self):
        body = b'{"error":{"message":"rejected"}}'
        upstream = b'HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body
        self.run_case(upstream, True, 400, body)


if __name__ == '__main__':
    unittest.main()

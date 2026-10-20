import base64
import http.client
import io
import json
import socket
import threading
import unittest
from unittest import mock
from analysis import NotDispatched
from qwen_transport import (CompletionUnproven, KEEP_ALIVE_IDLE_SECONDS, LOOKUP_PATH, QwenNotExecuted, QwenTransport,
                            SUBMIT_PATH, load_config)
from reasoning import digest

CONFIG = {'hostname': 'la-lab.example', 'port': 9447, 'ca_file': '/fixture/ca.pem',
          'client_certificate': '/fixture/client.pem', 'client_key': '/fixture/client.key',
          'deadline_seconds': 15, 'response_grace_seconds': 5, 'poll_seconds': .5}


def jpeg():
    from PIL import Image
    encoded = io.BytesIO()
    Image.new('RGB', (32, 18), (1, 2, 3)).save(encoded, format='JPEG')
    return encoded.getvalue()


class Response:
    """http.client.HTTPResponse as Python 3.10 behaves: read1() never marks a
    fully read Content-Length body complete; read() drains and releases it."""

    def __init__(self, status, value, content_type='application/json', will_close=True):
        self.status = status
        self._body = io.BytesIO(value if isinstance(value, bytes) else json.dumps(value).encode())
        self.headers = {'Content-Type': content_type}
        self.will_close, self.released = will_close, False

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read1(self, size):
        return self._body.read(size)

    def read(self, size=-1):
        data = self._body.read(size)
        self.released = self._body.tell() == len(self._body.getvalue())
        return data

    def isclosed(self):
        return self.released


class Server:
    """Scripted consumer: each connection pops one (status, value) reply."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []
        self.refuse = False
        self.partial = False

    def factory(self, timeout):
        server = self

        class Connection:
            sock = None

            def connect(inner):
                if server.refuse:
                    raise ConnectionRefusedError()

            def request(inner, method, path, body=None, headers=None):
                server.requests.append((method, path, json.loads(body), headers))
                if server.partial:
                    raise BrokenPipeError()

            def getresponse(inner):
                status, value = server.replies.pop(0)
                return Response(status, value(server.requests[-1][2]) if callable(value) else value)

            def close(inner):
                pass
        return Connection()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def sleep(self, seconds):
        self.now += seconds


class KeepAliveServer:
    """Scripted consumer over kept-alive connections, with http.client's reuse
    rules: each connection has a real socket (so a peer close or a stray byte
    makes it readable), and a request on a connection whose previous response
    was never released is sent but its response cannot be read
    (ResponseNotReady), as with Python 3.10's read1(). Each request pops one
    reply: (status, value), (status, value, 'close') or an exception raised
    after the request bytes were sent."""

    def __init__(self, *replies):
        self.replies, self.requests, self.connections = list(replies), [], []
        self.refuse = False

    def close(self):
        for connection in self.connections:
            connection.close()

    def factory(self, timeout):
        server = self

        class Connection:
            def __init__(inner):
                inner.sock = inner.peer = inner.response = None
                inner.connects, inner.sent = 0, []
                server.connections.append(inner)

            def connect(inner):
                if server.refuse:
                    raise ConnectionRefusedError()
                inner.connects += 1
                inner.sock, inner.peer = socket.socketpair()

            def request(inner, method, path, body=None, headers=None):
                if inner.sock is None:
                    raise http.client.NotConnected()  # never a hidden reconnect
                inner.sent.append(path)
                server.requests.append((method, path, json.loads(body), headers))

            def getresponse(inner):
                if inner.response is not None and not inner.response.isclosed():
                    raise http.client.ResponseNotReady('Idle')
                reply = server.replies.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                status, value = reply[:2]
                closing = reply[2:] == ('close',)
                value = value(server.requests[-1][2]) if callable(value) else value
                inner.response = Response(status, value, will_close=closing)
                if closing:  # http.client hands the socket to the response
                    inner.close()
                return inner.response

            def close(inner):
                for end in (inner.sock, inner.peer):
                    if end is not None:
                        end.close()
                inner.sock = inner.peer = None
        return Connection()


def read1_python310(self, n=-1):
    """HTTPResponse.read1 as shipped in Python 3.10 (the Jetson): a fully read
    Content-Length body is never marked complete."""
    if self.fp is None or self._method == 'HEAD':
        return b''
    if self.chunked:
        return self._read1_chunked(n)
    if self.length is not None and (n < 0 or n > self.length):
        n = self.length
    result = self.fp.read1(n)
    if not result and n:
        self._close_conn()
    elif self.length is not None:
        self.length -= len(result)
    return result


class QwenTransportTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        image = 'data:image/jpeg;base64,' + base64.b64encode(jpeg()).decode()
        body = {'model': 'qwen3-vl-30b', 'max_tokens': 256, 'stream': False, 'temperature': 0,
                'messages': [{'role': 'system', 'content': 'fixed'},
                             {'role': 'user', 'content': [{'type': 'text', 'text': '{}'}, {'type': 'text', 'text': '{}'},
                                                          {'type': 'image_url', 'image_url': {'url': image}}]}]}
        self.envelope = {'request_id': 'e' * 64, 'site_id': 'victoria', 'session_id': 'f' * 32, 'profile': {'engine_epoch': 'epoch1'},
                         'body': body, 'body_sha256': digest(body), 'prompt_version': 'lab-qwen-cutoff-2',
                         'task': 'lab_saved_activity', 'deadline': self.clock.now + 15}
        self.reference = {k: v for k, v in self.envelope.items() if k != 'body'}

    def proof(self, state, **extra):
        terminal = state in {'completed', 'failed', 'rejected', 'cancelled', 'queue_expired', 'not_admitted'}
        return {'version': 1, 'request_id': self.envelope['request_id'], 'body_sha256': self.envelope['body_sha256'],
                'terminal': terminal, 'state': state, 'engine_epoch': 'epoch1', 'artifact_sha256': 'a' * 64,
                'reason': None, **extra}

    def transport(self, server, **config):
        return QwenTransport(load_config({**CONFIG, **config}, check_files=False), connection_factory=server.factory,
                             clock=lambda: self.clock.now, monotonic=lambda: self.clock.now, sleep=self.clock.sleep)

    def test_provisioning_is_exact_and_bounded(self):
        self.assertEqual(load_config(CONFIG, check_files=False)['deadline_seconds'], 15)
        self.assertIn('token_file', load_config({**CONFIG, 'token_file': '/fixture/token'}, check_files=False))
        for keep_alive in (True, False):
            self.assertIs(load_config({**CONFIG, 'keep_alive': keep_alive}, check_files=False)['keep_alive'], keep_alive)
        self.assertTrue(self.transport(Server()).keep_alive)  # the default
        for changed in ({'deadline_seconds': 30}, {'deadline_seconds': 2}, {'hostname': 'la lab'}, {'port': 0},
                        {'poll_seconds': 0}, {'extra': 1}, {'response_grace_seconds': 600}, {'keep_alive': 'yes'},
                        {'keep_alive': 1}):
            with self.assertRaises(ValueError):
                load_config({**CONFIG, **changed}, check_files=False)
        missing = dict(CONFIG)
        del missing['client_key']
        with self.assertRaises(ValueError):
            load_config(missing, check_files=False)

    def test_submits_once_then_polls_identity_until_terminal_proof(self):
        answer = {'id': 'chatcmpl-1', 'model': 'qwen3-vl-30b', 'choices': [{'index': 0, 'finish_reason': 'stop',
                  'message': {'role': 'assistant', 'content': '{}'}}]}
        server = Server((202, self.proof('queued')), (200, self.proof('running')),
                        (200, self.proof('completed', response=answer, usage={'prompt_tokens': 9, 'completion_tokens': 3})))
        proof = self.transport(server)(self.envelope)
        self.assertEqual((proof['state'], proof['terminal'], proof['usage']), ('completed', True, {'prompt_tokens': 9, 'completion_tokens': 3}))
        paths = [r[1] for r in server.requests]
        self.assertEqual(paths, [SUBMIT_PATH, LOOKUP_PATH, LOOKUP_PATH])
        self.assertEqual(server.requests[0][2], self.envelope)
        for _, _, sent, headers in server.requests[1:]:
            self.assertEqual(sent, self.reference)
            self.assertNotIn('data:image', json.dumps(sent))
            self.assertNotIn('Authorization', headers)

    def test_only_exact_pre_admission_refusals_prove_non_execution(self):
        for reason in ('profile_mismatch', 'labels_rejected', 'input_bound_exceeded', 'invalid_request'):
            with self.assertRaises(QwenNotExecuted) as refused:
                self.transport(Server((400, {'reason': reason})))(self.envelope)
            self.assertEqual(refused.exception.reason, reason)
            self.assertIsInstance(refused.exception, NotDispatched)
        for status, value in ((400, {'reason': 'something_else'}), (400, {'reason': 'invalid_request', 'extra': 1}),
                              (503, {'reason': 'admission_unavailable'}), (500, {'reason': 'consumer_error'}),
                              (409, {'reason': 'request_conflict'}), (200, b'not json'), (202, {'surprise': True})):
            self.clock.now = 1000.0
            server = Server((status, value))
            with self.assertRaises(CompletionUnproven):
                self.transport(server)(self.envelope)
            # Unproven submissions are recovered by identity only, never resent.
            self.assertEqual([r[1] for r in server.requests][:2], [SUBMIT_PATH, LOOKUP_PATH])
            self.assertEqual(sum(1 for r in server.requests if r[1] == SUBMIT_PATH), 1)

    def test_connection_failure_before_sending_is_proof_but_not_for_lookup(self):
        server = Server()
        server.refuse = True
        with self.assertRaises(QwenNotExecuted) as refused:
            self.transport(server)(self.envelope)
        self.assertEqual(refused.exception.reason, 'transport_not_connected')
        with self.assertRaises(CompletionUnproven):
            self.transport(server).lookup(self.reference)

    def test_partial_upload_counts_only_an_exact_refusal(self):
        server = Server((413, {'reason': 'body_too_large'}))
        server.partial = True
        with self.assertRaises(QwenNotExecuted):
            self.transport(server)(self.envelope)
        server = Server((202, self.proof('queued')))
        server.partial = True
        with self.assertRaises(CompletionUnproven):
            self.transport(server)(self.envelope)

    def test_invalid_envelopes_never_connect(self):
        server = Server()
        cases = [({**self.envelope, 'body_sha256': '0' * 64}, 'envelope_invalid'),
                 ({**self.envelope, 'deadline': self.clock.now + 60}, 'envelope_invalid'),
                 ({**self.envelope, 'extra': 1}, 'envelope_invalid')]
        body = json.loads(json.dumps(self.envelope['body']))
        parts = body['messages'][1]['content']
        body['messages'][1]['content'] = parts + parts[1:] * 4
        cases.append(({**self.envelope, 'body': body, 'body_sha256': digest(body)}, 'envelope_images_invalid'))
        png = json.loads(json.dumps(self.envelope['body']))
        png['messages'][1]['content'][2]['image_url']['url'] = 'data:image/png;base64,iVBORw0KGgo='
        cases.append(({**self.envelope, 'body': png, 'body_sha256': digest(png)}, 'envelope_images_invalid'))
        for envelope, reason in cases:
            with self.assertRaises(QwenNotExecuted) as refused:
                self.transport(server)(envelope)
            self.assertEqual(refused.exception.reason, reason)
        self.assertEqual(server.requests, [])

    def test_mismatched_or_inconsistent_proofs_are_unproven(self):
        for proof in (self.proof('queued', body_sha256='0' * 64), {**self.proof('running'), 'terminal': True},
                      self.proof('completed', response='text'), self.proof('queued', usage={'prompt_tokens': 1}),
                      {**self.proof('queued'), 'engine_epoch': 'bad epoch!'}, {**self.proof('queued'), 'version': 2}):
            self.clock.now = 1000.0
            with self.assertRaises(CompletionUnproven):
                self.transport(Server((202, proof)))(self.envelope)

    def test_polling_is_bounded_by_deadline_plus_grace_and_survives_lookup_failures(self):
        replies = [(202, self.proof('queued'))] + [(200, self.proof('running')), (503, {'reason': 'admission_unavailable'})] * 40
        server = Server(*replies)
        with self.assertRaises(CompletionUnproven):
            self.transport(server)(self.envelope)
        lookups = [r for r in server.requests if r[1] == LOOKUP_PATH]
        # 20 s of polling at 0.5 s: bounded, identity-only, never a resubmission.
        self.assertLessEqual(len(lookups), 41)
        self.assertEqual(sum(1 for r in server.requests if r[1] == SUBMIT_PATH), 1)
        self.assertLessEqual(self.clock.now, self.envelope['deadline'] + CONFIG['response_grace_seconds'] + .5)

    def test_lost_submission_response_is_recovered_by_identity_lookup(self):
        answer = {'id': 'chatcmpl-1', 'model': 'qwen3-vl-30b', 'choices': [{'index': 0, 'finish_reason': 'stop',
                  'message': {'role': 'assistant', 'content': '{}'}}]}
        for lost in ((200, b'truncated'), (503, {'reason': 'admission_unavailable'})):
            self.clock.now = 1000.0
            server = Server(lost, (200, self.proof('not_found')), (200, self.proof('running')),
                            (200, self.proof('completed', response=answer)))
            proof = self.transport(server)(self.envelope)
            self.assertEqual(proof['state'], 'completed')
            self.assertEqual([r[1] for r in server.requests], [SUBMIT_PATH, LOOKUP_PATH, LOOKUP_PATH, LOOKUP_PATH])
            for _, _, sent, _ in server.requests[1:]:
                self.assertEqual(sent, self.reference)

    def test_closed_transport_stops_polling_and_refuses_new_operations(self):
        server = Server((202, self.proof('queued')), *[(200, self.proof('running'))] * 5)
        transport = self.transport(server)
        polls = []
        def sleep(seconds):
            polls.append(seconds)
            transport.close()  # a controller shutdown while the step is polled
        transport._sleep = sleep
        with self.assertRaises(CompletionUnproven):
            transport(self.envelope)
        self.assertEqual(len(polls), 1)
        self.assertEqual([r[1] for r in server.requests], [SUBMIT_PATH])
        with self.assertRaises(QwenNotExecuted) as refused:
            transport.submit(self.envelope)
        self.assertEqual(refused.exception.reason, 'transport_closed')
        with self.assertRaises(CompletionUnproven):
            transport.lookup(self.reference)
        self.assertEqual(len(server.requests), 1)

    def test_one_operation_at_a_time(self):
        transport = self.transport(Server())
        self.assertTrue(transport._inflight.acquire(blocking=False))
        try:
            with self.assertRaises(QwenNotExecuted) as refused:
                transport(self.envelope)
            self.assertEqual(refused.exception.reason, 'transport_busy')
            with self.assertRaises(CompletionUnproven):
                transport.lookup(self.reference)
        finally:
            transport._inflight.release()

    # --- keep-alive (speed plan S1.5) -------------------------------------
    def kept(self, *replies, **config):
        server = KeepAliveServer(*replies)
        self.addCleanup(server.close)
        return server, self.transport(server, **config)

    def answer(self):
        return {'id': 'chatcmpl-1', 'model': 'qwen3-vl-30b', 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': '{}'}}]}

    def test_one_connection_carries_the_submission_and_its_lookups(self):
        server, transport = self.kept((202, self.proof('queued')), (200, self.proof('running')),
                                      (200, self.proof('completed', response=self.answer())))
        self.assertEqual(transport(self.envelope)['state'], 'completed')
        [connection] = server.connections
        self.assertEqual((connection.connects, connection.sent), (1, [SUBMIT_PATH, LOOKUP_PATH, LOOKUP_PATH]))
        for _, _, _, headers in server.requests:
            self.assertNotIn('Connection', headers)
        self.assertIs(transport._kept, connection)
        # A later operation (the next tick's epoch probe) reuses it too.
        self.clock.now += 4
        server.replies.append((200, self.proof('completed', response=self.answer())))
        self.assertEqual(transport.lookup(self.reference)['state'], 'completed')
        self.assertEqual((len(server.connections), connection.connects), (1, 1))

    def test_every_response_is_released_before_the_connection_is_reused(self):
        # The fake refuses to read a response while the previous one is
        # unreleased, as http.client does on Python 3.10 after read1().
        server, transport = self.kept(*[(200, self.proof('running'))] * 3)
        for _ in range(3):
            self.assertEqual(transport.lookup(self.reference)['state'], 'running')
            self.assertTrue(server.connections[0].response.isclosed())
        self.assertEqual((len(server.connections), server.connections[0].sent), (1, [LOOKUP_PATH] * 3))

    def test_an_idle_connection_is_replaced_before_a_send(self):
        server, transport = self.kept(*[(200, self.proof('running'))] * 3)
        transport.lookup(self.reference)
        self.clock.now += KEEP_ALIVE_IDLE_SECONDS  # idle exactly 15 s: still used
        transport.lookup(self.reference)
        self.assertEqual(len(server.connections), 1)
        self.clock.now += KEEP_ALIVE_IDLE_SECONDS + .01  # more than 15 s: replaced, never sent on
        transport.lookup(self.reference)
        first, second = server.connections
        self.assertEqual((first.sent, first.sock, second.sent), ([LOOKUP_PATH] * 2, None, [LOOKUP_PATH]))

    def test_a_readable_socket_is_replaced_before_a_send(self):
        for event in ('closed', 'stray_byte'):
            with self.subTest(event=event):
                server, transport = self.kept(*[(200, self.proof('running'))] * 2)
                transport.lookup(self.reference)
                first = server.connections[0]
                if event == 'closed':
                    first.peer.close()  # LA closed it (idle timeout or its request cap)
                    first.peer = None
                else:
                    first.peer.sendall(b'x')
                transport.lookup(self.reference)
                self.assertEqual(len(server.connections), 2)
                self.assertEqual((first.sent, first.sock), ([LOOKUP_PATH], None))
                self.assertEqual(server.connections[1].sent, [LOOKUP_PATH])

    def test_a_connection_close_reply_is_honoured(self):
        server, transport = self.kept((200, self.proof('running'), 'close'), (200, self.proof('running')),
                                      (200, self.proof('running')))
        for _ in range(3):
            transport.lookup(self.reference)
        self.assertEqual([c.sent for c in server.connections], [[LOOKUP_PATH], [LOOKUP_PATH, LOOKUP_PATH]])

    def test_a_failed_fresh_connect_still_proves_the_submission_was_not_sent(self):
        server, transport = self.kept((200, self.proof('running')))
        transport.lookup(self.reference)
        self.clock.now += KEEP_ALIVE_IDLE_SECONDS + 1  # the kept one is dropped, a fresh one fails
        server.refuse = True
        with self.assertRaises(QwenNotExecuted) as refused:
            transport.submit({**self.envelope, 'deadline': self.clock.now + 15})
        self.assertEqual(refused.exception.reason, 'transport_not_connected')
        self.assertEqual([r[1] for r in server.requests], [LOOKUP_PATH])  # nothing more was sent
        with self.assertRaises(CompletionUnproven):
            transport.lookup(self.reference)

    def test_a_failure_after_sending_on_a_reused_socket_is_unproven_and_recovered_by_identity(self):
        for error in (ConnectionResetError(), http.client.RemoteDisconnected('closed'), TimeoutError()):
            with self.subTest(error=type(error).__name__):
                self.clock.now = 1000.0
                server, transport = self.kept((200, self.proof('not_found')), error)
                transport.lookup(self.reference)  # the engine-epoch probe leaves a kept connection
                with self.assertRaises(CompletionUnproven):
                    transport.submit(self.envelope)
                first = server.connections[0]
                self.assertEqual((first.sent, first.sock, transport._kept), ([LOOKUP_PATH, SUBMIT_PATH], None, None))
                # Recovery is the identity lookup on a fresh connection, never a resubmission.
                server.replies.append((200, self.proof('completed', response=self.answer())))
                self.assertEqual(transport.lookup(self.reference)['state'], 'completed')
                self.assertEqual(server.connections[1].sent, [LOOKUP_PATH])
                self.assertEqual(sum(1 for r in server.requests if r[1] == SUBMIT_PATH), 1)

    def test_a_lost_reply_on_a_reused_socket_is_polled_by_identity_within_the_call(self):
        server, transport = self.kept((200, self.proof('not_found')), ConnectionResetError(),
                                      (200, self.proof('completed', response=self.answer())))
        transport.lookup(self.reference)
        self.assertEqual(transport(self.envelope)['state'], 'completed')
        self.assertEqual([c.sent for c in server.connections], [[LOOKUP_PATH, SUBMIT_PATH], [LOOKUP_PATH]])

    def test_errors_and_typed_replies_on_kept_connections(self):
        # Any transport error closes the connection; an exact refusal is a clean
        # exchange and keeps it.
        server, transport = self.kept((503, {'reason': 'admission_unavailable'}), (200, b'not json'),
                                      (200, self.proof('running')))
        with self.assertRaises(CompletionUnproven):
            transport.lookup(self.reference)  # a clean exchange, an unproven status
        self.assertIsNotNone(transport._kept)
        with self.assertRaises(CompletionUnproven):
            transport.lookup(self.reference)  # malformed body: closed
        self.assertIsNone(transport._kept)
        transport.lookup(self.reference)
        self.assertEqual([c.sent for c in server.connections], [[LOOKUP_PATH, LOOKUP_PATH], [LOOKUP_PATH]])

    def test_keep_alive_off_restores_one_connection_per_request(self):
        server, transport = self.kept(*[(200, self.proof('running'))] * 2, keep_alive=False)
        transport.lookup(self.reference)
        transport.lookup(self.reference)
        self.assertEqual([c.sent for c in server.connections], [[LOOKUP_PATH], [LOOKUP_PATH]])
        self.assertEqual([r[3]['Connection'] for r in server.requests], ['close', 'close'])
        self.assertEqual([c.sock for c in server.connections], [None, None])

    def test_close_closes_the_kept_connection_and_an_inflight_one_after_it_ends(self):
        server, transport = self.kept((200, self.proof('running')))
        transport.lookup(self.reference)
        transport.close()
        self.assertEqual((server.connections[0].sock, transport._kept), (None, None))
        with self.assertRaises(QwenNotExecuted):
            transport.submit(self.envelope)
        # Closed while an operation is in flight: that one ends, then closes.
        server, transport = self.kept((200, self.proof('running')))
        reply = server.replies.pop(0)

        def answer_after_close(reference):
            transport.close()
            return reply[1]
        server.replies.append((200, answer_after_close))
        self.assertEqual(transport.lookup(self.reference)['state'], 'running')
        self.assertEqual((server.connections[0].sock, transport._kept), (None, None))


class KeepAliveRuleTests(unittest.TestCase):
    def test_reuse_rules_never_raise_and_doubt_means_reconnect(self):
        from qwen_transport import kept_connection_unusable, response_released
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        kept = type('Kept', (), {'sock': left})()
        self.assertFalse(kept_connection_unusable(kept, 100.0, 115.0))
        for last_used, now in ((100.0, 115.5), (None, 100.0), (100.0, 99.0), (float('nan'), 100.0)):
            self.assertTrue(kept_connection_unusable(kept, last_used, now))
        self.assertTrue(kept_connection_unusable(type('Odd', (), {'sock': object()})(), 100.0, 100.0))
        self.assertTrue(kept_connection_unusable(object(), 100.0, 100.0))
        released = Response(200, {}, will_close=False)
        released.read()
        self.assertTrue(response_released(kept, released))
        closing = Response(200, {})  # Connection: close, read to its end
        closing.read()
        for response in (Response(200, {}, will_close=False), closing, object()):  # unread, closing, unknown
            self.assertFalse(response_released(kept, response))
        self.assertFalse(response_released(object(), released))


class RealKeepAliveTests(unittest.TestCase):
    """Real http.client over loopback, with Python 3.10's read1() patched in."""

    def setUp(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.peers, self.clock = [], Clock()
        test = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            close_after = False

            def setup(inner):
                super().setup()
                test.peers.append(inner.client_address)

            def do_POST(inner):
                reference = json.loads(inner.rfile.read(int(inner.headers['Content-Length'])))
                body = json.dumps({'version': 1, 'request_id': reference['request_id'],
                                   'body_sha256': reference['body_sha256'], 'terminal': False, 'state': 'running',
                                   'engine_epoch': 'epoch1', 'artifact_sha256': 'a' * 64, 'reason': None}).encode()
                inner.send_response(200)
                inner.send_header('Content-Type', 'application/json')
                inner.send_header('Content-Length', str(len(body)))
                if Handler.close_after:
                    inner.send_header('Connection', 'close')
                inner.end_headers()
                inner.wfile.write(body)

            def log_message(inner, *args):
                pass
        self.handler = Handler
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        port = self.server.server_address[1]
        self.transport = QwenTransport(
            load_config(CONFIG, check_files=False), clock=lambda: self.clock.now, monotonic=lambda: self.clock.now,
            connection_factory=lambda timeout: http.client.HTTPConnection('127.0.0.1', port, timeout=timeout))
        self.addCleanup(self.transport.close)
        self.reference = {'request_id': 'e' * 64, 'site_id': 'victoria', 'session_id': 'f' * 32,
                          'profile': {'engine_epoch': 'epoch1'}, 'body_sha256': 'b' * 64,
                          'prompt_version': 'lab-qwen-cutoff-2', 'task': 'lab_saved_activity', 'deadline': 1015.0}

    def test_python_310_reads_reuse_one_tcp_connection(self):
        with mock.patch.object(http.client.HTTPResponse, 'read1', read1_python310):
            for _ in range(3):
                self.assertEqual(self.transport.lookup(self.reference)['state'], 'running')
            self.assertEqual(len(self.peers), 1)
            self.clock.now += KEEP_ALIVE_IDLE_SECONDS + 1  # idle: reconnect before the send
            self.transport.lookup(self.reference)
            self.assertEqual(len(self.peers), 2)
            self.handler.close_after = True  # LA's last request on a connection
            self.transport.lookup(self.reference)
            self.transport.lookup(self.reference)
            self.assertEqual(len(self.peers), 3)
            self.assertIsNone(self.transport._kept)


if __name__ == '__main__':
    unittest.main()

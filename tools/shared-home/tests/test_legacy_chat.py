"""Actual loopback JSON/SSE transport with synthetic admission authorities."""
from contextlib import contextmanager
from dataclasses import replace
import json
import base64
import io
import threading
from PIL import Image
from pathlib import Path
import tempfile
import time
import unittest

from home_shared.admission import AdmissionController, AdmissionLedger, ResourceManifest
from home_shared.legacy_chat import LegacyChatPolicy, OpenAIChatBackend, prepare_chat_request
from home_shared.openai_backend import BackendUnknown, BackendBusy, BackendDeadline
from test_admission import FakeVerifier, manifest_fixture
from test_openai_backend import model_server, SyntheticInputAuthority, SyntheticAttestor, response

POLICY = LegacyChatPolicy(1024*1024, 4, 1024*1024, 1024*1024, 65536,
                          frozenset({'tools','tool_choice','response_format'}))


def manifest(port):
    value=manifest_fixture()
    value.update(version=3, upstream=f'http://127.0.0.1:{port}/v1',
        consumer_deadline_seconds={'fake-echo':15,'fake-victoria':15},
        consumer_policy_sha256={'fake-echo':POLICY.digest,'fake-victoria':'f'*64})
    return ResourceManifest.from_dict(value)


def payload(stream=False):
    return {'model':'fake-model', 'messages':[{'role':'user','content':'Hello'}],
            'max_tokens':32, 'stream':stream,
            'tools':[{'type':'function','function':{'name':'light','parameters':{'type':'object'}}}]}


@contextmanager
def setup(port, stream=False, seconds=5):
    subject = manifest(port)
    with tempfile.TemporaryDirectory() as directory, AdmissionLedger(Path(directory)/'ledger.sqlite') as ledger:
        controller = AdmissionController(ledger, subject, FakeVerifier())
        controller.enable('fixture-epoch')
        attestor = SyntheticAttestor(subject)
        backend = OpenAIChatBackend(controller, 'fixture-epoch', policy=POLICY,
                                   attestor=attestor, input_authority=SyntheticInputAuthority(subject))
        request = prepare_chat_request(payload(stream), consumer_id='fake-echo', request_id='one',
                                       deadline=time.time()+seconds, manifest=subject, policy=POLICY)
        controller.submit(request.ledger_id, request.site_id, request.consumer_id, request.digest,
                          request.task_class, request.request_deadline)
        controller.claim_next()
        controller.mark_running(request.ledger_id)
        yield backend, request, controller, attestor


def sse(events, chunked=False):
    body = b''.join(events)
    framing = b'Transfer-Encoding: chunked\r\n' if chunked else f'Content-Length: {len(body)}\r\n'.encode()
    if chunked:
        body = b''.join(f'{len(e):x}\r\n'.encode()+e+b'\r\n' for e in events)+b'0\r\n\r\n'
    return b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n'+framing+b'\r\n'+body


class LegacyChatTests(unittest.TestCase):
    def test_json_tools_round_trip_and_only_verifier_releases(self):
        with model_server() as (port,calls), setup(port) as (backend,request,controller,attestor):
            result=backend.execute(request,request.request_deadline)
            self.assertEqual(result.status,200)
            self.assertEqual(calls[0][1]['tools'],payload()['tools'])
            self.assertEqual(controller.ledger.get(request.ledger_id).state,'running')
            controller.finish(request.ledger_id,result.terminal_evidence)
            self.assertEqual(controller.ledger.get(request.ledger_id).reservation,{})

    def test_chunked_sse_exact_tool_delta_preserved(self):
        events=[b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\\"x\\\":"}}]}}]}\n\n', b'data: [DONE]\n\n']
        with model_server(sse(events,True)) as (port,calls), setup(port,True) as (backend,request,controller,attestor):
            delivered=[]
            result=backend.execute(request,request.request_deadline,delivered.append)
            self.assertEqual(delivered,events)
            self.assertEqual(result.body,b''.join(events))
            self.assertTrue(controller.ledger.get(request.ledger_id).reservation)

    def test_done_does_not_supply_terminal_authority(self):
        with model_server(sse([b'data: [DONE]\n\n'])) as (port,calls), setup(port,True) as (backend,request,controller,attestor):
            attestor.deny=True
            with self.assertRaises(BackendUnknown):
                backend.execute(request,request.request_deadline)
            self.assertTrue(controller.ledger.get(request.ledger_id).reservation)

    def test_disconnect_partial_sse_remains_charged(self):
        with model_server(sse([b'data: {"choices":[]}\n\n'])) as (port,calls), setup(port,True) as (backend,request,controller,attestor):
            with self.assertRaises(BackendUnknown):
                backend.execute(request,request.request_deadline)
            self.assertFalse(attestor.observations)
            self.assertTrue(controller.ledger.get(request.ledger_id).reservation)

    def test_sink_disconnect_retains_reservation(self):
        with model_server(sse([b'data: [DONE]\n\n'])) as (port,calls), setup(port,True) as (backend,request,controller,attestor):
            def broken(_): raise BrokenPipeError()
            with self.assertRaises(BackendUnknown):
                backend.execute(request,request.request_deadline,broken)
            self.assertTrue(controller.ledger.get(request.ledger_id).reservation)

    def test_error_json_preserved_with_independent_terminal_evidence(self):
        with model_server(response({'error':{'message':'bounded failure'}},status=400)) as (port,calls), setup(port) as (backend,request,controller,attestor):
            result=backend.execute(request,request.request_deadline)
            self.assertEqual(result.status,400)
            self.assertIn(b'bounded failure',result.body)

    def test_token_denial_before_upstream_bytes(self):
        with model_server() as (port,calls), setup(port) as (backend,request,controller,attestor):
            backend._input_authority.transform=lambda value:replace(value,upper_bound_tokens=1001)
            with self.assertRaises(BackendUnknown): backend.execute(request,request.request_deadline)
            self.assertEqual(calls,[])

    def test_unreserved_request_rejected_before_upstream_bytes(self):
        with model_server() as (port,calls), setup(port) as (backend,request,controller,attestor):
            controller.finish(request.ledger_id,('fixture-epoch',request.ledger_id,'finished'))
            with self.assertRaises(BackendUnknown): backend.execute(request,request.request_deadline)
            self.assertEqual(calls,[])

    def test_truncated_content_length_never_attested(self):
        with model_server(response(length=99999)) as (port,calls), setup(port) as (backend,request,controller,attestor):
            with self.assertRaises(BackendUnknown): backend.execute(request,request.request_deadline)
            self.assertFalse(attestor.observations)
            self.assertTrue(controller.ledger.get(request.ledger_id).reservation)

    def test_slow_sink_deadline_retains_worker_and_reservation(self):
        release=threading.Event()
        with model_server(sse([b'data: [DONE]\n\n'])) as (port,calls), setup(port,True,seconds=.15) as (backend,request,controller,attestor):
            try:
                with self.assertRaises(BackendDeadline):
                    backend.execute(request,request.request_deadline,lambda _:release.wait(2))
                self.assertTrue(backend._inflight.locked())
                self.assertTrue(controller.ledger.get(request.ledger_id).reservation)
                self.assertFalse(attestor.observations)
            finally:
                release.set()
                limit=time.monotonic()+1
                while backend._inflight.locked() and time.monotonic()<limit: time.sleep(.005)
                self.assertFalse(backend._inflight.locked())

    def test_inline_multiimage_and_response_format_preserved(self):
        raw=io.BytesIO(); Image.new('RGB',(32,32)).save(raw,format='PNG')
        image={'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(raw.getvalue()).decode()}}
        body=payload(); body['messages'][0]['content']=[image,image]
        body['response_format']={'type':'json_object'}
        subject=manifest(19999)
        request=prepare_chat_request(body,consumer_id='fake-echo',request_id='r',deadline=time.time()+5,manifest=subject,policy=POLICY)
        self.assertEqual(json.loads(request.body),body)
        body['messages'][0]['content']=[image]*5
        with self.assertRaises(ValueError):
            prepare_chat_request(body,consumer_id='fake-echo',request_id='r',deadline=time.time()+5,manifest=subject,policy=POLICY)

    def test_policy_changes_require_new_manifest_binding(self):
        subject=manifest(19999)
        with self.assertRaises(ValueError):
            prepare_chat_request(payload(),consumer_id='fake-echo',request_id='r',deadline=time.time()+5,
                                 manifest=subject,policy=replace(POLICY,max_images=5))

    def test_wrong_site_remote_images_and_unknown_options_rejected(self):
        subject=manifest(19999)
        for change in ({'site_id':'victoria'},{'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'http://localhost/private'}}]}]}):
            body=payload(); body.update(change)
            with self.assertRaises(ValueError): prepare_chat_request(body,consumer_id='fake-echo',request_id='r',deadline=time.time()+5,manifest=subject,policy=POLICY)
        with self.assertRaises(ValueError): prepare_chat_request(payload(),consumer_id='fake-victoria',request_id='r',deadline=time.time()+5,manifest=subject,policy=POLICY)

if __name__=='__main__': unittest.main()

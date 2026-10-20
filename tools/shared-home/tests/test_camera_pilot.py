import tempfile
from pathlib import Path
import threading
import unittest

from home_shared.admission import AdmissionError, AdmissionClosed, CapacityExceeded, RequestConflict
from home_shared.camera_pilot import CameraPilot
from home_shared.camera_pilot import BeforeDispatchRejected
from unittest.mock import patch
from types import SimpleNamespace
import time
import json
from test_gateway import binding_fixture, request_fixture


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now, self.calls = 1000., []
        self.binding = binding_fixture()
        self.path = Path(self.tmp.name)/'pilot.sqlite'
        self.pilot = self.open(lambda req: self.calls.append(req) or 'An empty den.')
        self.addCleanup(lambda: self.pilot.close())

    def open(self, backend):
        return CameraPilot(self.path, binding=self.binding, backend=backend, model_revision='pinned',
                           clock=lambda:self.now, image_validator=lambda _:None, free_space_floor=0)

    def payload(self, rid='first'):
        return {**request_fixture(now=self.now, request_id=rid), 'task_class':'interactive'}

    def test_duplicate_conflict_cooldown_and_restart(self):
        payload = self.payload()
        self.pilot.submit(payload, self.binding)
        self.pilot.worker.join(2)
        self.assertEqual(self.pilot.status('first', self.binding)['observation']['description'], 'An empty den.')
        self.pilot.submit(payload, self.binding)
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(RequestConflict):
            self.pilot.submit({**payload,'frame_id':'changed'}, self.binding)
        self.pilot.close()
        self.pilot = self.open(lambda _: self.fail('restart must not infer'))
        with self.assertRaises(CapacityExceeded):
            self.pilot.submit(self.payload('second'), self.binding)
        self.assertNotIn('observation', self.pilot.status('first', self.binding))

    def test_unknown_survives_deadline_restart_and_duplicate(self):
        self.pilot.backend = lambda _: (_ for _ in ()).throw(TimeoutError())
        payload = self.payload()
        self.pilot.submit(payload, self.binding)
        self.pilot.worker.join(2)
        self.pilot.close()
        self.now += 100
        self.pilot = self.open(lambda _:self.fail('unknown must not retry'))
        self.assertEqual(self.pilot.submit(payload,self.binding)['status'],'indeterminate')
        with self.assertRaises(CapacityExceeded):
            self.pilot.submit(self.payload('second'),self.binding)

    def test_proven_predispatch_failure_is_terminal_without_replay(self):
        self.pilot.backend = lambda _: (_ for _ in ()).throw(BeforeDispatchRejected())
        payload=self.payload()
        self.pilot.submit(payload,self.binding); self.pilot.worker.join(2)
        result=self.pilot.status('first',self.binding)
        self.assertEqual(result['status'],'terminal')
        self.assertEqual(result['outcome'],'preflight_rejected')
        self.assertNotIn('observation',result)
        self.pilot.backend=lambda _:self.fail('duplicate cannot dispatch')
        self.assertEqual(self.pilot.submit(payload,self.binding)['outcome'],'preflight_rejected')
        self.now+=31
        self.pilot.backend=lambda _:'A fresh view.'
        self.pilot.submit(self.payload('second'),self.binding); self.pilot.worker.join(2)
        self.assertEqual(self.pilot.status('second',self.binding)['outcome'],'completed')

    def test_service_health_failure_cannot_start_model_transport(self):
        from home_shared.camera_pilot_service import infer
        with patch('home_shared.camera_pilot_service.model_health',side_effect=TimeoutError()), \
             patch('home_shared.camera_pilot_service.subprocess.run') as transport:
            with self.assertRaises(BeforeDispatchRejected):infer(SimpleNamespace())
            transport.assert_not_called()

    def test_service_transport_timeout_remains_unknown(self):
        from home_shared.camera_pilot_service import infer
        request=SimpleNamespace(request_deadline=time.time()+15,max_output_tokens=128,image=b'fixture')
        with patch('home_shared.camera_pilot_service.model_health'), \
             patch('home_shared.camera_pilot_service.ENGINE',SimpleNamespace(container_id='e'*64)), \
             patch('home_shared.camera_pilot_service.subprocess.run',side_effect=TimeoutError()):
            with self.assertRaises(TimeoutError):infer(request)

    def test_completion_wait_does_not_extend_observation_deadline(self):
        from home_shared.camera_pilot_service import infer
        deadline=time.time()+15
        request=SimpleNamespace(request_deadline=deadline,max_output_tokens=128,image=b'fixture')
        response={'model':'qwen3-vl-30b','choices':[{'finish_reason':'stop','message':{'content':'A den.'}}],
                  'usage':{'completion_tokens':4}}
        with patch('home_shared.camera_pilot_service.model_health'), \
             patch('home_shared.camera_pilot_service.ENGINE',SimpleNamespace(container_id='e'*64)), \
             patch('home_shared.camera_pilot_service.subprocess.run',return_value=SimpleNamespace(stdout=json.dumps(response))) as transport:
            self.assertEqual(infer(request),'A den.')
            self.assertEqual(request.request_deadline,deadline)
            self.assertEqual(transport.call_args.kwargs['timeout'],65)
            self.assertEqual(json.loads(transport.call_args.kwargs['input'])['timeout'],60)

    def test_concurrency_late_completion_and_wrong_authority(self):
        release = threading.Event()
        self.pilot.backend = lambda _:(release.wait(2) and 'A den.')
        self.pilot.submit(self.payload(),self.binding)
        self.now += 31
        with self.assertRaises(CapacityExceeded):
            self.pilot.submit(self.payload('second'),self.binding)
        with self.assertRaises(AdmissionError):
            self.pilot.status('first',binding_fixture('echo'))
        release.set()
        self.pilot.worker.join(2)
        result = self.pilot.status('first',self.binding)
        self.assertEqual(result['outcome'],'completed_after_deadline')
        self.assertNotIn('observation',result)

    def test_background_and_token_widening_rejected_before_dispatch(self):
        for payload in (request_fixture(), {**self.payload(),'max_output_tokens':129}):
            with self.assertRaises(AdmissionError):
                self.pilot.submit(payload,self.binding)
        self.assertEqual(self.calls,[])

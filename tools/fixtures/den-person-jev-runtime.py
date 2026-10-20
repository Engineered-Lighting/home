"""Real bounded Lab/JEV queue and Home projections with authored external I/O.

No network, camera, real model, service activation, 3D or physical operation.
"""
from contextlib import ExitStack
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'stack/services/perception-lab'))
spec = importlib.util.spec_from_file_location('person_facts_fixture', Path(__file__).with_name('den-person-semantics-runtime.py'))
facts_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(facts_fixture)
from jev_person_provider import JevPersonProvider
import jev_person_wire as wire
from managed_jev import reasoning_factory
from reasoning import ACTIVITIES


class Fixture:
    def __init__(self, *, viewing=True, clock_origin=1800000000.):
        # Authored source times must be deterministic across Python seconds
        # and Home's millisecond clock seam. Random wall fractions can round
        # one ULP below exact availability before any transport time elapses.
        # Keep the production future-evidence refusal and original TTL intact.
        with patch('time.time', return_value=clock_origin):
            self.f = facts_fixture.Fixture(viewing=viewing)
        self.store, self.session, self.service = self.f.store, self.f.session, self.f.service
        self.store.append_event(self.session['id'], 'reasoning.setting', dict(enabled=True, revision=2))
        self.calls, self.mode, self.closed = [], 'normal', False
        self.policy = dict(schema_version=1, enabled=True, scope='household_structured', revision='authored-person-jev',
                           generation=1, expires_at=self.f.la.now+60, site_id='victoria', camera_ids=['den'],
                           model_selector=wire.MODEL, max_calls_per_minute=30, max_session_calls=24,
                           structured_sources=[wire.SCHEMA, 'lab-live-person-scene-1'])
        config = dict(schema_version=1, policy_file='authored-person-policy', credential_file='authored-key',
                      response_version=wire.MODEL, wire=wire.SCHEMA)
        self.patches = ExitStack()
        self.patches.enter_context(patch('managed_jev.private_json', side_effect=lambda name: deepcopy(
            config if name == 'authored-person-config' else self.policy)))
        self.patches.enter_context(patch('jev_person_provider.JevPersonProvider', side_effect=lambda **kwargs:
            JevPersonProvider(**{**kwargs, 'credential': lambda: 'authored-not-a-secret',
                                  'transport': self.transport})))
        self.factory = reasoning_factory('authored-person-config')
        self.service.reasoning_factory = self.factory
        self.service.reasoning_min_offer_interval_s = 1.
        self.reasoner = None
        self.stop_command = None
        if viewing:
            # Compose the existing RAM lifecycle owner without activating a
            # camera, host, background pump or recorder process.
            from apartment_viewing_source import ViewingRecorder
            recorder = ViewingRecorder(self.store, 'den')
            if recorder.start(self.session)['state'] != 'recording':
                raise RuntimeError('authored_viewing_lifecycle_not_owned')
            self.service.recorders['den'] = recorder
            self.service.apartment_viewing_sessions[self.session['id']] = self.f.viewing_source
            self.service.qwen_live_pumps[self.session['id']] = self.f.pump

    def transport(self, raw, token, timeout):
        packet = json.loads(raw)
        self.calls.append(deepcopy(packet))
        # External JEV seam only. The real provider independently vetoes a
        # positive answer when that person's supplied facts are unavailable.
        answers = {}
        for index, key in enumerate(packet['questions']):
            activity = 'computer_work' if index == 0 else 'reading'
            answers[key] = dict(type='choice', choice=activity, confidence=.8,
                                probabilities={k: float(k == activity) for k in ACTIVITIES})
        if self.mode == 'foreign_reply':
            answers['foreign_question'] = answers.pop(next(reversed(answers)))
        if self.mode == 'late':
            self.f.la.now += 3
        return json.dumps(dict(model=wire.MODEL, answers=answers,
                               usage=dict(input_tokens=300, output_tokens=100))).encode()

    def wait(self):
        deadline = time.monotonic()+3
        while True:
            with self.store.lock:
                count = self.store.db.execute("SELECT COUNT(*) FROM lab_reasoning_requests WHERE session_id=? "
                                              "AND state IN ('pending','dispatching')", (self.session['id'],)).fetchone()[0]
            if count == 0:
                return
            if time.monotonic() > deadline:
                raise RuntimeError('authored_jev_queue_did_not_drain')
            time.sleep(.001)

    def step(self, people, *, label, qwen_mode='normal', jev_mode='normal'):
        facts = self.f.step(people, label=label, mode=qwen_mode)
        self.mode = jev_mode
        before = len(self.calls)
        offered = self.service.offer_reasoning(self.session['id'], [], generation=self.f.runner.generation,
                                               cutoff=self.f.la.now, only_new=True)
        self.reasoner = self.service.reasoners.get(self.session['id'])
        self.wait()
        state = dict(label=label, now=self.f.la.now, qwen_outcome=facts['outcome'], jev_outcome=offered,
                     jev_calls=len(self.calls)-before, live=self.f.controller.live(self.session['id']),
                     graph=self.f.controller.graph(self.session['id']))
        retry = self.service.offer_reasoning(self.session['id'], [], generation=self.f.runner.generation,
                                             cutoff=self.f.la.now, only_new=True)
        if retry['request_ids'] or len(self.calls) != before+state['jev_calls']:
            raise RuntimeError('same_person_facts_must_not_dispatch_twice')
        state['duplicate_outcome'] = retry
        return state

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.reasoner is not None:
            self.reasoner.close()
            self.reasoner.pool.shutdown(wait=True)
        with self.store.lock:
            active = self.store.db.execute("SELECT COUNT(*) FROM lab_reasoning_requests WHERE session_id=? "
                                          "AND state IN ('pending','dispatching','indeterminate')", (self.session['id'],)).fetchone()[0] if self.reasoner else 0
        self.cleanup = dict(jev_active_rows=active, old_global_outputs=len([
            e for e in self.store.list_events(self.session['id']) if e['kind'] in ('reasoning.output', 'reasoning.smoothed')]),
            per_person_outputs=len([e for e in self.store.list_events(self.session['id']) if e['kind'] == 'reasoning.person_output']))
        if self.f.viewing:
            self.stop_command = self.f.controller.stop(self.session['id'], reason='authored_validation')
            if self.stop_command['state'] != 'stopped':
                raise RuntimeError('authored_stop_not_confirmed')
            events = self.store.list_events(self.session['id'])
            self.cleanup['stop_command_confirmed'] = True
            self.cleanup['stop_receipt'] = next(e['payload'] for e in reversed(events) if e['kind'] == 'runtime.stop_result')
            self.cleanup['stop_reason'] = next(e['payload']['reason'] for e in reversed(events) if e['kind'] == 'recording.stop_requested')
            # This fixture drives tick() directly. It has no started pump
            # thread for ServiceRuntime.close() to join after the real Stop.
            if self.f.pump.thread.ident is None:
                self.service.qwen_live_pumps.pop(self.session['id'])
        self.stopped = self.f.close()
        self.patches.close()
        return self.stopped


def main():
    fixture = Fixture(viewing='--recording' not in sys.argv)
    try:
        states = [fixture.step([('desk-1',20,True),('sofa-1',350,True)],label='two_judgments'),
                  fixture.step([('sofa-2',342,False),('desk-2',28,True)],label='one_unknown',qwen_mode='one_unknown'),
                  fixture.step([('desk-3',36,True),('sofa-3',334,True)],label='foreign_judgment',jev_mode='foreign_reply'),
                  fixture.step([('cross-1',42,True),('cross-2',70,True)],label='ambiguous')]
        with fixture.store.lock:
            durable = fixture.store.db.execute('SELECT COUNT(*) FROM media WHERE session_id=?', (fixture.session['id'],)).fetchone()[0]
        stopped = fixture.close()
        print(json.dumps(dict(schema='den-person-jev-authored-integration-1', session=fixture.session,
            viewing_ram=fixture.f.viewing, states=states, media=fixture.f.media, stopped=stopped,
            jev_model_seam_calls=len(fixture.calls), cleanup={**fixture.cleanup,
                'paired_active':fixture.f.runner.snapshot()['active'],'paired_pending':fixture.f.runner.snapshot()['pending'],
                'temporary_storage_removed':not Path(fixture.f.directory.name).exists(),
                'ram_after_stop':fixture.f.viewing_source.status() if fixture.f.viewing_source else None},
            durable_media_rows=durable, host_operations=0, network_calls=0, camera_calls=0, real_model_calls=0,
            physical_operations=False, three_d_changes=False, external_io='authored_detector_qwen_jev_and_policy'),
            separators=(',',':'), allow_nan=False))
    finally:
        fixture.close()


if __name__ == '__main__':
    main()

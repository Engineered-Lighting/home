"""Authored model/detector I/O through real Lab, LA intake and Home read models.

Local fixture only: no real inference, camera, host, HTTP server or physical I/O.
"""
from copy import deepcopy
import base64
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / 'stack/services/perception-lab'
sys.path[:0] = [str(SERVICE), str(ROOT / 'tools/shared-home'), str(ROOT / 'tools/shared-home/tests')]
from config import Settings
from controller import Controller
from qwen_person_live import QwenPersonPump, EVENT
import qwen_person_scene as scene
from runtime import PairedRunner
from service_runtime import ServiceRuntime
from store import Store
from test_lab_live_person_scene import PersonPilotTests
from test_lab_live_pilot import completion

spec = importlib.util.spec_from_file_location('authored_person_geometry', Path(__file__).with_name('den-person-association-runtime.py'))
geometry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(geometry)


def jpeg(colour):
    from PIL import Image
    output = io.BytesIO()
    Image.new('RGB', (640, 360), (colour, 40, 80)).save(output, format='JPEG')
    return output.getvalue()


def facts(activity):
    if activity == 'unclear':
        return dict(subject_posture='unclear', held_object='unclear', book_state='unclear', gaze_target='unclear',
                    typing_visible='unclear', screen_facing_subject='unclear', activity_hypothesis='unclear', visible_basis=[])
    reading = activity == 'reading'
    return dict(subject_posture='sitting', held_object='book' if reading else 'none',
                book_state='open' if reading else 'not_visible', gaze_target='book' if reading else 'screen',
                typing_visible='no' if reading else 'yes', screen_facing_subject='not_visible',
                activity_hypothesis=activity, visible_basis=['book_in_hands', 'book_pages_open'] if reading else ['hands_on_keyboard'])


class Transport:
    config = dict(deadline_seconds=7)

    def __init__(self, owner):
        self.owner, self.envelopes = owner, []
        self.response_mode = 'normal'

    def __call__(self, envelope):
        self.envelopes.append(deepcopy(envelope))
        context = json.loads(envelope['body']['messages'][1]['content'][0]['text'])
        # Authored response at the external model seam, deliberately tied to
        # request-local slots, not hidden track references or array position.
        output = dict(scene=dict(people_visible='multiple'), subjects=[
            dict(subject_slot=s['subject_slot'], facts=facts('computer_work' if s['box']['x'] < 200 else
                 'unclear' if self.response_mode == 'one_unknown' else 'reading')) for s in context['subjects']])
        if self.response_mode == 'foreign_slot':
            output['subjects'][-1]['subject_slot'] = 'person-foreign'
        self.owner.la.model.result = completion(json.dumps(output))
        self.owner.la.live.submit(envelope, self.owner.la.binding)
        self.owner.la.finish()
        if self.response_mode == 'late':
            self.owner.la.now += 3
        return self.lookup({k: v for k, v in envelope.items() if k != 'body'})

    def lookup(self, reference):
        return self.owner.la.live.lookup(reference, self.owner.la.binding)

    def close(self):
        pass


class Fixture:
    def __init__(self, *, viewing=False):
        self.la = PersonPilotTests()
        self.la.setUp()
        self.la.now -= 5
        self.directory = tempfile.TemporaryDirectory(prefix='den-person-semantics-')
        self.store = Store(Settings(storage_root=Path(self.directory.name), min_free_bytes=0), clock=lambda: self.la.now)
        self.viewing = viewing
        recipe = 'apartment-viewing' if viewing else 'detection'
        session, _ = self.store.create_session('den', recipe, 'Authored person semantics', 'authored-person-semantics')
        self.store.transition(session['id'], 'starting')
        self.store.transition(session['id'], 'recording')
        self.session = self.store.get_session(session['id'])
        self.la.now += 5
        self.origin = self.la.now
        self.frames, self.media = {}, {}
        profiles = {arm: dict(profile_id='authored-'+arm, artifact_sha256='b'*64,
                             model_version='authored-pose-1', engine_epoch='authored-pose-'+arm) for arm in ('a', 'b')}
        def execute(request):
            return dict(request_id=request['request_id'], engine_epoch=request['profile']['engine_epoch'],
                        artifact_sha256=request['profile']['artifact_sha256'], terminal=True, state='completed',
                        result=geometry.result(request, self.frames[request['frame']['frame_id']]))
        self.service = ServiceRuntime(self.store, {}, lambda *_: profiles, execute)
        self.service._activate(self.session, recipe, profiles)
        self.runner = self.service.runners[self.session['id']]
        self.viewing_source = None
        if viewing:
            from apartment_viewing_source import ViewingFactory, POLICY
            factory = ViewingFactory(self.store, camera_id='den', policy=POLICY)
            factory.bind_runtime(self.service)
            self.viewing_source = factory.new_session(self.session)
            self.viewing_source.generation = self.runner.generation
        self.controller = Controller(self.store, self.service)
        self.transport = Transport(self)
        profile = dict(model_version=scene.MODEL, served_model=scene.SERVED_MODEL, artifact_sha256='a'*64,
                       engine_epoch='unprobed', runtime_revision='authored-runtime', adapter_revision=scene.PROMPT_VERSION)
        if viewing:
            from live_detector_app import qwen_live_factory
            from test_qwen_live import TRANSPORT_CONFIG
            block = dict(transport_file='/authored/private.json', artifact_sha256='a'*64,
                         runtime_revision='authored-runtime', enabled=True, protocol=scene.SCHEMA)
            with patch('live_detector_app.private_json', return_value=dict(TRANSPORT_CONFIG)):
                factory = qwen_live_factory({'qwen_live': block},
                    reasoning_factory=SimpleNamespace(qwen_person_granted=lambda: True),
                    transport_factory=lambda _: self.transport)
            if not factory.kill_switch():
                raise RuntimeError('authored_person_grant_missing')
            self.pump = factory(self.store, self.session, active=lambda: True, enabled=factory.kill_switch,
                                occupied=lambda: dict(state='occupied', reason='authored'))
            self.pump.clock = self.pump.monotonic = lambda: self.la.now
            self.pump.local_time = lambda _: (12, 4)
        else:
            self.pump = QwenPersonPump(self.store, self.session, transport=self.transport, profile=profile,
                                      clock=lambda: self.la.now, monotonic=lambda: self.la.now,
                                      local_time=lambda _: (12, 4), occupied=lambda: dict(state='occupied', reason='authored'),
                                      config=dict(interval_seconds=.5, frames=3, span_seconds=3., max_per_minute=15))
        self.index = 0
        self.closed = False
        for offset in ((-1.2,) if viewing else (-3.2, -1.7)):
            self.add_media(self.origin+offset, 'context-'+str(offset), 'capture-1')

    def add_media(self, observed, identity, epoch):
        raw = jpeg((self.index+1) % 250)
        meta = dict(site_id='victoria', camera_id='den', frame_id=identity, capture_epoch=epoch, stream='rgb',
                    width=640, height=360, observed_at=observed, available_at=observed, valid_until=observed+2.5,
                    clock_mapping_status='verified', clock_mapping_id='authored-clock', uncertainty_seconds=.01)
        if self.viewing_source is not None:
            item = self.viewing_source.add_media(self.session['id'], raw, 'image/jpeg', meta, identity)
        else:
            item = self.store.add_media(self.session['id'], raw, 'image/jpeg', meta)
        from apartment_viewing_source import registered_media
        descriptor = registered_media(self.store, self.session['id'], item['id'], self.session['generation'])
        self.media[item['id']] = dict(descriptor={k: v for k, v in descriptor.items() if k not in {'path', 'storage_path'}},
                                     base64=base64.b64encode(raw).decode())
        return item

    def step(self, people, *, label='frame', epoch='capture-1', mode='normal', advance=1.):
        if self.index:
            self.la.now += advance
        self.index += 1
        frame_id = 'authored-person-frame-'+str(self.index)
        observed = self.la.now-.2
        item = self.add_media(observed, frame_id, epoch)
        self.frames[frame_id] = people
        frame = dict(site_id='victoria', camera_id='den', session_id=self.session['id'], frame_id=frame_id,
                     capture_epoch=epoch, observed_at=observed, available_at=observed,
                     valid_until=observed+2.5, media_ids=[item['id']])
        self.runner.offer(frame)
        deadline = time.monotonic()+3
        while True:
            with self.store.lock:
                count = self.store.db.execute("SELECT COUNT(*) FROM events WHERE session_id=? AND kind='perception.output' "
                                             "AND json_extract(payload,'$.frame_id')=?", (self.session['id'], frame_id)).fetchone()[0]
            if count == 2:
                break
            if time.monotonic() > deadline:
                raise RuntimeError('bounded_authored_detector_timeout')
            time.sleep(.001)
        self.transport.response_mode = mode
        outcome = self.pump.tick()
        return dict(label=label, now=self.la.now, outcome=outcome,
                    live=self.controller.live(self.session['id']), graph=self.controller.graph(self.session['id']))

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.pump.stop(timeout=2)
        self.runner.close()
        self.runner.pool.shutdown(wait=True)
        if self.viewing_source is not None:
            self.viewing_source.stop()
        self.store.transition(self.session['id'], 'stopped')
        stopped = self.controller.live(self.session['id'])
        self.service.close(timeout=2)
        self.store.close()
        self.directory.cleanup()
        self.la.tearDown()
        return stopped


def main():
    fixture = Fixture(viewing='--viewing' in sys.argv)
    try:
        states = [fixture.step([('desk-1', 20, True), ('sofa-1', 350, True)], label='two_facts'),
                  fixture.step([('sofa-2', 342, False), ('desk-2', 28, True)], label='reorder_one_unknown', mode='one_unknown'),
                  fixture.step([('desk-3', 36, True), ('sofa-3', 334, True)], label='foreign_slot', mode='foreign_slot'),
                  fixture.step([('cross-1', 42, True), ('cross-2', 70, True)], label='ambiguous')]
        source_calls = len(fixture.transport.envelopes)
        durable_rows = fixture.store.db.execute('SELECT COUNT(*) FROM media WHERE session_id=?', (fixture.session['id'],)).fetchone()[0]
        if fixture.viewing and durable_rows != 0:
            raise RuntimeError('viewing_must_not_archive_model_media')
        stopped = fixture.close()
        output = dict(schema='den-person-semantics-offline-fixture-1', session=fixture.session, states=states,
                      stopped=stopped, media=fixture.media, model_seam_calls=source_calls,
                      viewing_ram=fixture.viewing, durable_media_rows=durable_rows,
                      cleanup=dict(runner_closed=fixture.runner.snapshot()['closed'],
                                   active=fixture.runner.snapshot()['active'], pending=fixture.runner.snapshot()['pending'],
                                   temporary_storage_removed=not Path(fixture.directory.name).exists()),
                      physical_operations=False, host_operations=0, network_calls=0, real_model_calls=0,
                      three_d_placement=False, source_mode='authored_external_detector_and_model_io')
        if fixture.viewing:
            output['cleanup']['ram_after_stop'] = fixture.viewing_source.status()
        print(json.dumps(output, separators=(',', ':'), allow_nan=False))
    finally:
        fixture.close()


if __name__ == '__main__':
    main()

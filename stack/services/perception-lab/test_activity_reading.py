"""Raw producer binding, origin expiry, worker recovery and same-frame joins."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from activity_reading import SoleReaderBank, freeze_context, reader_key
import test_activity_loop_runtime as loop_fixtures


def raw(at=1800000000., *, count=1, instance='one', x=200., generation=1, capture='camera-epoch'):
    boxes = [dict(label='person', instance_id=instance if i == 0 else 'two', confidence=.9,
                  x=x + i * 150, y=60., w=100., h=230.) for i in range(count)]
    return dict(arm='b', status='completed', current=True, site_id='victoria', camera_id='den', generation=generation,
        capture_epoch=capture, engine_epoch='detector-engine', observed_at=at, available_at=at, valid_until=at + 8,
        frame_id='frame-%s' % at, request_id='request-%s' % at,
        result=dict(objects=[{k: b[k] for k in ('label', 'instance_id', 'confidence')} for b in boxes], boxes=boxes,
            pose_instances=[dict(instance_id=b['instance_id'], joints=[]) for b in boxes]))


class RawBindingTests(unittest.TestCase):
    def setUp(self):
        self.bank = SoleReaderBank()
        self.session = dict(id='recording-1')

    def commit(self, output):
        preview = self.bank.preview(output, self.session)
        self.bank.commit(preview)
        return preview[0]

    def test_frame_local_array_id_change_is_not_identity_or_a_reader_change(self):
        first = self.commit(raw())
        next_frame = self.commit(raw(1800000001., instance='reordered-slot'))
        self.assertEqual(reader_key(first), reader_key(next_frame))
        self.assertNotEqual(first['instance_id'], next_frame['instance_id'])

    def test_second_person_and_missing_person_reset_requalification(self):
        first = self.commit(raw())
        self.assertEqual(self.commit(raw(1800000001., count=2))['status'], 'unavailable')
        remaining = self.commit(raw(1800000002.))
        self.assertNotEqual(reader_key(first), reader_key(remaining))
        self.commit(raw(1800000003., count=0))
        self.assertNotEqual(reader_key(remaining), reader_key(self.commit(raw(1800000004.))))

    def test_gap_jump_capture_and_session_roll_change_epoch(self):
        previous = self.commit(raw())
        for output in (raw(1800000004.), raw(1800000005., x=450.), raw(1800000006., x=450., capture='replacement')):
            current = self.commit(output)
            self.assertNotEqual(reader_key(previous), reader_key(current)); previous = current
        self.session = dict(id='recording-2')
        self.assertNotEqual(reader_key(previous), reader_key(self.commit(raw(1800000007., capture='replacement'))))

    def test_late_expired_and_generation_zero_never_bind(self):
        self.commit(raw())
        self.assertEqual(self.commit(raw(1799999999.))['status'], 'unavailable')
        late = raw(); late['available_at'] = late['valid_until']
        self.assertEqual(self.commit(late)['status'], 'unavailable')
        self.assertEqual(self.commit(raw(generation=0))['status'], 'unavailable')

    def test_unmarked_zones_skip_but_partial_or_malformed_marks_refuse(self):
        import activity_pose
        geometry = dict(schema='lighting-brain-geometry-1', home='victoria', room='den', zones={'desk': {}})
        self.assertEqual(activity_pose.check_zones(geometry), {})
        for target in (None, {'zone_vertices_m': None}, {'zone_normal_unit': [0, 0, 1]},
                       {'zone_vertices_m': [[0, 0, 0]] * 3, 'zone_normal_unit': None}):
            geometry['zones']['desk'] = target
            with self.assertRaises(ValueError):
                activity_pose.check_zones(geometry)


class AlwaysOnQwenBindingTests(unittest.TestCase):
    def setUp(self):
        from la_qwen_context import LaQwenReceipts
        import qwen_scene as scene
        import test_qwen_scene as fixture
        self.source = LaQwenReceipts(SimpleNamespace(subscribe_receipts=lambda _: None, unsubscribe_receipts=lambda _: None))
        self.addCleanup(self.source.close)
        self.session = dict(id='recording', site_id='victoria', camera_id='den', started_at=100.)
        self.binding = dict(status='available', session_id='recording', capture_epoch='e', interval_start=100., observed_at=105.)
        frames = [dict(f, observed_at=f['observed_at'] + 3) for f in fixture.FRAMES]
        self.receipt = scene.first_receipt(fixture.draft(frames=frames, generated=104.), 104.1)

    def eligible(self, receipt=None, *, session=None, binding=None, cutoff=105.):
        self.source.receipts.clear()
        self.source.observe({}, self.receipt if receipt is None else receipt)
        return self.source.reading_receipts(session or self.session, binding or self.binding, cutoff)

    def test_daily_scope_uses_same_capture_and_returns_immutable_original_times(self):
        self.assertNotEqual(self.receipt['session_id'], self.session['id'])
        result, = self.eligible()
        self.assertEqual(result, self.receipt)
        result['valid_until'] += 1000
        result['facts']['held_object'] = 'phone'
        self.assertEqual(self.source.receipts[self.receipt['request_id']], self.receipt)

    def test_other_camera_capture_schema_and_temporal_overlap_are_refused(self):
        for field, wrong in [('site_id', 'echo'), ('camera_id', 'other'), ('capture_epoch', 'other'),
                             ('schema', 'unknown'), ('window_start', 99.), ('observed_at', 106.),
                             ('generated_at', 106.), ('available_at', 106.), ('first_received_at', 104.2)]:
            with self.subTest(field=field):
                self.assertEqual(self.eligible(dict(self.receipt, **{field: wrong})), [])

    def test_recording_start_reader_reset_and_source_retirement_do_not_adopt_old_qwen(self):
        self.assertEqual(self.eligible(session=dict(self.session, started_at=101.)), [])
        self.assertEqual(self.eligible(binding=dict(self.binding, interval_start=101.)), [])
        self.assertEqual(self.eligible(binding=dict(self.binding, session_id='previous-recording')), [])
        self.assertEqual(self.eligible(binding=dict(self.binding, status='unavailable')), [])
        self.source.close()
        self.assertEqual(self.source.reading_receipts(self.session, self.binding, 105.), [])

    def test_newer_incompatible_receipt_cannot_reveal_an_older_compatible_one(self):
        # A newly received scene can be ahead of the latest detector, or from a
        # replacement capture. Neither permits falling back to an older book.
        for change in (dict(observed_at=106.), dict(capture_epoch='replacement'), dict(window_start=99.)):
            with self.subTest(change=change):
                self.source.receipts.clear()
                self.source.observe({}, self.receipt)
                newer = dict(self.receipt, request_id='n' * 64, observed_at=104., generated_at=106.1,
                    available_at=106.2, first_received_at=106.2)
                newer.update(change)
                self.source.observe({}, newer)
                self.assertEqual(self.source.reading_receipts(self.session, self.binding, 107.), [])
        self.source.receipts.clear()
        self.source.observe({}, self.receipt)
        self.source.observe({}, dict(newer, camera_id='other'))
        self.assertEqual(self.source.reading_receipts(self.session, self.binding, 107.), [self.receipt],
            'another camera is outside the requested source scope')

    def test_view_packets_and_rebinding_cannot_supply_a_trusted_original_bank(self):
        from live_reasoning import LiveReasoning
        reasoner = object.__new__(LiveReasoning)
        reasoner.lock, reasoner.closed, reasoner.reading_qwen_source = __import__('threading').RLock(), False, None
        with self.assertRaises(ValueError):
            reasoner.bind_reading_qwen(SimpleNamespace(reading_receipts=lambda *_: [self.receipt]))
        reasoner.bind_reading_qwen(self.source)
        reasoner.bind_reading_qwen(self.source)
        other = type(self.source)(SimpleNamespace(subscribe_receipts=lambda _: None, unsubscribe_receipts=lambda _: None))
        self.addCleanup(other.close)
        with self.assertRaises(ValueError):
            reasoner.bind_reading_qwen(other)


class BoundDeepStackTests(unittest.TestCase):
    setUp = loop_fixtures.DeepStackTests.setUp
    viewing = loop_fixtures.DeepStackTests.viewing

    def output(self):
        sid = self.deep.sid
        value = raw(self.now[0])
        bank = getattr(self, 'bank', None) or SoleReaderBank(); self.bank = bank
        preview = bank.preview(value, self.store.get_session(sid)); bank.commit(preview)
        value['reading_binding'] = preview[0]
        event_id = self.store.append_event(sid, 'perception.output', value)
        self.viewing(sid)
        self.placement.packet = dict(renderable=True,
            frame=dict(frame_id=value['frame_id'], source_epoch=value['capture_epoch'], original_times=dict(observed_at=self.now[0])),
            approximate_pose=dict(instance_index=0, provenance=dict(source_event_id=event_id),
                joints=[dict(id=5, position_m=[.8, 2., 1.2]), dict(id=6, position_m=[1.2, 2., 1.2])]))
        return value, event_id

    def answer(self, value, event_id, **overrides):
        start = self.store.get_session(self.deep.sid)['started_at']
        binding = dict(value['reading_binding'], source_event_id=event_id)
        answer = dict(arm='b', status='ready', provider_status='ready', fresh_on_arrival=True,
            source_end_s=value['observed_at'] - start, available_at_s=self.now[0] - start,
            valid_until=value['valid_until'], reading_context=dict(binding=binding, valid_until=value['valid_until'], input_digest='fixture'),
            judgment=dict(activity='reading', probability=.9, model_version='jev-test'))
        answer.update(overrides)
        return self.store.append_event(self.deep.sid, 'reasoning.output', answer)

    def test_bound_activity_and_pose_keep_original_expiry_and_matching_source(self):
        self.deep.start(); value, event_id = self.output(); self.answer(value, event_id)
        seen = self.deep.observe()
        self.assertEqual(seen['activity']['valid_until'], value['valid_until'])
        self.assertEqual(seen['pose']['binding']['source_event_id'], event_id)
        self.assertEqual(reader_key(seen['activity']['binding']), reader_key(seen['pose']['binding']))

    def test_stale_abstained_late_and_wrong_frame_replies_never_publish_reading(self):
        self.deep.start(); value, event_id = self.output()
        for changes in (dict(status='stale'), dict(status='abstain'), dict(provider_status='abstain'),
                        dict(fresh_on_arrival=False), dict(valid_until=self.now[0])):
            self.answer(value, event_id, **changes)
            self.assertNotIn('activity', self.deep.observe())
        self.answer(value, event_id)
        self.placement.packet['frame']['frame_id'] = 'wrong-frame'
        self.assertEqual(self.deep.observe()['pose']['people'], [])

    def test_worker_failure_retries_but_operator_and_monitor_stops_do_not(self):
        self.deep.start(); first = self.deep.sid
        self.store.transition(first, 'failed', 'worker_preparation_failed')
        self.deep.tick()
        self.assertFalse(self.deep.ended_externally)
        self.assertTrue(self.deep.wanted); self.assertIsNone(self.deep.sid)
        self.now[0] += 9; self.deep.tick(); self.assertIsNone(self.deep.sid)
        self.now[0] += 1; self.deep.tick(); self.assertIsNotNone(self.deep.sid)
        second = self.deep.sid
        self.store.transition(second, 'failed', 'worker_preparation_failed'); self.deep.tick()
        self.assertEqual(self.deep.retry_at - self.now[0], 20)
        for reason in ('operator_stop', 'monitor_backoff'):
            self.deep.stop(reason); self.now[0] += 100; self.deep.tick()
            self.assertIsNone(self.deep.sid); self.assertFalse(self.deep.wanted)

    def test_loop_status_epoch_is_stable_across_rolls(self):
        self.deep.start(); epoch = self.deep.snapshot()['producer_epoch']
        self.now[0] += 280; self.deep.tick()
        self.assertEqual(self.deep.snapshot()['producer_epoch'], epoch)
        self.assertEqual(self.deep.snapshot()['rolls'], 1)

    def test_frozen_context_and_JEV_output_preserve_original_lease(self):
        from live_reasoning import LiveReasoning
        self.deep.start(); value, event_id = self.output()
        session = self.store.get_session(self.deep.sid)
        record = dict(id=str(event_id), source_kind='perception.output', frame_id=value['frame_id'],
            capture_epoch=value['capture_epoch'], observed_at=value['observed_at'], available_at=value['available_at'],
            valid_until=value['valid_until'], arm='b', generation=1, objects=value['result']['objects'])
        records, shared, context = freeze_context(self.store, session, [record], [], self.now[0])
        self.assertEqual(context['binding']['source_event_id'], event_id)
        runner = object.__new__(LiveReasoning)
        runner.store, runner.session, runner.sid, runner.policy = self.store, session, session['id'], {}
        request = dict(request_id='test-request', site_id='victoria', arm='b', generation=1, records=records,
            cutoff=self.now[0], deadline=self.now[0] + 15, reading_context=context)
        result = dict(status='ready', judgment=dict(activity='reading', probabilities={'reading': 1}), provenance={})
        output = runner._output(request, result, self.now[0] + 9)
        self.assertEqual(output['valid_until'], value['valid_until'])
        self.assertEqual(output['status'], 'stale')
        self.assertFalse(output['fresh_on_arrival'])

    def test_existing_always_on_qwen_source_binds_without_a_recording_qwen_ledger(self):
        from la_qwen_context import LaQwenReceipts
        from live_reasoning import LiveReasoning
        from service_runtime import ServiceRuntime
        import qwen_scene as scene
        import test_qwen_scene as qwen_fixture
        self.deep.start()
        start = self.now[0]
        for offset in range(5):
            self.now[0] = start + offset
            value, event_id = self.output()
        session = self.store.get_session(self.deep.sid)
        bridge = LaQwenReceipts(SimpleNamespace(subscribe_receipts=lambda _: None, unsubscribe_receipts=lambda _: None))
        self.addCleanup(bridge.close)
        frames = [dict(media_id='ring-' + str(i), frame_id='ring-frame-' + str(i),
            capture_epoch=value['capture_epoch'], observed_at=start + .2 + i * 1.5) for i in range(3)]
        receipt = scene.first_receipt(qwen_fixture.draft(frames=frames, generated=start + 3.3), start + 3.4)
        bridge.observe({}, receipt)
        shared = bridge.context(session, self.now[0], generation=1, arms={'b'})
        self.assertEqual(shared['b'][0]['qwen_scene']['status'], 'supplied')
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE kind='qwen.observation'").fetchone()[0], 0)
        runtime = ServiceRuntime(self.store, {}, None, None, la_qwen=bridge)
        reasoner = LiveReasoning(self.store, session['id'], SimpleNamespace())
        self.addCleanup(reasoner.pool.shutdown, wait=True)
        self.addCleanup(reasoner.close)
        runtime.reasoning_factory = lambda *_: reasoner
        self.store.db.execute('INSERT INTO lab_experiment_state VALUES(?,?,?)', (session['id'], 'apartment-viewing', 1))
        self.store.db.commit()
        runtime._new_reasoner(session['id'])
        record = dict(id=str(event_id), source_kind='perception.output', site_id='victoria', session_id=session['id'],
            frame_id=value['frame_id'], capture_epoch=value['capture_epoch'], observed_at=value['observed_at'],
            available_at=value['available_at'], valid_until=value['valid_until'], arm='b', generation=1,
            objects=value['result']['objects'])
        with patch.object(reasoner, '_pump'):
            request_id, = reasoner.offer([record], generation=1, cutoff=self.now[0], shared_context=shared, arms=('b',))
        frozen = json.loads(self.store.db.execute('SELECT payload FROM lab_reasoning_requests WHERE id=?', (request_id,)).fetchone()[0])
        self.assertIsNotNone(frozen['reading_context'])
        self.assertEqual(frozen['reading_context']['binding']['source_event_id'], event_id)
        self.assertEqual(frozen['reading_context']['valid_until'], min(value['valid_until'], receipt['valid_until']))
        self.assertEqual(frozen['shared_context'][0]['qwen_scene']['latest']['facts']['held_object'], 'book')
        # Runtime grant/availability withholding must not be undone by reading
        # qualification's access to the original cache.
        with patch.object(reasoner, '_pump'):
            withheld_id, = reasoner.offer([record], generation=1, cutoff=self.now[0], shared_context={}, arms=('b',))
        withheld = json.loads(self.store.db.execute('SELECT payload FROM lab_reasoning_requests WHERE id=?', (withheld_id,)).fetchone()[0])
        self.assertIsNone(withheld['reading_context'])
        self.assertNotIn('shared_context', withheld)
        invalid = dict(receipt, request_id='i' * 64, observed_at=start + 3.5,
            generated_at=start + 3.6, available_at=start + 3.7, first_received_at=start + 3.7,
            status='invalid', facts=None)
        bridge.observe({}, invalid)
        with patch.object(reasoner, '_pump'):
            invalid_id, = reasoner.offer([record], generation=1, cutoff=self.now[0], shared_context=shared, arms=('b',))
        invalid_request = json.loads(self.store.db.execute('SELECT payload FROM lab_reasoning_requests WHERE id=?', (invalid_id,)).fetchone()[0])
        self.assertIsNone(invalid_request['reading_context'], 'an older good Qwen receipt cannot replace the latest invalid one')
        ambiguous = dict(receipt, request_id='a' * 64, observed_at=start + 3.7,
            generated_at=start + 3.8, available_at=start + 3.9, first_received_at=start + 3.9,
            facts=dict(receipt['facts'], people_visible='multiple'))
        bridge.observe({}, ambiguous)
        with patch.object(reasoner, '_pump'):
            ambiguous_id, = reasoner.offer([record], generation=1, cutoff=self.now[0], shared_context=shared, arms=('b',))
        ambiguous_request = json.loads(self.store.db.execute('SELECT payload FROM lab_reasoning_requests WHERE id=?', (ambiguous_id,)).fetchone()[0])
        self.assertIsNone(ambiguous_request['reading_context'], 'Qwen disagreement cannot qualify a sole reader')
        bridge.receipts.clear()
        bridge.observe({}, receipt)
        bridge.observe({}, dict(ambiguous, request_id='n' * 64, observed_at=start + 4.1,
            generated_at=start + 4.2, available_at=start + 4.3, first_received_at=start + 4.3))
        self.now[0] = start + 4.4
        with patch.object(reasoner, '_pump'):
            ahead_id, = reasoner.offer([record], generation=1, cutoff=self.now[0], shared_context=shared, arms=('b',))
        ahead = json.loads(self.store.db.execute('SELECT payload FROM lab_reasoning_requests WHERE id=?', (ahead_id,)).fetchone()[0])
        self.assertIsNone(ahead['reading_context'], 'a newer scene ahead of the detector must withhold older reading authority')

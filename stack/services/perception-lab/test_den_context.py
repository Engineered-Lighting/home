"""Offline den context builder (den-context-1): causality, stale and missing
statuses (never a negative), multi-person, the PC-input inferred location, no
coordinates anywhere, deterministic output, a read-only ledger, and bake-off
tick files as a hypothesis source. Temp ledgers and synthetic values only."""
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import unittest

import den_context
import lab_eval
from test_lab_regression import LedgerFixture

DURATION = 20
OCCUPANCY = 'binary_sensor.den_person_occupancy'


def known(state, **extra):
    return {'state': state, 'status': 'known', **extra}


def states(*, recency='under_15s', audio='playing', occupancy='on'):
    return {OCCUPANCY: known(occupancy, origin='same_camera_visual'), 'light.den_lamp': known('on'),
            'media_player.den': known(audio), 'person.owner': known('home'), 'sun.sun': known('below_horizon'),
            'derived.computer_active': known('on'), 'derived.computer_input_recency': known(recency)}


class DenLedger(LedgerFixture):
    def build(self, *, persons=lambda t: 1, ha_until=DURATION + 2, ha=True, recency=lambda t: 'under_15s', qwen=(),
              answers=()):
        sid, start = self.session('den', DURATION)
        self.sid, self.start = sid, start
        for i in range(2 * (DURATION + 3)):
            t = start - 3 + i * .5
            for arm in ('a', 'b'):
                self.store.append_event(sid, 'perception.output', {
                    'site_id': 'victoria', 'camera_id': 'den', 'session_id': sid, 'arm': arm, 'generation': 1,
                    'status': 'completed', 'observed_at': t, 'available_at': t + .2, 'valid_until': t + 2.5,
                    'result': {'objects': [{'label': 'person', 'confidence': .9, 'box': [1.0, 2.0, 3.0, 4.0]}] * persons(t - start)
                               + [{'label': 'chair', 'confidence': .8}]}})
        if ha:
            t = start - 2
            while t <= start + ha_until:
                self.snapshot(t, states(recency=recency(t - start)))
                t += 1
        for offset, available, facts in qwen:
            self.store.append_event(sid, 'qwen.observation', {
                'schema': 'qwen-observation-1', 'status': 'valid', 'site_id': 'victoria', 'camera_id': 'den', 'session_id': sid,
                'observed_at': start + offset, 'available_at': start + available, 'valid_until': start + offset + 10,
                'facts': facts})
        for offset, probabilities in answers:
            self.store.append_event(sid, 'reasoning.output', {
                'arm': 'a', 'mode': 'live', 'request_id': 'r%s' % offset, 'judgment': {'probabilities': probabilities},
                'prediction': {'available_at': offset, 'start': offset - 2, 'end': offset, 'time_basis': 'session_relative_seconds'}})
        return sid

    def snapshot(self, t, values):
        key = 'ha:primary:%s' % t
        self.store.append_event(self.sid, 'ha.observation', {
            'observed_at': t, 'available_at': t + .5, 'valid_until': t + 3.0, 'ha': values, 'site_id': 'victoria',
            'camera_id': 'den', 'session_id': self.sid, 'evidence_roots': [key], 'snapshot_key': key,
            'snapshot_digest': '0' * 64, 'quality': {'timing_basis': 'reconciled_snapshot_acquisition', 'source_id': 'primary'}})

    def path(self):
        return str(Path(self.directory.name) / 'lab.sqlite3')

    def run_cli(self, *extra, name='context.jsonl'):
        out = str(Path(self.directory.name) / name)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            den_context.main(['--ledger', self.path(), '--session', self.sid[:8], '--out', out, *extra])
        with open(out, encoding='utf-8') as stream:
            text = stream.read()
        return [json.loads(line) for line in text.splitlines()], text, stdout.getvalue()

    def records(self, **kwargs):
        view = den_context.LedgerView(self.path())
        try:
            records, _ = den_context.session_context(view, self.sid, **kwargs)
        finally:
            view.close()
        return {r['t']: r for r in records}


class DenContextTests(DenLedger):
    def test_one_record_per_second_with_the_contract_shape(self):
        self.build(answers=[(4.5, {'computer_work': .5, 'insufficient_evidence': .5})])
        records, _, printed = self.run_cli('--summary')
        self.assertEqual([r['t'] for r in records], [float(k) for k in range(1, DURATION + 1)])
        self.assertEqual([r['seq'] for r in records], list(range(1, DURATION + 1)))
        r = records[4]
        self.assertEqual(r['schema'], 'den-context-1')
        self.assertEqual(set(r), {'schema', 'session_id', 't', 'seq', 'built_at_version', 'occupancy', 'persons', 'devices',
                                  'ambience', 'multi_person', 'unobserved', 'evidence_refs'})
        self.assertEqual(set(r['devices']), {'pc_input', 'pc_active', 'den_audio', 'den_light', 'owner_home'})
        self.assertEqual(r['devices']['den_audio'], {'value': 'playing', 'status': 'supplied', 'age_s': r['devices']['den_audio']['age_s']})
        self.assertEqual(r['ambience']['music_playing'], {'value': True, 'status': 'supplied'})
        self.assertEqual(r['ambience']['daylight'], {'value': 'dark', 'status': 'supplied'})
        self.assertEqual(r['ambience']['part_of_day']['status'], 'missing')
        for item in list(r['devices'].values()) + [r['occupancy']]:
            self.assertIn(item['status'], den_context.STATUSES)
        hyp = r['persons'][0]['activity_hypotheses']
        self.assertEqual(hyp, {'computer_work': 1.0})
        self.assertEqual(r['persons'][0]['hypothesis_sources'], ['device_rule:pc_input_recent', 'jev:arm_a'])
        self.assertTrue(all(isinstance(i, int) for i in r['evidence_refs']) and r['evidence_refs'])
        self.assertIn('"ticks": 20', printed)
        self.assertIn('top_activity_share', printed)

    def test_pc_input_gives_an_inferred_desk_location_else_approximate(self):
        self.build(recency=lambda s: 'under_15s' if s < 10 else 'over_5_min')
        at = self.records()
        person = at[5.0]['persons'][0]
        self.assertEqual(person['location'], {'anchor_id': 'desk_work_area', 'zone_id': 'desk', 'quality': 'inferred',
                                              'basis': ['pc_input_recent']})
        self.assertEqual([e['target_id'] for e in person['engagements']], ['desk_work_area'])
        later = at[15.0]['persons'][0]
        self.assertEqual((later['location']['anchor_id'], later['location']['zone_id'], later['location']['quality']),
                         (None, None, 'approximate'))
        self.assertEqual(later['engagements'], [])
        self.assertEqual(later['activity_hypotheses'], {})

    def test_a_row_available_after_t_is_not_used(self):
        # Qwen observed at 5 but available only at 9.5: invisible at 6..9.
        self.build(qwen=[(5.0, 9.5, {'held_object': 'book', 'activity_hypothesis': 'reading', 'subject_posture': 'sitting'})],
                   answers=[(12.5, {'reading': .8, 'insufficient_evidence': .2})])
        at = self.records()
        for t in (6.0, 7.0, 8.0, 9.0):
            person = at[t]['persons'][0]
            self.assertEqual(person['posture']['status'], 'missing')
            self.assertNotIn('reading_chair_book', [e['target_id'] for e in person['engagements']])
        person = at[10.0]['persons'][0]
        self.assertEqual(person['posture'], {'value': 'sitting', 'status': 'supplied', 'quality': 'inferred'})
        reading = [e for e in person['engagements'] if e['target_id'] == 'reading_chair_book'][0]
        self.assertEqual((reading['quality'], reading['basis']), ('inferred', ['qwen_held_object_book', 'qwen_activity_reading']))
        # The JEV answer available at 12.5 is absent at 12, present at 13.
        self.assertNotIn('jev:arm_a', at[12.0]['persons'][0]['hypothesis_sources'])
        self.assertIn('jev:arm_a', at[13.0]['persons'][0]['hypothesis_sources'])
        self.assertGreater(at[13.0]['persons'][0]['activity_hypotheses']['reading'], 0)
        # Qwen goes stale 10 s after observation.
        self.assertEqual(at[16.0]['persons'][0]['posture']['status'], 'stale')
        self.assertIsNone(at[16.0]['persons'][0]['posture']['value'])

    def test_an_expired_ha_snapshot_is_stale_and_carries_no_value(self):
        self.build(ha_until=6)
        at = self.records()
        self.assertEqual(at[5.0]['devices']['pc_input']['status'], 'supplied')
        late = at[18.0]
        for name, item in late['devices'].items():
            self.assertEqual((item['status'], item['value']), ('stale', None), name)
            self.assertGreater(item['age_s'], 8)
        self.assertEqual(late['ambience']['music_playing'], {'value': None, 'status': 'stale'})
        # The detector still counts the person; no PC input means no desk location.
        self.assertEqual(late['occupancy']['count'], 1)
        self.assertIsNone(late['persons'][0]['location']['anchor_id'])

    def test_missing_ha_is_missing_never_a_negative(self):
        self.build(ha=False, persons=lambda s: 0)
        at = self.records()
        r = at[10.0]
        for item in r['devices'].values():
            self.assertEqual((item['value'], item['status']), (None, 'not_provisioned'))
        self.assertEqual(r['ambience']['music_playing'], {'value': None, 'status': 'not_provisioned'})
        self.assertEqual(r['ambience']['daylight'], {'value': None, 'status': 'missing'})
        # No Frigate entity: the detector alone measures an empty room.
        self.assertEqual((r['occupancy']['count'], r['occupancy']['quality']), (0, 'measured'))
        self.assertEqual(r['persons'], [])
        # Bound by the JEV config but no row at all: missing, still no value.
        bound = {'derived.computer_input_recency': 'computer_input_recency', 'derived.computer_active': 'computer_active',
                 'media_player.den': 'den_audio_playback', 'light.den_lamp': 'den_light', 'person.owner': 'owner_home'}
        r = self.records(config={'ha_bindings': bound})[10.0]
        for item in r['devices'].values():
            self.assertEqual((item['value'], item['status']), (None, 'missing'))
        self.assertEqual(r['ambience']['music_playing'], {'value': None, 'status': 'missing'})

    def test_bound_entity_without_a_row_yet_is_missing(self):
        self.build()
        first = self.records()[1.0]
        # Snapshots start 2 s before the session: a fresh one exists at t=1.
        self.assertEqual(first['devices']['pc_input']['status'], 'supplied')
        view = den_context.LedgerView(self.path())
        try:
            rows = den_context.SessionRows(view, view.get_session(self.sid))
            item, ref = den_context.ha_item(rows, 'light.den_lamp', self.start - 5)
        finally:
            view.close()
        self.assertEqual((item, ref), ({'value': None, 'status': 'missing', 'age_s': None}, None))

    def test_two_detected_people_are_multi_person(self):
        self.build(persons=lambda s: 3 if 8 <= s < 14 else 1)
        at = self.records()
        self.assertEqual((at[5.0]['occupancy']['count'], at[5.0]['multi_person']), (1, False))
        r = at[11.0]
        self.assertEqual((r['occupancy']['count'], r['multi_person'], r['occupancy']['quality']), (2, True, 'measured'))
        self.assertEqual([p['pid'] for p in r['persons']], [1])
        self.assertIn('frigate_occupancy', [s['id'] for s in r['occupancy']['sources']])

    def test_frigate_holds_occupancy_when_the_detector_sees_nobody(self):
        self.build(persons=lambda s: 0)
        r = self.records()[10.0]
        self.assertEqual((r['occupancy']['count'], r['occupancy']['quality']), (1, 'inferred'))

    def test_no_coordinates_anywhere(self):
        self.build(persons=lambda s: 2, qwen=[(2.0, 2.5, {'held_object': 'book', 'subject_posture': 'sitting'})])
        records, text, _ = self.run_cli()

        def walk(value, path):
            if isinstance(value, list):
                numbers = [v for v in value if isinstance(v, (int, float)) and not isinstance(v, bool)]
                self.assertFalse(2 <= len(value) <= 3 and len(numbers) == len(value) and any(isinstance(v, float) for v in value)
                                 and path != 'evidence_refs', path)
                for item in value:
                    walk(item, path)
            elif isinstance(value, dict):
                for key, item in value.items():
                    self.assertNotIn(key, ('pos', 'position', 'x', 'y', 'z', 'box', 'head_toward'), path)
                    walk(item, key)
        for record in records:
            walk(record, '')
            for person in record['persons']:
                for value in person['location'].values():
                    self.assertFalse(isinstance(value, list) and 2 <= len(value) <= 3 and all(isinstance(v, float) for v in value))
        self.assertNotIn('"box"', text)

    def test_output_is_deterministic_and_the_ledger_is_untouched(self):
        self.build(answers=[(3.5, {'phone_use': .6, 'insufficient_evidence': .4})])
        before = os.path.getmtime(self.path()), os.path.getsize(self.path())
        _, first, _ = self.run_cli(name='one.jsonl')
        _, second, _ = self.run_cli(name='two.jsonl')
        self.assertEqual(first, second)
        self.assertEqual((os.path.getmtime(self.path()), os.path.getsize(self.path())), before)

    def test_tick_files_are_averaged_into_the_hypotheses(self):
        self.build(recency=lambda s: 'over_5_min')
        classes = list(lab_eval.CLASSES)
        ticks = str(Path(self.directory.name) / 'ticks.jsonl')
        with open(ticks, 'w', encoding='utf-8') as out:
            out.write(json.dumps({'schema': 'lab-ticks-1', 'run': 'rules', 'arm': 'E', 'consumes_qwen': True,
                                  'calibration': 'distribution', 'classes': classes, 'source': {}}) + '\n')
            for k in range(1, DURATION + 1):
                for arm, cls in (('a', 'reading'), ('b', 'phone_use')):
                    probabilities = dict({c: 0.0 for c in classes}, **{cls: .5, 'abstain': .5})
                    out.write(json.dumps({'session_id': self.sid, 'arm': arm, 't': float(k), 'available_at': float(k),
                                          'closed_at': k + 1.0, 'probabilities': probabilities}) + '\n')
        records, _, _ = self.run_cli('--ticks', ticks)
        person = records[4]['persons'][0]
        self.assertEqual(person['activity_hypotheses'], {'reading': 1.0})
        self.assertEqual(person['hypothesis_sources'], ['ticks:E/rules'])


if __name__ == '__main__':
    unittest.main()

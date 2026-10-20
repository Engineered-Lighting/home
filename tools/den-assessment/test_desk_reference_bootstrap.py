"""Candidate-free owner-reference capture through the real bounded writer.

Only entry clocks are authored. Windows directory-fsync is replaced in the
same explicit test-only scope as incumbent Guide tests; file fsync stays real.
This cannot prove target POSIX durability or real reference qualification.
"""
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(r'C:\Claude\home')
for folder in (ROOT/'tools/den-assessment',ROOT/'stack/services/perception-lab',ROOT/'stack/services/home-context'):
    if str(folder) not in sys.path:sys.path.insert(0,str(folder))
import desk_reference_bootstrap as bootstrap
from desk_method_assessment import _math, PARTS, REQUIREMENTS, HeldDeskAssessment


def references():
    return dict(frame='z_up_metric_floor0',unit='m',
        equipment_ref=dict(revision=1,digest='a'*64,file_sha256='b'*64),
        binding=dict(content_revision='authored-room',registration_version=1,
            manifest_digest='c'*64,frame_digest='d'*64,canonical_collision_digest='e'*64),
        floor=dict(id='authored-floor',normal=[0,0,1],offset_m=0),
        walls=[dict(id='authored-wall-x',label='wall X',normal_xy=[1,0],offset_m=0),
            dict(id='authored-wall-y',label='wall Y',normal_xy=[0,1],offset_m=0)],
        reference_uncertainty_m=.005)


def measured_point(x,y,z):
    return dict(wall_distances_m=[x,y],height_above_floor_m=z,uncertainty_m=.01)


def partial_parts():
    result = {name:dict(status='unknown',outer_corners=None,thickness_m=None) for name in PARTS}
    result['work'] = dict(status='available',thickness_m=.02,
        outer_corners=[measured_point(1,1,1),measured_point(2,2,1)])
    result['head'] = dict(status='available',thickness_m=0,
        outer_corners=[measured_point(1.2,1.3,1.4),measured_point(1.6,1.7,1.8)])
    result['body']['status'] = 'unavailable'
    result['hand_right']['status'] = 'unavailable'
    return result


class IndependentReferenceCaptureTests(unittest.TestCase):
    def build(self, *, bad_ack=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        guide, _ = _math()
        # Native directory fsync on POSIX remains a real target prerequisite.
        context = patch.object(guide,'sync_reference_directory') if sys.platform == 'win32' else nullcontext()
        with context:writer = guide.ReferenceActions(Path(directory.name)/'references')
        self.addCleanup(writer.close,'authored_test_complete')
        state = dict(utc=100.,mono=1_000_000_000)
        def record(action):
            acknowledgement = writer.record(action)
            return dict(status='retained',sha256='f'*64) if action['kind'] == bad_ack else acknowledgement
        core = bootstrap.IndependentReferenceCapture(references(),clock=lambda:state['utc'],
            monotonic=lambda:state['mono'],record=record)
        protocol = dict(id='authored-held-protocol',reference_method='tape',
            supported_configuration_ids=['whole-desk'],max_windows=2)
        requirements = dict.fromkeys(REQUIREMENTS,None)
        return core,writer,state,protocol,requirements

    def freeze(self,core,protocol,requirements):
        return core.freeze(protocol,requirements,selected_anchor_label='owner named reference point; anatomical correspondence unqualified')

    def start(self,core,window='reference-1'):
        return core.start(window,'whole-desk',partial_parts(),
            dict(status='available',original_measurement=measured_point(1.4,1.5,1.6)),.02,'unknown')

    def rows(self,writer):
        return [json.loads(line) for line in (writer.directory/'actions.jsonl').read_bytes().splitlines()]

    def test_actual_writer_records_desk_head_and_explicit_unknown_parts_without_candidate(self):
        core,writer,state,protocol,requirements = self.build()
        first = self.freeze(core,protocol,requirements)
        self.assertEqual(first['status'],'frozen')
        self.assertEqual(self.freeze(core,protocol,requirements),first)
        self.assertEqual(self.rows(writer)[0]['body']['requirements']['body'],dict.fromkeys(REQUIREMENTS,None))
        state.update(utc=100.1,mono=1_100_000_000)
        started = self.start(core)
        self.assertEqual((started['status'],started['window_id']),('started','reference-1'))
        active = core.read_results()
        self.assertEqual((active['status'],active['open_windows']),('incomplete',1))
        self.assertIsNone(core.windows[0]['ended'])
        self.assertEqual(self.rows(writer)[-1]['kind'],'results')
        count = writer.count
        self.assertEqual(core.read_results(),active)
        self.assertEqual(writer.count,count)
        state.update(utc=101.1,mono=2_100_000_000)
        ended = core.end('reference-1')
        self.assertEqual((ended['status'],ended['duration_s']),('ended',1.))
        self.assertEqual(core.end('reference-1'),ended)
        result = core.read_results()
        self.assertEqual((result['status'],result['recorded_windows'],result['ended_windows'],result['open_windows']),
            ('references_recorded',1,1,0))
        self.assertEqual(result['missingness']['hand_left'],dict(available=0,unavailable=0,unknown=1))
        self.assertEqual(result['missingness']['hand_right'],dict(available=0,unavailable=1,unknown=0))
        self.assertEqual(result['operational_qualification']['decision'],'block')
        self.assertFalse(any(result['authority'].values()))
        self.assertFalse(result['candidate_available']);self.assertFalse(result['qualified'])
        self.assertNotIn('reference_document',result)
        retained = self.rows(writer)[-1]['body']['reference_document']
        self.assertIsNone(retained['candidate_ref']);self.assertIsNone(retained['collection_binding'])
        self.assertIsNone(retained['clock']['qualification'])
        window = retained['windows'][0]
        self.assertEqual(set(window['request']['reference_parts']),set(PARTS))
        head = window['request']['reference_parts']['head']
        guide, _ = _math()
        self.assertEqual(head['shape'],guide.outer_box(head['outer_corners'],references(),require_volume=True))
        self.assertEqual(window['request']['selected_anchor']['point_m'],[1.4,1.5,1.6])
        for name in ('body','hand_left','hand_right','protection'):
            self.assertIsNone(window['request']['reference_parts'][name]['shape'])
            self.assertIsNone(window['request']['reference_parts'][name]['outer_corners'])

    def test_raw_missingness_identity_and_independent_freeze_refuse_fabrication(self):
        core,writer,state,protocol,requirements = self.build()
        with self.assertRaisesRegex(ValueError,'freeze_independent_protocol_first'):self.start(core)
        for invalid in ({},[]):
            changed = deepcopy(protocol);changed['supported_configuration_ids'] = [invalid]
            with self.assertRaisesRegex(ValueError,'opaque_reference_identity_required'):
                self.freeze(core,changed,requirements)
        self.assertEqual(writer.count,0)
        self.freeze(core,protocol,requirements)
        before = writer.count
        parts = partial_parts();parts['hand_left']['outer_corners'] = partial_parts()['head']['outer_corners']
        with self.assertRaisesRegex(ValueError,'missing_reference_has_no_measurements'):
            core.start('reference-1','whole-desk',parts,dict(status='unknown',original_measurement=None),0,'unknown')
        with self.assertRaisesRegex(ValueError,'missing_anchor_has_no_measurement'):
            core.start('reference-1','whole-desk',partial_parts(),
                dict(status='unknown',original_measurement=measured_point(1,1,1)),0,'unknown')
        changed = deepcopy(requirements);changed['min_containment_fraction'] = .9
        with self.assertRaisesRegex(ValueError,'independent_freeze_identity_changed'):
            self.freeze(core,protocol,changed)
        self.assertEqual(writer.count,before)
        self.assertIsNone(core.active)
        # The pre-existing full-candidate boundary remains strict.
        with self.assertRaisesRegex(ValueError,'existing_unqualified_candidate2_required'):
            HeldDeskAssessment(None,clock=lambda:100,monotonic=lambda:1,verified_collection_loader=lambda:{},record=writer.record)

    def test_bad_durable_ack_never_promotes_freeze_start_or_end(self):
        for kind in ('freeze','start','end'):
            with self.subTest(kind=kind):
                core,writer,state,protocol,requirements = self.build(bad_ack=kind)
                if kind != 'freeze':self.freeze(core,protocol,requirements)
                if kind == 'end':self.start(core);state.update(utc=101.,mono=2_000_000_000)
                function = (lambda:self.freeze(core,protocol,requirements)) if kind == 'freeze' else (
                    (lambda:self.start(core)) if kind == 'start' else (lambda:core.end('reference-1')))
                with self.assertRaisesRegex(ValueError,'reference_capture_recording_incomplete'):function()
                self.assertTrue(core.failed)
                self.assertIsNone(core.frozen if kind == 'freeze' else core.active if kind == 'start' else core.windows[0]['ended'])
                with self.assertRaisesRegex(ValueError,'reference_capture_recording_incomplete'):core.read_results()

    def test_open_interval_clock_limits_and_capacity_refusal_preserve_prior_results(self):
        core,writer,state,protocol,requirements = self.build()
        self.freeze(core,protocol,requirements);self.start(core)
        state.update(utc=99.,mono=900_000_000)
        count = writer.count
        with self.assertRaisesRegex(ValueError,'reference_entry_clock_regressed'):core.end('reference-1')
        self.assertEqual(writer.count,count);self.assertIsNone(core.windows[0]['ended'])
        self.assertTrue(core.failed)
        state.update(utc=101.,mono=2_000_000_000)
        with self.assertRaisesRegex(ValueError,'reference_capture_recording_incomplete'):core.end('reference-1')
        with self.assertRaisesRegex(ValueError,'reference_capture_recording_incomplete'):core.read_results()
        late,_,late_state,late_protocol,late_requirements = self.build()
        self.freeze(late,late_protocol,late_requirements);self.start(late)
        late_state.update(utc=401.,mono=302_000_000_000)
        with self.assertRaisesRegex(ValueError,'bounded_positive_held_interval_required'):late.end('reference-1')
        self.assertIsNone(late.windows[0]['ended'])
        self.assertEqual(late.read_results()['status'],'incomplete')
        # Refusal never records/qualifies a repaired time. A separate authored
        # capture exercises pre-Start capacity and preserves already ended work.
        other,writer2,state2,protocol2,requirements2 = self.build()
        self.freeze(other,protocol2,requirements2);self.start(other)
        state2.update(utc=101.,mono=2_000_000_000);other.end('reference-1')
        count = writer2.count
        with patch.object(bootstrap,'MAX_RECORD_BYTES',other.recorded_bytes+1024):
            with self.assertRaisesRegex(ValueError,'reserved_reference_end_results_capacity'):
                self.start(other,'reference-2')
        self.assertEqual(writer2.count,count);self.assertIsNone(other.active)
        self.assertEqual(other.read_results()['status'],'references_recorded')

"""Authored RGB-conditioned assessment through actual Store/Journal/worker/replay.

All camera, person, independent assessment and applicability inputs are generated
fixtures. This does not implement a real desk capability, derive stereo anchors,
qualify household geometry or start any model/device owner.
"""
from copy import deepcopy
import http.client
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
for directory in (ROOT / 'stack/services/home-context', ROOT / 'tools/den-live-runtime'):
    if str(directory) not in sys.path:
        sys.path.append(str(directory))

from journal import PREFIX, digest, canonical
from live_capture import _original_digest
from live_test_helpers import NOW, TOKEN
from live_worker_test_helpers import describe
from controlled_external_io import assemble_journal_grants
from pc_support_configuration import upgrade_configuration
from lab_desk_capability import TrustedDeskCapability
import lab_live_runtime as runtime
import test_lab_live_semantic_runtime as semantic_fixture
import test_lab_live_desk_admission as desk_fixture
import test_runtime as replay_fixture


ALLOCATE_CURRENT_CUT = r"""
'use strict';
const fs=require('node:fs'),D=require('./app/src/home-live-task-delivery.js');
const L=require('./app/src/home-lighting-target-contract.js');
const v=JSON.parse(fs.readFileSync(0,'utf8')),cut=v.cut;
const ref=p=>({...Object.fromEntries(['stream_epoch','cursor','producer_id','id','payload_digest'].map(k=>[k,p[k]])),component_id:null});
const rows=cut.items.filter(p=>p.payload_type==='home-live-geometry-1');
const work=rows.find(p=>p.payload.source_id==='surveyed-desk'),g=work.payload;
const until=g.valid_until,refs=rows.map(ref),d=D.createDelivery({identity:v.identity,policy:v.policy,
  lifecycle_policy:v.lifecycle,max_output_age_s:3});
let seq=0;
function input(t,empty=false){return {t,seq:++seq,
  scene:empty?{status:'unavailable',reason:'authored_empty'}:{status:'selected',scene_id:'authored-stationary-scene',
    episode_id:'authored-stationary-episode',person_ref:g.person_ref,subject_ref:g.subject_ref,nominal:v.nominal,valid_until:until},
  goal:empty?{status:'unavailable',reason:'authored_empty'}:{status:'requested',goal_id:'authored-stationary-goal',kind:'illuminate_work',
    person_ref:g.person_ref,subject_ref:g.subject_ref,episode_id:'authored-stationary-episode',scene_id:'authored-stationary-scene',
    geometry_refs:[ref(work)],requirements_ref:v.policy.requirements_ref,nominal:v.nominal,valid_until:until},
  geometries:empty?[]:rows.map(p=>p.payload),geometry_refs:empty?[]:refs,
  facts:empty?[]:cut.items.filter(p=>p.payload_type==='home-live-fact-1').map(p=>p.payload),lifecycles:[],
  publication_refs:empty?{}:Object.fromEntries(cut.items.map(p=>[p.id,ref(p)])),
  pending_clearance_intents:[],intent_refs:{},authority_generation:cut.revocation_generation,
  source_generations:cut.source_generations,input_cut_digest:L.digest(cut)};}
const first=d.step(input(v.bootstrap_at,true)),at=v.bootstrap_at+1;
const value={...v.lifecycle.operator_clearance,hazard_refs:first.person_hazards.items.map(h=>h.hazard_ref),inspection_at:at};
delete value.max_age_s;
const intent={schema:'home-live-intent-event-2',version:2,id:'authored-allocator-inspection',...v.identity,
  manifest_ref:v.manifest_ref,actor_ref:'controlled-owner',origin:'human_declaration',truth_role:'runtime_intent',
  kind:'clear_person_hazards',target:null,set_at:at,available_at:at,valid_until:at+5,reply_to:null,value};
const ir={stream_epoch:'authored-inspection-stream',cursor:1,producer_id:'controlled-owner',id:intent.id,
  payload_digest:L.digest(intent),component_id:null};
const cleared=d.step({...input(at,true),pending_clearance_intents:[intent],intent_refs:{[intent.id]:ir}});
if(cleared.intent_results[0]?.status!=='applied')throw Error('authored_allocator_clearance_failed');
const start=d.step(input(v.now)),later=d.step(input(v.now+1));
process.stdout.write(JSON.stringify({classification:'authored_actual_cut_feasibility_only',
  source_geometry_id:g.id,source_geometry_digest:L.digest(g),parts:g.parts,
  start:{allocation:start.allocation,execution:start.execution},later:{allocation:later.allocation,execution:later.execution}}));
"""


class AuthoredRGBCapability(TrustedDeskCapability):
    """Fictional pre-assessed RGB-conditioned source, never a real classifier."""
    def __init__(self, owner):
        self.owner, self.originals, self.descriptors = owner, {}, []
        self.closed, self.unavailable, self.wrong_frame = False, False, False
        self.on_select = None
        self.expected = runtime.desk_capability_binding(owner.producer['live_config']['manifest']['identity'],
            owner.producer['desk_policy'], owner.lab_binding_digest)

    def binding(self):
        return deepcopy(self.expected)

    def select(self, descriptor):
        self.descriptors.append(deepcopy(descriptor))
        if self.on_select:
            self.on_select()
        if self.closed or self.unavailable:
            return {'status': 'unavailable', 'reason': 'missing_applicability'}
        value = deepcopy(self.owner.prototype)
        a, b = value['anchor'], value['binding']
        frame, publication = descriptor['frame_metadata'], descriptor['publication']
        observed = publication['payload']['observed_at']
        a.update(id='authored-rgb-anchor:' + str(descriptor['source_position']['capture_seq']),
                 observed_at=observed, available_at=self.owner.f.now, valid_until=observed + 2,
                 source_generation=descriptor['source_generation'], source_position=deepcopy(descriptor['source_position']))
        if self.wrong_frame:
            a['source_position']['capture_seq'] += 1
        a['clock'].update(original_value=str(frame['source_timing_receipt']['observed_at_unix_ns']))
        b.update(source_generation=a['source_generation'], source_position=deepcopy(a['source_position']),
                 parent_publication_refs=[deepcopy(descriptor['publication_ref'])])
        b['anchor_original_ref'].update(receipt_id=a['id'],
            digest=desk_fixture.distinct.consumer.digest(a), observed_at=observed, available_at=a['available_at'])
        value.update(capture_ref=deepcopy(descriptor['publication_ref']), capture_original=deepcopy(descriptor['original']),
                     capture_record=deepcopy(descriptor['original_record']))
        ref = b['anchor_original_ref']
        self.originals[canonical(ref)] = value
        return {'status': 'selected', 'anchor_ref': deepcopy(ref)}

    def resolve_inputs(self, anchor_ref):
        return deepcopy(self.originals[canonical(anchor_ref)])

    def quiesce(self):
        self.closed = True
        return {'status': 'quiescent', 'in_flight': False}


class LabLiveDeskRuntimeTests(unittest.TestCase):
    replay = replay_fixture.ActualOrdinaryReplayTests.replay

    def setUp(self):
        self.base = semantic_fixture.LabLiveSemanticRuntimeTests()
        temporary_directory = tempfile.TemporaryDirectory
        def writable_temporary(*args, **kwargs):
            # Only the external authored-file location differs: this runner's
            # isolated source checkout is read-only for native child processes.
            # Default OS temporary storage is writable, exclusive and cleaned.
            kwargs.pop('dir', None)
            return temporary_directory(*args, **kwargs)
        with mock.patch('tempfile.TemporaryDirectory', side_effect=writable_temporary):
            self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.f = self.base.f
        authored = desk_fixture.LabLiveDeskAdmissionTests()
        with mock.patch('tempfile.TemporaryDirectory', side_effect=writable_temporary):
            authored.setUp()
        self.addCleanup(authored.doCleanups)
        config, worker = deepcopy(self.base.producer['live_config']), deepcopy(self.base.worker_config)
        self.prototype, policy = deepcopy(authored.values), deepcopy(authored.policy)
        # The fictional camera registration uses the already assessed modeled
        # room geometry, rather than attaching a new geometry digest to an old
        # room assessment. Rebind every dependent original before commissioning.
        room_registration = worker['delivery_policy']['registration']
        r = self.prototype['registration']
        r.update(site_id=config['manifest']['identity']['site_id'],
                 room_id=config['manifest']['identity']['room_id'],
                 geometry_revision=room_registration['geometry_revision'],
                 geometry_digest=room_registration['geometry_digest'])
        self.prototype['projection']['site_id'] = r['site_id']
        policy['projection_ref'] = {'id': self.prototype['projection']['id'],
                                    'digest': digest(self.prototype['projection'])}
        registration_ref = {'id': r['id'], 'digest': digest(r)}
        self.prototype['binding']['registration_ref'] = deepcopy(registration_ref)
        self.prototype['assessment']['registration']['ref'] = deepcopy(registration_ref)
        assessment_ref = {'id': self.prototype['assessment']['id'],
                          'digest': desk_fixture.distinct.consumer.digest(self.prototype['assessment'])}
        self.prototype['binding']['assessment_ref'] = deepcopy(assessment_ref)
        policy['assessment_ref'] = deepcopy(assessment_ref)
        registration = deepcopy(authored.config['manifest']['sources']['synthetic-source']['registration'])
        registration.update(registration_digest=digest(r), geometry_revision=r['geometry_revision'],
                            geometry_digest=r['geometry_digest'])
        camera = config['manifest']['sources']['synthetic-source']
        camera['registration'] = deepcopy(registration)
        desk = deepcopy(authored.config['manifest']['sources']['surveyed-desk'])
        for key in ('capture_epoch', 'capture_clock', 'control_clock', 'sensor_roots'):
            desk[key] = deepcopy(camera[key])
        desk['registration'] = deepcopy(registration)
        # This separate fictional method has known rigid stationary ground
        # truth: every authored frame uses these identical surveyed shapes.
        # Reuse the nonzero radius/motion contract already independently
        # authored in test_desk_enclosure_feasibility.py's ALLOCATE, rather
        # than copying the unrelated generic wrist/depth estimator bounds.
        self.authored_stationary_method = {
            'schema': 'authored-fixed-desk-error-method-1', 'version': 1,
            'method_ref': deepcopy(policy['method_ref']), 'candidate_digest': policy['candidate_digest'],
            'assessment_ref': deepcopy(policy['assessment_ref']),
            'truth': 'identical_surveyed_shapes_and_anchor_in_all_authored_frames',
            'parts': ['work', 'body', 'head', 'hand_left', 'hand_right', 'protection'],
            'additional_radius_m': .01, 'max_speed_m_s': .01, 'max_accel_m_s2': .01,
            'provenance': 'test_desk_enclosure_feasibility.py:ALLOCATE'}
        applicability = {'id': 'authored-fixed-desk-error-method', 'digest': digest(self.authored_stationary_method)}
        for name in self.authored_stationary_method['parts']:
            part = desk['part_policies'][name]
            part.update(bound_at_observation_m=.01,
                components_m={'capture_association': 0, 'depth': 0, 'registration': 0, 'estimator': .01},
                applicability_ref=deepcopy(applicability))
            part['age'].update(max_speed_m_s=.01, max_accel_m_s2=.01)
        config['manifest']['sources']['surveyed-desk'] = desk
        # Reuse the existing conservative delivery profile and the passed
        # distinct-part fixture placement. Room1 requires four assessed task
        # candidates; the actual allocator selects one. No measured part is
        # shrunk and none of these declarations represents physical hardware.
        task = worker['delivery_policy']
        work_center = self.prototype['candidate']['work_patch']['center_m']
        task['fixture']['origin_m'] = [work_center[0], work_center[1] - 2, 3]
        task['fixture']['initial'] = {'pan_deg': 90, 'tilt_deg': 45}
        for index, fixture in enumerate(worker['room_policy']['fixtures']):
            fixture['task_policy'] = deepcopy(task)
            fixture['task_policy']['fixture'].update(id=fixture['fixture_id'],
                fixture_pose_ref=deepcopy(fixture['pose_ref']))
            fixture['task_policy']['fixture']['origin_m'][0] += index * .05
        # The generic room fixture uses a small relative example clock. These
        # independent, fictional STATIC assessments are commissioned before
        # source admission for this declared 30-second epoch-clock run. Never
        # apply this conversion to dynamic geometry, protection or evidence.
        static_until = NOW + 40
        worker['room_policy']['valid_until'] = static_until
        worker['room_policy']['exposure']['valid_until'] = static_until
        for fixture in worker['room_policy']['fixtures']:
            fixture['valid_until'] = static_until
            for domain in fixture['ambient']:
                domain['valid_until'] = static_until
        for node in (worker['room_policy'], task,
                     *(f['task_policy'] for f in worker['room_policy']['fixtures'])):
            node['registration'] = deepcopy(registration)
            if 'protection' in node:
                node['protection']['policy_ref'] = deepcopy(policy['protection_policy_ref'])
                node['required_body_parts'] = [policy['body_primitive_id']]
        pc = worker['pc_support_policy']
        pc['bindings'][0].update(geometry_source_id='surveyed-desk', geometry_method_ref=deepcopy(policy['method_ref']),
            association_method_ref=deepcopy(policy['association_method_ref']),
            subject_ref=deepcopy(self.prototype['binding']['subject_ref']))
        semantics = deepcopy(self.base.producer['semantic_policy'])
        semantics.update(schema='home-lab-semantic-admission-policy-3', version=3,
                         root_lineage='persisted-observation-record-1')
        for family in ('ha', 'jev'):
            semantics[family]['association_method_ref'] = deepcopy(policy['association_method_ref'])
            source = config['manifest']['sources'][semantics[family]['source_id']]
            if policy['association_method_ref'] not in source['association_method_refs']:
                source['association_method_refs'].append(deepcopy(policy['association_method_ref']))
        # Explicitly close a new authored config12 after commissioning the new
        # source; never mutate the accepted manifest underneath an open Journal.
        config.update(schema='home-live-ingress-config-9', version=9)
        config['manifest'].update(schema='home-live-ingress-manifest-9', version=9)
        config['manifest'].pop('pc_support_interface_digest')
        config['manifest']['worker'].update(room_policy=deepcopy(worker['room_policy']),
                                            delivery_policy=deepcopy(worker['delivery_policy']))
        for key in ('pc_support_policy', 'pc_support_schema_pins'):
            config['manifest']['worker'].pop(key, None)
        config, worker = upgrade_configuration(config, worker, pc)
        semantics['pc_support_policy_digest'] = digest(worker['pc_support_policy'])
        grants = assemble_journal_grants(config, worker, publication_token=TOKEN,
            worker_token=self.base.worker_token, human_token='authored-desk-runtime-owner')
        for grant in grants:
            grant['revisions'] = list(dict.fromkeys([*grant['revisions'], 'synthetic-r1', worker['revision']]))
        self.producer = deepcopy(self.base.producer)
        self.producer.update(schema='home-incumbent-lab-producer-3', version=3, live_config=config,
            semantic_policy=semantics, desk_policy=policy, journal_grants=grants)
        self.producer['limits'].update(max_cycles=24, max_runtime_s=30)
        self.producer['original_archive']['max_records'] = 48
        self.worker = worker
        self.f.config, self.f.source = config, config['manifest']['sources']['synthetic-source']
        join, graph_config = runtime._join_and_configuration(self.producer, self.f.session)
        self.lab_binding_digest = join.binding_digest
        self.capability = AuthoredRGBCapability(self)
        self.composition = self.graph = self.recorder = self.driver = self.rpc = None
        self.addCleanup(self.cleanup)

    def compose(self, capability=None):
        self.composition = runtime.LabRuntimeComposition(self.producer, store=self.f.store,
            preflight=lambda: True, control_clock=self.f.control_clock,
            desk_capability=self.capability if capability is None else capability,
            clock=lambda: self.f.now, monotonic=lambda: self.f.now - NOW)
        self.base.base.composition = self.composition
        return self.composition

    @staticmethod
    def actual_shaped_lab(row):
        # Actual incumbent detector/pose envelopes omit evidence_roots. This
        # authors that wire shape BEFORE Store admission; canonical persisted
        # observation_record then supplies the immutable original event ID.
        row['payload'].update(arm='b')
        row['payload'].pop('evidence_roots', None)

    def create(self, *, recorded=False):
        self.graph = self.compose().create()
        self.f.graph, self.f.originals = self.graph, self.composition.originals
        if recorded:
            self.record()
        else:
            self.graph.journal.attach_recorder(self.f.record)
        return self.graph

    def record(self):
        from archive import Recorder, RecorderRPC
        from supervisor import NodeWorkerDriver
        self.work = self.f.work
        sanitized = {k: deepcopy(v) for k, v in self.worker.items()
                     if k not in {'token', 'token_file', 'service_url'}}
        description = describe(sanitized)
        self.manifest = {'schema': 'home-live-session-manifest-1', 'version': 1,
            'session_ref': deepcopy(self.graph.join.scope), 'configuration_digest': description['config_digest'],
            'source_bundle_digest': 'b' * 64, 'worker_config': sanitized, 'worker_description': description}
        self.archive = self.f.path / 'recorded-desk.jsonl'
        self.recorder = Recorder(self.archive, self.manifest, max_records=512,
            max_bytes=16 * 1024**2, min_free_bytes=1)
        self.graph.journal.attach_recorder(self.recorder.record)
        self.rpc = RecorderRPC(self.recorder)
        port = self.rpc.start()
        self.driver = NodeWorkerDriver(node_runtime=shutil.which('node'),
            config={**deepcopy(self.worker), 'token': self.base.worker_token,
                    'service_url': 'http://127.0.0.1:' + str(self.graph.address[1])},
            recorder=port, controlled_time=self.f.now)

    def cleanup(self):
        if self.driver is not None:
            self.driver.close()
        if self.rpc is not None:
            self.rpc.close()
        if self.recorder is not None:
            self.recorder.drain({'status': 'incomplete', 'reason': 'authored_cleanup'})

    def publications(self):
        return [json.loads(row[0]) for row in self.graph.journal.db.execute('SELECT body FROM publications ORDER BY cursor')]

    def records(self):
        return [json.loads(line)['record'] for line in self.archive.read_text(encoding='utf8').splitlines()]

    def tick(self):
        before = len(self.records())
        self.driver.cycle(self.f.now)
        fresh = self.records()[before:]
        accepted = {row['data']['receipt']['id'] for row in fresh if row['kind'] == 'worker_commit_result'
                    and row['data']['receipt']['status'] == 'accepted'}
        decisions = [row['data']['prepared']['decision'] for row in fresh if row['kind'] == 'worker_prepared'
                     and row['data']['prepared']['commit_id'] in accepted]
        self.assertEqual(len(decisions), 1, {'records': fresh, 'worker': self.driver.status()})
        return decisions[0]

    def clear_authored_bootstrap(self):
        first = self.tick()
        # The ordinary worker's declared semantic cadence is one second.
        # A faster cut is valid but intentionally produces no new decision.
        self.f.now += 1
        value = {key: deepcopy(value) for key, value in self.worker['lifecycle_policy']['operator_clearance'].items()
                 if key != 'max_age_s'}
        value.update(hazard_refs=[item['hazard_ref'] for item in first['person_hazards']['items']],
                     inspection_at=self.f.now)
        intent = dict(schema='home-live-intent-event-2', version=2, id='authored-desk-room-inspection',
            **self.graph.join.scope, manifest_ref=deepcopy(self.worker['manifest_ref']),
            actor_ref='controlled-owner', origin='human_declaration', truth_role='runtime_intent',
            kind='clear_person_hazards', target=None, set_at=self.f.now, available_at=self.f.now,
            valid_until=self.f.now+5, reply_to=None, value=value)
        connection = http.client.HTTPConnection('127.0.0.1', self.graph.address[1], timeout=4)
        try:
            connection.request('POST', PREFIX + '/intent-events', json.dumps(intent),
                {'Authorization': 'Bearer authored-desk-runtime-owner', 'Content-Type': 'application/json'})
            response = connection.getresponse()
            result = json.loads(response.read())
            self.assertEqual(response.status, 200, result)
        finally:
            connection.close()
        self.assertEqual(self.tick()['intent_results'][0]['status'], 'applied')

    def advance_capture_and_input(self, seq):
        observed = float(NOW + seq)
        self.f.now = observed + .55
        self.f.add_original(observed=observed, seq=7+seq,
                            mutation=self.actual_shaped_lab)
        ha = semantic_fixture.authored_pc_fixture(seq=seq, snapshot_at=observed+.1,
                                                 input_at=observed-.1)['original']['ha_event']
        self.f.store.append_event(self.f.session['id'], 'ha.observation', ha['payload'])
        capture = self.graph.step()
        self.assertEqual(capture['status'], 'admitted_desk', capture)
        pc = self.graph.step()
        self.assertEqual(pc['status'], 'admitted_attributed', pc)
        facts = [row['payload'] for row in self.publications()
                 if row['payload'].get('source_id') == 'ha-semantic']
        self.assertEqual(facts[-1]['value'], 'attributed_recent', pc)
        return self.tick()

    def admit_current_chain(self):
        """Author each source at its causal time, after actual parent admission."""
        self.f.now = NOW + 2.35
        camera = self.f.add_original(observed=NOW+2, seq=8,
                                    mutation=self.actual_shaped_lab)
        ha = semantic_fixture.authored_pc_fixture(seq=2, snapshot_at=NOW+2.1,
                                                  input_at=NOW+1.9)['original']['ha_event']
        hid = self.f.store.append_event(self.f.session['id'], 'ha.observation', ha['payload'])
        ha = next(row for row in self.f.store.list_events(self.f.session['id']) if row['id'] == hid)
        first = self.graph.step()
        self.assertEqual(first['status'], 'admitted_desk', first)
        pc = self.graph.step()
        self.assertEqual(pc['status'], 'admitted_attributed', pc)
        # This is a NEW authored request after the actual surveyed publication.
        # Historical request/availability stamps are never moved forward.
        packet = {'model': 'jev-1.13.0', 'state': {'schema': 'den-facts-7', 'now': {
            'people_in_view': 'one', 'camera_evidence': 'current'}, 'devices': {
            'pc_input': 'under_15s', 'pc_active': 'on'}}, 'questions': {'activity': {'instructions': 'activity?'}}}
        evidence, timeline = [str(camera['id']), str(hid)], [str(camera['id'])]
        rid, cutoff, available = 'authored-desk-current-jev', NOW+2.36, NOW+2.4
        provenance = {'request_id': rid, 'session_id': self.f.session['id'], 'site_id': 'victoria',
            'arm': 'b', 'generation': 1, 'observed_at': cutoff, 'deadline': NOW+15,
            'wire_version': 'den-facts-7', 'response_version': 'jev-1.13.0', 'input_digest': digest(packet),
            'evidence_ids': evidence, 'timeline_ids': timeline,
            'evidence_digest': digest([camera['payload'], ha['payload']])}
        self.f.now = NOW + 2.38
        self.f.store.append_event(self.f.session['id'], 'reasoning.request', {
            'request_id': rid, 'arm': 'b', 'generation': 1, 'available_at': NOW+2.38,
            'packet_sha256': digest(packet), 'request': packet, 'status': 'admitted_before_transport',
            'wire_version': 'den-facts-7', 'evidence_ids': evidence, 'timeline_ids': timeline,
            'evidence_digest': provenance['evidence_digest'], 'timeline_verified': 'full'})
        self.f.now = available
        self.f.store.append_event(self.f.session['id'], 'reasoning.delivery', {
            'request_id': rid, 'arm': 'b', 'generation': 1, 'available_at': available,
            'state': 'completed', 'status': 'ready', 'current': True})
        runner = semantic_fixture.LiveReasoning.__new__(semantic_fixture.LiveReasoning)
        runner.session, runner.sid, runner.policy = self.f.session, self.f.session['id'], {}
        runner.store = self.f.store
        provider = {'status': 'ready', 'judgment': {'activity': 'computer_work', 'confidence': .8,
            'probabilities': {key: 1. if key == 'computer_work' else 0. for key in semantic_fixture.ACTIVITIES},
            'model_version': 'jev-1.13.0'}, 'diagnostics': {'request': packet}, 'provenance': provenance}
        output = runner._output({'request_id': rid, 'site_id': 'victoria', 'arm': 'b', 'generation': 1,
            'cutoff': cutoff, 'deadline': provenance['deadline'], 'records': [{**camera['payload'],
                'source_kind': 'perception.output', 'objects': [{'label': 'person'}]}]}, provider, available)
        self.f.store.append_event(self.f.session['id'], 'reasoning.output', output)
        self.f.now = NOW + 2.45
        jev = self.graph.step()
        self.assertEqual(jev['status'], 'admitted_attributed', jev)
        return first

    def test_absent_or_json_capability_refuses_before_any_graph_or_archive(self):
        for capability in (None, {'scope': self.capability.expected}):
            with self.subTest(capability=capability), self.assertRaisesRegex(ValueError, 'trusted_desk_capability'):
                runtime.LabRuntimeComposition(self.producer, store=self.f.store,
                    preflight=lambda: True, control_clock=self.f.control_clock,
                    desk_capability=capability, clock=lambda: self.f.now)
        self.assertFalse(Path(self.producer['journal_path']).exists())
        self.assertFalse(Path(self.producer['original_archive']['filename']).exists())

    def test_actual_factory_capture_desk_semantics_worker_archive_and_ordinary_replay(self):
        from home_den_runtime.lab_original_archive import read_lab_originals
        # The actual initial worker reconciles bootstrap history through the
        # existing attributed inspection intent before any camera admission.
        # No work geometry, relation, scene, decision or checkpoint is seeded.
        self.f.now = NOW + .3
        graph = self.create(recorded=True)
        self.clear_authored_bootstrap()
        self.assertEqual(graph.config['version'], 3)
        first = self.admit_current_chain()
        self.assertEqual(first['after_id'], self.capability.descriptors[0]['original']['event']['id'])
        self.assertEqual(first['desk']['event']['payload']['parent_refs'], [])
        self.assertEqual(first['desk']['event']['payload']['parts']['body']['basis'], 'inferred')
        captured = self.capability.descriptors[0]
        self.assertNotIn('evidence_roots', captured['original']['event']['payload'])
        self.assertEqual(captured['publication']['payload']['observation_roots'],
                         [str(captured['original']['event']['id'])])
        self.assertEqual(first['desk']['event']['payload']['observation_roots'],
                         captured['publication']['payload']['observation_roots'])
        self.tick()
        decisions = [self.advance_capture_and_input(seq) for seq in range(3, 11)]
        saved = graph.journal.db.execute('SELECT checkpoint FROM workers WHERE worker_id=?',
                                        (self.worker['worker_id'],)).fetchone()
        self.assertIsNotNone(saved)
        checkpoint = json.loads(saved[0])
        self.assertEqual(checkpoint['schema'], 'home-live-worker-checkpoint-11')
        relations = list(checkpoint['state']['engagement']['reducer']['state']['relations'].values())
        supported = [row for row in relations if row['state'] == 'supported']
        self.assertEqual(len(supported), 1, relations)
        self.assertEqual(supported[0]['chosen_clause']['qualification'], 'qualified_pc_input')
        self.assertGreaterEqual(supported[0]['enter_dwell'], 5)
        self.assertTrue(checkpoint['state']['pc_input_originals'])
        positive = [decision for decision in decisions if decision['execution']['status'] == 'virtual'
                    and any(sample['brightness_fraction'] > 0 for sample in decision['execution']['readback'])]
        self.assertTrue(positive, decisions[-1])
        self.assertEqual(len(positive[-1]['room_output']['fixtures']), 4)
        expected_originals = {r['digest'] for publication in self.publications()
            if publication['payload'].get('source_id') in {'synthetic-source', 'surveyed-desk'}
            for r in publication['payload'].get('original_receipt_refs', [])}
        expected_originals.update(_original_digest(entry['retention_record'], limit=2 * 1024**2)
                                  for entry in graph.semantic.prepared.values())
        withdrawal = graph.withdraw()
        self.assertEqual(withdrawal['withdrawal'], 'recorded', withdrawal)
        self.assertEqual([e['payload']['source_id'] for e in graph.source_barriers],
                         ['synthetic-source', 'surveyed-desk', 'ha-semantic', 'jev-semantic'])
        # The final cut also needs a later decision time at the ordinary
        # one-second cadence. Barrier bodies retain their actual earlier time.
        self.f.now += 1
        stopped = self.tick()
        self.assertEqual(stopped['execution']['status'], 'unavailable', stopped)
        self.assertGreaterEqual(stopped['input_cut']['input_cursor'], withdrawal['input_cursor'])
        drained = self.driver.drain(deadline_ms=2000, through_cursor=withdrawal['input_cursor'])
        self.assertEqual(drained['status'], 'settled', drained)
        self.recorder.drain({'status': 'complete', 'reason': 'authored_desk_join',
                             'terminal_cursor': drained['input_cursor']})
        receipt = self.f.drain_receipt(withdrawal)
        finished = graph.finalize(drain_receipt=receipt)
        self.assertEqual(finished['status'], 'stopped', finished)
        original_path = Path(self.producer['original_archive']['filename'])
        retained = read_lab_originals(original_path,
            expected_digests=expected_originals,
            expected_desk_binding=runtime._desk_archive_binding(graph.join.scope, self.producer['desk_policy']))
        self.assertLessEqual(expected_originals, retained.keys())
        self.assertTrue(any(row['schema'] == 'home-original-lab-observation-1' for row in retained.values()))
        self.assertTrue(any(row['schema'] == 'home-original-lab-desk-1' for row in retained.values()))
        code, replayed = self.replay()
        self.assertEqual(code, 0, replayed)
        self.assertEqual(replayed['status'], 'reproduced')
        # Remove an ACTUAL retained desk record. Its sealed chain cannot become
        # a complete original archive by retaining capture/HA/JEV alone.
        lines = original_path.read_text(encoding='utf-8').splitlines()
        changed = self.f.path / 'missing-desk.jsonl'
        changed.write_text('\n'.join(line for line in lines if 'home-original-lab-desk-1' not in line) + '\n',
                           encoding='utf-8')
        with self.assertRaises(ValueError):
            read_lab_originals(changed,
                expected_digests=expected_originals,
                expected_desk_binding=runtime._desk_archive_binding(graph.join.scope, self.producer['desk_policy']))

    def test_actual_source_prelude_has_causal_capture_pc_and_jev_bindings(self):
        self.create()
        self.admit_current_chain()
        facts = {row['payload']['source_id']: row['payload'] for row in self.publications()}
        self.assertEqual(facts['ha-semantic']['value'], 'attributed_recent')
        self.assertEqual(facts['jev-semantic']['value'], 'computer_work')
        self.assertEqual(facts['jev-semantic']['person_ref'], self.prototype['binding']['person_ref'])
        original = self.capability.descriptors[0]['original']['event']
        self.assertNotIn('evidence_roots', original['payload'])
        self.assertIn(str(original['id']), facts['jev-semantic']['observation_roots'])
        request = self.graph.semantic.requests['authored-desk-current-jev']
        self.assertIsNotNone(request['work_parent_ref'])

    def test_known_stationary_method_actual_source_cut_is_independently_feasible(self):
        graph = self.create()
        self.admit_current_chain()
        query = '?site_id=' + graph.join.scope['site_id'] + '&room_id=' + graph.join.scope['room_id']
        query += '&consumer_id=' + self.worker['worker_id']
        connection = http.client.HTTPConnection('127.0.0.1', graph.address[1], timeout=4)
        try:
            connection.request('GET', PREFIX + '/live-input-cut' + query,
                headers={'Authorization': 'Bearer ' + self.base.worker_token})
            response = connection.getresponse()
            cut = json.loads(response.read())
            self.assertEqual(response.status, 200, cut)
        finally:
            connection.close()
        example = self.worker['scene_policy']['catalog']['examples'][0]
        data = dict(cut=cut, identity=graph.join.scope,
            policy=self.worker['room_policy']['fixtures'][0]['task_policy'],
            lifecycle=self.worker['lifecycle_policy'], manifest_ref=self.worker['manifest_ref'],
            nominal=example['nominal'], bootstrap_at=NOW+.3, now=self.f.now)
        result = subprocess.run([shutil.which('node'), '--max-old-space-size=192', '-e', ALLOCATE_CURRENT_CUT],
            cwd=ROOT, input=json.dumps(data), text=True, capture_output=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stderr)
        checked = json.loads(result.stdout)
        self.assertEqual(checked['classification'], 'authored_actual_cut_feasibility_only')
        source = next(row['payload'] for row in cut['items'] if row['payload'].get('source_id') == 'surveyed-desk')
        self.assertEqual(checked['source_geometry_digest'], digest(source))
        self.assertEqual(checked['parts'], source['parts'])
        self.assertEqual(checked['start']['allocation']['status'], 'allocated', checked)
        self.assertEqual(checked['later']['execution']['status'], 'virtual', checked)
        self.assertGreater(checked['later']['execution']['readback'][0]['brightness_fraction'], 0, checked)

    def test_wrong_frame_cannot_become_geometry_and_unavailable_does_not_refresh(self):
        self.f.add_original(observed=NOW+1, mutation=self.actual_shaped_lab)
        self.capability.wrong_frame = True
        graph = self.create()
        result = graph.step()
        self.assertEqual(result['status'], 'desk_unavailable', result)
        self.assertFalse(result['current'])
        self.assertFalse(any(p['payload']['source_id'] == 'surveyed-desk' for p in self.publications()))

    def test_pending_desk_lost_ack_holds_cursor_then_settles_before_all_source_barriers(self):
        event = self.f.add_original(observed=NOW+1, mutation=self.actual_shaped_lab)
        graph = self.create()
        append, attempted = graph.journal.append_event, []
        def lost(envelope, principal):
            ack = append(envelope, principal)
            if envelope['payload']['source_id'] == 'surveyed-desk' and envelope['payload']['status'] == 'supplied':
                attempted.append(deepcopy(envelope))
                if len(attempted) == 1:
                    raise TimeoutError('authored_desk_ack_lost')
            return ack
        with mock.patch.object(graph.journal, 'append_event', side_effect=lost):
            first = graph.step()
            self.assertEqual(first['status'], 'failed', first)
            self.assertLess(graph.after_id, event['id'])
            withdrawal = graph.withdraw()
        self.assertEqual(withdrawal['withdrawal'], 'recorded', withdrawal)
        self.assertEqual(attempted[0], attempted[1])
        self.assertEqual(graph.after_id, event['id'])
        self.assertEqual(len(graph.source_barrier_acks), 4)
        self.assertEqual(sum(p['payload']['source_id'] == 'surveyed-desk' and p['payload'].get('status') == 'supplied'
                             for p in self.publications()), 1)

    def test_stop_during_selection_prevents_desk_append_without_replacing_capture_owner(self):
        self.f.add_original(observed=NOW+1, mutation=self.actual_shaped_lab)
        graph = self.create()
        self.capability.on_select = graph.quiesce
        result = graph.step()
        self.assertEqual(result['status'], 'stopped', result)
        self.assertFalse(any(p['payload']['source_id'] == 'surveyed-desk' for p in self.publications()))
        self.assertEqual(graph.withdraw()['withdrawal'], 'recorded')
        self.assertEqual(self.f.store.get_session(self.f.session['id'])['state'], 'recording')

    def test_generation_change_during_desk_retention_keeps_original_without_first_append(self):
        self.f.add_original(observed=NOW+1, mutation=self.actual_shaped_lab)
        graph = self.create()
        archive = self.composition.originals
        wrapper = graph.original_retainer
        retained = []
        def retire_during_retention(original):
            ack = archive(original)
            if original['schema'] == 'home-original-lab-desk-1':
                retained.append(deepcopy(original))
                self.f.store.append_event(self.f.session['id'], 'experiment.activated', {
                    'site_id': 'victoria', 'camera_id': 'den', 'generation': 2, 'profiles': {}})
            return ack
        with mock.patch.object(wrapper, 'archive', side_effect=retire_during_retention):
            result = graph.step()
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['reason'], 'current_incumbent_la_worker_receipts_required')
        self.assertEqual(len(retained), 1)
        original_digest = _original_digest(retained[0], limit=512*1024)
        self.assertIn(original_digest, {row[0] for row in archive.records.values()})
        self.assertFalse(any(p['payload']['source_id'] == 'surveyed-desk' for p in self.publications()))
        self.assertIsNotNone(graph.pending_desk)
        self.assertNotEqual(graph.withdraw()['withdrawal'], 'recorded')


if __name__ == '__main__':
    unittest.main()

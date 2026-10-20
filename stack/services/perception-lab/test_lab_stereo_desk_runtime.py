"""Generated retained stereo -> actual producer4 Journal and sealed originals.

Only external camera/model/lifecycle I/O and Windows directory-fsync ports are
doubled. Room registration, independent distinct-part/association assessments
and their applicability are explicitly fictional commissioning. The anchor
comes from the actual generated NPZ lift, never an authored point. No worker,
Home positive delivery, household qualification or physical truth is claimed.
"""
from copy import deepcopy
from decimal import Decimal
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for directory in (HERE.parent / 'home-context', ROOT / 'tools/den-live-runtime'):
    if str(directory) not in sys.path: sys.path.append(str(directory))

from journal import digest
from live_capture import _original_digest
import live_test_helpers as live
from live_den_mount import _runtime_module
from controlled_external_io import assemble_journal_grants
from pc_support_configuration import upgrade_configuration
from lab_live_desk_anchor import LabLiveDeskAnchor, body_ref
from lab_stereo_desk_capability import LabStereoDeskCapability, COMMISSIONING_KEYS
import lab_live_runtime as runtime
import test_lab_live_desk_anchor as generated
import test_lab_live_desk_admission as desk_fixture
import test_lab_live_semantic_admission_v2 as semantic_fixture


class LabStereoDeskRuntimeTests(unittest.TestCase):
    PRODUCER_VERSION = 4
    ORIGINAL_FAMILY = None

    def make_camera_fixture(self):
        return generated.GeneratedStereoFixture(self)

    def setUp(self):
        self.f = self.make_camera_fixture()
        self.f.session = self.f.store.get_session(self.f.session['id'])
        self.scope = deepcopy(self.f.scope)
        self.graph = self.composition = None
        self.addCleanup(self.cleanup_graph)
        # All existing helpers share this mutable authored scope dictionary;
        # freeze it to the ACTUAL Store session before any config is assembled.
        scope_patch = patch.dict(live.SCOPE, self.scope, clear=True)
        scope_patch.start(); self.addCleanup(scope_patch.stop)
        config, semantic, _, worker = semantic_fixture.semantic_v2_fixture(include_worker=True)

        # Independent fictional registration, fixed before any anchor is lifted.
        # The known generated scene uses depth1234mm with the declared optical
        # model. No model result is inspected to fit/squeeze these room bounds.
        registration = self.f.registration
        registration.update(geometry_revision=worker['delivery_policy']['registration']['geometry_revision'],
                            geometry_digest=worker['delivery_policy']['registration']['geometry_digest'])
        registration['room_from_rectified_left'] = [[1, 0, 0, 3.5], [0, 0, 1, .75],
                                                    [0, -1, 0, 1.7], [0, 0, 0, 1]]
        registration['transform_ref'] = {'id': 'authored-stereo-room-transform',
                                         'digest': digest(registration['room_from_rectified_left'])}
        camera = config['manifest']['sources']['synthetic-source']
        camera['capture_clock'] = deepcopy(self.f.clock_mapping)
        camera['sensor_roots'] = ['camera:den']
        # The incumbent capture join uses its own method for unavailable
        # association; the independent seated-person method belongs only to
        # the separate surveyed source below.
        if live.METHOD not in camera['association_method_refs']:
            camera['association_method_refs'].append(deepcopy(live.METHOD))
        camera['model_refs'] = [{'id': 'authored-composite', 'digest': self.f.profile['artifact_sha256']}]
        wire_registration = deepcopy(camera['registration'])
        wire_registration.update(registration_ref=registration['id'], registration_digest=digest(registration),
            geometry_revision=registration['geometry_revision'], geometry_digest=registration['geometry_digest'],
            calibration_digest=self.f.projection['id'], mount_digest=digest(self.f.projection['mount_signature']),
            coverage_refs=deepcopy(registration['coverage_refs']),
            transform_chain_refs=[deepcopy(registration['transform_ref'])])
        camera['registration'] = deepcopy(wire_registration)

        definition = {'schema': 'home-desk-protection-definition-1', 'version': 1,
            'id': 'authored-stereo-desk-protection', 'required_body_part_ids': ['torso'],
            'protected_parts': ['head'], 'body_primitive_id': 'torso',
            'protection_primitive_id': 'head-protection'}
        protection_ref = {'id': definition['id'], 'digest': digest(definition)}
        distinct = desk_fixture.distinct
        survey = distinct.survey()
        survey['parts']['head']['outer_corners'] = distinct.measured_box([3.5, 2, 1.8], [.3, .3, .3])
        survey['parts']['protection']['outer_corners'] = distinct.measured_box([3.5, 2, 1.8], [.35, .35, .35])
        survey['protection_policy']['policy_ref'] = deepcopy(protection_ref)
        _, assessment, _, binding = distinct.inputs(survey)
        candidate = distinct.guide.prepare_candidate(survey, now=lambda: live.NOW - 10)
        assessment.update(scope=deepcopy(self.scope), person_ref=deepcopy(self.f.person_ref),
            subject_ref=deepcopy(self.f.subject_ref), association_method_ref=deepcopy(self.f.association_method_ref),
            candidate_digest=candidate['digest'], parts=deepcopy(candidate['parts']),
            work=deepcopy(candidate['work_patch']), protection_policy_ref=deepcopy(protection_ref),
            anchor_part='head', anchor_region={'kind': 'aabb', 'center_m': [3.5, 2, 1.8],
                                              'half_extents_m': [.25, .25, .25]},
            observed_at=live.NOW - 10, available_at=live.NOW - 9, valid_until=live.NOW + 30,
            max_anchor_error_m=.2)
        reg_ref = {'id': registration['id'], 'digest': digest(registration)}
        clock_ref = {'id': self.f.clock_mapping['mapping_id'], 'digest': self.f.clock_mapping['mapping_digest']}
        assessment['registration'].update(ref=deepcopy(reg_ref), error_bound_m=.02, valid_until=live.NOW + 30)
        assessment['clock_mapping'].update(ref=deepcopy(clock_ref), uncertainty_s=.1, valid_until=live.NOW + 30)
        assessment_ref = {'id': assessment['id'], 'digest': distinct.consumer.digest(assessment)}
        binding.update(scope=deepcopy(self.scope), person_ref=deepcopy(self.f.person_ref),
            subject_ref=deepcopy(self.f.subject_ref), association_method_ref=deepcopy(self.f.association_method_ref),
            candidate_digest=candidate['digest'], assessment_ref=deepcopy(assessment_ref),
            registration_ref=deepcopy(reg_ref), clock_mapping_ref=deepcopy(clock_ref))
        self.commissioning = {'candidate': candidate, 'assessment': assessment, 'binding': binding,
            'projection': deepcopy(self.f.projection), 'registration': deepcopy(registration),
            'clock_mapping': deepcopy(self.f.clock_mapping), 'protection_policy': definition}
        self.assertEqual(set(self.commissioning), COMMISSIONING_KEYS)

        # Only extract the fixture's STATIC independent policy. This seed is
        # never presented as a capture publication, associated anchor or input.
        seed = {'original': {'event': deepcopy(self.f.row)}, 'source_generation': camera['initial_generation'],
                'source_position': {'capture_epoch': camera['capture_epoch'], 'capture_seq': 7, 'revision': 0},
                'publication_ref': None}
        static_stereo_policy = self.f.qualification(seed)['stereo_policy']
        policy = {'schema': 'home-lab-live-desk-admission-policy-2', 'version': 2,
            'source_id': 'surveyed-desk', 'capture_source_id': 'synthetic-source',
            'method_ref': deepcopy(binding['method_ref']), 'association_method_ref': deepcopy(self.f.association_method_ref),
            'candidate_digest': candidate['digest'], 'assessment_ref': deepcopy(assessment_ref),
            'projection_ref': body_ref(self.f.projection), 'protection_policy_ref': deepcopy(protection_ref),
            'receipt_store_id': 'desk-originals', 'receipt_component_id': 'desk-assessment-original',
            'body_primitive_id': 'torso', 'protection_primitive_id': 'head-protection',
            'stereo_policy_ref': body_ref(static_stereo_policy)}
        if self.ORIGINAL_FAMILY is not None:
            policy.update(schema='home-lab-live-desk-admission-policy-3', version=3,
                original_family=self.ORIGINAL_FAMILY,
                assessment_policy_sha256=self.f.runtime.existing_api_assessment.policy_sha256)
        desk = deepcopy(camera)
        desk.update(method_refs=[deepcopy(policy['method_ref'])],
            association_method_refs=[deepcopy(policy['association_method_ref'])], model_refs=[],
            receipt_store_ids=['desk-originals'], receipt_component_ids=['desk-assessment-original'],
            primitive_ids={'body': ['torso'], 'protection': ['head-protection']},
            protection_policy_refs=[deepcopy(protection_ref)])
        for name in ('body', 'head', 'hand_left', 'hand_right', 'protection'):
            desk['part_policies'][name] = deepcopy(desk['part_policies']['work'])
        config['manifest']['sources']['surveyed-desk'] = desk
        pc = worker['pc_support_policy']
        pc['bindings'][0].update(geometry_source_id='surveyed-desk', geometry_method_ref=deepcopy(policy['method_ref']),
            association_method_ref=deepcopy(policy['association_method_ref']), subject_ref=deepcopy(self.f.subject_ref))
        for family in ('ha', 'jev'):
            semantic[family]['association_method_ref'] = deepcopy(policy['association_method_ref'])
            source = config['manifest']['sources'][semantic[family]['source_id']]
            source['association_method_refs'].append(deepcopy(policy['association_method_ref']))
            source['sensor_roots'] = ['device:authored-pc', 'camera:den']
        semantic['jev']['kind_sensor_roots']['perception.output'] = ['camera:den']
        for node in (worker['room_policy'], worker['delivery_policy'],
                     *(fixture['task_policy'] for fixture in worker['room_policy']['fixtures'])):
            if node is None:
                continue  # Explicit uncommissioned fixture slots stay unavailable.
            node['registration'] = deepcopy(wire_registration)
            if 'protection' in node:
                node['protection']['policy_ref'] = deepcopy(protection_ref)
                node['required_body_parts'] = ['torso']
        # Reclose config12 before constructing the sole actual Journal.
        config.update(schema='home-live-ingress-config-9', version=9)
        config['manifest'].update(schema='home-live-ingress-manifest-9', version=9)
        config['manifest'].pop('pc_support_interface_digest')
        config['manifest']['worker'].update(room_policy=deepcopy(worker['room_policy']),
                                            delivery_policy=deepcopy(worker['delivery_policy']))
        for key in ('pc_support_policy', 'pc_support_schema_pins'):
            config['manifest']['worker'].pop(key, None)
        config, worker = upgrade_configuration(config, worker, pc)
        semantic.update(schema='home-lab-semantic-admission-policy-3', version=3,
            root_lineage='persisted-observation-record-1', pc_support_policy_digest=digest(worker['pc_support_policy']))
        self.config, self.policy = config, policy
        self.path = Path(self.f.directory.name)
        token_path = self.path / 'authored-publication-token'; token_path.write_text(live.TOKEN, encoding='utf8')
        if sys.platform == 'win32':
            # Linux's owner/0600 credential port has no Windows geteuid
            # equivalent. The double accepts ONLY this fixture-owned file
            # with its exact original bytes; it grants no alternate token.
            def authored_private_token(filename):
                file = Path(filename)
                self.assertEqual(file.resolve(), token_path.resolve())
                self.assertFalse(file.is_symlink())
                self.assertEqual(file.read_bytes(), live.TOKEN.encode('utf8'))
                return live.TOKEN
            token_port = patch.object(runtime, 'private_token', side_effect=authored_private_token)
            token_port.start(); self.addCleanup(token_port.stop)
        grants = assemble_journal_grants(config, worker, publication_token=live.TOKEN,
            worker_token='authored-no-worker-start', human_token='authored-stereo-owner')
        for grant in grants:
            grant['revisions'] = list(dict.fromkeys([*grant['revisions'], 'synthetic-r1', worker['revision']]))
        # Real factory activation already exists. Only the external lifecycle
        # ready receipt is authored; no activation/model output is fabricated.
        rows = self.f.store.list_events(self.f.session['id'])
        activated = next(row['payload'] for row in reversed(rows) if row['kind'] == 'experiment.activated')
        self.model_generation = activated['generation']
        self.f.store.append_event(self.f.session['id'], 'workers.ready', {'receipts': {'b': {
            'state': 'ready', 'session_id': self.f.session['id'], 'engine_epoch': self.f.profile['engine_epoch'],
            'profile': deepcopy(self.f.profile), 'expires_at': self.f.now + 10}}})
        self.producer = {'schema': 'home-incumbent-lab-producer-' + str(self.PRODUCER_VERSION),
            'version': self.PRODUCER_VERSION,
            'lab_binding': {'session_id': self.f.session['id'], 'arm': 'b', 'model_generation': self.model_generation,
                'media_generation': self.f.session['generation'], 'lab_profile': deepcopy(self.f.profile),
                'model_manifests': deepcopy(self.f.model_manifests)},
            'live_config': config, 'source_id': 'synthetic-source', 'revision': 'synthetic-r1',
            'method_ref': deepcopy(live.METHOD), 'model_ref': deepcopy(camera['model_refs'][0]),
            'receipt_store_id': 'synthetic-receipts', 'receipt_component_id': 'pose', 'after_id': 0,
            'limits': {'max_cycles': 8, 'max_runtime_s': 10, 'page_limit': 128},
            'journal_path': str(self.path / 'stereo-journal.sqlite'), 'journal_grants': grants,
            'publication_token_file': str(token_path), 'original_archive': {
                'filename': str(self.path / 'stereo-originals.jsonl'), 'max_records': 16,
                'max_bytes': 2 * 1024**2, 'min_free_bytes': 1},
            'semantic_policy': semantic, 'desk_policy': policy}
        self.assert_constructor_bindings(worker)
        join, _ = runtime._join_and_configuration(self.producer, self.f.session)
        self.f.lab_binding_digest = join.binding_digest
        _runtime_module()
        self.media_module = importlib.import_module('home_den_runtime.lab_stereo_media')
        self.original_module = importlib.import_module('home_den_runtime.lab_original_archive')
        if sys.platform == 'win32':
            for module, name in ((self.media_module, 'sync_directory'), (self.original_module, 'sync_parent')):
                sync = patch.object(module, name); sync.start(); self.addCleanup(sync.stop)
        holder = {}
        def current():
            holder['composition'].require_current()
            return True
        self.media = self.media_module.LabStereoMedia(self.path / 'stereo-media', store=self.f.store,
            scope=self.scope, lab_binding_digest=join.binding_digest, media_generation=self.f.session['generation'],
            current=current, max_objects=16, max_bytes=8 * 1024**2, min_free_bytes=1,
            **({k: policy[k] for k in ('original_family', 'assessment_policy_sha256')}
               if self.ORIGINAL_FAMILY is not None else {}))
        options = {}
        if self.ORIGINAL_FAMILY is not None:
            from lab_existing_api_stereo import ExistingApiStereoSource
            from depth_clock import DepthClock
            options['existing_api_source'] = ExistingApiStereoSource(self.f.store,
                expected_assessment_binding=runtime._assessment_binding(self.scope, policy, join.binding_digest),
                assessment_tap=self.f.runtime.existing_api_assessment,
                depth_clock=DepthClock(self.f.store, site_id=self.scope['site_id'], camera_id='den',
                    clock=self.f.depth_clock_policy, calibration=self.f.projection),
                current=lambda _: current(), clock=lambda: self.f.now)
        self.anchor = LabLiveDeskAnchor(self.f.store, scope=self.scope, lab_binding_digest=join.binding_digest,
            stereo_policy_ref=deepcopy(policy['stereo_policy_ref']), resolve_qualification=self.f.qualification,
            retain_media=self.media.retain_media, resolve_media=self.media.resolve_retained,
            original_retainer=None, clock=lambda: self.f.now, **options)
        self.capability = LabStereoDeskCapability(anchor_source=self.anchor, commissioning=self.commissioning,
            policy=policy, lab_binding_digest=join.binding_digest, media_archive=self.media, clock=lambda: self.f.now)
        self.composition = runtime.LabRuntimeComposition(self.producer, store=self.f.store,
            preflight=lambda: True, control_clock=self.control_clock, desk_capability=self.capability,
            clock=lambda: self.f.now, monotonic=lambda: self.f.now - live.NOW)
        holder['composition'] = self.composition

    def assert_constructor_bindings(self, worker):
        """Declare the authored source closure before any graph is opened.

        These assertions catch fixture wiring errors separately from the real
        production constructors, which still validate the complete config.
        They do not substitute an invented receipt, profile or source grant.
        """
        p, session = self.producer, self.f.session
        sources, scope = self.config['manifest']['sources'], self.config['manifest']['identity']
        camera, desk = sources[p['source_id']], sources[self.policy['source_id']]
        self.assertEqual(set(p), runtime.PRODUCER_KEYS | {'semantic_policy', 'desk_policy'})
        self.assertEqual(set(p['lab_binding']), runtime.BINDING_KEYS)
        self.assertEqual(set(p['limits']), runtime.LIMIT_KEYS)
        self.assertEqual(set(p['original_archive']), runtime.ORIGINAL_KEYS)
        self.assertEqual((p['schema'], p['version']),
                         ('home-incumbent-lab-producer-' + str(self.PRODUCER_VERSION), self.PRODUCER_VERSION))
        self.assertEqual(scope, self.scope)
        self.assertEqual((scope['site_id'], scope['session_id']), (session['site_id'], session['id']))
        self.assertEqual(session['camera_id'], 'den')
        self.assertEqual(p['lab_binding'], {'session_id': session['id'], 'arm': 'b',
            'model_generation': self.model_generation, 'media_generation': session['generation'],
            'lab_profile': self.f.profile, 'model_manifests': self.f.model_manifests})
        self.assertEqual(p['after_id'], 0)
        self.assertEqual(p['limits'], {'max_cycles': 8, 'max_runtime_s': 10, 'page_limit': 128})
        self.assertTrue(all(Path(p[key]).is_absolute() for key in ('journal_path', 'publication_token_file')))
        self.assertTrue(Path(p['original_archive']['filename']).is_absolute())
        self.assertNotEqual(p['original_archive']['filename'], p['journal_path'])
        self.assertEqual({k: p['original_archive'][k] for k in ('max_records', 'max_bytes', 'min_free_bytes')},
                         {'max_records': 16, 'max_bytes': 2 * 1024**2, 'min_free_bytes': 1})
        self.assertEqual(p['method_ref'], live.METHOD)
        self.assertIn(p['method_ref'], camera['method_refs'])
        self.assertIn(p['method_ref'], camera['association_method_refs'])
        self.assertEqual(p['model_ref'], {'id': 'authored-composite', 'digest': self.f.profile['artifact_sha256']})
        self.assertEqual(camera['model_refs'], [p['model_ref']])
        self.assertIn(p['receipt_store_id'], camera['receipt_store_ids'])
        self.assertIn(p['receipt_component_id'], camera['receipt_component_ids'])
        self.assertEqual(camera['capture_clock'], self.f.clock_mapping)
        self.assertEqual(camera['capture_clock']['original_unit'], 'unix_ns')
        self.assertEqual(camera['sensor_roots'], ['camera:den'])
        self.assertEqual(camera['capture_epoch'], self.f.row['payload']['capture_epoch'])
        self.assertEqual(self.f.profile['artifact_sha256'], self.f.profile['sha256'])
        for stage in ('detector', 'pose'):
            actual = self.f.row['payload']['result']['processing'][stage]
            self.assertEqual(actual['artifact_sha256'], self.f.model_manifests[stage]['sha256'])
            self.assertEqual(actual['device_id'], self.f.model_manifests[stage]['device_id'])
        semantic = p['semantic_policy']
        self.assertEqual((semantic['schema'], semantic['version'], semantic['root_lineage']),
            ('home-lab-semantic-admission-policy-3', 3, 'persisted-observation-record-1'))
        self.assertEqual(semantic['generation'], self.model_generation)
        self.assertEqual(semantic['pc_support_policy_digest'], digest(worker['pc_support_policy']))
        for family in ('ha', 'jev'):
            binding = semantic[family]
            source = sources[binding['source_id']]
            self.assertIn(binding['method_ref'], source['method_refs'])
            self.assertIn(binding['association_method_ref'], source['association_method_refs'])
            self.assertIn(binding['receipt_store_id'], source['receipt_store_ids'])
            self.assertIn(binding['receipt_component_id'], source['receipt_component_ids'])
            self.assertEqual(source['sensor_roots'], ['device:authored-pc', 'camera:den'])
            self.assertEqual(source['capture_clock']['original_unit'], 'unix_s')
            self.assertEqual(source['capture_clock']['epoch_origin_s'], 0)
            self.assertLessEqual(binding['clock_uncertainty_s'], source['capture_clock']['max_uncertainty_s'])
            self.assertEqual(binding['kind_sensor_roots']['ha.observation'], ['device:authored-pc'])
        self.assertEqual(semantic['jev']['kind_sensor_roots']['perception.output'], ['camera:den'])
        pc_binding = worker['pc_support_policy']['bindings'][0]
        self.assertEqual(pc_binding['geometry_source_id'], self.policy['source_id'])
        self.assertEqual(pc_binding['geometry_method_ref'], self.policy['method_ref'])
        self.assertEqual(pc_binding['association_method_ref'], self.policy['association_method_ref'])
        self.assertEqual(pc_binding['subject_ref'], self.f.subject_ref)
        self.assertEqual(pc_binding['source_id'], semantic['ha']['source_id'])
        self.assertEqual(pc_binding['method_ref'], semantic['ha']['method_ref'])
        self.assertEqual(pc_binding['ha_source_id'], semantic['ha_source_id'])
        self.assertEqual(pc_binding['proof_component_id'], semantic['ha']['receipt_component_id'])
        self.assertEqual(pc_binding['input_sensor_root'], 'device:authored-pc')
        self.assertIn(self.policy['method_ref'], desk['method_refs'])
        self.assertIn(self.policy['association_method_ref'], desk['association_method_refs'])
        self.assertEqual(desk['receipt_store_ids'], [self.policy['receipt_store_id']])
        self.assertEqual(desk['receipt_component_ids'], [self.policy['receipt_component_id']])
        for key in ('registration', 'capture_epoch', 'capture_clock', 'control_clock', 'sensor_roots'):
            self.assertEqual(desk[key], camera[key])
        self.assertIsNotNone(desk['registration'])
        self.assertEqual(desk['primitive_ids'], {'body': ['torso'], 'protection': ['head-protection']})
        self.assertEqual(desk['protection_policy_refs'], [self.policy['protection_policy_ref']])
        self.assertTrue(all(desk['part_policies'][part] is not None
                            for part in ('work', 'body', 'head', 'hand_left', 'hand_right', 'protection')))
        self.assertNotIn(self.policy['receipt_store_id'],
                         {p['receipt_store_id'], *(semantic[k]['receipt_store_id'] for k in ('ha', 'jev'))})
        self.assertNotIn(self.policy['source_id'], [semantic[k]['source_id'] for k in ('ha', 'jev')])
        expected_roots = sorted({root for source in sources.values() for root in source['sensor_roots']})
        self.assertEqual(expected_roots, ['camera:den', 'device:authored-pc'])
        for grant in p['journal_grants']:
            self.assertEqual(grant['scopes'], [{'site_id': scope['site_id'], 'room_id': scope['room_id']}])
            self.assertEqual(set(grant['source_ids']), set(sources))
            self.assertEqual(grant['sensor_roots'], [] if 'intent' in grant['operations'] else expected_roots)
            self.assertEqual({entry['source_id'] for entry in grant['live_bindings']}, set(sources))
            self.assertTrue(all(entry['manifest_ref'] == self.config['manifest_ref'] for entry in grant['live_bindings']))
            self.assertIn(p['revision'], grant['revisions'])

    def control_clock(self):
        policy = self.config['manifest']['sources']['synthetic-source']['control_clock']
        return {'observed_at': self.f.now, 'clock': {'status': 'commissioned',
            **{key: policy[key] for key in ('mapping_id', 'mapping_digest', 'source_epoch', 'original_unit')},
            'original_value': str(int(Decimal(str(self.f.now)) * Decimal(1000000000))), 'uncertainty_s': .001}}

    def create(self):
        self.graph = self.composition.create()
        self.journal_records, self.precommit_original_schemas = [], []
        def record(value):
            self.journal_records.append(deepcopy(value))
            if value['kind'] == 'journal_prepare' and any(event['payload'].get('source_id') == 'surveyed-desk'
                    and event['payload'].get('status') == 'supplied' for event in value['data']['events']):
                rows = [json.loads(line)['body'] for line in Path(self.producer['original_archive']['filename']).read_text().splitlines()]
                schemas = [json.loads(row['original_json'])['schema'] for row in rows if row['kind'] == 'original']
                self.assertIn('home-original-lab-stereo-nose-' + str(2 if self.ORIGINAL_FAMILY else 1), schemas)
                self.assertIn('home-original-lab-desk-' + str(self.policy['version']), schemas)
                self.assertEqual(len(self.media.media_refs()),
                    (5 if self.ORIGINAL_FAMILY else 4) * (len(self.precommit_original_schemas) + 1))
                self.precommit_original_schemas.append(schemas)
        self.graph.journal.attach_recorder(record)
        return self.graph

    def publications(self):
        return [json.loads(row[0]) for row in self.graph.journal.db.execute('SELECT body FROM publications ORDER BY cursor')]

    def drain(self, withdrawal, *, recorder='drained', worker='settled'):
        # Authored external drain witness only: this source test starts no
        # worker and makes no reducer/allocator/Home or worker-drain claim.
        return {'schema': 'home-acquisition-drain-1', 'version': 1, 'session_ref': deepcopy(self.scope),
            'configuration_digest': self.graph.configuration_digest,
            'withdrawal_receipt': deepcopy(withdrawal['barrier_receipt']), 'recorder_status': recorder,
            'worker_status': worker, 'terminal_cursor': withdrawal['input_cursor'] or 0}

    def cleanup_graph(self):
        if self.graph is None:
            return
        if not self.graph.finished:
            withdrawal = self.graph.withdraw()
            result = self.graph.finalize(drain_receipt=self.drain(withdrawal, recorder='incomplete', worker='unconfirmed'))
            if not self.graph.finished:
                # Failed fixture construction must not leave its owned loopback
                # server alive. This cleanup never upgrades evidence to complete.
                self.graph.server.shutdown(); self.graph.server.server_close()
                self.graph.server_thread.join(1); self.graph.journal.close()
                if self.composition.originals is not None: self.composition.originals.finalize()

    def test_actual_generated_pair_factory_publication_spool_seal_and_missing_binary_refusal(self):
        graph = self.create()
        result = graph.step()
        self.assertEqual(result['status'], 'admitted_desk', result)
        self.assertEqual(graph.config['version'], 4)
        self.assertIsInstance(graph.journal, generated.Journal)
        body = result['desk']['event']['payload']
        self.assertEqual(body['schema'], 'home-live-geometry-1')
        self.assertEqual(body['status'], 'supplied')
        self.assertEqual(body['person_ref'], self.f.person_ref)
        self.assertNotEqual(body['parts']['head']['shape'], body['parts']['work']['shape'])
        self.assertNotEqual(body['parts']['body']['shape'], body['parts']['protection']['shape'])
        self.assertIsNone(result['desk']['hazard_clearance'])
        self.assertEqual(body['parent_refs'], [])
        original_path = Path(self.producer['original_archive']['filename'])
        original_rows = [json.loads(line)['body'] for line in original_path.read_text().splitlines()]
        originals = [json.loads(row['original_json']) for row in original_rows if row['kind'] == 'original']
        self.assertEqual([value['schema'] for value in originals],
            ['home-original-lab-observation-1', 'home-original-lab-stereo-nose-1', 'home-original-lab-desk-2'])
        nose = originals[1]['support']
        self.assertEqual(nose['landmark']['surface'], 'visible_nose_surface')
        self.assertEqual(nose['landmark']['rgb_pixel'], [320., 150.])
        self.assertEqual(originals[2]['inputs']['anchor']['point_m'], nose['point']['room_xyz_m'])
        self.assertEqual(nose['depth']['observed_at'], self.f.mapped['metadata']['observed_at'])
        self.assertLess(nose['point']['point_observed_at'], body['observed_at'])
        self.assertEqual(body['observation_root_times'][str(self.f.row['id'])], self.f.row['payload']['observed_at'])
        self.assertEqual(len(body['observation_roots']), 2, 'RGB and depth remain distinct originals')
        self.assertEqual(len(self.precommit_original_schemas), 1)
        expected = {self.original_module.original_digest(value) for value in originals}
        refs, media_binding = self.media.media_refs(), deepcopy(self.media.binding)
        self.assertEqual(len(refs), 4)
        withdrawal = graph.withdraw(); self.assertEqual(withdrawal['withdrawal'], 'recorded', withdrawal)
        finished = graph.finalize(drain_receipt=self.drain(withdrawal))
        self.assertEqual(finished['status'], 'stopped', finished)
        media_receipt = finished['originals']['stereo_media']
        self.assertEqual(media_receipt['status'], 'finalized')
        sealed = self.media_module.SealedStereoMedia(self.path / 'stereo-media', expected_binding=media_binding,
            expected_refs=refs, manifest_digest=media_receipt['manifest_digest'])
        archive_binding = runtime._desk_archive_binding(self.scope, self.policy, self.composition.join.binding_digest)
        retained = self.original_module.read_lab_originals(original_path, expected_digests=expected,
            expected_desk_binding=archive_binding, stereo_media_resolver=sealed.resolve_media)
        self.assertEqual(set(retained), expected)
        missing = self.path / 'stereo-media' / 'bytes' / (refs[0]['sha256'] + '.bin')
        missing.unlink()
        with self.assertRaises(ValueError):
            self.original_module.read_lab_originals(original_path, expected_digests=expected,
                expected_desk_binding=archive_binding, stereo_media_resolver=sealed.resolve_media)

    def test_raw_nose_ack_uncertainty_holds_cursor_stop_withdraws_but_stays_incomplete(self):
        graph = self.create()
        before = graph.after_id
        archive_type = type(self.composition.originals)
        retain = archive_type.__call__
        nose_attempts = []
        def unknown_ack(owner, original):
            result = retain(owner, original)
            if original.get('schema') == 'home-original-lab-stereo-nose-1':
                nose_attempts.append(deepcopy(original))
                raise TimeoutError('authored_original_nose_ack_lost_after_fsync')
            return result
        with patch.object(archive_type, '__call__', unknown_ack):
            result = graph.step()
            self.assertEqual(result['status'], 'failed', result)
            self.assertIn('retention_unconfirmed', result['reason'])
            self.assertEqual(graph.pending_desk['event_id'], self.f.row['id'])
            blocked_cursor = graph.after_id
            self.assertGreaterEqual(blocked_cursor, before)
            self.assertLess(blocked_cursor, graph.pending_desk['event_id'])
            retried = graph.step()
            self.assertEqual(retried['status'], 'failed', retried)
            self.assertIn('retention_unconfirmed', retried['reason'])
            self.assertEqual(graph.pending_desk['event_id'], self.f.row['id'])
            self.assertEqual(graph.after_id, blocked_cursor)
        self.assertEqual(len(nose_attempts), 2)
        self.assertEqual(nose_attempts[0], nose_attempts[1], 'uncertain ACK retries the exact retained original')
        self.assertFalse(any(p['payload'].get('source_id') == 'surveyed-desk' for p in self.publications()))
        self.assertEqual(self.anchor.quiesce(), {'status': 'unconfirmed', 'in_flight': False})
        withdrawal = graph.withdraw()
        self.assertEqual(withdrawal['withdrawal'], 'recorded', withdrawal)
        self.assertEqual(len(graph.source_barrier_acks), 4)
        finished = graph.finalize(drain_receipt=self.drain(withdrawal, recorder='incomplete', worker='unconfirmed'))
        self.assertEqual(finished['status'], 'unconfirmed', finished)
        self.assertEqual(finished['originals']['stereo_media']['status'], 'incomplete')
        self.assertFalse(finished['buffers_cleared'])


if __name__ == '__main__':
    unittest.main()

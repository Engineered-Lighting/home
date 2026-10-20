"""One actual source6 C cut -> worker commit -> withdrawal/drain -> replay3.

Camera/model/clock I/O and independent qualification are authored. Factory,
RegisteredImage, full original validators, metric lifting, Journal, worker,
commit validation, retention and ordinary replay are real. This narrow gate
does not claim positive light, engagement dwell, commands or continuous service.
No production cap, lease, epoch or reducer timing is changed.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for directory in (HERE.parent / 'home-context', ROOT / 'tools/den-live-runtime'):
    if str(directory) not in sys.path:
        sys.path.append(str(directory))

from journal import digest
import live_test_helpers as live
from controlled_external_io import assemble_journal_grants
from pc_support_configuration import upgrade_configuration
from lab_live_desk_anchor import LabLiveDeskAnchor, body_ref
from lab_stereo_desk_capability import LabStereoDeskCapability, COMMISSIONING_KEYS
from live_den_mount import _runtime_module
from depth_clock import DepthClock
from lab_bundled_rgb_stereo import BundledRgbStereoSource, BASE_ROLES, ROLES
from test_lab_bundled_rgb_stereo import GeneratedBundledFixture
import lab_live_runtime as runtime
import test_lab_live_desk_admission as desk_fixture
import test_lab_live_semantic_admission_v2 as semantic_fixture
import test_lab_stereo_desk_runtime as stereo_fixture
import test_lab_stereo_worker_runtime as worker_fixture


class _BundledDeskFixture:
    PRODUCER_VERSION = 6
    ORIGINAL_FAMILY = 'existing-api-bundled-rgb-1'
    control_clock = stereo_fixture.LabStereoDeskRuntimeTests.control_clock
    drain = stereo_fixture.LabStereoDeskRuntimeTests.drain
    cleanup_graph = stereo_fixture.LabStereoDeskRuntimeTests.cleanup_graph

    def __getattr__(self, name):
        return getattr(self.case, name)

    def __init__(self, case):
        self.case = case
        self.f = GeneratedBundledFixture(case)
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
        policy.update(schema='home-lab-live-desk-admission-policy-4', version=4,
            original_family='existing-api-bundled-rgb-1',
            bundle_input_policy_sha256=self.f.runtime.bundled_rgb_sessions[self.f.session['id']].policy_sha256)
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
        _runtime_module()
        self.media_module = importlib.import_module('home_den_runtime.lab_stereo_media')
        self.original_module = importlib.import_module('home_den_runtime.lab_original_archive')
        if sys.platform == 'win32':
            for module, name in ((self.media_module, 'sync_directory'), (self.original_module, 'sync_parent')):
                port = patch.object(module, name)
                port.start(); self.addCleanup(port.stop)


class LabBundledWorkerRuntimeTests(unittest.TestCase):
    # Reuse real worker process/recorder/tick plumbing, not the old seven-pair
    # positive test or its five-second dwell assertions.
    create = worker_fixture.LabStereoWorkerRuntimeTests.create
    records = worker_fixture.LabStereoWorkerRuntimeTests.records
    publications = worker_fixture.LabStereoWorkerRuntimeTests.publications
    tick = worker_fixture.LabStereoWorkerRuntimeTests.tick
    cleanup_worker = worker_fixture.LabStereoWorkerRuntimeTests.cleanup_worker

    def setUp(self):
        self.source = _BundledDeskFixture(self)
        self.f, self.path = self.source.f, self.source.path
        self.bundle = worker_fixture.authored_stereo_worker_bundle(self.source)
        self.worker = self.bundle['worker_config']
        self.worker_token, self.owner_token = 'authored-stereo-worker-token', 'authored-stereo-owner'
        config = self.bundle['live_config']
        grants = assemble_journal_grants(config, self.worker, publication_token=live.TOKEN,
            worker_token=self.worker_token, human_token=self.owner_token)
        for grant in grants:
            grant['revisions'] = list(dict.fromkeys([*grant['revisions'], 'synthetic-r1', self.worker['revision']]))
        self.producer = deepcopy(self.source.producer)
        self.producer.update(live_config=config, journal_grants=grants,
            semantic_policy=self.bundle['semantic_policy'], desk_policy=self.bundle['desk_policy'])
        # Consume the actual C already produced by the actual config4 factory.
        # No seeded Journal publication, scene or engagement is used.
        self.producer['after_id'] = 0
        self.producer['limits'].update(max_cycles=8, max_runtime_s=10)
        self.producer['original_archive'].update(max_records=16, max_bytes=8 * 1024**2)
        join, _ = runtime._join_and_configuration(self.producer, self.f.session)
        self.f.lab_binding_digest = join.binding_digest
        self.media_module, self.original_module = self.source.media_module, self.source.original_module
        self.desk_original_version, self.nose_original_version = 4, 3
        self.replay_input_version, self.media_per_capture = 3, 8
        holder = {}
        def current():
            holder['composition'].require_current()
            return True
        self.bundled_source = BundledRgbStereoSource(self.f.store,
            expected_bundle_binding=join.bundled_binding(), incumbent_runtime=self.f.runtime,
            capture_clock_policy=self.f.clock_mapping,
            depth_clock=DepthClock(self.f.store, site_id=self.source.scope['site_id'], camera_id='den',
                clock=self.f.depth_clock_policy, calibration=self.f.projection),
            current=lambda _: current(), clock=lambda: self.f.now)
        self.media = self.media_module.LabStereoMedia(self.path / 'stereo-worker-media', store=self.f.store,
            scope=self.source.scope, lab_binding_digest=join.binding_digest,
            media_generation=self.f.session['generation'], current=current,
            max_objects=32, max_bytes=8 * 1024**2, min_free_bytes=1,
            **{key: self.producer['desk_policy'][key] for key in
                ('original_family', 'bundle_input_policy_sha256')})
        self.anchor = LabLiveDeskAnchor(self.f.store, scope=self.source.scope, lab_binding_digest=join.binding_digest,
            stereo_policy_ref=deepcopy(self.producer['desk_policy']['stereo_policy_ref']),
            resolve_qualification=self.f.qualification, retain_media=self.media.retain_media,
            resolve_media=self.media.resolve_retained, original_retainer=None, clock=lambda: self.f.now,
            bundled_rgb_source=self.bundled_source, capture_clock_policy=self.f.clock_mapping)
        capability = LabStereoDeskCapability(anchor_source=self.anchor,
            commissioning=self.bundle['commissioning'], policy=self.producer['desk_policy'],
            lab_binding_digest=join.binding_digest, media_archive=self.media, clock=lambda: self.f.now)
        self.composition = runtime.LabRuntimeComposition(self.producer, store=self.f.store,
            preflight=lambda: True, control_clock=self.source.control_clock, desk_capability=capability,
            bundled_source=self.bundled_source, clock=lambda: self.f.now, monotonic=lambda: self.f.now-live.NOW)
        holder['composition'] = self.composition
        self.source.composition = self.composition
        self.graph = self.recorder = self.rpc = self.driver = None
        self.addCleanup(self.cleanup_worker)

    def source_graph(self):
        self.graph = self.composition.create()
        self.source.graph = self.graph
        self.graph.journal.attach_recorder(lambda *_args, **_kwargs: None)
        return self.graph

    def no_supplied_desk(self):
        return not any(p['payload'].get('source_id') == 'surveyed-desk'
            and p['payload'].get('status') == 'supplied' for p in self.publications())

    def test_one_actual_C_cut_worker_commit_withdrawal_and_sealed_replay(self):
        graph = self.create()
        self.assertIs(graph.bundled_source, self.bundled_source)
        result = graph.step()
        self.assertEqual(result['status'], 'admitted_desk', result)
        self.assertTrue(result['operational_geometry'])
        body = result['desk']['event']['payload']
        self.assertEqual(body['status'], 'supplied')
        self.assertEqual(body['clock']['original_unit'], 'unix_s')
        self.assertNotIn('source_timing_receipt', self.f.frame)
        self.assertEqual(body['observed_at'], self.f.row['payload']['observed_at'])
        self.assertEqual(len(body['observation_roots']), 2)
        self.assertIn(str(self.f.row['id']), body['observation_roots'])
        self.assertIsNone(result['desk']['hazard_clearance'])
        decision = self.tick()
        # tick pairs the real prepared decision with its accepted commit. Its
        # exact immutable input cut must contain this actual desk receipt.
        receipt = result['desk']['receipt']
        self.assertTrue(any(ref['cursor'] == receipt['cursor'] and ref['id'] == receipt['id']
            and ref['producer_id'] == receipt['producer_id'] and ref['payload_digest'] == receipt['payload_digest']
            for ref in decision['input_cut']['input_receipts']))
        self.assertEqual(len(self.precommit_stereo), 1)
        support = self.precommit_stereo[0]
        self.assertEqual((support['schema'], support['version']), ('home-desk-stereo-nose-anchor-3', 3))
        self.assertEqual(set(support['media_refs']), ROLES)
        self.assertEqual(len(self.media.media_refs()), 8)
        original_path = Path(self.producer['original_archive']['filename'])
        original_rows = [json.loads(line)['body'] for line in original_path.read_text().splitlines()]
        originals = [json.loads(row['original_json']) for row in original_rows if row['kind'] == 'original']
        self.assertEqual([o['schema'] for o in originals], ['home-original-lab-observation-2',
            'home-original-lab-stereo-nose-3', 'home-original-lab-desk-4'])
        clock = originals[0]['clock_original']
        self.assertEqual(originals[0]['bundled_originals']['clock_original'], clock)
        self.assertEqual(clock['clock'], body['clock'])
        self.assertEqual(set(clock['media_refs']), BASE_ROLES)
        refs, binding = self.media.media_refs(), deepcopy(self.media.binding)
        withdrawal = graph.withdraw()
        self.assertEqual(withdrawal['withdrawal'], 'recorded', withdrawal)
        # Public withdrawal is immediate. Ask for the next actual scheduled
        # semantic/control commit, rather than assuming a10ms cycle commits.
        self.f.now += self.worker['semantic_interval_s']
        self.tick()
        drained = self.driver.drain(deadline_ms=2000, through_cursor=withdrawal['input_cursor'])
        self.assertEqual(drained['status'], 'settled', drained)
        causal = self.recorder.drain({'status': 'complete', 'reason': 'authored_source6_single_cut',
            'terminal_cursor': drained['input_cursor']})
        self.assertEqual(causal['status'], 'drained')
        receipt = self.source.drain(withdrawal)
        receipt['terminal_cursor'] = drained['input_cursor']
        final = graph.finalize(drain_receipt=receipt)
        self.assertEqual(final['status'], 'stopped', final)
        self.assertEqual(final['originals']['stereo_media']['status'], 'finalized')
        self.assertTrue(self.bundled_source.closed)
        self.assertEqual(self.bundled_source.prepared, {})
        self.assertEqual(self.bundled_source.selected, {})
        fingerprint = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        join_digest = self.composition.join.binding_digest
        expected = dict(schema='home-live-stereo-replay-inputs-3', version=3,
            causal_sha256=fingerprint(self.archive), originals_sha256=fingerprint(original_path),
            manifest_digest=digest(self.manifest), config_digest=self.manifest['configuration_digest'],
            scope=deepcopy(self.source.scope), capture_binding=deepcopy(original_rows[0]['binding']),
            capture_receipt_binding={key:self.producer[key] for key in
                ('source_id','receipt_store_id','receipt_component_id')} | {'root_lineage':'persisted-observation-record-1'},
            semantic_binding=deepcopy(original_rows[0]['semantic_binding']),
            desk_binding=runtime._desk_archive_binding(self.source.scope,self.producer['desk_policy'],join_digest),
            original_digests=sorted(self.original_module.original_digest(o) for o in originals),
            media_binding=binding, media_refs=refs,
            media_manifest_digest=final['originals']['stereo_media']['manifest_digest'],
            replay_source_sha256=fingerprint(ROOT/'tools/den-live-runtime/replay.cjs'),
            capture_clock_policy=deepcopy(self.f.clock_mapping))
        self.assertEqual(binding['lab_binding_digest'], join_digest)
        replay = importlib.import_module('home_den_runtime.lab_stereo_replay').replay_stereo
        replayed = replay(self.archive,original_path,self.path/'stereo-worker-media',expected=expected)
        self.assertEqual(replayed['status'],'reproduced')
        self.assertTrue(replayed['originals_verified'])
        self.assertTrue(replayed['worker_reproduced'])
        self.assertFalse(replayed['physical_authority'])
        self.assertFalse(replayed['acceptance_eligible'])
        self.retained_evidence_receipt = worker_fixture.export_authored_evidence(
            self, original_path, self.path/'stereo-worker-media', expected, replayed)
        # Preserve the same complete expected pins but remove one real binary.
        missing = self.path/'stereo-worker-media/bytes'/(refs[0]['sha256']+'.bin')
        missing.unlink()
        with self.assertRaises(ValueError):
            replay(self.archive,original_path,self.path/'stereo-worker-media',expected=expected)

    def test_missing_independent_qualification_never_supplies_geometry(self):
        self.anchor.resolve_qualification = lambda _: None
        graph = self.source_graph()
        result = graph.step()
        self.assertEqual(result['status'], 'desk_unavailable', result)
        self.assertFalse(result['operational_geometry'])
        self.assertTrue(self.no_supplied_desk())

    def test_missing_actual_terminal_keeps_original_cursor_and_no_geometry(self):
        from bundle_rgb_input import TERMINAL_KIND
        with self.f.store.lock:
            self.f.store.db.execute('DELETE FROM events WHERE session_id=? AND kind=?',
                (self.f.session['id'],TERMINAL_KIND))
            self.f.store.db.commit()
        graph = self.source_graph()
        first = graph.step()
        self.assertEqual(first['status'],'pending',first)
        cursor = graph.after_id
        second = graph.step()
        self.assertEqual(second['status'],'pending',second)
        self.assertEqual(graph.after_id,cursor)
        self.assertFalse(second['operational_geometry'])
        self.assertTrue(self.no_supplied_desk())

    def test_B_clock_anchor_cannot_alias_the_actual_C_observation(self):
        self.source_graph()
        source = self.bundled_source.capture(self.f.row,self.f.frame,self.f.now)
        self.assertEqual(source['status'],'selected',source)
        anchor_b = source['rows']['anchor_b'][0]
        foreign = deepcopy(self.f.row)
        foreign['payload'].update(frame_id=anchor_b['metadata']['frame_id'],media_ids=[anchor_b['id']],
            observed_at=anchor_b['metadata']['observed_at'])
        foreign['payload']['result']['frame_id'] = anchor_b['metadata']['frame_id']
        selected = self.bundled_source.capture(foreign,anchor_b['metadata'],self.f.now)
        self.assertEqual(selected['status'],'unavailable',selected)
        self.assertFalse(self.bundled_source.selected)


class LegacySource5CompatibilityTests(unittest.TestCase):
    def test_actual_source5_family_keeps_legacy_original_versions(self):
        import test_lab_existing_api_desk_runtime as legacy
        source = legacy.ExistingApiDeskRuntimeTests.make_source()
        self.addCleanup(source.doCleanups)
        source.setUp()
        graph = source.create()
        result = graph.step()
        self.assertEqual(result['status'], 'admitted_desk', result)
        self.assertEqual(graph.config['version'], 5)
        original_path = Path(source.producer['original_archive']['filename'])
        values = [json.loads(json.loads(line)['body']['original_json']) for line in original_path.read_text().splitlines()
            if json.loads(line)['body']['kind'] == 'original']
        self.assertEqual([value['schema'] for value in values], ['home-original-lab-observation-1',
            'home-original-lab-stereo-nose-2', 'home-original-lab-desk-3'])
        self.assertEqual(result['desk']['event']['payload']['clock']['original_unit'], 'unix_ns')


if __name__ == '__main__':unittest.main()

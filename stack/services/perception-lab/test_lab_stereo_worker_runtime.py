"""Authored stereo factory -> desk/HA/JEV -> actual worker/validator/replay.

All camera/model responses and independent commissioning are fictional. The
metric anchor is actually lifted from retained generated NPZ, never supplied
by the test. The existing Store, Journal, worker and independent commit
validator remain authoritative. This is source integration evidence only.
"""
from copy import deepcopy
import hashlib
import http.client
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for directory in (HERE.parent / 'home-context', ROOT / 'tools/den-live-runtime'):
    if str(directory) not in sys.path:
        sys.path.append(str(directory))

from journal import PREFIX, digest
from live_capture import _original_digest
import live_test_helpers as live
from live_worker_test_helpers import describe
from controlled_external_io import assemble_journal_grants
from pc_support_configuration import upgrade_configuration
from lab_live_desk_anchor import LabLiveDeskAnchor
from lab_stereo_desk_capability import LabStereoDeskCapability
from live_reasoning import LiveReasoning
from reasoning import ACTIVITIES
import lab_live_runtime as runtime
import test_lab_stereo_desk_runtime as stereo_fixture
from test_live_pc_support import authored_pc_fixture


def export_authored_evidence(owner, original_path, media_directory, expected, result):
    """Opt-in fixed-file copy of this positive fixture, before refusal tests.

    A partial copy is preserved without a complete receipt. This helper opens
    no device/network/process and never chooses an implicit destination.
    """
    requested = os.environ.get('DEN_STEREO_TEST_EXPORT')
    if requested is None:
        return None
    destination = Path(requested)
    if (not destination.is_absolute() or destination.exists() or destination.is_symlink()
            or not destination.parent.is_dir() or destination.parent.is_symlink()):
        raise ValueError('fresh_absolute_authored_evidence_destination_required')
    maximum_files, maximum_bytes = 128, 32 * 1024**2
    files = {'causal.jsonl': owner.archive, 'originals.jsonl': original_path,
             'media/manifest.json': media_directory / 'manifest.json'}
    media_manifest = json.loads((media_directory / 'manifest.json').read_bytes())
    for entry in media_manifest['objects']:
        record = 'records/' + entry['record_digest'] + '.json'
        binary = 'bytes/' + entry['ref']['sha256'] + '.bin'
        files['media/' + record] = media_directory / record
        files['media/' + binary] = media_directory / binary
    encoded = owner.original_module.encoded
    blobs = {'expected.json': encoded(expected), 'authored-commissioning.json': encoded(owner.bundle)}
    if len(files) + len(blobs) + 1 > maximum_files:
        raise ValueError('authored_evidence_file_bound')
    sizes = {name: path.stat().st_size for name, path in files.items()}
    if any(size <= 0 for size in sizes.values()) or sum(sizes.values()) + sum(map(len, blobs.values())) > maximum_bytes:
        raise ValueError('authored_evidence_byte_bound')
    source_names = {str(Path(__file__).relative_to(ROOT)).replace('\\', '/'),
        'stack/services/perception-lab/test_lab_stereo_desk_runtime.py',
        'stack/services/perception-lab/test_lab_live_desk_anchor.py',
        'stack/services/perception-lab/lab_live_runtime.py',
        'stack/services/perception-lab/lab_live_acquisition.py',
        'stack/services/perception-lab/lab_live_journal_join.py',
        'stack/services/perception-lab/lab_live_desk_anchor.py',
        'stack/services/perception-lab/lab_stereo_desk_capability.py',
        'stack/services/perception-lab/lab_live_desk_admission.py',
        'stack/services/perception-lab/lab_live_semantic_admission.py',
        'tools/den-live-runtime/lab_stereo_replay.py',
        'tools/den-live-runtime/lab_original_archive.py',
        'tools/den-live-runtime/lab_stereo_media.py',
        'tools/den-live-runtime/replay.cjs', 'tools/den-live-runtime/supervisor.py',
        *owner.manifest['worker_description']['module_hashes']}
    if getattr(owner, 'desk_original_version', 2) == 3:
        source_names.update({'stack/services/perception-lab/lab_existing_api_stereo.py',
            'stack/services/perception-lab/assessment_existing_api.py',
            'stack/services/perception-lab/test_lab_existing_api_desk_runtime.py',
            'stack/services/perception-lab/test_lab_existing_api_worker_runtime.py'})
    source_hashes = {}
    for name in sorted(source_names):
        path = ROOT / name
        if path.is_symlink() or not 0 < path.stat().st_size <= 1024**2:
            raise ValueError('authored_evidence_source_bound')
        source_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    destination.mkdir()  # Create-exclusive; existing evidence is never replaced.
    copied, total = {}, 0
    def write(relative, chunks, length):
        nonlocal total
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        fingerprint, count = hashlib.sha256(), 0
        with target.open('xb') as stream:
            for chunk in chunks:
                count += len(chunk)
                if count > length or total + count > maximum_bytes:
                    raise ValueError('authored_evidence_byte_bound')
                stream.write(chunk)
                fingerprint.update(chunk)
            if count != length:
                raise ValueError('authored_evidence_original_changed')
            stream.flush(); os.fsync(stream.fileno())
        verified = hashlib.sha256()
        with target.open('rb') as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                verified.update(chunk)
        if verified.hexdigest() != fingerprint.hexdigest():
            raise ValueError('authored_evidence_copy_mismatch')
        copied[relative] = {'sha256': fingerprint.hexdigest(), 'size': count}
        total += count
    for name, path in files.items():
        if path.is_symlink():
            raise ValueError('authored_evidence_original_symlink')
        with path.open('rb') as stream:
            write(name, iter(lambda: stream.read(65536), b''), sizes[name])
    for name, blob in blobs.items():
        write(name, (blob,), len(blob))
    if (copied['causal.jsonl']['sha256'] != expected['causal_sha256']
            or copied['originals.jsonl']['sha256'] != expected['originals_sha256']
            or copied['media/manifest.json']['sha256'] != expected['media_manifest_digest']):
        raise ValueError('authored_evidence_external_pin_mismatch')
    for name, metadata in copied.items():
        if name.startswith(('media/records/', 'media/bytes/')) and metadata['sha256'] != Path(name).stem:
            raise ValueError('authored_evidence_media_pin_mismatch')
    # This final marker certifies only the copied positive dataset. Destructive
    # refusal assertions follow; they do not touch this independent copy.
    receipt = {'schema': 'home-authored-stereo-worker-evidence-1', 'version': 1, 'status': 'complete',
        'classification': 'authored_software_integration_only',
        'test': owner.id(), 'files': copied, 'source_hashes': source_hashes,
        'positive_replay': deepcopy(result), 'bytes': total,
        'external_doubles': ['generated_camera_and_detector_pose', 'authored_HA_and_JEV_provider',
            'fictional_independent_registration_association_parts_and_stationary_method',
            'authored_external_model_lifecycle_ready', 'Windows_credential_and_directory_sync_ports'],
        'household_qualification': False, 'physical_authority': False, 'whole_store_replay_proven': False}
    raw = encoded(receipt)
    write('receipt.json', (raw,), len(raw))
    return str(destination / 'receipt.json')


def authored_stereo_worker_bundle(source):
    """Reuse the closed source4 fixture; independently commission static work.

    The returned bodies may seed an authored software package only. In
    particular this known stationary error method is not a household accuracy
    assessment or permission to use these room bounds on a real camera.
    """
    config = deepcopy(source.producer['live_config'])
    declared = config['manifest']['worker']
    keys = ('worker_id', 'producer_id', 'producer_epoch', 'revision', 'source_pins',
            'max_output_age_s', 'semantic_interval_s', 'engagement_policy',
            'scene_policy', 'delivery_policy', 'lifecycle_policy', 'room_policy', 'pc_support_policy')
    worker = {'profile': live.PROFILE, 'identity': deepcopy(source.scope),
              **{key: deepcopy(declared[key]) for key in keys}}
    commissioning = deepcopy(source.commissioning)
    policy = deepcopy(source.policy)
    method = {'schema': 'authored-fixed-stereo-desk-error-method-1', 'version': 1,
        'method_ref': deepcopy(policy['method_ref']), 'candidate_digest': policy['candidate_digest'],
        'assessment_ref': deepcopy(policy['assessment_ref']),
        'truth': 'identical_independently_surveyed_shapes_in_all_authored_frames',
        'parts': ['work', 'body', 'head', 'hand_left', 'hand_right', 'protection'],
        'additional_radius_m': .01, 'max_speed_m_s': .01, 'max_accel_m_s2': .01,
        'provenance': 'test_lab_live_desk_runtime.py:authored_stationary_method'}
    applicability = {'id': 'authored-fixed-stereo-desk-error-method', 'digest': digest(method)}
    desk = config['manifest']['sources'][policy['source_id']]
    for name in method['parts']:
        part = desk['part_policies'][name]
        part.update(bound_at_observation_m=.01,
            components_m={'capture_association': 0, 'depth': 0, 'registration': 0, 'estimator': .01},
            applicability_ref=deepcopy(applicability))
        part['age'].update(max_speed_m_s=.01, max_accel_m_s2=.01)
    task, room = worker['delivery_policy'], worker['room_policy']
    center = commissioning['candidate']['work_patch']['center_m']
    task['fixture']['origin_m'] = [center[0], center[1] - 2, 3]
    task['fixture']['initial'] = {'pan_deg': 90, 'tilt_deg': 45}
    for index, fixture in enumerate(room['fixtures']):
        fixture['task_policy'] = deepcopy(task)
        fixture['task_policy']['fixture'].update(id=fixture['fixture_id'],
            fixture_pose_ref=deepcopy(fixture['pose_ref']))
        fixture['task_policy']['fixture']['origin_m'][0] += index * .05
    # Static fictional room/fixture assessments are declared before generating
    # the new frame sequence. Dynamic support leases are never lengthened.
    room['valid_until'] = room['exposure']['valid_until'] = live.NOW + 40
    for fixture in room['fixtures']:
        fixture['valid_until'] = live.NOW + 40
        for domain in fixture['ambient']:
            domain['valid_until'] = live.NOW + 40
    for node in (room, task, *(fixture['task_policy'] for fixture in room['fixtures'])):
        node['registration'] = deepcopy(desk['registration'])
        if 'protection' in node:
            node['protection']['policy_ref'] = deepcopy(policy['protection_policy_ref'])
            node['required_body_parts'] = [policy['body_primitive_id']]
    config.update(schema='home-live-ingress-config-9', version=9)
    config['manifest'].update(schema='home-live-ingress-manifest-9', version=9)
    config['manifest'].pop('pc_support_interface_digest')
    config['manifest']['worker'].update(room_policy=deepcopy(room), delivery_policy=deepcopy(task))
    for key in ('pc_support_policy', 'pc_support_schema_pins'):
        config['manifest']['worker'].pop(key, None)
    config, worker = upgrade_configuration(config, worker, worker['pc_support_policy'])
    semantic = deepcopy(source.producer['semantic_policy'])
    semantic['pc_support_policy_digest'] = digest(worker['pc_support_policy'])
    return {'classification': 'authored_stationary_software_fixture_only',
            'live_config': config, 'worker_config': worker, 'desk_policy': policy,
            'semantic_policy': semantic, 'commissioning': commissioning,
            'stationary_error_method': method, 'depth_clock_policy': deepcopy(source.f.depth_clock_policy)}


class LabStereoWorkerRuntimeTests(unittest.TestCase):
    def make_source(self):
        return stereo_fixture.LabStereoDeskRuntimeTests()

    def setUp(self):
        self.source = self.make_source()
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.f, self.path = self.source.f, self.source.path
        self.bundle = authored_stereo_worker_bundle(self.source)
        self.worker = self.bundle['worker_config']
        self.worker_token = 'authored-stereo-worker-token'
        self.owner_token = 'authored-stereo-owner'
        config = self.bundle['live_config']
        grants = assemble_journal_grants(config, self.worker, publication_token=live.TOKEN,
            worker_token=self.worker_token, human_token=self.owner_token)
        for grant in grants:
            grant['revisions'] = list(dict.fromkeys([*grant['revisions'], 'synthetic-r1', self.worker['revision']]))
        self.producer = deepcopy(self.source.producer)
        self.producer.update(live_config=config, journal_grants=grants,
            semantic_policy=self.bundle['semantic_policy'], desk_policy=self.bundle['desk_policy'],
            # The initial pair was fixture setup; this graph consumes only the
            # subsequent actual factory outputs, never a seeded publication.
            after_id=self.f.row['id'])
        self.producer['limits'].update(max_cycles=24, max_runtime_s=30)
        self.producer['original_archive'].update(max_records=48, max_bytes=8 * 1024**2)
        self.f.store.append_event(self.f.session['id'], 'workers.ready', {'receipts': {'b': {
            'state': 'ready', 'session_id': self.f.session['id'], 'engine_epoch': self.f.profile['engine_epoch'],
            'profile': deepcopy(self.f.profile), 'expires_at': self.f.now + 30}}})
        join, _ = runtime._join_and_configuration(self.producer, self.f.session)
        self.f.lab_binding_digest = join.binding_digest
        self.media_module, self.original_module = self.source.media_module, self.source.original_module
        self.desk_original_version = self.producer['desk_policy']['version']
        self.nose_original_version = 2 if self.desk_original_version == 3 else 1
        self.replay_input_version = 2 if self.desk_original_version == 3 else 1
        self.media_per_capture = 5 if self.desk_original_version == 3 else 4
        media_options, anchor_options = {}, {}
        holder = {}
        def current():
            holder['composition'].require_current()
            return True
        if self.desk_original_version == 3:
            from depth_clock import DepthClock
            from lab_existing_api_stereo import ExistingApiStereoSource
            media_options = {key: self.producer['desk_policy'][key]
                for key in ('original_family', 'assessment_policy_sha256')}
            anchor_options['existing_api_source'] = ExistingApiStereoSource(self.f.store,
                expected_assessment_binding=runtime._assessment_binding(self.source.scope,
                    self.producer['desk_policy'], join.binding_digest),
                assessment_tap=self.f.runtime.existing_api_assessment,
                depth_clock=DepthClock(self.f.store, site_id=self.source.scope['site_id'], camera_id='den',
                    clock=self.f.depth_clock_policy, calibration=self.f.projection),
                current=lambda descriptor: current(), clock=lambda: self.f.now)
        self.media = self.media_module.LabStereoMedia(self.path / 'stereo-worker-media', store=self.f.store,
            scope=self.source.scope, lab_binding_digest=join.binding_digest,
            media_generation=self.f.session['generation'], current=current,
            max_objects=40 if self.desk_original_version == 3 else 32,
            max_bytes=8 * 1024**2, min_free_bytes=1, **media_options)
        self.anchor = LabLiveDeskAnchor(self.f.store, scope=self.source.scope, lab_binding_digest=join.binding_digest,
            stereo_policy_ref=deepcopy(self.producer['desk_policy']['stereo_policy_ref']),
            resolve_qualification=self.f.qualification, retain_media=self.media.retain_media,
            resolve_media=self.media.resolve_retained, original_retainer=None, clock=lambda: self.f.now,
            **anchor_options)
        capability = LabStereoDeskCapability(anchor_source=self.anchor,
            commissioning=self.bundle['commissioning'], policy=self.producer['desk_policy'],
            lab_binding_digest=join.binding_digest, media_archive=self.media, clock=lambda: self.f.now)
        self.composition = runtime.LabRuntimeComposition(self.producer, store=self.f.store,
            preflight=lambda: True, control_clock=self.source.control_clock, desk_capability=capability,
            clock=lambda: self.f.now, monotonic=lambda: self.f.now - live.NOW)
        holder['composition'] = self.composition
        self.source.composition = self.composition
        self.graph = self.recorder = self.rpc = self.driver = None
        self.addCleanup(self.cleanup_worker)

    def cleanup_worker(self):
        if self.driver is not None:
            self.driver.close()
        if self.rpc is not None:
            self.rpc.close()
        if self.recorder is not None:
            self.recorder.drain({'status': 'incomplete', 'reason': 'authored_stereo_cleanup'})

    def create(self):
        from archive import Recorder, RecorderRPC
        from supervisor import NodeWorkerDriver
        self.graph = self.composition.create()
        self.source.graph = self.graph
        sanitized = {k: deepcopy(v) for k, v in self.worker.items()
                     if k not in {'token', 'token_file', 'service_url'}}
        description = describe(sanitized)
        self.manifest = {'schema': 'home-live-session-manifest-1', 'version': 1,
            'session_ref': deepcopy(self.source.scope), 'configuration_digest': description['config_digest'],
            'source_bundle_digest': 'b' * 64, 'worker_config': sanitized, 'worker_description': description}
        self.archive = self.path / 'stereo-worker-session.jsonl'
        self.recorder = Recorder(self.archive, self.manifest, max_records=512,
            max_bytes=16 * 1024**2, min_free_bytes=1)
        self.precommit_stereo = []
        def record(value, **kwargs):
            if value['kind'] == 'journal_prepare':
                for event in value['data']['events']:
                    body = event['payload']
                    if body.get('source_id') == 'surveyed-desk' and body.get('status') == 'supplied':
                        rows = [json.loads(line)['body'] for line in
                                Path(self.producer['original_archive']['filename']).read_text().splitlines()]
                        originals = [json.loads(row['original_json']) for row in rows if row['kind'] == 'original']
                        matching = [original for original in originals if original['schema'] == 'home-original-lab-desk-' + str(self.desk_original_version)
                                    and original['inputs']['anchor']['source_position'] == body['head']['position']]
                        self.assertEqual(len(matching), 1)
                        nose = [original for original in originals if original['schema'] == 'home-original-lab-stereo-nose-' + str(self.nose_original_version)
                                and original['support']['source_position'] == body['head']['position']]
                        self.assertEqual(len(nose), 1)
                        self.precommit_stereo.append(deepcopy(nose[0]['support']))
            return self.recorder.record(value, **kwargs)
        self.graph.journal.attach_recorder(record)
        self.rpc = RecorderRPC(self.recorder)
        self.driver = NodeWorkerDriver(node_runtime=shutil.which('node'),
            config={**deepcopy(self.worker), 'token': self.worker_token,
                    'service_url': 'http://127.0.0.1:' + str(self.graph.address[1])},
            recorder=self.rpc.start(), controlled_time=self.f.now)
        self.assertEqual(self.driver.readiness()['status'], 'ready')
        return self.graph

    def records(self):
        return [json.loads(line)['record'] for line in self.archive.read_text(encoding='utf8').splitlines()]

    def publications(self):
        return [json.loads(row[0]) for row in self.graph.journal.db.execute('SELECT body FROM publications ORDER BY cursor')]

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
        self.f.now += 1
        value = {k: deepcopy(v) for k, v in self.worker['lifecycle_policy']['operator_clearance'].items()
                 if k != 'max_age_s'}
        value.update(hazard_refs=[item['hazard_ref'] for item in first['person_hazards']['items']],
                     inspection_at=self.f.now)
        intent = dict(schema='home-live-intent-event-2', version=2, id='authored-stereo-room-inspection',
            **self.source.scope, manifest_ref=deepcopy(self.worker['manifest_ref']),
            actor_ref='controlled-owner', origin='human_declaration', truth_role='runtime_intent',
            kind='clear_person_hazards', target=None, set_at=self.f.now, available_at=self.f.now,
            valid_until=self.f.now + 5, reply_to=None, value=value)
        connection = http.client.HTTPConnection('127.0.0.1', self.graph.address[1], timeout=4)
        try:
            connection.request('POST', PREFIX + '/intent-events', json.dumps(intent),
                {'Authorization': 'Bearer ' + self.owner_token, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            receipt = json.loads(response.read())
            self.assertEqual(response.status, 200, receipt)
        finally:
            connection.close()
        self.assertEqual(self.tick()['intent_results'][0]['status'], 'applied')

    def append_pair_and_input(self, offset):
        self.f.now = live.NOW + offset + .35
        camera, _ = self.f.append_pair(sequence=7 + offset, observed_at=live.NOW + offset)
        ha = authored_pc_fixture(seq=offset, snapshot_at=live.NOW + offset + .1,
                                input_at=live.NOW + offset - .1)['original']['ha_event']
        hid = self.f.store.append_event(self.f.session['id'], 'ha.observation', ha['payload'])
        ha = next(row for row in self.f.store.list_events(self.f.session['id']) if row['id'] == hid)
        admitted = self.graph.step()
        self.assertEqual(admitted['status'], 'admitted_desk', admitted)
        pc = self.graph.step()
        self.assertEqual(pc['status'], 'admitted_attributed', pc)
        return camera, ha, admitted

    def append_current_jev(self, camera, ha):
        # A new actual Store request freezes the already committed work parent;
        # its external provider response does not create geometry or ancestry.
        observed = camera['payload']['observed_at']
        cutoff, request_at, available = observed + .36, observed + .38, observed + .4
        packet = {'model': 'jev-1.13.0', 'state': {'schema': 'den-facts-7', 'now': {
            'people_in_view': 'one', 'camera_evidence': 'current'}, 'devices': {
            'pc_input': 'under_15s', 'pc_active': 'on'}}, 'questions': {'activity': {'instructions': 'activity?'}}}
        evidence, timeline = [str(camera['id']), str(ha['id'])], [str(camera['id'])]
        rid = 'authored-stereo-current-jev'
        provenance = {'request_id': rid, 'session_id': self.f.session['id'], 'site_id': 'victoria',
            'arm': 'b', 'generation': self.source.model_generation, 'observed_at': cutoff,
            'deadline': live.NOW + 20, 'wire_version': 'den-facts-7', 'response_version': 'jev-1.13.0',
            'input_digest': digest(packet), 'evidence_ids': evidence, 'timeline_ids': timeline,
            'evidence_digest': digest([camera['payload'], ha['payload']])}
        self.f.now = request_at
        self.f.store.append_event(self.f.session['id'], 'reasoning.request', {
            'request_id': rid, 'arm': 'b', 'generation': self.source.model_generation,
            'available_at': request_at, 'packet_sha256': digest(packet), 'request': packet,
            'status': 'admitted_before_transport', 'wire_version': 'den-facts-7',
            'evidence_ids': evidence, 'timeline_ids': timeline,
            'evidence_digest': provenance['evidence_digest'], 'timeline_verified': 'full'})
        self.f.now = available
        self.f.store.append_event(self.f.session['id'], 'reasoning.delivery', {
            'request_id': rid, 'arm': 'b', 'generation': self.source.model_generation,
            'available_at': available, 'state': 'completed', 'status': 'ready', 'current': True})
        runner = LiveReasoning.__new__(LiveReasoning)
        runner.session, runner.sid, runner.policy, runner.store = self.f.session, self.f.session['id'], {}, self.f.store
        provider = {'status': 'ready', 'judgment': {'activity': 'computer_work', 'confidence': .8,
            'probabilities': {k: 1. if k == 'computer_work' else 0. for k in ACTIVITIES},
            'model_version': 'jev-1.13.0'}, 'diagnostics': {'request': packet}, 'provenance': provenance}
        output = runner._output({'request_id': rid, 'site_id': 'victoria', 'arm': 'b',
            'generation': self.source.model_generation, 'cutoff': cutoff, 'deadline': provenance['deadline'],
            'records': [{**camera['payload'], 'source_kind': 'perception.output', 'objects': [{'label': 'person'}]}]},
            provider, available)
        self.f.store.append_event(self.f.session['id'], 'reasoning.output', output)
        self.f.now = observed + .45
        result = self.graph.step()
        self.assertEqual(result['status'], 'admitted_attributed', result)
        self.assertIsNotNone(self.graph.semantic.requests[rid]['work_parent_ref'])

    def test_actual_stereo_ha_jev_worker_positive_commits_and_complete_causal_replay(self):
        graph = self.create()
        self.clear_authored_bootstrap()
        decisions, camera_rows = [], []
        for offset in range(2, 9):
            camera, ha, admitted = self.append_pair_and_input(offset)
            camera_rows.append(camera)
            geometry = admitted['desk']['event']['payload']
            self.assertEqual(geometry['status'], 'supplied')
            self.assertEqual(geometry['parent_refs'], [])
            self.assertEqual(len(geometry['observation_roots']), 2)
            self.assertIn(str(camera['id']), geometry['observation_roots'])
            self.assertNotIn('evidence_roots', camera['payload'])
            self.assertEqual(geometry['parts']['protection']['shape']['policy_ref'],
                             self.producer['desk_policy']['protection_policy_ref'])
            self.assertIsNone(admitted['desk']['hazard_clearance'])
            if offset == 2:
                self.append_current_jev(camera, ha)
            self.f.now = live.NOW + offset + .55
            decisions.append(self.tick())
        # No ninth pair or refreshed evidence: this final cut must still obey
        # the last original geometry/protection lease, not an endpoint sample.
        self.f.now = live.NOW + 9.55
        decisions.append(self.tick())
        positive = [d for d in decisions if d['execution']['status'] == 'virtual'
                    and any(s['brightness_fraction'] > 0 for s in d['execution']['readback'])]
        self.assertGreaterEqual(len(positive), 2, decisions[-2:])
        self.assertTrue(all(len(d['room_output']['fixtures']) == 4 for d in positive))
        checkpoint = json.loads(graph.journal.db.execute('SELECT checkpoint FROM workers WHERE worker_id=?',
            (self.worker['worker_id'],)).fetchone()[0])
        self.assertEqual(checkpoint['schema'], 'home-live-worker-checkpoint-11')
        relations = list(checkpoint['state']['engagement']['reducer']['state']['relations'].values())
        supported = [r for r in relations if r['state'] == 'supported']
        self.assertEqual(len(supported), 1, relations)
        self.assertEqual(supported[0]['chosen_clause']['qualification'], 'qualified_pc_input')
        self.assertGreaterEqual(supported[0]['enter_dwell'], 5)
        self.assertTrue(checkpoint['state']['pc_input_originals'])
        self.assertEqual(len(self.precommit_stereo), 7)
        self.assertTrue(all(s['landmark']['surface'] == 'visible_nose_surface' for s in self.precommit_stereo))
        self.assertTrue(all(s['point']['point_observed_at'] < r['payload']['observed_at']
                            for s, r in zip(self.precommit_stereo, camera_rows)))
        self.assertEqual(len(self.media.media_refs()), 7 * self.media_per_capture)
        if self.desk_original_version == 3:
            self.assertTrue(all(s['original_family'] == 'existing-api-assessment-1'
                and set(s['media_refs']) == {'rgb', 'bundle', 'calibration_original', 'assessment_association', 'mapped_depth'}
                for s in self.precommit_stereo))
            rows = self.f.store.list_media(self.source.scope['session_id'])['items']
            self.assertFalse(any(row['metadata'].get('stream') == 'paired_preview_original' for row in rows))
        semantic_facts = [p['payload'] for p in self.publications() if p['payload'].get('source_id') == 'jev-semantic']
        self.assertEqual(len(semantic_facts), 1)
        self.assertEqual(semantic_facts[0]['person_ref'], self.f.person_ref)
        original_path = Path(self.producer['original_archive']['filename'])
        original_rows = [json.loads(line)['body'] for line in original_path.read_text().splitlines()]
        originals = [json.loads(row['original_json']) for row in original_rows if row['kind'] == 'original']
        expected = {self.original_module.original_digest(value) for value in originals}
        self.assertEqual(len(originals), 29)  # 7*(capture+nose+desk+HA) + one JEV.
        media_refs, media_binding = self.media.media_refs(), deepcopy(self.media.binding)
        withdrawal = graph.withdraw()
        self.assertEqual(withdrawal['withdrawal'], 'recorded', withdrawal)
        self.assertEqual(len(graph.source_barrier_acks), 4)
        self.f.now += 1
        stopped = self.tick()
        self.assertEqual(stopped['execution']['status'], 'unavailable', stopped)
        drained = self.driver.drain(deadline_ms=2000, through_cursor=withdrawal['input_cursor'])
        self.assertEqual(drained['status'], 'settled', drained)
        recorder_receipt = self.recorder.drain({'status': 'complete', 'reason': 'authored_stereo_worker_join',
                                               'terminal_cursor': drained['input_cursor']})
        self.assertEqual(recorder_receipt['status'], 'drained')
        receipt = self.source.drain(withdrawal)
        receipt['terminal_cursor'] = drained['input_cursor']
        finished = graph.finalize(drain_receipt=receipt)
        self.assertEqual(finished['status'], 'stopped', finished)
        media_receipt = finished['originals']['stereo_media']
        self.assertEqual(media_receipt['status'], 'finalized')
        desk_binding = runtime._desk_archive_binding(self.source.scope, self.producer['desk_policy'],
                                                   self.composition.join.binding_digest)
        replay = importlib.import_module('home_den_runtime.lab_stereo_replay').replay_stereo
        sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        expected_replay = {'schema': 'home-live-stereo-replay-inputs-' + str(self.replay_input_version),
            'version': self.replay_input_version,
            'causal_sha256': sha(self.archive), 'originals_sha256': sha(original_path),
            'manifest_digest': digest(self.manifest), 'config_digest': self.manifest['configuration_digest'],
            'scope': deepcopy(self.source.scope), 'capture_binding': deepcopy(original_rows[0]['binding']),
            'capture_receipt_binding': {key: self.producer[key]
                for key in ('source_id', 'receipt_store_id', 'receipt_component_id')} | {
                    'root_lineage': 'persisted-observation-record-1'},
            'semantic_binding': deepcopy(original_rows[0]['semantic_binding']), 'desk_binding': desk_binding,
            'original_digests': sorted(expected), 'media_binding': media_binding, 'media_refs': media_refs,
            'media_manifest_digest': media_receipt['manifest_digest'],
            'replay_source_sha256': sha(ROOT / 'tools/den-live-runtime/replay.cjs')}
        media_directory = self.path / 'stereo-worker-media'
        replayed = replay(self.archive, original_path, media_directory, expected=expected_replay)
        self.assertEqual(replayed['status'], 'reproduced')
        self.assertTrue(replayed['originals_verified'])
        self.assertTrue(replayed['worker_reproduced'])
        self.assertFalse(replayed['physical_authority'])
        self.assertFalse(replayed['acceptance_eligible'])
        self.retained_evidence_receipt = export_authored_evidence(self, original_path, media_directory,
                                                                expected_replay, replayed)
        # Refusals below use the same production outer replay gate. A new
        # valid JSON hash chain cannot replace a missing causal desk original.
        entries = [json.loads(line) for line in original_path.read_text().splitlines()]
        removed = False
        kept = []
        for entry in entries:
            body = entry['body']
            if (not removed and body['kind'] == 'original'
                    and json.loads(body['original_json'])['schema'] == 'home-original-lab-desk-' + str(self.desk_original_version)):
                removed = True
                continue
            kept.append(deepcopy(body))
        self.assertTrue(removed)
        missing_original = self.path / 'missing-stereo-desk-original.jsonl'
        previous, encoded_lines = None, []
        for body in kept:
            body['previous'] = previous
            if body['kind'] == 'end':
                body['count'] -= 1
            previous = hashlib.sha256(self.original_module.encoded(body)).hexdigest()
            encoded_lines.append(self.original_module.encoded({'body': body, 'sha256': previous}) + b'\n')
        missing_original.write_bytes(b''.join(encoded_lines))
        missing_expected = deepcopy(expected_replay)
        missing_expected['originals_sha256'] = sha(missing_original)
        with self.assertRaises(ValueError):
            replay(self.archive, missing_original, media_directory, expected=missing_expected)
        # Change committed capture metadata while keeping the retained source
        # event, desk original, manifest and original pins untouched. Rebuilding
        # the causal chain/file pin must not turn false physical roots into an
        # original receipt accepted by the outer replay correspondence check.
        altered = [json.loads(line) for line in self.archive.read_text().splitlines()]
        capture_pins = {self.original_module.original_digest(original) for original in originals
                        if original['schema'] == 'home-original-lab-observation-1'}
        changed = False
        for entry in altered:
            row = entry['record']
            if row['kind'] != 'journal_prepare':
                continue
            for metadata in row['data']['original_receipts']:
                if metadata['ref']['digest'] in capture_pins:
                    root = next(iter(metadata['observation_root_times']))
                    metadata['observation_root_times'][root] += .25
                    metadata['sensor_root_bindings'][root] = ['camera:authored-wrong-camera']
                    changed = True
                    break
            if changed:
                break
        self.assertTrue(changed)
        previous, encoded_lines = None, []
        for entry in altered:
            entry['previous_digest'] = previous
            entry.pop('digest')
            entry['digest'] = previous = digest(entry)
            encoded_lines.append(self.original_module.encoded(entry) + b'\n')
        wrong_capture = self.path / 'wrong-capture-original-metadata.jsonl'
        wrong_capture.write_bytes(b''.join(encoded_lines))
        wrong_capture_expected = deepcopy(expected_replay)
        wrong_capture_expected['causal_sha256'] = sha(wrong_capture)
        with self.assertRaisesRegex(ValueError, 'stereo_replay_capture_original_mismatch'):
            replay(wrong_capture, original_path, media_directory, expected=wrong_capture_expected)
        wrong_binding = deepcopy(expected_replay)
        wrong_binding['media_binding']['lab_binding_digest'] = 'f' * 64
        with self.assertRaises(ValueError):
            replay(self.archive, original_path, media_directory, expected=wrong_binding)
        wrong_pin = deepcopy(expected_replay)
        wrong_pin['causal_sha256'] = 'e' * 64
        with self.assertRaises(ValueError):
            replay(self.archive, original_path, media_directory, expected=wrong_pin)
        # The outer gate also needs every actual NPZ/depth/RGB binary; the
        # unchanged inner journal replay never attempts live media recovery.
        missing = media_directory / 'bytes' / (media_refs[0]['sha256'] + '.bin')
        missing.unlink()
        with self.assertRaises(ValueError):
            replay(self.archive, original_path, media_directory, expected=expected_replay)


if __name__ == '__main__':
    unittest.main()

"""Generated pair -> actual Lab factory/Store -> existing DepthClock -> anchor.

Only external camera/model I/O and Windows directory-fsync are doubled. All
qualification originals are explicitly fictional, independently supplied test
inputs. A visible nose never supplies hidden body or lighting authority here.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for directory in (HERE.parent / 'home-context', ROOT / 'tools/den-live-runtime'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from config import Settings
from store import Store
from live_detector_app import build_runtime
from preview_client import PreviewPump
from depth_clock import DepthClock
from journal import Journal, digest
from live_test_helpers import configuration, grant, METHOD, NOW, PROFILE, TOKEN, publication_ref
from live_capture import _original_digest
from lab_live_journal_join import LabLiveJournalJoin
from lab_live_desk_anchor import LabLiveDeskAnchor, validate_stereo_support, body_ref
from paired_preview_client import encoded, sha
from staged_specialist import composite_manifest
from pose_composition import POLICY, MODEL_VERSION
from rtmpose_worker import RTMPoseWorker
from test_ha_provisioning import live_config
import live_capture_fixtures as capture


class _Recorder:
    def start(self, _): return {'state': 'recording'}
    def stop(self, _): return {'state': 'stopped'}


class GeneratedStereoFixture:
    """Reusable real-factory fixture; no hand-authored metric anchor.

    Root's runtime test can use ``store``, ``session``, ``profiles``, ``profile``,
    ``model_manifests``, ``append_pair`` and ``qualification(descriptor)`` with
    its existing sole Journal. ``admit`` is a convenience actual public Journal
    join for these focused tests, not a substitute publication fixture.
    """
    def __init__(self, case, *, registration=None, projection=None, clock_mapping=None,
                 person_ref=None, subject_ref=None, association_method_ref=None, depth_delta_s=-.01,
                 original_family=None):
        if original_family not in (None, 'existing-api-assessment-1'):
            raise ValueError('explicit_authored_stereo_family_required')
        self.original_family = original_family
        self.case, self.now = case, NOW-.5
        self.directory = tempfile.TemporaryDirectory(prefix='authored-stereo-anchor-')
        case.addCleanup(self.directory.cleanup)
        self.store = Store(Settings(storage_root=Path(self.directory.name), min_free_bytes=0,
            cameras=({'id': 'den'},)), clock=lambda: self.now)
        case.addCleanup(self.store.close)
        self.session, _ = self.store.create_session('den', 'detection', 'Authored', 'generated-stereo-request')
        self.store.transition(self.session['id'], 'starting')
        self.store.transition(self.session['id'], 'recording')
        self.session = self.store.get_session(self.session['id'])
        self.scope = dict(site_id='victoria', room_id='den', session_id=self.session['id'], domain_epoch='authored-domain')
        _, cp, _, _, _ = capture.setup()
        self.projection = deepcopy(projection or cp['projection'])
        self.registration = deepcopy(registration or cp['registration'])
        if projection is None:
            self.projection.update(site_id='victoria', camera_id='den')
        if registration is None:
            self.registration.update(site_id='victoria', room_id='den', camera_id='den')
        self.projection.setdefault('mount_signature', deepcopy(cp['mount_signature']))
        self.clock_mapping = deepcopy(clock_mapping or {
            'mapping_id': 'authored-original-rgb-clock', 'mapping_digest': digest(cp['clock_binding']),
            'source_epoch': 'synthetic-camera-epoch', 'original_unit': 'unix_ns',
            'epoch_origin_s': 0, 'max_uncertainty_s': .1})
        self.depth_clock_policy = deepcopy(cp['depth_clock'])
        self.person_ref = deepcopy(person_ref or {**self.scope, 'pid': 1, 'track_epoch': 'assessed-track'})
        self.subject_ref = deepcopy(subject_ref or {'subject_id': 'desk-work', 'subject_epoch': 'assessed-work'})
        self.association_method_ref = deepcopy(association_method_ref or capture.pin('authored-visible-nose-association'))
        self.model_manifests = {
            'detector': {'sha256': '1'*64, 'device_id': 'authored-detector', 'engine_epoch': 'authored-model-epoch'},
            'pose': {'sha256': '2'*64, 'device_id': 'authored-pose', 'engine_epoch': 'authored-model-epoch'}}
        self.profile = composite_manifest(self.model_manifests['detector'], self.model_manifests['pose'],
            profile_id='yolo26s-rtmpose-cpu-v1', engine_epoch='authored-model-epoch', policy=POLICY,
            model_version=MODEL_VERSION, pose_preprocessing_revision=RTMPoseWorker.preprocessing_revision,
            runtime_revision='3'*64)
        self.profile['artifact_sha256'] = self.profile['sha256']
        self.profiles = {arm: deepcopy(self.profile) for arm in ('a', 'b')}
        if original_family is None:
            config = live_config(schema_version=2, paired_preview={
                'schema': 'home-lab-paired-preview-1', 'version': 1, 'enabled': True, 'max_pairs': 8})
        else:
            from test_existing_api_assessment_factory import POLICY as assessment_policy
            config = live_config(schema_version=3, existing_api_assessment=deepcopy(assessment_policy))
        config['receiver_clock']['uncertainty_seconds'] = .0001
        self.runtime = build_runtime(self.store, SimpleNamespace(camera_id='den'), config,
            transport_factory=lambda *_: lambda *_: None, lease=SimpleNamespace(ready=lambda: True),
            experiment_id='placement-best', profiles_override=self.profiles)
        case.addCleanup(self.runtime.close)
        self.runtime.recorders['den'] = _Recorder()
        self.runtime.resolve_profiles = lambda *_: {'state': 'ready', 'profiles': deepcopy(self.profiles)}
        self.runtime.executor = self.execute
        if original_family is None:
            self.runtime.paired_depth_clocks['den'] = deepcopy(self.depth_clock_policy)
        def dormant(pump):
            pump.thread = threading.Thread(target=lambda: None)
            pump.thread.start(); pump.thread.join()
        # Only the external OS directory sync lacks a Windows equivalent in
        # this workstation fixture; production still requires the actual ACK.
        if sys.platform == 'win32':
            self.sync_patch = patch(('paired_preview_client' if original_family is None else 'assessment_existing_api')
                                    + '.sync_directory')
            self.sync_patch.start(); case.addCleanup(self.sync_patch.stop)
        with patch.object(PreviewPump, 'start', dormant):
            self.runtime.start_session(self.session)
        self.now = NOW+.2
        self.append_pair(depth_delta_s=depth_delta_s)
        self.retained, self.media_calls, self.media = [], [], {}
        self.original_hook = None
        self.lab_binding_digest = '4'*64

    def execute(self, request):
        # Exact source-shaped composite result from the external model double.
        result = {'schema_version': 1, 'coordinate_space': 'source_pixels', 'frame_id': request['frame']['frame_id'],
            'boxes': [{'label': 'person', 'instance_id': 'generated-person', 'x': 260., 'y': 80., 'w': 120., 'h': 260.}],
            'objects': [{'label': 'person', 'instance_id': 'generated-person'}],
            'pose_instances': [{'layout': 'coco17', 'instance_id': 'generated-person',
                'joints': [{'joint_id': 0, 'pixel': [320., 150.], 'score': .9, 'status': 'supported'}]}],
            'processing': {'pipeline': 'detector-pose-v1', 'model_version': MODEL_VERSION,
                'manifest_sha256': self.profile['artifact_sha256'],
                'pose_preprocessing_revision': RTMPoseWorker.preprocessing_revision,
                'crop_policy': deepcopy(POLICY), 'correlated_evidence': True,
                'shared_source_request': request['request_id'],
                **{stage: {'artifact_sha256': manifest['sha256'], 'device_id': manifest['device_id']}
                   for stage, manifest in self.model_manifests.items()}}}
        return {'request_id': request['request_id'], 'engine_epoch': self.profile['engine_epoch'],
            'artifact_sha256': self.profile['artifact_sha256'], 'terminal': True, 'state': 'completed', 'result': result}

    def append_pair(self, *, sequence=7, observed_at=NOW, depth_delta_s=-.01, depth_sequence=None,
                    depth_device_s=None, derive=True):
        if self.original_family is None:
            from test_paired_preview_client import authored_pair
            pair = authored_pair(sequence=sequence, observed_ns=int(observed_at*1000000000))
        else:
            import hashlib
            jpeg, header, bundle, calibration = capture.inputs(sequence=sequence,
                observed_ns=int(observed_at*1000000000),
                preview_sha256=hashlib.sha256(bytes(540 * 640)).hexdigest())
            pair = dict(jpeg=jpeg, header=header, bundle=bundle, calibration=calibration)
        if depth_delta_s != -.01 or depth_sequence is not None or depth_device_s is not None:
            receipt = capture.decoded_header(pair['header'])
            meta = capture.frame_metadata(sequence=sequence, depth_sequence=(41+sequence-7 if depth_sequence is None else depth_sequence),
                rgb_device_s=receipt['frame_device_ns']/1e9,
                depth_device_s=receipt['frame_device_ns']/1e9+depth_delta_s if depth_device_s is None else depth_device_s)
            pair['bundle'] = capture.bundle(meta)
        pair['received_at'] = self.now
        self.pair = deepcopy(pair)
        if self.original_family is None:
            self.runtime.ingest_paired_preview(self.session['id'], pair['jpeg'], pair['header'], pair)
        else:
            from preview_client import LocalBundleClient
            def camera_read(client, path):
                if path == '/frame.npz':
                    return pair['bundle'], ''
                if path == '/calibration':
                    return json.dumps(pair['calibration'], indent=2).encode(), ''
                raise AssertionError('authored_existing_api_unknown_camera_route')
            with patch.object(LocalBundleClient, '_read', autospec=True, side_effect=camera_read):
                self.runtime.preview_pumps[self.session['id']].consume(pair['jpeg'], pair['header'])
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            runner = self.runtime.runners[self.session['id']]
            if all(value is None for value in runner.snapshot()['active'].values()):
                rows = [e for e in self.store.list_events(self.session['id']) if e['kind']=='perception.output'
                        and e['payload'].get('arm')=='b' and e['payload'].get('frame_id')==capture.decoded_header(pair['header'])['session']+'-0-'+str(sequence)]
                if rows:
                    self.row = deepcopy(rows[-1]); break
            time.sleep(.005)
        else:
            raise AssertionError('bounded actual factory model double did not produce original arm-B output')
        self.rgb = self.store.get_media(self.session['id'], self.row['payload']['media_ids'][0])
        self.frame = deepcopy(self.rgb['metadata'])
        stream = 'paired_preview_original' if self.original_family is None else 'assessment_existing_api_association'
        originals = [m for m in self.store.list_media(self.session['id'])['items']
                     if m['metadata'].get('stream') == stream and m['metadata'].get('rgb_media_id') == self.rgb['id']]
        original = json.loads(self.store.media_path(self.session['id'], originals[0]['id']).read_bytes())
        if self.original_family is None:
            self.paired = original
            bundle_id = original['bundle_media_id']
        else:
            self.assessment_association = original
            bundle_id = original['bundle_media_ref']['id']
        self.mapped = None
        if derive:
            self.mapped = DepthClock(self.store, site_id='victoria', camera_id='den', clock=self.depth_clock_policy,
                calibration=self.projection).derive(self.session['id'], bundle_id, self.rgb['id'])
        return deepcopy(self.row), deepcopy(self.frame)

    def qualification(self, descriptor):
        policy = dict(schema='home-desk-stereo-policy-1', version=1, id='authored-stereo-policy',
            status='independently_assessed', scope=deepcopy(self.scope), method_ref=capture.pin('authored-stereo-nose-method'),
            association_method_ref=deepcopy(self.association_method_ref), projection_ref=body_ref(self.projection),
            registration_ref=body_ref(self.registration), clock_mapping_ref={'id': self.clock_mapping['mapping_id'],
                'digest': self.clock_mapping['mapping_digest']}, depth_clock_ref=body_ref(self.depth_clock_policy),
            calibration_content_id=self.projection['id'], requirements_ref=capture.pin('authored-stereo-requirements'),
            assessment_report_ref=capture.pin('authored-stereo-independent-assessment'), applicability_ref=capture.pin('authored-nose-visible-region'),
            nose_error_bound_m=.02, max_abs_skew_s=.1, max_depth_age_s=3, max_speed_m_s=.01,
            max_accel_m_s2=.01, radius_px=4, min_support=5, max_spread_m=.08,
            valid_from=NOW-10, available_at=NOW-9, valid_until=NOW+100)
        observed = descriptor['original']['event']['payload']['observed_at']
        association = dict(schema='home-desk-stereo-association-1', version=1, id='authored-association-'+str(descriptor['source_position']['capture_seq']),
            status='qualified', scope=deepcopy(self.scope), person_ref=deepcopy(self.person_ref), subject_ref=deepcopy(self.subject_ref),
            method_ref=deepcopy(self.association_method_ref), source_generation=descriptor['source_generation'],
            source_position=deepcopy(descriptor['source_position']), capture_ref=deepcopy(descriptor['publication_ref']),
            instance_ref='object-0', state='seated_unique', occupant_count=1, nose_visible=True,
            observed_at=observed, available_at=self.now, valid_until=observed+2.5,
            requirements_ref=capture.pin('authored-association-requirements'), assessment_ref=capture.pin('authored-association-assessment'))
        return dict(schema='home-desk-stereo-qualification-1', version=1, stereo_policy=policy, association=association,
            projection=deepcopy(self.projection), registration=deepcopy(self.registration),
            clock_mapping=deepcopy(self.clock_mapping), depth_clock_policy=deepcopy(self.depth_clock_policy))

    def admit(self):
        if not hasattr(self, 'join'):
            self.config = configuration(); self.config['manifest']['identity'] = deepcopy(self.scope)
            source = self.config['manifest']['sources']['synthetic-source']
            source['association_method_refs'] = [deepcopy(METHOD)]
            source['model_refs'] = [{'id': 'authored-composite', 'digest': self.profile['artifact_sha256']}]
            source['capture_clock'] = deepcopy(self.clock_mapping)
            source['sensor_roots'] = ['camera:den']
            source['registration'].update(registration_ref=self.registration['id'], registration_digest=digest(self.registration),
                geometry_revision=self.registration['geometry_revision'], geometry_digest=self.registration['geometry_digest'],
                calibration_digest=self.projection['id'], transform_chain_refs=[deepcopy(self.registration['transform_ref'])])
            self.config['manifest_ref']['digest'] = digest(self.config['manifest'])
            self.grant = grant(); self.grant['scopes']=[{'site_id': 'victoria', 'room_id': 'den'}]
            self.grant['sensor_roots'] = ['camera:den']
            self.grant['live_bindings'][0]['manifest_ref'] = deepcopy(self.config['manifest_ref'])
            self.join = LabLiveJournalJoin(self.session, arm='b', generation=1, live_config=self.config,
                source_id='synthetic-source', revision='synthetic-r1', method_ref=METHOD,
                model_ref=source['model_refs'][0], lab_profile=self.profile, model_manifests=self.model_manifests,
                receipt_store_id='synthetic-receipts', receipt_component_id='pose', root_lineage='persisted-observation-record-1')
            self.journal = Journal(Path(self.directory.name)/'journal.sqlite', grants=[self.grant], profile=PROFILE,
                live_config=self.config, live_receipt_resolver=self.join.resolve_receipt, clock=lambda: self.now)
            self.case.addCleanup(self.journal.close)
            self.principal = self.journal.authenticate(TOKEN)
            self.lab_binding_digest = self.join.binding_digest
        result = self.join.publish(self.row, frame_metadata=self.frame, received_at=self.now,
            journal=self.journal, grant=self.principal)
        self.case.assertEqual(result['status'], 'admitted_unqualified', result)
        receipt = result['receipts'][0]; pref = publication_ref(receipt)
        publication = next(p for p in self.journal.snapshot(self.principal,'victoria','den')['items'] if p['id']==pref['id'])
        original_ref = publication['payload']['original_receipt_refs'][0]
        self.descriptor = dict(schema='home-lab-admitted-capture-1', version=1, scope=deepcopy(self.scope),
            lab_binding_digest=self.join.binding_digest, publication=publication, publication_ref=pref,
            original=self.join.original_receipt(original_ref['store_id'],original_ref['receipt_id']),
            original_record=self.join.resolve_receipt(original_ref['store_id'],original_ref['receipt_id']),
            frame_metadata=deepcopy(self.frame), source_position=deepcopy(publication['payload']['head']['position']),
            source_generation=publication['payload']['source_generation'], model_generation=1,
            media_generation=self.session['generation'])
        return deepcopy(self.descriptor)

    def retain_media(self, refs):
        self.media_calls.append(deepcopy(refs))
        for ref in refs:
            row = self.store.get_media(self.session['id'], ref['id'], generation=self.session['generation'])
            self.media[ref['id']] = {'record': deepcopy(row), 'data': self.store.media_path(self.session['id'], ref['id']).read_bytes()}
        return {'status':'retained','binding_digest':sha(encoded({'scope':self.scope,
            'lab_binding_digest':self.lab_binding_digest,'media_generation':self.session['generation'],'camera_id':'den'})),
            'media_refs':deepcopy(refs)}

    def resolve_media(self, ref):
        return deepcopy(self.media[ref['id']])

    def retain_original(self, original):
        self.retained.append(deepcopy(original))
        if self.original_hook:
            return self.original_hook(original)
        return {'status':'retained','digest':sha(encoded(original))}

    def producer(self, *, original_retainer=True):
        q = self.qualification(self.descriptor)
        return LabLiveDeskAnchor(self.store, scope=self.scope, lab_binding_digest=self.lab_binding_digest,
            stereo_policy_ref=body_ref(q['stereo_policy']), resolve_qualification=self.qualification,
            retain_media=self.retain_media, resolve_media=self.resolve_media,
            original_retainer=self.retain_original if original_retainer else None, clock=lambda:self.now)


class LabLiveDeskAnchorTests(unittest.TestCase):
    def setUp(self):
        self.f = GeneratedStereoFixture(self)
        self.descriptor = self.f.admit()
        self.producer = self.f.producer()

    def selected(self):
        result = self.producer.select(self.descriptor, self.f.now)
        self.assertEqual(result['status'],'selected',result)
        return result

    def test_actual_factory_pair_existing_depth_and_public_journal_select_only_visible_nose(self):
        result = self.selected(); support=result['stereo_support']; anchor=result['anchor']
        self.assertEqual(len(self.f.media_calls[0]),4)
        self.assertEqual(support['landmark']['surface'],'visible_nose_surface')
        self.assertEqual(support['landmark']['rgb_pixel'],[320.,150.])
        self.assertEqual(support['depth']['observed_at'],self.f.mapped['metadata']['observed_at'])
        self.assertEqual(anchor['observed_at'],self.f.row['payload']['observed_at'])
        self.assertLess(support['point']['point_observed_at'],anchor['observed_at'])
        self.assertGreater(anchor['error_bound_m'],.02)
        self.assertNotIn('body',anchor); self.assertNotIn('protection',anchor)
        self.assertEqual(self.f.retained[0]['support'],support)
        self.assertEqual(self.producer.select(self.descriptor,self.f.now),result)
        self.assertEqual(len(self.f.retained),1)

    def test_lost_original_ack_retries_exact_bytes_without_refreshing_support(self):
        seen=[]
        def lose_once(original):
            seen.append(encoded(original))
            if len(seen)==1:raise TimeoutError('authored ACK loss')
            return {'status':'retained','digest':sha(encoded(original))}
        self.f.original_hook=lose_once
        with self.assertRaisesRegex(RuntimeError,'retention_unconfirmed'):
            self.producer.select(self.descriptor,self.f.now)
        self.f.now+=.1
        result=self.selected()
        self.assertEqual(seen[0],seen[1])
        self.assertEqual(result['stereo_support']['produced_at'],NOW+.2)
        self.assertEqual(result['anchor']['available_at'],NOW+.3)
        self.assertEqual(result['stereo_support']['depth']['available_at'],NOW+.2)

    def test_same_physical_depth_sequence_changed_device_time_cannot_create_new_identity(self):
        result=self.selected(); first=deepcopy(result['stereo_support']['depth'])
        self.f.now=NOW+1.2
        self.f.append_pair(sequence=8,observed_at=NOW+1,depth_sequence=41,depth_device_s=51.199)
        self.descriptor=self.f.admit()
        result=self.producer.select(self.descriptor,self.f.now)
        self.assertEqual(result,{'status':'unavailable','reason':'physical_depth_original_changed_or_refreshed'})
        self.assertEqual(list(self.producer.depth_components.values()),[first])

    def test_missing_existing_mapped_depth_never_calls_mapper_as_fallback(self):
        self.f.now=NOW+1.2
        self.f.append_pair(sequence=8,observed_at=NOW+1,derive=False)
        self.descriptor=self.f.admit()
        with patch.object(DepthClock,'derive',side_effect=AssertionError('no new mapper')):
            result=self.producer.select(self.descriptor,self.f.now)
        self.assertEqual(result,{'status':'unavailable','reason':'existing_depth_clock_media_unavailable'})
        self.assertFalse(self.f.retained)

    def test_later_than_rgb_depth_remains_unavailable(self):
        self.f.now=NOW+1.2
        self.f.append_pair(sequence=8,observed_at=NOW+1,depth_delta_s=.01)
        self.descriptor=self.f.admit()
        result=self.producer.select(self.descriptor,self.f.now)
        self.assertEqual(result['status'],'unavailable',result)
        self.assertFalse(self.f.retained)

    def test_qualification_and_binary_binding_cannot_be_replaced(self):
        original=self.f.qualification
        self.producer.resolve_qualification=lambda d:{**original(d),'association':{**original(d)['association'],'nose_visible':False}}
        self.assertEqual(self.producer.select(self.descriptor,self.f.now)['status'],'unavailable')
        self.producer.resolve_qualification=original
        retain=self.producer.retain_media
        self.producer.retain_media=lambda refs:{**retain(refs),'binding_digest':'f'*64}
        result=self.producer.select(self.descriptor,self.f.now)
        self.assertEqual(result,{'status':'unavailable','reason':'stereo_media_retention_binding_changed'})
        self.assertFalse(self.f.retained)

    def test_store_generation_change_during_original_retention_cannot_select(self):
        def changed(_):
            self.store_generation=self.f.store.get_session(self.f.session['id'])['generation']
            with self.f.store.lock:
                self.f.store.db.execute('UPDATE sessions SET generation=generation+1 WHERE id=?',(self.f.session['id'],))
                self.f.store.db.commit()
            return {'status':'retained','digest':sha(encoded(_))}
        self.f.original_hook=changed
        result=self.producer.select(self.descriptor,self.f.now)
        self.assertEqual(result,{'status':'unavailable','reason':'stereo_incumbent_session_changed'})

    def test_final_binary_verification_cannot_select_after_original_lease_expires(self):
        delayed=[False]
        def ack(original):
            delayed[0]=True
            return {'status':'retained','digest':sha(encoded(original))}
        def resolve(ref):
            result=self.f.resolve_media(ref)
            if delayed[0]:
                delayed[0]=False
                self.f.now+=5
            return result
        self.f.original_hook=ack
        self.producer.resolve_media=resolve
        result=self.producer.select(self.descriptor,self.f.now)
        self.assertEqual(result,{'status':'unavailable','reason':'stereo_original_support_expired'})
        entry=next(iter(self.producer.prepared.values()))
        self.assertNotIn('selected',entry)
        self.assertEqual(entry['record'],self.f.retained[0])
        self.assertEqual(entry['support']['produced_at'],NOW+.2)
        self.assertEqual(entry['anchor']['valid_until'],NOW+2.5)

    def test_quiesce_during_original_retention_and_finalize_clear_only_after_settlement(self):
        self.f.original_hook=lambda original:(self.producer.quiesce() and {'status':'retained','digest':sha(encoded(original))})
        result=self.producer.select(self.descriptor,self.f.now)
        self.assertEqual(result,{'status':'unavailable','reason':'stereo_anchor_quiesced'})
        self.assertEqual(self.producer.finalize(),{'status':'finalized','buffers_cleared':True})
        self.assertFalse(self.producer.prepared); self.assertFalse(self.producer.depth_components)

    def test_retainer_installs_once_before_selection_and_unknown_ack_cannot_finalize(self):
        source=self.f.producer(original_retainer=False)
        source.install_original_retainer(self.f.retain_original)
        with self.assertRaisesRegex(ValueError,'already_installed'):
            source.install_original_retainer(self.f.retain_original)
        self.f.original_hook=lambda _:{'status':'unknown'}
        with self.assertRaises(RuntimeError):source.select(self.descriptor,self.f.now)
        self.assertEqual(source.quiesce(),{'status':'unconfirmed','in_flight':False})
        self.assertEqual(source.finalize(),{'status':'unconfirmed','buffers_cleared':False})


if __name__=='__main__':unittest.main()

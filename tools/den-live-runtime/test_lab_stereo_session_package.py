"""Preparation-only source4 package; no activation or live qualification claim.

The future runner constructs the existing authored source4 fixture once. Tests
call only the new pure preparation function, never a supervisor start or host.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
for directory in (ROOT/'stack/services/home-context',ROOT/'stack/services/perception-lab'):
    if str(directory) not in sys.path:sys.path.append(str(directory))

import lab_stereo_session_package as package
from journal import digest
from paired_preview_client import encoded,sha
from lab_live_runtime import _join_and_configuration
import test_lab_stereo_desk_runtime as fixture
import test_live_den_preflight as preflight_fixture


class LabStereoSessionPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=fixture.LabStereoDeskRuntimeTests()
        cls.source.setUp();cls.addClassCleanup(cls.source.doCleanups)
        cls.template=cls.inputs()

    @classmethod
    def inputs(cls):
        s=cls.source;producer=deepcopy(s.producer);join,graph=_join_and_configuration(producer,s.f.session)
        scope=producer['live_config']['manifest']['identity'];base=s.path
        declared=producer['live_config']['manifest']['worker']
        worker={k:deepcopy(v) for k,v in declared.items() if k!='config_digest' and not k.endswith('schema_pins')}
        worker.update(profile='home-live-shadow-1',identity=deepcopy(scope),manifest_ref=deepcopy(producer['live_config']['manifest_ref']),
            live_manifest=deepcopy(producer['live_config']['manifest']),token_file=str(base/'worker.secret'))
        seed={'original':{'event':deepcopy(s.f.row)},'source_generation':1,
            'source_position':{'capture_epoch':'synthetic-camera-epoch','capture_seq':7,'revision':0},'publication_ref':None}
        p=s.f.qualification(seed)['stereo_policy'];c=deepcopy(s.commissioning)
        authority=dict(scope=deepcopy(scope),lab_binding_digest=join.binding_digest,
            stereo_policy_ref=deepcopy(producer['desk_policy']['stereo_policy_ref']),
            association_method_ref=deepcopy(producer['desk_policy']['association_method_ref']),
            **{k:deepcopy(p[k]) for k in ('projection_ref','registration_ref','clock_mapping_ref','depth_clock_ref',
                'requirements_ref','assessment_report_ref','applicability_ref')})
        provision={k:str(base/name) for k,name in (
            ('mount_file','mount.json'),('bootstrap_file','bootstrap.json'),('operator_file','operator.json'),
            ('preflight_file','preflight.json'),('commissioning_file','stereo-commissioning.json'),
            ('qualification_file','stereo-qualification.json'))}
        provision.update(context_token_sha256=producer['journal_grants'][2]['token_sha256'],
            operator_token_sha256=hashlib.sha256(b'authored-stereo-operator').hexdigest(),
            publication_token_sha256=producer['journal_grants'][0]['token_sha256'],
            worker_token_sha256=producer['journal_grants'][1]['token_sha256'],lab_token_sha256=['e'*64])
        preflight=preflight_fixture.LocalDenPreflightTests();preflight.setUp()
        preflight.receipt.update(configuration_digest=digest(graph),control_clock={
            'policy':deepcopy(join.source['control_clock']),'uncertainty_s':.001})
        s.f.store.append_event(s.f.session['id'],'authored.original-clock',
            {'observed_at_unix_ns':1800000000000000123,'role':'nonqualifying_original_preservation_fixture'})
        events=deepcopy(s.f.store.list_events(s.f.session['id']))
        # Preserve an original integer clock above JavaScript's safe range in a
        # whole-Store record. It is never sent to a browser or public schema.
        return dict(schema='home-den-lab-session-inputs-2',version=2,session=deepcopy(s.f.session),
            producer=producer,worker_config=worker,
            runtime=dict(node_runtime=str(base/'node.exe'),archive_file=str(base/'decisions.jsonl'),
                archive_limits=dict(max_records=1024,max_bytes=8*1024**2,min_free_bytes=1),
                source_bundle_digest='b'*64,max_cycles=8,max_runtime_s=10,interval_s=.5,drain_timeout_s=3),
            operator=dict(upstream='https://authored-victoria.invalid:8443',ca_file=str(base/'ca.pem'),
                token_file=str(base/'operator.secret'),timeout_s=5),provisioning=provision,preflight=preflight.receipt,
            evidence=dict(registration=deepcopy(worker['room_policy']['registration']),
                equipment={'authored':'owner-export'},collision={'authored':'assessed-fictional-room'},
                fixtures=deepcopy(worker['room_policy']['fixtures']),examples=deepcopy(worker['scene_policy']['catalog']),
                qualifications={'authored':'independent-fixture-only'},clocks={'authored':'clock-originals'}),
            stereo=dict(schema='home-den-stereo-package-inputs-1',version=1,commissioning=c,stereo_policy=p,
                depth_clock_policy=deepcopy(s.f.depth_clock_policy),authority_binding=authority,association_originals=[],
                media=dict(directory=str(base/'prepared-media'),max_objects=32,max_bytes=16*1024**2,min_free_bytes=1)),
            store_window=dict(schema='home-lab-store-window-export-1',version=1,session_id=s.f.session['id'],
                after_id=0,through_id=events[-1]['id'],events=events,export_receipt=None),
            used_originals=None,home_entry=None,command_capture=None)

    def setUp(self):self.value=deepcopy(self.template);self.now=1800000000.2
    def prepare(self):return package.prepare_stereo_session_package(self.value,now=self.now)
    def refused(self):
        result=self.prepare();self.assertEqual(result['status'],'refused',result)
        self.assertEqual(result['files'],{});self.assertFalse(result['activation_ready']);return result

    def test_exact_source4_mount3_bootstrap2_and_complete_session_are_preserved(self):
        before=deepcopy(self.value);result=self.prepare()
        self.assertEqual(result['status'],'prepared',result)
        self.assertEqual(self.value,before);self.assertEqual(result['files']['lab-session.json'],before['session'])
        mount=result['files']['mount.json'];self.assertEqual(mount['schema'],'home-managed-den-mount-3')
        self.assertEqual(mount['producer'],before['producer']);self.assertEqual(mount['runtime']['worker_config'],before['worker_config'])
        self.assertEqual(mount['runtime']['session']['mode'],'live_shadow_unqualified')
        self.assertEqual(result['files']['bootstrap.json']['schema'],'home-den-lab-bootstrap-2')
        self.assertFalse(result['capability_constructed']);self.assertFalse(result['activation_ready'])
        self.assertIn('concrete_exact_capture_association_authority_not_installed',result['activation_blockers'])
        self.assertEqual(result,self.prepare())

    def test_prepare_is_inert_and_does_not_resolve_associations_or_construct_resources(self):
        fail=lambda *_args,**_kwargs:(_ for _ in ()).throw(AssertionError('prepare attempted runtime operation'))
        with patch('store.Store',side_effect=fail),patch('subprocess.run',side_effect=fail),\
                patch('subprocess.Popen',side_effect=fail),patch('threading.Thread.start',side_effect=fail),\
                patch('socket.socket',side_effect=fail),patch('lab_live_desk_anchor.LabLiveDeskAnchor',side_effect=fail):
            result=self.prepare()
        self.assertEqual(result['status'],'prepared',result)

    def test_qualifying_json_or_unavailable_command_extension_cannot_enable_activation(self):
        result=self.prepare();self.assertFalse(result['activation_ready'])
        self.value['stereo']['authority_binding']['caller_qualified']=True;self.refused()
        self.value=deepcopy(self.template);self.value['command_capture']={'kind':'invented-driver'};self.refused()

    def test_changed_independent_static_original_or_authority_pin_refuses(self):
        for mutate in (lambda v:v['stereo']['commissioning']['assessment'].update(max_anchor_error_m=.00001),
                       lambda v:v['stereo']['authority_binding'].update(lab_binding_digest='f'*64),
                       lambda v:v['stereo']['stereo_policy'].update(valid_until=self.now)):
            with self.subTest(mutate=mutate):
                self.value=deepcopy(self.template);mutate(self.value);self.refused()

    def test_reclosed_foreign_registration_clock_and_understated_registration_error_refuse(self):
        # These bodies retain valid internal assessment/binding/policy hashes.
        # The contradiction is against the ACTUAL separately supplied camera
        # registration/clock, not a changed body under a stale reference.
        from desk_enclosure import digest as assessment_digest
        join,_=_join_and_configuration(self.value['producer'],self.value['session'])
        for attack in ('registration','clock','error'):
            with self.subTest(attack=attack):
                stereo=deepcopy(self.template['stereo']);producer=deepcopy(self.template['producer'])
                c=stereo['commissioning'];assessment,binding=c['assessment'],c['binding']
                if attack=='registration':
                    foreign={'id':'foreign-independent-registration','digest':'f'*64}
                    assessment['registration']['ref']=deepcopy(foreign)
                    binding['registration_ref']=deepcopy(foreign)
                elif attack=='clock':
                    foreign={'id':'foreign-independent-clock','digest':'f'*64}
                    assessment['clock_mapping']['ref']=deepcopy(foreign)
                    binding['clock_mapping_ref']=deepcopy(foreign)
                else:
                    assessment['registration']['error_bound_m']=c['registration']['error_bound_m']/2
                reclosed={'id':assessment['id'],'digest':assessment_digest(assessment)}
                binding['assessment_ref']=deepcopy(reclosed)
                producer['desk_policy']['assessment_ref']=deepcopy(reclosed)
                self.assertEqual(binding['assessment_ref']['digest'],assessment_digest(assessment))
                with self.assertRaisesRegex(package.PackageRefusal,'registration_or_clock_original_mismatch'):
                    package._stereo(stereo,producer,join,self.now)

    def test_model_media_generations_and_disjoint_credentials_are_closed(self):
        for mutate in (lambda v:v['producer']['lab_binding'].update(media_generation=999),
                       lambda v:v['provisioning'].update(operator_token_sha256=v['provisioning']['context_token_sha256']),
                       lambda v:v['stereo']['media'].update(max_objects=129)):
            with self.subTest(mutate=mutate):
                self.value=deepcopy(self.template);mutate(self.value);self.refused()

    def test_full_store_rows_keep_integer_originals_but_do_not_claim_replay_completeness(self):
        result=self.prepare();self.assertEqual(result['status'],'prepared',result)
        retained=result['files']['whole-store-window.json']
        self.assertEqual(retained,self.value['store_window'])
        self.assertTrue(result['bindings']['whole_store_window']['ordered_originals_intact'])
        self.assertFalse(result['bindings']['whole_store_window']['window_completeness_verified'])
        self.assertFalse(result['bindings']['whole_store_window']['whole_store_replay_complete'])
        self.assertEqual(retained['events'][-1]['payload']['observed_at_unix_ns'],1800000000000000123)
        import json
        self.assertEqual(json.loads(encoded(retained))['events'][-1]['payload']['observed_at_unix_ns'],1800000000000000123)
        self.assertEqual(result['files']['lab-session.json'],self.value['session'])
        self.value['store_window']['events'].reverse();self.refused()

    def test_selected_original_integrity_is_separate_from_whole_window_replay(self):
        result=self.prepare();self.assertEqual(result['status'],'prepared',result)
        join,_=_join_and_configuration(self.value['producer'],self.value['session'])
        from lab_original_archive import semantic_archive_binding,original_digest
        record=dict(schema='home-original-lab-observation-1',version=1,event=deepcopy(self.source.f.row),
            frame_metadata=deepcopy(self.source.f.frame),first_consumer_received_at=self.now,
            method_ref=deepcopy(join.method),model_ref=deepcopy(join.model))
        self.value['used_originals']=dict(schema='home-lab-used-original-export-1',version=1,
            binding={'capture':{'session_id':join.scope['session_id'],'method_ref':join.method,'model_ref':join.model},
                'semantic':semantic_archive_binding(join.scope,self.value['producer']['semantic_policy']),
                'desk':package._desk_archive_binding(join.scope,self.value['producer']['desk_policy'],join.binding_digest),
                'media':result['bindings']['media_binding']},records=[record],expected_digests=[original_digest(record)])
        result=self.prepare();self.assertEqual(result['status'],'prepared',result)
        self.assertTrue(result['bindings']['used_originals']['content_integrity_complete'])
        self.assertFalse(result['bindings']['used_originals']['source_authenticity_verified'])
        self.assertFalse(result['bindings']['used_originals']['ordinary_replay_complete'])
        self.value['used_originals']['expected_digests']=['f'*64];self.refused()

    def test_wrong_session_window_and_shared_state_paths_refuse(self):
        self.value['store_window']['session_id']='different-session';self.refused()
        self.value=deepcopy(self.template)
        self.value['stereo']['media']['directory']=self.value['producer']['journal_path'];self.refused()


if __name__=='__main__':unittest.main()

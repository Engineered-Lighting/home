"""HA source provisioning is checked at controller startup, before workers."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_paired_runtime as fixtures
from live_detector_app import CHECKPOINT, PREPROCESSING, build_runtime
from managed_detector_app import check_ha_provisioning, check_reasoning_windows
from managed_sources import UnavailableHA
from ha_ingest import HAIngestor
from jev_provider import history_windows

OCCUPANCY='binary_sensor.den_oak_person_occupancy'


def live_config(**extra):
    value={'schema_version':1,'capture_config':'/private/capture.json','camera_env':'/private/camera.env',
        'lease_file':'/private/lease.json','receiver_clock':{'validation_id':'measured','uncertainty_seconds':.4},'arms':{},**extra}
    for arm,device,ip in [('a','victoria-den-jetson','192.168.1.168'),('b','la-ai','192.168.0.100')]:
        value['arms'][arm]={'origin':'https://'+ip+':8791','token_file':'/private/'+arm+'.token','ca_file':'/private/'+arm+'.pem',
            'profile':{'checkpoint':'yolo26s','artifact_sha256':CHECKPOINT,'sha256':CHECKPOINT,'precision':'fp32',
                'preprocessing':deepcopy(PREPROCESSING),'task':'detect','runtime_version':'8.4.0','profile_id':arm,
                'model_version':'pinned','engine_epoch':'trial-'+arm,'device_id':device,'runtime_revision':'exact-runtime'}}
    return value


def ha_file(**changes):
    return {'schema_version':1,'site_id':'victoria','camera_id':'den','source_id':'frigate-ha',
        'origin':'https://ha-read.example.test','credential_file':'/private/ha.token',
        'entities':[OCCUPANCY],'camera_derived_entities':[OCCUPANCY],**changes}


class HAProvisioningTests(unittest.TestCase):
    def setUp(self):
        fixtures.PairedRuntimeTests.setUp(self)
        self.store.settings=replace(self.store.settings,cameras=({'id':'den'},))
        self.source=SimpleNamespace(camera_id='den')
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def runtime(self, ha, reasoning_factory=None):
        def read(_):
            if isinstance(ha,Exception):raise ha
            return deepcopy(ha)
        with patch('live_detector_app.private_json',side_effect=read):
            return build_runtime(self.store,self.source,live_config(ha_source_file='/private/ha-source.json'),
                transport_factory=lambda *_:(lambda *a:None),lease=SimpleNamespace(ready=lambda:False),
                reasoning_factory=reasoning_factory)

    def test_session_build_keeps_valid_ha_and_degrades_invalid_ha_after_workers_prepared(self):
        runtime=self.runtime(ha_file())
        try:self.assertIs(type(runtime.ha_sources['den']),HAIngestor)
        finally:runtime.close()
        occupancy=SimpleNamespace(ha_bindings={OCCUPANCY:'person_present'})
        for invalid,factory in ((ha_file(origin='http://ha-read.example.test'),None),(PermissionError(),None),
                                (ha_file(camera_derived_entities=[]),occupancy),(ha_file(credential_file='ha.token'),None)):
            runtime=self.runtime(invalid,factory)
            try:
                self.assertIsInstance(runtime.ha_sources['den'],UnavailableHA)
                with self.assertRaisesRegex(ValueError,'ha_source_invalid'):
                    runtime.ingest_ha(self.session['id'],**runtime.ha_clients['den']()[0])
            finally:runtime.close()
        events=[e['payload'] for e in self.store.list_events(self.session['id']) if e['kind']=='ha.unavailable']
        self.assertEqual(events,[{'reason':'ha_source_invalid','available_at':100.0}])

    def test_controller_startup_cross_checks_jev_bindings_against_ha_source(self):
        settings=SimpleNamespace(site_id='victoria')
        def check(ha,bindings,config=None):
            with patch('live_detector_app.private_json',return_value=ha):
                check_ha_provisioning(settings,self.source,config or live_config(ha_source_file='/private/ha-source.json'),
                    SimpleNamespace(ha_bindings=bindings))
        check(ha_file(),{OCCUPANCY:'person_present'})
        check(ha_file(camera_derived_entities=[]),{})
        check_ha_provisioning(settings,self.source,live_config(),None)
        with self.assertRaisesRegex(ValueError,'require a provisioned HA source file'):
            check(ha_file(),{OCCUPANCY:'person_present'},live_config())
        with self.assertRaisesRegex(ValueError,'outside the HA source allowlist'):
            check(ha_file(),{'binary_sensor.pc':'computer_active'})
        with self.assertRaisesRegex(ValueError,'Camera-derived provenance required'):
            check(ha_file(camera_derived_entities=[]),{OCCUPANCY:'person_present'})
        with self.assertRaisesRegex(ValueError,'Registered local HA source'):
            check(ha_file(camera_id='other'),{})

    def test_controller_startup_accepts_only_declared_derived_bindings(self):
        settings=SimpleNamespace(site_id='victoria')
        session,last='sensor.formd_t1_sessionstate','sensor.formd_t1_lastactive'
        derived={'computer_active':{'session_entity':session,'unlocked_values':['Unlocked'],
                                    'last_active_entity':last,'max_idle_seconds':300}}
        pc=ha_file(source_id='pc-ha',entities=[session,last],camera_derived_entities=[],derived=derived)
        def check(ha,bindings):
            with patch('live_detector_app.private_json',return_value=ha):
                check_ha_provisioning(settings,self.source,live_config(ha_source_file='/private/ha-source.json'),
                    SimpleNamespace(ha_bindings=bindings))
        check(pc,{'derived.computer_active':'computer_active'})
        with self.assertRaisesRegex(ValueError,'outside the HA source allowlist'):
            check({k:v for k,v in pc.items() if k!='derived'},{'derived.computer_active':'computer_active'})
        with self.assertRaisesRegex(ValueError,'Derived HA binding must name its own fact'):
            check(pc,{'derived.computer_active':'screen_playback'})
        with self.assertRaisesRegex(ValueError,'computer_active derivation'):
            check(ha_file(source_id='pc-ha',entities=[session,last],camera_derived_entities=[],
                          derived={'computer_active':{**derived['computer_active'],'max_idle_seconds':5}}),{})
        # A valid file builds a session ingestor that derives the fact.
        runtime=self.runtime(pc,SimpleNamespace(ha_bindings={'derived.computer_active':'computer_active'}))
        try:self.assertEqual(set(runtime.ha_sources['den'].derived),{'derived.computer_active'})
        finally:runtime.close()

    def test_managed_controller_fails_closed_before_app_or_workers(self):
        import managed_detector_app
        provision={'schema_version':1,'live_config':'/private/live.json','jev_config':'/private/jev.json',
            'lifecycle':{'a':{'kind':'jetson_local','release':'/release'},
                         'b':{'origin':'https://192.168.0.100:8792','token_file':'/private/b.token','ca_file':'/private/b.pem','site_id':'echo'}}}
        settings=SimpleNamespace(site_id='victoria',max_session_seconds=60)
        files={'/managed.json':provision,'/private/live.json':live_config(ha_source_file='/private/ha-source.json')}
        reasoning=SimpleNamespace(ha_bindings={OCCUPANCY:'person_present'},readiness=lambda:{})
        created=[]
        def start(ha):
            with patch.dict('os.environ',{'PERCEPTION_LAB_MANAGED_CONFIG':'/managed.json'}), \
                    patch('managed_detector_app.private_json',side_effect=lambda p:deepcopy(files[p])), \
                    patch('live_detector_app.private_json',return_value=ha), \
                    patch('capture_app.load_provisioning',return_value=(settings,self.source,None,None,None)), \
                    patch('session_budget.provisioned_budget',return_value={'session_seconds':60}), \
                    patch('jetson_lifecycle.JetsonLifecycleClient'),patch('worker_lifecycle_client.LifecycleClient'), \
                    patch('managed_jev.reasoning_factory',return_value=reasoning), \
                    patch('live_video.LiveVideo'),patch('live_planes.LivePlanes'), \
                    patch('app.create_app',side_effect=lambda *a,**k:created.append(k) or 'app'):
                return managed_detector_app.create_managed_detector_app()
        self.assertEqual(start(ha_file()),'app')
        for invalid in (ha_file(camera_derived_entities=[]),ha_file(origin='http://ha-read.example.test')):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                start(invalid)
        # The runtime factory (and so any worker preparation) was never built.
        self.assertEqual(len(created),1)

    def test_live_trial_app_validates_ha_source_at_startup(self):
        import live_detector_app
        settings=SimpleNamespace(site_id='victoria',max_session_seconds=60)
        created=[]
        def start(ha):
            files={'/live.json':live_config(ha_source_file='/private/ha-source.json'),'/private/ha-source.json':ha}
            with patch.dict('os.environ',{'PERCEPTION_LAB_LIVE_CONFIG':'/live.json'}), \
                    patch('live_detector_app.private_json',side_effect=lambda p:deepcopy(files[p])), \
                    patch('capture_app.load_provisioning',return_value=(settings,self.source,None,None,None)), \
                    patch('live_video.LiveVideo'),patch('live_planes.LivePlanes'), \
                    patch('app.create_app',side_effect=lambda *a,**k:created.append(k) or 'app'):
                return live_detector_app.create_live_detector_app()
        self.assertEqual(start(ha_file()),'app')
        with self.assertRaisesRegex(ValueError,'Absolute HA'):start(ha_file(credential_file='ha.token'))
        self.assertEqual(len(created),1)

    def test_history_windows_are_rejected_not_clamped(self):
        recipes=json.loads(Path(__file__).with_name('experiments.json').read_text())['experiments']
        factory=SimpleNamespace(check_windows=history_windows)
        check_reasoning_windows(recipes,{'placement-same','placement-best'},factory)
        check_reasoning_windows(recipes,{'history'},None)
        # The catalog's history recipe was revised to a 10 s arm; an over-bound recipe is still rejected.
        check_reasoning_windows(recipes,{'placement-same','history'},factory)
        over=[{'id':'history-20','a':{'history':0},'b':{'history':20}}]
        with self.assertRaisesRegex(ValueError,r"Recipe history-20 cannot run live JEV: .*b=20s is outside the 0-10s"):
            check_reasoning_windows(over,{'history-20'},factory)
        self.assertEqual(history_windows({'a':0,'b':10}),{'a':0,'b':10})
        for invalid in ({'a':10.5},{'b':-1},{'a':float('nan')},{'a':True}):
            with self.assertRaises(ValueError):history_windows(invalid)


if __name__=='__main__':unittest.main()

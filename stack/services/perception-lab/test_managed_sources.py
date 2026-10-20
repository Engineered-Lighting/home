import unittest
from unittest.mock import patch
from types import SimpleNamespace
from managed_sources import ha_source, ha_sources
import test_paired_runtime as fixtures

OCCUPANCY='binary_sensor.den_oak_person_occupancy'


class SourceTests(unittest.TestCase):
    def setUp(self):
        from dataclasses import replace
        fixtures.PairedRuntimeTests.setUp(self)
        self.store.settings=replace(self.store.settings,cameras=({'id':'den'},))
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def config(self):
        return {'schema_version':1,'site_id':'victoria','camera_id':'den','source_id':'ha-den',
            'origin':'https://ha.example.test','credential_file':'/private/ha.token',
            'entities':['binary_sensor.pc']}

    def build(self, cfg, **kw):
        with patch('live_detector_app.private_json',return_value=cfg):
            return ha_sources(self.store,SimpleNamespace(camera_id='den'),'/private/ha-source.json',**kw)

    def test_binding_constructs_without_reading_credentials_or_network(self):
        with patch('live_detector_app.private_json',return_value=self.config()),patch('ha_client.http.client.HTTPSConnection') as network:
            binding=ha_sources(self.store,SimpleNamespace(camera_id='den'),'private-config')
            self.assertEqual(set(binding),{'ha_sources','ha_clients'})
            self.assertEqual(binding['ha_sources']['den'].entities,{'binary_sensor.pc'})
            network.assert_not_called()

    def test_other_site_rejected(self):
        cfg=self.config();cfg['site_id']='echo'
        with patch('live_detector_app.private_json',return_value=cfg):
            with self.assertRaisesRegex(ValueError,'local HA'):
                ha_sources(self.store,SimpleNamespace(camera_id='den'),'private-config')

    def test_camera_derived_entities_pass_through_and_are_bounded(self):
        cfg=self.config();cfg['entities'].append(OCCUPANCY);cfg['camera_derived_entities']=[OCCUPANCY]
        self.assertEqual(self.build(cfg)['ha_sources']['den'].camera_derived_entities,{OCCUPANCY})
        self.assertEqual(self.build(self.config())['ha_sources']['den'].camera_derived_entities,frozenset())
        for derived in (['binary_sensor.unlisted'],OCCUPANCY,{OCCUPANCY:True},[{'entity':OCCUPANCY}]):
            with self.subTest(derived=derived),self.assertRaisesRegex(ValueError,'allowlist'):
                self.build({**cfg,'camera_derived_entities':derived})

    def test_plain_http_origin_unknown_keys_and_relative_paths_rejected(self):
        with self.assertRaisesRegex(ValueError,'provisioned HTTPS origin required'):
            self.build({**self.config(),'origin':'http://ha.example.test'})
        for extra in ({'token':'inline'},{'attributes':True},{'ca':'/private/ca.pem'}):
            with self.subTest(extra=extra),self.assertRaisesRegex(ValueError,'Registered local HA source'):
                self.build({**self.config(),**extra})
        for changed in ({'credential_file':'ha.token'},{'credential_file':'../private/ha.token'},
                        {'ca_file':'ca.pem'},{'credential_file':None}):
            with self.subTest(changed=changed),self.assertRaisesRegex(ValueError,'Absolute HA'):
                self.build({**self.config(),**changed})
        with self.assertRaisesRegex(ValueError,'entity allowlist'):
            self.build({**self.config(),'entities':{'binary_sensor.pc':True}})

    def test_private_ca_is_bound_to_the_reader_connection_only(self):
        cfg={**self.config(),'ca_file':'/private/ha-boundary.pem'}
        with patch('managed_sources.ssl.create_default_context') as context:
            client=self.build(cfg)['ha_clients']['den']
        context.assert_called_once_with(cafile='/private/ha-boundary.pem')
        with patch('managed_sources.http.client.HTTPSConnection') as connection:
            client.connection_factory('ha.example.test',443,timeout=1.5)
        connection.assert_called_once_with('ha.example.test',443,timeout=1.5,context=context.return_value)
        with patch('managed_sources.ssl.create_default_context',side_effect=FileNotFoundError()):
            with self.assertRaises(FileNotFoundError):self.build(cfg)

    def test_api_selects_core_or_scoped_read_path(self):
        self.assertEqual(self.build(self.config())['ha_clients']['den'].state_path,'/api/states/')
        self.assertEqual(self.build({**self.config(),'api':'core'})['ha_clients']['den'].state_path,'/api/states/')
        client=self.build({**self.config(),'api':'perception_lab_source'})['ha_clients']['den']
        self.assertEqual((client.api,client.state_path),
                         ('perception_lab_source','/api/perception_lab_source/v1/states/'))
        for api in ('perception_lab_source_write','CORE',None,1,['core'],{'core':True}):
            with self.subTest(api=api),self.assertRaisesRegex(ValueError,'supported HA read api'):
                self.build({**self.config(),'api':api})

    def test_scoped_source_keeps_https_pinning_and_private_credential_rules(self):
        scoped={**self.config(),'api':'perception_lab_source'}
        with self.assertRaisesRegex(ValueError,'provisioned HTTPS origin required'):
            self.build({**scoped,'origin':'http://ha.example.test'})
        with self.assertRaisesRegex(ValueError,'Absolute HA'):
            self.build({**scoped,'credential_file':'lab.secret'})
        with self.assertRaisesRegex(ValueError,'Pinned HA origin'):
            self.build({**scoped,'expected_hostname':'other.example.test'})
        with self.assertRaisesRegex(ValueError,'Registered local HA source'):
            self.build({**scoped,'attributes':{'binary_sensor.pc':['device_class']}})

    def test_expected_hostname_pins_the_origin(self):
        self.assertEqual(self.build({**self.config(),'expected_hostname':'HA.example.test'})['ha_clients']['den'].host,'ha.example.test')
        for pinned in ('other.example.test','',None):
            with self.subTest(pinned=pinned),self.assertRaisesRegex(ValueError,'Pinned HA origin'):
                self.build({**self.config(),'expected_hostname':pinned})

    def test_jev_bindings_must_be_read_and_occupancy_must_keep_camera_origin(self):
        cfg=self.config();cfg['entities'].append(OCCUPANCY)
        with self.assertRaisesRegex(ValueError,'outside the HA source allowlist'):
            self.build(cfg,ha_bindings={'binary_sensor.other':'computer_active'})
        with self.assertRaisesRegex(ValueError,'Camera-derived provenance required'):
            self.build(cfg,ha_bindings={OCCUPANCY:'person_present'})
        self.build(cfg,ha_bindings={'binary_sensor.pc':'computer_active'})
        self.build({**cfg,'camera_derived_entities':[OCCUPANCY]},ha_bindings={OCCUPANCY:'person_present'})

    def derived_config(self):
        session,last='sensor.formd_t1_sessionstate','sensor.formd_t1_lastactive'
        return {**self.config(),'entities':[session,last],'derived':{'computer_active':{
            'session_entity':session,'unlocked_values':['Unlocked'],'last_active_entity':last,'max_idle_seconds':300}}}

    def test_derived_computer_active_passes_through_and_is_strictly_validated(self):
        cfg=self.derived_config()
        source=self.build(cfg)['ha_sources']['den']
        self.assertEqual(set(source.derived),{'derived.computer_active'})
        self.assertEqual(source.camera_derived_entities,frozenset())
        self.assertEqual(self.build(self.config())['ha_sources']['den'].derived,{})
        rule=cfg['derived']['computer_active']
        for derived in (None,{},{'computer_active':None},{'screen_active':rule},
                        {'computer_active':{**rule,'last_active_entity':'sensor.unlisted'}},
                        {'computer_active':{**rule,'max_idle_seconds':10}},{'computer_active':{**rule,'max_idle_seconds':7200}},
                        {'computer_active':{**rule,'unlocked_values':['Unlocked']*2}},
                        {'computer_active':{**rule,'unlocked_values':['a','b','c','d','e']}},
                        {'computer_active':{**rule,'extra':1}}):
            with self.subTest(derived=derived),self.assertRaisesRegex(ValueError,'derived|computer_active'):
                self.build({**cfg,'derived':derived})
        with self.assertRaisesRegex(ValueError,'reserved'):
            self.build({**cfg,'entities':[*cfg['entities'],'derived.computer_active']})

    def test_derived_binding_needs_a_declared_rule_and_its_own_fact(self):
        cfg=self.derived_config()
        self.build(cfg,ha_bindings={'derived.computer_active':'computer_active'})
        with self.assertRaisesRegex(ValueError,'outside the HA source allowlist'):
            self.build({k:v for k,v in cfg.items() if k!='derived'},ha_bindings={'derived.computer_active':'computer_active'})
        for fact in ('screen_playback','person_present'):
            with self.subTest(fact=fact),self.assertRaisesRegex(ValueError,'Derived HA binding must name its own fact'):
                self.build(cfg,ha_bindings={'derived.computer_active':fact})
        # The derived fact is independent: it never satisfies camera provenance.
        with self.assertRaisesRegex(ValueError,'camera-derived entities'):
            self.build({**cfg,'camera_derived_entities':['derived.computer_active']})

    def test_startup_validation_needs_no_store_credential_or_network(self):
        with patch('live_detector_app.private_json',return_value=self.config()),patch('managed_sources.http.client.HTTPSConnection') as network:
            config,client=ha_source('victoria','den','/private/ha-source.json')
        self.assertEqual(config['source_id'],'ha-den')
        self.assertEqual(client.entities,('binary_sensor.pc',))
        network.assert_not_called()
        with patch('live_detector_app.private_json',side_effect=PermissionError()):
            with self.assertRaises(PermissionError):ha_source('victoria','den','/private/ha-source.json')

    def test_invalid_file_after_startup_records_unavailable_ha_without_failing_the_session(self):
        from managed_sources import unavailable_ha
        from service_runtime import ServiceRuntime
        binding=unavailable_ha(self.store,SimpleNamespace(camera_id='den'))
        runtime=ServiceRuntime(self.store,{},lambda *_:None,lambda *_:None,**binding)
        source=binding['ha_clients']['den']
        for _ in range(3):
            with self.assertRaisesRegex(ValueError,'ha_source_invalid'):
                runtime.ingest_ha(self.session['id'],**source()[0])
        events=[e for e in self.store.list_events(self.session['id']) if e['kind'].startswith('ha.')]
        self.assertEqual([(e['kind'],e['payload']['reason']) for e in events],[('ha.unavailable','ha_source_invalid')])
        runtime.close()


PERSON='person.engineeredlighting'
REVISION='cd'*32


class AreaAndPresenceSourceTests(unittest.TestCase):
    setUp=SourceTests.setUp
    tearDown=SourceTests.tearDown
    build=SourceTests.build
    config=SourceTests.config
    derived_config=SourceTests.derived_config

    def listed(self, **extra):
        # 'entities' is required with 'entity_list': it narrows the listing.
        return {'schema_version':1,'site_id':'victoria','camera_id':'den','source_id':'victoria-den-ha',
                'origin':'https://ha.example.test','credential_file':'/private/victoria.secret',
                'api':'perception_lab_source','entity_list':True,
                'entities':['sensor.a','binary_sensor.pc',OCCUPANCY],**extra}

    def presence(self, **extra):
        return {'source_id':'la-owner-presence','origin':'https://la-ha.example.test',
                'credential_file':'/private/la-presence.secret','entity':PERSON,**extra}

    def test_listed_and_presence_sources_construct_without_credentials_or_network(self):
        from managed_sources import HASourceSet,HASourceSetClient
        with patch('ha_client.http.client.HTTPSConnection') as network,patch('managed_sources.Path') as files:
            binding=self.build(self.listed(presence=self.presence(expected_hostname='LA-HA.example.test')))
        network.assert_not_called();files.assert_not_called()
        source,client=binding['ha_sources']['den'],binding['ha_clients']['den']
        self.assertIs(type(source),HASourceSet)
        self.assertIs(type(client),HASourceSetClient)
        self.assertEqual([(m.source_id,m.role,m.host,m.entities) for m in client.members],
                         [('victoria-den-ha','primary','ha.example.test',None),
                          ('la-owner-presence','presence','la-ha.example.test',(PERSON,))])
        self.assertEqual(client.members[1].client.projection,'home_away_v1')
        self.assertEqual(client.members[1].client.state_path,'/api/perception_lab_source/v1/states/')
        # An exact primary plus presence also uses the per-source set.
        exact={**self.config(),'api':'perception_lab_source','presence':self.presence()}
        self.assertIs(type(self.build(exact)['ha_sources']['den']),HASourceSet)
        # The v0.1 single-source file keeps its exact classes.
        from ha_ingest import HAIngestor
        from ha_client import HAStateClient
        plain=self.build(self.config())
        self.assertEqual((type(plain['ha_sources']['den']),type(plain['ha_clients']['den'])),(HAIngestor,HAStateClient))

    def test_listed_source_is_strictly_provisioned(self):
        with self.assertRaisesRegex(ValueError,'scoped'):
            self.build({**self.listed(),'api':'core'})
        for changed in ({'entity_list':False},{'entity_list':'true'},{'entity_list':1},{'unknown':1}):
            with self.subTest(changed=changed),self.assertRaisesRegex(ValueError,'Registered local HA source'):
                self.build({**self.listed(),**changed})
        base={k:v for k,v in self.config().items() if k!='entities'}
        with self.assertRaisesRegex(ValueError,'Registered local HA source'):
            self.build(base)                                  # no entities and no listing
        with self.assertRaisesRegex(ValueError,'entity allowlist'):
            self.build(self.listed(entities='sensor.a'))
        for entities in ([],[f'sensor.s{i}' for i in range(9)],['sensor.*'],['sensor.a','sensor.a'],[None]):
            with self.subTest(entities=entities),self.assertRaisesRegex(ValueError,'one to eight'):
                self.build(self.listed(entities=entities))
        with self.assertRaisesRegex(ValueError,'Pinned HA origin'):
            self.build(self.listed(expected_hostname='other.example.test'))
        with self.assertRaisesRegex(ValueError,'camera-derived'):
            self.build(self.listed(camera_derived_entities=['binary_sensor.*']))
        with self.assertRaisesRegex(ValueError,'camera-derived'):
            self.build(self.listed(entities=['sensor.a'],camera_derived_entities=[OCCUPANCY]))
        # The narrowing list keeps the exact-mode binding rules.
        with self.assertRaisesRegex(ValueError,'outside the HA source allowlist'):
            self.build(self.listed(entities=['sensor.a']),ha_bindings={'binary_sensor.pc':'computer_active'})
        # Bindings need exact narrowed ids and derived ids need their rule.
        self.build(self.listed(),ha_bindings={'binary_sensor.pc':'computer_active'})
        for entity in ('binary_sensor.*','derived.computer_active'):
            with self.subTest(entity=entity),self.assertRaisesRegex(ValueError,'outside the HA source allowlist'):
                self.build(self.listed(),ha_bindings={entity:'computer_active'})
        with self.assertRaisesRegex(ValueError,'Camera-derived provenance'):
            self.build(self.listed(),ha_bindings={OCCUPANCY:'person_present'})
        self.build(self.listed(camera_derived_entities=[OCCUPANCY]),ha_bindings={OCCUPANCY:'person_present'})
        derived=self.derived_config()
        self.build(self.listed(entities=derived['entities'],derived=derived['derived']),
                   ha_bindings={'derived.computer_active':'computer_active'})
        with self.assertRaisesRegex(ValueError,'computer_active derivation'):  # inputs must be narrowed ids
            self.build(self.listed(derived=derived['derived']))

    def test_listed_source_without_an_entities_narrowing_list_is_refused(self):
        from ha_client import ListedHAStateClient
        bare={k:v for k,v in self.listed().items() if k!='entities'}
        for cfg in (bare,{**bare,'presence':self.presence()},{**bare,'entities':None}):
            with self.subTest(cfg=cfg),self.assertRaisesRegex(ValueError,'entities list|entity allowlist'):
                self.build(cfg)
        with self.assertRaisesRegex(ValueError,'one to eight'):
            ListedHAStateClient('https://ha.example.test',lambda:'x',wanted=None)

    def test_presence_source_is_independent_and_person_only(self):
        for presence in (None,[],'person.a',{},{**self.presence(),'api':'core'},{**self.presence(),'attributes':[]},
                         {k:v for k,v in self.presence().items() if k!='entity'}):
            with self.subTest(presence=presence),self.assertRaisesRegex(ValueError,'Registered presence HA source'):
                self.build(self.listed(presence=presence))
        cases=[({'source_id':'victoria-den-ha'},'own source_id'),({'credential_file':'/private/victoria.secret'},'own credential'),
               ({'credential_file':'la.secret'},'Absolute HA'),({'ca_file':'ca.pem'},'Absolute HA'),
               ({'entity':'device_tracker.phone'},'person entity'),({'entity':'person.*'},'person entity'),
               ({'origin':'http://la-ha.example.test'},'HTTPS origin'),({'expected_hostname':'ha.example.test'},'Pinned HA origin')]
        for changed,message in cases:
            with self.subTest(changed=changed),self.assertRaisesRegex(ValueError,message):
                self.build(self.listed(presence=self.presence(**changed)))
        from store import LabError
        with self.assertRaises(LabError):             # same identifier rule as the primary source_id
            self.build(self.listed(presence=self.presence(source_id='bad id!')))
        with self.assertRaisesRegex(ValueError,'must not also be read'):
            self.build(self.listed(entities=[PERSON,'sensor.a'],presence=self.presence()))

    def test_owner_home_binds_only_the_presence_entity(self):
        cfg=self.listed(presence=self.presence())
        self.build(cfg,ha_bindings={PERSON:'owner_home','binary_sensor.pc':'computer_active'})
        with self.assertRaisesRegex(ValueError,'binds only owner_home'):
            self.build(cfg,ha_bindings={PERSON:'person_present'})
        with self.assertRaisesRegex(ValueError,'owner_home binds only the presence'):
            self.build(cfg,ha_bindings={'binary_sensor.pc':'owner_home'})
        with self.assertRaisesRegex(ValueError,'owner_home binds only the presence'):
            self.build(self.config(),ha_bindings={'binary_sensor.pc':'owner_home'})

    # ---- ledger: one observation stream per source ------------------------

    def runtime(self, cfg, **kw):
        from service_runtime import ServiceRuntime
        binding=self.build(cfg,**kw)
        runtime=ServiceRuntime(self.store,{},lambda *_:None,lambda *_:None,**binding)
        self.addCleanup(runtime.close)
        return runtime,binding['ha_clients']['den']

    def fake(self, client, source_id, values):
        member=next(m for m in client.members if m.source_id==source_id)
        values=iter(values)
        def fetch():
            value=next(values)
            if isinstance(value,Exception): raise value
            return value,None
        member.client=fetch

    def snap(self, key, states, observed_at=99.5, entity_list=None):
        value={'snapshot_id':key,'observed_at':observed_at,'states':states,'acquisition_duration_s':0.2}
        if entity_list is not None:value['entity_list']=entity_list
        return value

    def events(self, kind):
        return [e['payload'] for e in self.store.list_events(self.session['id']) if e['kind']==kind]

    def test_each_source_records_its_own_observations_and_status(self):
        entity_list={'revision':REVISION,'listed':['media_player.den_den','sensor.formd_t1_sessionstate','sun.sun'],
                     'read':['media_player.den_den','sun.sun'],'unlisted':['sensor.not_in_den']}
        runtime,client=self.runtime(self.listed(entities=['media_player.den_den','sun.sun','sensor.not_in_den'],
                                                presence=self.presence()),ha_bindings={PERSON:'owner_home'})
        self.fake(client,'victoria-den-ha',[self.snap(f'v{i}',{'media_player.den_den':'idle','sun.sun':'below_horizon'},
                                                      99.5+i*.1,entity_list) for i in range(2)])
        self.fake(client,'la-owner-presence',[self.snap('p0',{PERSON:'home'},99.6),self.snap('p1',{PERSON:'away'},99.7)])
        for _ in range(2):
            runtime.ingest_ha(self.session['id'],**client()[0])
        observations=self.events('ha.observation')
        by_source={}
        for value in observations:by_source.setdefault(value['quality']['source_id'],[]).append(value)
        self.assertEqual(set(by_source),{'victoria-den-ha','la-owner-presence'})
        self.assertEqual([v['ha'][PERSON] for v in by_source['la-owner-presence']],
                         [{'state':'home','status':'known'},{'state':'away','status':'known'}])
        victoria=by_source['victoria-den-ha'][0]
        self.assertEqual(victoria['ha']['sensor.not_in_den'],{'state':None,'status':'unknown'})
        self.assertNotIn(PERSON,victoria['ha'])
        self.assertEqual(victoria['snapshot_key'],'ha:victoria-den-ha:v0')
        self.assertEqual(victoria['observed_at'],99.5)          # acquisition time, not last_changed
        status=self.events('ha.source')
        self.assertEqual(len(status),2)                           # once per source, not per poll
        self.assertEqual({s['source_id']:s['revision'] for s in status},{'victoria-den-ha':REVISION,'la-owner-presence':None})
        primary=next(s for s in status if s['role']=='primary')
        self.assertEqual((primary['entities'],primary['origin_host'],primary['entity_list']),
                         ('listed','ha.example.test',{k:entity_list[k] for k in ('listed','read','unlisted')}))
        presence=next(s for s in status if s['role']=='presence')
        self.assertEqual((presence['fact'],presence['projection'],presence['origin_host']),
                         ('owner_home','home_away_v1','la-ha.example.test'))

    def test_one_failing_source_never_blocks_the_other(self):
        runtime,client=self.runtime(self.listed(presence=self.presence()))
        entity_list={'revision':REVISION,'listed':['sensor.a'],'read':['sensor.a'],'unlisted':[]}
        self.fake(client,'victoria-den-ha',[self.snap('v0',{'sensor.a':'on'},entity_list=entity_list),
                                            ValueError('ha_snapshot_unavailable')])
        self.fake(client,'la-owner-presence',[OSError('la unreachable'),self.snap('p1',{PERSON:'home'})])
        first,_=client()
        self.assertEqual(first['sources']['la-owner-presence'],{'failure':'ha_snapshot_unavailable'})
        runtime.ingest_ha(self.session['id'],**first)
        runtime.ingest_ha(self.session['id'],**client()[0])
        self.assertEqual(sorted(v['quality']['source_id'] for v in self.events('ha.observation')),
                         ['la-owner-presence','victoria-den-ha'])
        self.assertEqual(self.events('ha.unavailable'),[])        # transient failures stay pump counters

    def test_a_refused_listing_is_recorded_once_and_all_failed_raises(self):
        runtime,client=self.runtime(self.listed())
        self.fake(client,'victoria-den-ha',[ValueError('ha_entity_list_over_cap')]*3)
        for _ in range(3):
            with self.assertRaisesRegex(ValueError,'ha_snapshot_unavailable'):
                runtime.ingest_ha(self.session['id'],**client()[0])
        self.assertEqual([(e['reason'],e['source_id']) for e in self.events('ha.unavailable')],
                         [('ha_entity_list_over_cap','victoria-den-ha')])
        self.assertEqual(self.events('ha.observation'),[])

    def test_snapshot_outside_the_listing_or_sources_is_rejected(self):
        runtime,client=self.runtime(self.listed(presence=self.presence()))
        with self.assertRaisesRegex(ValueError,'registered HA sources'):
            runtime.ingest_ha(self.session['id'],sources={'someone-else':self.snap('x',{})})
        entity_list={'revision':REVISION,'listed':['sensor.a'],'read':['sensor.a'],'unlisted':[]}
        bad=[self.snap('v0',{'sensor.b':'on'},entity_list=entity_list),        # state outside the listing
             self.snap('v1',{'sensor.a':'on'}),                                # listed source without its listing
             self.snap('v2',{'sensor.a':'on'},observed_at=90,entity_list=entity_list)]  # stale acquisition
        for value in bad:
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'ha_snapshot_unavailable'):
                runtime.ingest_ha(self.session['id'],sources={'victoria-den-ha':value})
        with self.assertRaisesRegex(ValueError,'ha_snapshot_unavailable'):
            runtime.ingest_ha(self.session['id'],sources={'la-owner-presence':self.snap('p',{PERSON:'home'},
                                                                                      entity_list=entity_list)})
        self.assertEqual(self.events('ha.observation'),[])

    def test_parallel_poll_returns_every_source_with_its_own_acquisition(self):
        import threading
        runtime,client=self.runtime(self.listed(presence=self.presence()))
        seen=[]
        entity_list={'revision':REVISION,'listed':['sensor.a'],'read':['sensor.a'],'unlisted':[]}
        member={m.source_id:m for m in client.members}
        member['victoria-den-ha'].client=lambda:(seen.append(threading.current_thread().name) or
                                                 self.snap('v',{'sensor.a':'on'},99.4,entity_list),None)
        member['la-owner-presence'].client=lambda:(seen.append(threading.current_thread().name) or
                                                   self.snap('p',{PERSON:'home'},99.6),None)
        value,header=client()
        self.assertIsNone(header)
        self.assertEqual({k:v['observed_at'] for k,v in value['sources'].items()},
                         {'victoria-den-ha':99.4,'la-owner-presence':99.6})
        self.assertEqual(sorted(seen),sorted(['MainThread','lab-ha-source']))


if __name__ == '__main__': unittest.main()

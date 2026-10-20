import unittest
from ha_ingest import HAIngestor
from live_inputs import live_records
from persisted_evidence import observation_record
import test_paired_runtime as fixtures
from dataclasses import replace


class HAIngestTests(unittest.TestCase):
    def setUp(self):
        fixtures.PairedRuntimeTests.setUp(self)
        self.store.settings = replace(self.store.settings, cameras=({'id':'den'},))
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def source(self):
        return HAIngestor(self.store, site_id='victoria', camera_id='den',
                          source_id='local-ha', entities=['media_player.tv','light.desk'])

    def test_durable_shared_evidence_and_replay_projection(self):
        source = self.source()
        kwargs = dict(snapshot_id='one', observed_at=99, states={'media_player.tv':'playing'})
        first = source.ingest(self.session['id'], **kwargs)
        self.assertEqual(source.ingest(self.session['id'], **kwargs), first)
        records = live_records(self.store, self.session, 1, 100)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]['ha'], records[1]['ha'])
        self.assertEqual(records[0]['ha']['light.desk']['status'], 'unknown')
        event = next(e for e in self.store.list_events(self.session['id']) if e['id'] == first)
        saved = observation_record(first, event['kind'], event['payload'], self.session)
        self.assertEqual(saved['ha'], records[0]['ha'])
        self.assertEqual(saved['evidence_roots'], records[0]['evidence_roots'])
        self.assertEqual(live_records(self.store, self.session, 1, 103), [])

    def test_reject_stale_changed_out_of_order_and_unregistered_input(self):
        source = self.source(); sid = self.session['id']
        source.ingest(sid, snapshot_id='one', observed_at=99, states={})
        for args in [dict(snapshot_id='one', observed_at=99, states={'light.desk':'on'}),
                     dict(snapshot_id='older', observed_at=98, states={}),
                     dict(snapshot_id='stale', observed_at=90, states={}),
                     dict(snapshot_id='wrong', observed_at=100, states={'person.owner':'home'}),
                     dict(snapshot_id='attributes', observed_at=100, states={'light.desk':{'secret':'value'}})]:
            with self.assertRaises(ValueError): source.ingest(sid, **args)
        self.store.transition(sid,'stopping')
        with self.assertRaises(ValueError):
            source.ingest(sid, snapshot_id='late', observed_at=100, states={})

    def test_runtime_routes_only_provisioned_sources_and_stops_at_shutdown(self):
        from service_runtime import ServiceRuntime
        runtime = ServiceRuntime(self.store, {}, lambda *_: None, lambda *_: None,
                                 ha_sources={'den':self.source()})
        snapshot = dict(snapshot_id='routed', observed_at=100, states={'light.desk':'on'})
        self.assertIsInstance(runtime.ingest_ha(self.session['id'], **snapshot), int)
        runtime.close()
        with self.assertRaises(ValueError): runtime.ingest_ha(self.session['id'], **snapshot)

    def test_latest_only_arm_keeps_visual_and_only_ha_known_by_then(self):
        from reasoning import causal_evidence
        sid=self.session['id']
        for arm in ('a','b'):
            self.store.append_event(sid,'perception.output',{'site_id':'victoria','camera_id':'den','arm':arm,
                'generation':1,'status':'completed','observed_at':98.0,'available_at':98.2,'valid_until':101,
                'result':{'objects':[{'label':'book'}]}})
        source=self.source()
        self.store.clock=lambda:97.8
        source.ingest(sid,snapshot_id='before',observed_at=97.5,states={'media_player.tv':'playing'})
        # Started before the frame, but its sequential reads finished after it.
        self.store.clock=lambda:99.5
        source.ingest(sid,snapshot_id='straddle',observed_at=97.9,states={'media_player.tv':'idle'},acquisition_duration_s=1.5)
        self.store.clock=lambda:100.0
        source.ingest(sid,snapshot_id='after',observed_at=99.8,states={'media_player.tv':'paused'})
        records=live_records(self.store,self.session,1,100,retain_history=True)
        params=dict(site_id='victoria',session_id=sid,generation=1,cutoff=100,window_seconds=0,retain_history=True)
        for arm in ('a','b'):
            evidence=causal_evidence(records,arm=arm,**params)
            self.assertEqual([(r['source_kind'],r['observed_at'],r['available_at']) for r in evidence],
                             [('ha.observation',97.5,97.8),('perception.output',98.0,98.2)])
            self.assertEqual(evidence[0]['evidence_roots'],['ha:local-ha:before'])
            # A 10 s arm still sees every snapshot in its window.
            self.assertEqual(len(causal_evidence(records,arm=arm,**{**params,'window_seconds':10})),4)
        # Only a snapshot persisted after the frame: the arm keeps the frame alone.
        late=[r for r in records if r['evidence_roots']!=['ha:local-ha:before']]
        self.assertEqual([r['source_kind'] for r in causal_evidence(late,arm='a',**params)],['perception.output'])
        ha_only=[r for r in records if r['source_kind']=='ha.observation']
        self.assertEqual(causal_evidence(ha_only,arm='a',**params),[])

    def test_camera_occupancy_provenance_survives_both_arms_and_cloud_projection(self):
        from jev_evidence import opaque, project
        from jev_semantics import semantic_state
        entity='binary_sensor.den_oak_person_occupancy'
        source=HAIngestor(self.store,site_id='victoria',camera_id='den',source_id='frigate-ha',
            entities=[entity],camera_derived_entities=[entity])
        source.ingest(self.session['id'],snapshot_id='occupancy',observed_at=99,states={entity:'on'})
        records=live_records(self.store,self.session,1,100)
        projected=project(records,ha_bindings={entity:'person_present'})
        self.assertEqual(len(projected),2)
        for record in projected:
            self.assertEqual(record['quality']['camera_derived_ha_facts'],['person_present'])
            self.assertEqual(record['quality']['correlated_with'],{'relation':'same_camera_visual','camera_id':'den'})
        self.assertEqual(project(projected,ha_bindings={entity:'person_present'}),projected)
        semantic=semantic_state(projected,100)
        self.assertEqual(semantic['version'],'den-semantics-7')
        self.assertIsNone(semantic['latest_visual_evidence_id'])
        self.assertEqual(semantic['observations'][0]['camera_derived_device_facts'],['person_present'])
        self.assertEqual(semantic['observations'][0]['correlated_with'],{'relation':'same_camera_visual','camera_id':'den'})
        self.assertIn('correlated_with',semantic['limitations'])
        # An independent HA source carries no correlation marker.
        independent=HAIngestor(self.store,site_id='victoria',camera_id='den',source_id='other-ha',entities=[entity])
        independent.ingest(self.session['id'],snapshot_id='independent',observed_at=99.5,states={entity:'on'})
        other=[r for r in project(live_records(self.store,self.session,1,100),ha_bindings={entity:'person_present'})
               if r['evidence_roots']==[opaque('root','ha:other-ha:independent')]]
        self.assertEqual(len(other),2)
        self.assertTrue(all('correlated_with' not in r['quality'] and 'camera_derived_ha_facts' not in r['quality'] for r in other))
        with self.assertRaises(ValueError):
            HAIngestor(self.store,site_id='victoria',camera_id='den',source_id='frigate-ha',
                entities=[entity],camera_derived_entities=['person.unregistered'])


SESSION='sensor.formd_t1_sessionstate'
LAST_ACTIVE='sensor.formd_t1_lastactive'
DERIVED={'computer_active':{'session_entity':SESSION,'unlocked_values':['Unlocked'],
                            'last_active_entity':LAST_ACTIVE,'max_idle_seconds':300}}


def iso(epoch, suffix='+00:00'):
    from datetime import datetime, timedelta, timezone
    text=(datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(seconds=epoch)).replace(tzinfo=None).isoformat()
    return text+suffix


class DerivedComputerActiveTests(unittest.TestCase):
    setUp=HAIngestTests.setUp
    tearDown=HAIngestTests.tearDown

    def source(self, derived=DERIVED, **kw):
        return HAIngestor(self.store,site_id='victoria',camera_id='den',source_id='pc-ha',
                          entities=[SESSION,LAST_ACTIVE],derived=derived,**kw)

    def derive(self, states, observed_at=99):
        self.snapshots=getattr(self,'snapshots',0)+1
        event_id=self.source().ingest(self.session['id'],snapshot_id=f'pc{self.snapshots}',observed_at=observed_at,states=states)
        return next(e for e in self.store.list_events(self.session['id']) if e['id']==event_id)['payload']

    def test_on_off_unknown_idle_bound_and_clock_skew(self):
        cases=[({SESSION:'Unlocked',LAST_ACTIVE:iso(89)},'on'),
               ({SESSION:'Unlocked',LAST_ACTIVE:iso(99-300)},'on'),        # exactly at the idle bound
               ({SESSION:'Unlocked',LAST_ACTIVE:iso(99-301)},'off'),       # idle bound exceeded
               ({SESSION:'Unlocked',LAST_ACTIVE:iso(104)},'on'),           # 5 s skew is tolerated
               ({SESSION:'Unlocked',LAST_ACTIVE:iso(104.5)},None),         # future beyond skew
               ({SESSION:'Locked',LAST_ACTIVE:iso(105)},None),
               ({SESSION:'Locked',LAST_ACTIVE:iso(89)},'off'),
               ({SESSION:'LoggedOff',LAST_ACTIVE:iso(89)},'off'),
               ({SESSION:'unlocked',LAST_ACTIVE:iso(89)},'off'),           # exact configured values only
               ({SESSION:'unavailable',LAST_ACTIVE:iso(89)},None),
               ({SESSION:'unknown',LAST_ACTIVE:iso(89)},None),
               ({LAST_ACTIVE:iso(89)},None),                               # session never read
               ({SESSION:'Unlocked'},None),
               ({SESSION:'Unlocked',LAST_ACTIVE:'unavailable'},None),
               ({SESSION:'Locked',LAST_ACTIVE:'unknown'},None),
               ({SESSION:'Unlocked',LAST_ACTIVE:'yesterday'},None),
               ({SESSION:'Unlocked',LAST_ACTIVE:iso(89,'')},None),         # naive timestamp
               ({SESSION:'Unlocked',LAST_ACTIVE:'1970-13-01T00:00:00+00:00'},None),
               ({SESSION:'Unlocked',LAST_ACTIVE:iso(89,'Z')},'on'),
               ({SESSION:'Unlocked',LAST_ACTIVE:'1970-01-01T02:01:29.250000+02:00'},'on')]
        for states,expected in cases:
            with self.subTest(states=states):
                fact=self.derive(states)['ha']['derived.computer_active']
                self.assertEqual((fact['state'],fact['status']),(expected,'unknown' if expected is None else 'known'))
                self.assertEqual(fact['provenance'],{'inputs':[SESSION,LAST_ACTIVE],'rule':'computer_active_v1','max_idle_seconds':300})
                # Independent of the camera: no visual correlation marker.
                self.assertNotIn('origin',fact)

    def test_raw_states_stay_local_and_freshness_is_not_extended(self):
        stamp=iso(80)
        payload=self.derive({SESSION:'Unlocked',LAST_ACTIVE:stamp},observed_at=98)
        self.assertEqual(payload['ha'][SESSION],{'state':'Unlocked','status':'known'})
        self.assertEqual(payload['ha'][LAST_ACTIVE],{'state':stamp,'status':'known'})
        self.assertEqual(payload['ha']['derived.computer_active']['state'],'on')
        self.assertEqual(payload['valid_until'],98+3)
        self.assertEqual(set(payload['ha']),{SESSION,LAST_ACTIVE,'derived.computer_active'})
        # The derived id is computed, never supplied by the adapter.
        with self.assertRaisesRegex(ValueError,'entity allowlist'):
            self.source().ingest(self.session['id'],snapshot_id='forged',observed_at=99,
                                 states={SESSION:'Unlocked','derived.computer_active':'on'})
        # Without a declared rule nothing is derived.
        plain=HAIngestor(self.store,site_id='victoria',camera_id='den',source_id='plain-ha',entities=[SESSION,LAST_ACTIVE])
        event=plain.ingest(self.session['id'],snapshot_id='plain',observed_at=99,states={SESSION:'Unlocked',LAST_ACTIVE:stamp})
        self.assertNotIn('derived.computer_active',next(e for e in self.store.list_events(self.session['id']) if e['id']==event)['payload']['ha'])

    def test_invalid_rules_and_reserved_domain_rejected(self):
        rule=DERIVED['computer_active']
        for derived in ({},[],'computer_active',{'screen_active':rule},
                        {'computer_active':{**rule,'max_idle_seconds':29}},{'computer_active':{**rule,'max_idle_seconds':3601}},
                        {'computer_active':{**rule,'max_idle_seconds':True}},{'computer_active':{**rule,'max_idle_seconds':'300'}},
                        {'computer_active':{**rule,'max_idle_seconds':float('nan')}},
                        {'computer_active':{**rule,'unlocked_values':[]}},{'computer_active':{**rule,'unlocked_values':'Unlocked'}},
                        {'computer_active':{**rule,'unlocked_values':['a','b','c','d','e']}},
                        {'computer_active':{**rule,'unlocked_values':['Unlocked','Unlocked']}},
                        {'computer_active':{**rule,'unlocked_values':['x'*33]}},{'computer_active':{**rule,'unlocked_values':['']}},
                        {'computer_active':{**rule,'unlocked_values':['unavailable']}},{'computer_active':{**rule,'unlocked_values':[['Unlocked']]}},
                        {'computer_active':{**rule,'session_entity':'sensor.other'}},{'computer_active':{**rule,'last_active_entity':SESSION}},
                        {'computer_active':{**rule,'session_entity':[SESSION]}},
                        {'computer_active':{k:v for k,v in rule.items() if k!='max_idle_seconds'}},
                        {'computer_active':{**rule,'attributes':['friendly_name']}}):
            with self.subTest(derived=derived),self.assertRaises(ValueError):
                self.source(derived)
        with self.assertRaisesRegex(ValueError,'reserved'):
            HAIngestor(self.store,site_id='victoria',camera_id='den',source_id='pc-ha',entities=[SESSION,'derived.computer_active'])
        self.assertEqual(self.source({'computer_active':{**rule,'max_idle_seconds':30,'unlocked_values':['Unlocked','Active']}})
                         .derived['derived.computer_active']['unlocked_values'],('Unlocked','Active'))

    def test_projection_sends_only_the_bound_fact_without_raw_inputs(self):
        import json
        from jev_evidence import project
        from jev_semantics import semantic_state
        stamp=iso(97.5)
        source=self.source()
        source.ingest(self.session['id'],snapshot_id='pc-active',observed_at=99,states={SESSION:'Unlocked',LAST_ACTIVE:stamp})
        bindings={'derived.computer_active':'computer_active'}
        records=live_records(self.store,self.session,1,100)
        projected=project(records,ha_bindings=bindings)
        self.assertEqual(len(projected),2)
        for record in projected:
            self.assertEqual(record['ha'],{'computer_active':'on'})
            self.assertNotIn('camera_derived_ha_facts',record['quality'])
            self.assertNotIn('correlated_with',record['quality'])
            self.assertEqual(record['valid_until'],102)
            text=json.dumps(record)
            for private in ('formd','sessionstate','lastactive','derived.',stamp,'1970-','Unlocked','computer_active_v1','max_idle','provenance'):
                self.assertNotIn(private,text)
        self.assertEqual(project(projected,ha_bindings=bindings),projected)
        self.assertEqual(semantic_state(projected,100)['observations'][0]['camera_derived_device_facts'],[])
        # A derived id binds only its own registered fact.
        for invalid in ({'derived.computer_active':'screen_playback'},{'derived.computer_active':'person_present'},
                        {'derived.screen_active':'computer_active'}):
            with self.subTest(invalid=invalid),self.assertRaisesRegex(ValueError,'Derived HA binding'):
                project(records,ha_bindings=invalid)


if __name__ == '__main__': unittest.main()

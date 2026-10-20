import unittest
from copy import deepcopy
import test_registered_depth as depth_fixtures
import test_paired_runtime as paired_fixtures
import test_room_context as map_fixtures
from service_runtime import ServiceRuntime
from spatial_consumer import SpatialConsumer
from controller import Controller
from live_inputs import live_records


class SpatialConsumerTests(unittest.TestCase):
    setUp = paired_fixtures.PairedRuntimeTests.setUp
    tearDown = paired_fixtures.PairedRuntimeTests.tearDown
    source = depth_fixtures.RegisteredDepthTests.source

    def frame(self, **changes):
        return paired_fixtures.PairedRuntimeTests.frame(self, **{'observed_at':100, 'available_at':100, **changes})

    def rgb_media(self, frame):
        return self.store.add_media(self.session['id'], b'synthetic-rgb', 'image/jpeg', {
            'site_id':'victoria','camera_id':'den','capture_epoch':frame['capture_epoch'],'frame_id':frame['frame_id'],
            'stream':'rgb','format':'jpeg','width':640,'height':360,'observed_at':frame['observed_at'],
            'clock_mapping_status':'verified','clock_mapping_id':'causal:fixture'})

    def setup_consumer(self, policy=None, *, latency=.1, source_changes=None, **config):
        frame, cal, _ = self.source()
        room = map_fixtures.RoomContextTests(); room.setUp()
        registration = {**room.registration, 'calibration_version':cal['id'], 'mount_revision':cal['mount_revision'],
                        'floor_homography':deepcopy(room.homography)}
        consumer = SpatialConsumer(self.store, cal, registration, room.map, association_revision='assoc-test', **config)
        runtime = ServiceRuntime(self.store, {}, None, None, spatial_sources={'den':consumer})
        sid = self.session['id']
        self.store.db.execute('INSERT INTO lab_experiment_state VALUES(?,?,?)', (sid,'spatial',1))
        self.store.db.commit()
        self.store.append_event(sid,'experiment.activated', {'generation':1,'effective_at':100,
            'spatial_policy':policy or {'a':{'localization':'stereo','room_relations':True},
                                        'b':{'localization':'none','room_relations':False}}})
        observed = frame['observed_at']
        self.output = {**frame, 'status':'completed', 'arm':'a', 'generation':1, 'result':{'objects':[]},
                       'available_at':observed+latency, 'valid_until':observed+3, **(source_changes or {})}
        eid = self.store.append_event(sid,'perception.output', self.output)
        association = dict(source_event_id=eid, depth_frame=frame, roi=[0,0,20,20],
            roi_frame='rectified_left', visible=True, track_id='track1', association_revision='assoc-test')
        self.store.clock = lambda:observed+latency
        return runtime, association

    def floor_output(self, arm='b', **changes):
        sid = self.session['id']
        frame = self.frame(frame_id='rgb-frame-2', observed_at=self.store.clock()-.2)
        media = self.rgb_media(frame)
        eid = self.store.append_event(sid,'perception.output', {**frame, 'status':'completed', 'arm':arm, 'generation':1,
            'result':{'objects':[]}, 'media_ids':[media['id']], 'available_at':self.store.clock(),
            'valid_until':frame['observed_at']+3})
        return {'source_event_id':eid, 'association_revision':'assoc-test', 'localization':'floor_projection',
                'track_id':'floor-track', 'contact_pixel':[320, 300], 'contact_kind':'ankle_midpoint',
                'contact_visible':True, 'pixel_uncertainty_px':2, **changes}

    def test_registered_bytes_reach_replay_and_reasoning_with_original_roots(self):
        runtime, association = self.setup_consumer()
        sid = self.session['id']
        room = runtime.ingest_spatial(sid,[association],generation=1)
        self.assertEqual(room['people'][0]['pos'], [0,2,0])
        replay = Controller(self.store).detail(sid,at_ms=100)['room']
        self.assertEqual(replay['people'][0]['track_id'], 'track1')
        records = live_records(self.store,self.store.get_session(sid),1,100.1)
        spatial = next(r for r in records if r.get('quality',{}).get('method')=='stereo_surface')
        self.assertEqual(spatial['evidence_roots'],[str(association['source_event_id'])])
        self.assertFalse(spatial['quality']['independent_confirmation'])
        self.assertIsNone(room['people'][0]['identity'])

    def test_specialist_latency_of_one_and_a_half_seconds_still_measures(self):
        runtime, association = self.setup_consumer(latency=1.5)
        sid = self.session['id']
        room = runtime.ingest_spatial(sid,[association],generation=1)
        self.assertEqual(len(room['people']), 1)
        person = room['people'][0]
        self.assertEqual((person['observed_at'], person['available_at'], person['valid_until']), (100, 101.5, 102.9))
        self.assertEqual(Controller(self.store).detail(sid,at_ms=2500)['room']['people'][0]['track_id'], 'track1')
        self.assertEqual(Controller(self.store).detail(sid,at_ms=3100)['room']['people'], [])
        sampling = [e['payload'] for e in self.store.list_events(sid) if e['kind']=='room.sampling'][-1]
        self.assertEqual((sampling['max_age_s'], sampling['max_skew_s']), (.5, .04))

    def test_source_expiry_is_never_extended(self):
        runtime, association = self.setup_consumer(latency=1.5, source_changes={'valid_until':101.6})
        room = runtime.ingest_spatial(self.session['id'],[association],generation=1)
        self.assertEqual(room['people'][0]['valid_until'], 101.6)

    def test_abstention_clears_previous_position_and_is_recorded(self):
        runtime, association = self.setup_consumer(); sid = self.session['id']
        runtime.ingest_spatial(sid,[association],generation=1)
        room = runtime.ingest_spatial(sid,[{**association,'visible':False}],generation=1)
        self.assertEqual(room['people'], [])
        sampling = [e['payload'] for e in self.store.list_events(sid) if e['kind']=='room.sampling'][-1]
        self.assertEqual(sampling['abstentions'][0]['reason'],'occluded_surface')
        self.assertEqual(Controller(self.store).detail(sid,at_ms=100)['room']['people'],[])

    def test_upstream_association_abstentions_are_recorded_not_dropped(self):
        runtime, association = self.setup_consumer(); sid = self.session['id']
        abstained = {'source_event_id':association['source_event_id'], 'association_revision':'assoc-test',
                     'abstention':'no_projected_support'}
        room = runtime.ingest_spatial(sid,[association, abstained],generation=1)
        self.assertEqual(len(room['people']), 1)
        sampling = [e['payload'] for e in self.store.list_events(sid) if e['kind']=='room.sampling'][-1]
        self.assertEqual([a['reason'] for a in sampling['abstentions']], ['no_projected_support'])
        # Regions beyond the per-output limit are one counted abstention.
        limited = {**abstained, 'abstention':'person_region_limit', 'count':120}
        runtime.ingest_spatial(sid,[association, limited],generation=1)
        sampling = [e['payload'] for e in self.store.list_events(sid) if e['kind']=='room.sampling'][-1]
        self.assertEqual([(a['reason'], a.get('count')) for a in sampling['abstentions']], [('person_region_limit', 120)])
        for bad in ({**abstained,'abstention':'invented'}, {**limited,'count':0}, {**limited,'count':True},
                    {**association,'count':3}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                runtime.ingest_spatial(sid,[bad],generation=1)

    def test_rgb_floor_contact_arm_publishes_without_depth(self):
        runtime, association = self.setup_consumer({'a':{'localization':'stereo','room_relations':True},
                                                    'b':{'localization':'floor_projection','room_relations':True}})
        sid = self.session['id']
        floor = self.floor_output()
        room = runtime.ingest_spatial(sid,[association, floor],generation=1)
        methods = {p['arm']:p['method'] for p in room['people']}
        self.assertEqual(methods, {'A':'stereo_surface','B':'rgb_floor_contact'})
        rgb = next(p for p in room['people'] if p['arm']=='B')
        self.assertEqual(rgb['pos'][2], 0.0)
        self.assertAlmostEqual(rgb['pos'][0], 3.2)
        observations = [e['payload'] for e in self.store.list_events(sid) if e['kind']=='room.observation']
        quality = {o['arm']:o['result']['quality'] for o in observations}
        self.assertEqual(quality['b']['method'], 'rgb_floor_contact')
        self.assertFalse(quality['b']['surface_only'])
        self.assertEqual(quality['b']['homography_version'], 'fh1')
        self.assertEqual(quality['a']['method'], 'stereo_surface')
        records = live_records(self.store,self.store.get_session(sid),1,self.store.clock())
        self.assertEqual({r['arm'] for r in records if r.get('source_kind')=='room.observation'}, {'a','b'})

    def test_each_arm_replaces_only_its_own_positions(self):
        runtime, association = self.setup_consumer({'a':{'localization':'stereo','room_relations':True},
                                                    'b':{'localization':'floor_projection','room_relations':True}})
        sid = self.session['id']
        first = runtime.ingest_spatial(sid,[association],generation=1)['people'][0]
        room = runtime.ingest_spatial(sid,[self.floor_output()],generation=1)
        self.assertEqual(sorted(p['arm'] for p in room['people']), ['A','B'])
        carried = next(p for p in room['people'] if p['arm']=='A')
        self.assertEqual(carried, first)
        replay = Controller(self.store).detail(sid,at_ms=100)['room']
        self.assertEqual(sorted(p['arm'] for p in replay['people']), ['A','B'])
        # An abstention for arm a clears only arm a.
        room = runtime.ingest_spatial(sid,[{**association,'visible':False}],generation=1)
        self.assertEqual([p['arm'] for p in room['people']], ['B'])
        sampling = [e['payload'] for e in self.store.list_events(sid) if e['kind']=='room.sampling'][-1]
        self.assertEqual(sampling['arms'], ['a'])

    def test_hidden_or_misconfigured_floor_contact_does_not_become_a_position(self):
        runtime, association = self.setup_consumer({'a':{'localization':'floor_projection','room_relations':False},
                                                    'b':{'localization':'none','room_relations':False}})
        sid = self.session['id']
        room = runtime.ingest_spatial(sid,[self.floor_output('a',contact_visible=False)],generation=1)
        self.assertEqual(room['people'], [])
        sampling = [e['payload'] for e in self.store.list_events(sid) if e['kind']=='room.sampling'][-1]
        self.assertEqual(sampling['abstentions'][0]['reason'], 'floor_contact_not_visible')
        # A box bottom may be the edge of the desk in front of a seated person:
        # even marked visible it never becomes a floor position.
        room = runtime.ingest_spatial(sid,[self.floor_output('a',contact_kind='box_bottom_centre')],generation=1)
        self.assertEqual(room['people'], [])
        sampling = [e['payload'] for e in self.store.list_events(sid) if e['kind']=='room.sampling'][-1]
        self.assertEqual(sampling['abstentions'][0]['reason'], 'floor_contact_not_visible')
        for change in ({'contact_pixel':[900, 10]}, {'contact_kind':'bbox_centre'}, {'pixel_uncertainty_px':None},
                       {'localization':'guess'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                runtime.ingest_spatial(sid,[self.floor_output('a',**change)],generation=1)

    def test_wrong_generation_source_epoch_revision_and_rgb_roi_rejected(self):
        runtime, association = self.setup_consumer(); sid = self.session['id']
        for change in ({'source_event_id':99999},{'association_revision':'other'}, {'roi_frame':'rgb'},
                       {'depth_frame':{**association['depth_frame'],'capture_epoch':'other'}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                runtime.ingest_spatial(sid,[{**association,**change}],generation=1)
        with self.assertRaises(ValueError):runtime.ingest_spatial(sid,[association],generation=2)
        self.assertFalse(any(e['kind']=='room.output' for e in self.store.list_events(sid)))

    def test_freshness_configuration_can_only_tighten(self):
        for config in ({'max_skew_s':.2}, {'max_age_s':1.5}, {'max_age_s':float('nan')}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                self.setup_consumer(**config)


if __name__=='__main__':unittest.main()

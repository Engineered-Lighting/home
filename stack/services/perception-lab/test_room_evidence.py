import unittest
import test_paired_runtime as fixtures
import test_room_context as maps
from controller import Controller
from service_runtime import ServiceRuntime
from room_evidence import publish_room


class RoomEvidenceTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def setup_map(self,policy=None):
        room=maps.RoomContextTests();room.setUp()
        sid=self.session['id']
        ServiceRuntime(self.store,{},None,None)
        self.store.db.execute('INSERT INTO lab_experiment_state VALUES(?,?,?)',(sid,'spatial',1))
        self.store.db.commit()
        self.store.append_event(sid,'experiment.activated',{'site_id':'victoria','camera_id':'den',
            'generation':1,'effective_at':100,'experiment_id':'spatial',
            'spatial_policy':policy or {'a':{'localization':'stereo','room_relations':True},'b':{'localization':'none','room_relations':False}}})
        media=self.store.add_media(sid,b'synthetic registered depth','application/octet-stream',{
            'site_id':'victoria','camera_id':'den','capture_epoch':'capture1','frame_id':'f1',
            'format':'depth_u16_le','units':'millimetres','invalid_value':0,
            'alignment_frame':room.registration['source_frame'],'calibration_id':'camera-session-digest',
            'calibration_content_id':room.point['calibration_version'],
            'derivation':'depth-clock-v1','source_media_id':'bundle-fixture',
            'geometric_validity':'validated','depth_validation_receipt':'synthetic-only',
            'clock_mapping_status':'verified','clock_mapping_id':'synthetic-only','observed_at':room.point['observed_at']},
            'depth-mapped-v1:bundle-fixture')
        point={**room.point,'session_id':sid,'generation':1,'frame_id':'f1','capture_epoch':'capture1','media_ids':[media['id']],'evidence_roots':['original-detection']}
        return room,point

    def floor_point(self,room,**changes):
        sid=self.session['id']
        media=self.store.add_media(sid,b'synthetic rgb','image/jpeg',{'site_id':'victoria','camera_id':'den',
            'capture_epoch':'capture1','frame_id':'rgb1','stream':'rgb','format':'jpeg','width':640,'height':360,
            'observed_at':100,'clock_mapping_status':'verified','clock_mapping_id':'causal:fixture'})
        return {**room.floor_point,'session_id':sid,'generation':1,'frame_id':'rgb1','capture_epoch':'capture1',
                'media_ids':[media['id']],'evidence_roots':['original-rgb-detection'],**changes}

    def test_published_context_reaches_replay_but_not_before_publication_or_after_expiry(self):
        room,point=self.setup_map();sid=self.session['id']
        self.store.clock=lambda:100.5
        publish_room(self.store,sid,[point],room.registration,room.map,generation=1,as_of=100)
        controller=Controller(self.store)
        self.assertIsNone(controller.detail(sid,at_ms=250)['room'])
        replay=controller.detail(sid,at_ms=500)['room']
        self.assertEqual(replay['people'][0]['track_id'],'t1')
        self.assertEqual(replay['map_version'],'7')
        from live_inputs import live_records
        from persisted_evidence import observation_record
        records=live_records(self.store,self.store.get_session(sid),1,100.5)
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['arm'],'a')
        self.assertEqual(records[0]['evidence_roots'],['original-detection'])
        self.assertFalse(records[0]['quality']['independent_confirmation'])
        self.assertEqual(records[0]['relations'][0]['zone_id'],'desk')
        event=next(e for e in self.store.list_events(sid) if e['kind']=='room.observation')
        self.assertEqual(records[0],observation_record(event['id'],event['kind'],event['payload'],self.store.get_session(sid)))
        self.assertEqual(controller.detail(sid,at_ms=2000)['room']['people'],[])
        self.store.append_event(sid,'experiment.activated',{'generation':2,'effective_at':100.75})
        self.assertIsNone(controller.detail(sid,at_ms=800)['room'])

    def test_missing_frame_wrong_generation_and_wrong_site_do_not_publish(self):
        room,point=self.setup_map();sid=self.session['id']
        for altered in ({**point,'frame_id':None},{**point,'generation':2},{**point,'site_id':'echo'}):
            with self.assertRaises(ValueError):
                publish_room(self.store,sid,[altered],room.registration,room.map,generation=1,as_of=100)
        self.assertFalse(any(e['kind']=='room.output' for e in self.store.list_events(sid)))

    def test_floor_baseline_cannot_receive_stereo_and_map_withholding_removes_zones(self):
        room,point=self.setup_map({'a':{'localization':'stereo','room_relations':False},
                                  'b':{'localization':'floor_projection','room_relations':True}})
        sid=self.session['id']
        publish_room(self.store,sid,[point,{**point,'arm':'b'}],room.registration,room.map,generation=1,as_of=100)
        events=[e['payload'] for e in self.store.list_events(sid) if e['kind']=='room.observation']
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['arm'],'a')
        self.assertEqual(events[0]['result']['relations'],[])
        self.assertEqual(len(events[0]['result']['tracks']),1)

    def test_unvalidated_depth_or_wrong_source_cannot_become_room_measurement(self):
        from store import encode
        room,point=self.setup_map();sid=self.session['id'];mid=point['media_ids'][0]
        original=self.store.get_media(sid,mid)['metadata']
        for change in ({'geometric_validity':'unvalidated'},{'clock_mapping_status':'unverified'},
                {'alignment_frame':'rgb'},{'capture_epoch':'other'},{'camera_id':'other'},
                {'observed_at':99.9},{'calibration_content_id':'other'},{'depth_validation_receipt':None},
                {'frame_id':'other'},{'format':'numeric_npz'},{'derivation':None},{'source_media_id':'elsewhere'}):
            with self.subTest(change=change):
                with self.store.transaction() as db:db.execute('UPDATE media SET metadata=? WHERE id=?',(encode({**original,**change}),mid))
                with self.assertRaises(ValueError):publish_room(self.store,sid,[point],room.registration,room.map,generation=1,as_of=100)
        self.assertFalse(any(e['kind']=='room.output' for e in self.store.list_events(sid)))

    def test_arm_scoped_publication_cannot_carry_other_arm_points(self):
        room,point=self.setup_map();sid=self.session['id']
        for arms in (['b'],['c'],'a'):
            with self.subTest(arms=arms),self.assertRaises(ValueError):
                publish_room(self.store,sid,[point],room.registration,room.map,generation=1,as_of=100,arms=arms)
        self.store.clock=lambda:100.5
        publish_room(self.store,sid,[point],room.registration,room.map,generation=1,as_of=100.5,arms=['a'])
        # A later empty snapshot for arm b keeps arm a until its own expiry only.
        self.store.clock=lambda:100.9
        self.assertEqual(len(publish_room(self.store,sid,[],room.registration,room.map,generation=1,as_of=100.9,arms=['b'])['people']),1)
        self.store.clock=lambda:101.2
        self.assertEqual(publish_room(self.store,sid,[],room.registration,room.map,generation=1,as_of=101.2,arms=['b'])['people'],[])

    def test_client_posted_validated_metadata_is_not_trusted(self):
        # An ingest-scope sample can carry any metadata but never the derived key.
        room,point=self.setup_map();sid=self.session['id']
        meta=self.store.get_media(sid,point['media_ids'][0])['metadata']
        posted=self.store.add_media(sid,b'synthetic registered depth','application/octet-stream',meta,'["capture1","depth",1]')
        with self.assertRaises(ValueError):
            publish_room(self.store,sid,[{**point,'media_ids':[posted['id']]}],room.registration,room.map,generation=1,as_of=100)

    def test_rgb_floor_arm_is_published_without_depth_and_display_follows_policy(self):
        room,point=self.setup_map({'a':{'localization':'floor_projection','room_relations':True},
                                   'b':{'localization':'stereo','room_relations':False}})
        sid=self.session['id']
        registration={**room.registration,'floor_homography':room.homography}
        floor=self.floor_point(room,arm='a')
        context=publish_room(self.store,sid,[floor,point],registration,room.map,generation=1,as_of=100)
        # Arm a is a floor-projection arm: its stereo point is withheld from display too.
        self.assertEqual([(p['arm'],p['method']) for p in context['people']],[('A','rgb_floor_contact')])
        self.assertEqual(context['unavailable'],[{'track_id':'t1','arm':'a','reason':'arm_policy_excludes_method'}])
        self.assertTrue(all(m['track_id']=='t2' for m in context['memberships']))
        events=[e['payload'] for e in self.store.list_events(sid) if e['kind']=='room.observation']
        self.assertEqual(len(events),1)
        quality=events[0]['result']['quality']
        self.assertEqual((quality['method'],quality['surface_only'],quality['floor_contact']),('rgb_floor_contact',False,True))
        self.assertEqual((quality['homography_version'],quality['validation_receipt']),('fh1','fixture-only'))
        self.assertEqual(events[0]['calibration_version'],'fh1')
        self.assertEqual(events[0]['result']['tracks'][0]['pos'],[1,2,0.0])
        self.assertEqual(events[0]['result']['relations'][0]['zone_id'],'desk')
        replay=Controller(self.store).detail(sid,at_ms=100)['room']
        self.assertEqual([p['method'] for p in replay['people']],['rgb_floor_contact'])

    def test_rgb_floor_point_needs_its_own_verified_rgb_observation(self):
        room,point=self.setup_map({'a':{'localization':'floor_projection','room_relations':True},
                                   'b':{'localization':'none','room_relations':False}})
        sid=self.session['id']
        registration={**room.registration,'floor_homography':room.homography}
        floor=self.floor_point(room,arm='a')
        for altered in ({**floor,'media_ids':point['media_ids']},{**floor,'media_ids':floor['media_ids']+point['media_ids']},
                        {**floor,'frame_id':'other'},{**floor,'observed_at':100.5},{**floor,'calibration_version':'cal1'}):
            with self.subTest(altered=altered),self.assertRaises(ValueError):
                publish_room(self.store,sid,[altered],registration,room.map,generation=1,as_of=100)
        from store import encode
        mid=floor['media_ids'][0];original=self.store.get_media(sid,mid)['metadata']
        with self.store.transaction() as db:db.execute('UPDATE media SET metadata=? WHERE id=?',(encode({**original,'clock_mapping_status':'unverified'}),mid))
        with self.assertRaises(ValueError):publish_room(self.store,sid,[floor],registration,room.map,generation=1,as_of=100)
        self.assertFalse(any(e['kind']=='room.output' for e in self.store.list_events(sid)))


if __name__=='__main__':unittest.main()

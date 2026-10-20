"""Actual owner runtime/Store/ASGI: selected frame clocks survive graph advance."""
from copy import deepcopy
from dataclasses import replace
import hashlib,time,unittest
from fastapi.testclient import TestClient
from app import create_app,PREFIX
import test_apartment_viewing_source as viewing_fixture
import live_capture_fixtures as capture

FIELDS=('request_id','arm','site_id','camera_id','frame_id','media_ids','capture_epoch','generation',
        'run_id','model_version','artifact_sha256','engine_epoch','observed_at','available_at','valid_until')

class LiveObservationTests(unittest.TestCase):
    setUp=viewing_fixture.ApartmentViewingTests.setUp
    build=viewing_fixture.ApartmentViewingTests.build
    consume=viewing_fixture.ApartmentViewingTests.consume
    wait_output=viewing_fixture.ApartmentViewingTests.wait_output
    def outputs(self,mid):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            found=[e for e in self.store.list_events(self.session['id'],0,100) if e['kind']=='perception.output' and e['payload']['media_ids']==[mid]]
            if len(found)==2:return found
            time.sleep(.005)
        self.fail('both actual paired outputs must complete')
    def test_public_live_copies_same_selected_output_identity_and_original_times_when_graph_advances(self):
        token='authored-observation-'+('c'*40)
        self.store.settings=replace(self.store.settings,token_grants=({'subject':'viewer','site_id':'victoria',
            'token_sha256':hashlib.sha256(token.encode()).hexdigest(),'capabilities':['read']},))
        runtime=self.build();first=self.consume(runtime);events=self.outputs(first['media_ids'][0])
        headers={'Authorization':'Bearer '+token,'X-Home-Lab-Site':'victoria'}
        with TestClient(create_app(self.store.settings,runtime,self.store)) as client:
            path=PREFIX+'/sessions/'+self.session['id']
            chosen=client.get(path+'/live',headers=headers).json()
            for event in events:
                p=event['payload'];arm=chosen['arms'][p['arm'].upper()];observation=arm['observation']
                self.assertEqual(observation['schema'],'home-lab-arm-observation-1')
                self.assertEqual(observation['session_id'],self.session['id'])
                self.assertEqual({key:observation[key] for key in FIELDS},{key:p.get(key) for key in FIELDS})
                self.assertEqual(observation['frame_id'],arm['frame']['id'])
                self.assertEqual(observation['media_ids'],[arm['frame']['media_id']])
            original=deepcopy(chosen['arms']['B']['observation'])
            self.now+=1.1
            second=runtime.preview_pumps[self.session['id']].consume(self.jpeg,
                capture.header(self.jpeg,sequence=2,observed_ns=int((capture.NOW+1.1)*1e9)))
            later=self.outputs(second['media_ids'][0])
            graph=client.get(path+'/graph',headers=headers).json()
            specialist=next(item for item in graph['arms']['b']['chain'] if item['stage']=='specialist_output')
            newest=next(e['payload'] for e in later if e['payload']['arm']=='b')
            self.assertEqual(specialist['frame_id'],newest['frame_id'])
            self.assertNotEqual(specialist['frame_id'],original['frame_id'])
            self.assertEqual(chosen['arms']['B']['observation'],original,'new graph cannot overwrite chosen frame clocks')
            descriptor=client.get(path+'/media/'+first['media_ids'][0]+'/descriptor',
                params={'generation':self.session['generation']},headers=headers).json()
            self.authored_race_fixture={'schema':'home-authored-live-graph-race-fixture-1',
                'chosen_live':deepcopy(chosen),'later_graph':deepcopy(graph),'chosen_descriptor':descriptor,
                'external_camera_and_model':'authored','pixel_bytes_included':False}
            # Public response containers cannot mutate the immutable cache.
            chosen['arms']['B']['observation']['media_ids'].append('authored-mutation')
            cached=runtime._latest_live_payload(self.session['id'],'perception.output','b',runtime.runners[self.session['id']].generation)
            self.assertEqual(cached['media_ids'],newest['media_ids'])
    def test_missing_original_time_and_profile_fields_remain_null_without_delivery_fallback(self):
        runtime=self.build();first=self.consume(runtime);events=self.outputs(first['media_ids'][0])
        p=deepcopy(next(e['payload'] for e in events if e['payload']['arm']=='b'))
        for key in ('observed_at','available_at','model_version','artifact_sha256'):p.pop(key,None)
        self.store.append_event(self.session['id'],'perception.output',p)
        live=runtime.live(self.session);observation=live['arms']['B']['observation']
        for key in ('observed_at','available_at','model_version','artifact_sha256'):self.assertIsNone(observation[key])
        self.assertIsNotNone(live['arms']['B']['delivery']['last_completed_at'])
        self.assertEqual(observation['request_id'],p['request_id'])

if __name__=='__main__':unittest.main()

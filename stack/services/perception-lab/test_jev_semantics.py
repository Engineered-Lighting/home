import json
import unittest
from jev_evidence import project
from jev_semantics import semantic_state
from jev_provider import JevProvider
from reasoning import ACTIVITIES


class SemanticTests(unittest.TestCase):
    def record(self):
        return {'id':'e1','site_id':'victoria','camera_id':'den','session_id':'s',
            'arm':'a','generation':1,'observed_at':9,'available_at':10,'valid_until':12,
            'quality':{'image_width':640,'image_height':360},
            'objects':[{'label':'person','instance_id':'PRIVATE_PERSON'},{'label':'book','instance_id':'PRIVATE_BOOK'}],
            'boxes':[{'label':'person','instance_id':'PRIVATE_PERSON','x':0,'y':0,'w':100,'h':200},
                     {'label':'book','instance_id':'PRIVATE_BOOK','x':50,'y':90,'w':30,'h':20}],
            'pose_instances':[{'layout':'coco_wholebody133','instance_id':'PRIVATE_PERSON',
                'score_semantics':'heatmap_response_not_probability','joints':[
                {'joint_id':9,'pixel':[60,100],'score':.8,'status':'supported'}]}]}

    def test_common_joints_named_and_score_semantics_preserved(self):
        output=project([self.record()])
        p=output[0]['pose_instances'][0]
        self.assertEqual(p['source_layout'],'coco_wholebody133')
        self.assertEqual(p['score_semantics'],'heatmap_response_not_probability')
        self.assertEqual(p['joints'][0]['name'],'left_wrist')
        self.assertEqual(project(output),output)
        self.assertNotIn('PRIVATE',json.dumps(output))

    def test_geometry_facts_are_not_contact_or_activity(self):
        source=self.record();state=semantic_state(project([source]),10)
        fact=state['observations'][0]['facts'][0]
        self.assertEqual(fact['fact'],'left_wrist_near_book_in_image')
        self.assertEqual(fact['physical_contact'],'unknown')
        self.assertEqual(state['observations'][0]['freshness'],'fresh')
        self.assertEqual(semantic_state(project([source]),13)['observations'][0]['freshness'],'historical')
        source['pose_instances'][0]['joints'][0]['status']='uncertain'
        self.assertEqual(semantic_state(project([source]),10)['observations'][0]['facts'],[])

    def test_registered_ha_only_and_unknown_preserved(self):
        r=self.record();r['ha']={'binary_sensor.pc':{'state':'on'},'secret.person':{'state':'PRIVATE'}}
        bindings={'binary_sensor.pc':'computer_active'}
        self.assertNotIn('ha',project([r])[0])
        out=project([r],ha_bindings=bindings)
        self.assertEqual(out[0]['ha'],{'computer_active':'on'})
        self.assertEqual(project(out,ha_bindings=bindings),out)
        self.assertNotIn('PRIVATE',json.dumps(out))
        r['ha']['binary_sensor.pc']['state']='unavailable'
        self.assertEqual(project([r],ha_bindings=bindings)[0]['ha']['computer_active'],'unknown')

    def test_room_tracks_are_session_scoped_not_identity(self):
        r=self.record();r.update(source_kind='room.observation',tracks=[{'track_id':'PRIVATE',
            'method':'stereo_surface','pos':[1,2,3],'uncertainty_m':.2}],
            relations=[{'zone_id':'private_room_name','relation':'boundary'}])
        bindings={'private_room_name':'desk'}
        out=project([r],zone_bindings=bindings)
        self.assertEqual(out[0]['relations'],[{'zone_type':'desk','relation':'boundary'}])
        self.assertEqual(out[0]['quality']['metric_depth'],'validated_surface')
        self.assertEqual(project(out,zone_bindings=bindings),out)
        other=dict(r,session_id='another')
        self.assertNotEqual(out[0]['tracks'][0]['track_ref'],project([other])[0]['tracks'][0]['track_ref'])
        r['source_kind']='perception.output'
        self.assertNotIn('tracks',project([r],zone_bindings=bindings)[0])

    def test_historical_caption_is_a_correlated_interpretation_not_an_observation(self):
        caption={'id':'c1','site_id':'victoria','camera_id':'den','session_id':'s','arm':'a','generation':1,
            'observed_at':1,'available_at':9,'valid_until':12,'source_kind':'caption.receipt','evidence_roots':['caption:k'],
            'historical_caption':{'activity':'reading','interpretation':'historical_model_interpretation',
                                  'version':'caption-projection-2:0123456789ab'}}
        records=project([self.record(),caption])
        state=semantic_state(records,10)
        self.assertEqual(state['version'],'den-semantics-7')
        self.assertEqual([o['evidence_id'] for o in state['observations']],['e1'])
        self.assertEqual(state['latest_visual_evidence_id'],'e1')
        self.assertEqual(state['historical_caption'],{'evidence_id':'c1','activity':'reading',
            'interpretation':'historical_model_interpretation','age_seconds':9,'freshness':'within_validity',
            'correlated_with':{'relation':'same_camera_visual','camera_id':'den'},'independent_confirmation':False})
        self.assertEqual(state['source_coverage']['qwen_caption'],'supplied')
        self.assertIn('never makes evidence current',state['limitations'])
        self.assertEqual(semantic_state(records,12)['historical_caption']['freshness'],'expired')
        empty=semantic_state(project([self.record()]),10)
        self.assertIsNone(empty['historical_caption'])
        self.assertEqual(empty['source_coverage']['qwen_caption'],'not_supplied')

    def test_rgb_floor_position_is_not_reported_as_depth(self):
        r=self.record();r.update(source_kind='room.observation',tracks=[{'track_id':'t','method':'rgb_floor_contact',
            'pos':[1,2,0],'uncertainty_m':.4}])
        state=semantic_state(project([r]),10)
        self.assertEqual(state['observations'][0]['depth'],'rgb_floor_estimate')
        self.assertEqual(state['observations'][0]['spatial_tracks'][0]['method'],'rgb_floor_contact')
        self.assertEqual((state['source_coverage']['depth'],state['source_coverage']['rgb_floor_position'],
                          state['source_coverage']['tracking']),('not_supplied','supplied','supplied'))
        r['tracks'][0]['method']='stereo_surface'
        coverage=semantic_state(project([r]),10)['source_coverage']
        self.assertEqual((coverage['depth'],coverage['rgb_floor_position']),('supplied','not_supplied'))

    def test_batched_provider_retains_exact_packet_answers_usage(self):
        calls=[]
        def transport(body,*_):
            packet=json.loads(body);calls.append(packet)
            answers={k:({'type':'noul','noul':.2} if q['type']=='noul' else
                {'type':'choice','choice':'insufficient_evidence','confidence':1,
                 'probabilities':{x:float(x=='insufficient_evidence') for x in ACTIVITIES}})
                for k,q in packet['questions'].items()}
            return json.dumps({'model':'jev-1.13.0','answers':answers,'usage':{'input_tokens':123,'output_tokens':20}}).encode()
        provider=JevProvider(model_selector='jev-1.13.0',response_version='jev-1.13.0',
            semantic=True,clock=lambda:10,transport=transport,credential=lambda:'fixture',
            authorize=lambda *_:{'allowed':True,'scope':'household_structured','revision':'r','generation':1,'expires_at':20})
        result=provider.evaluate([self.record()],request_id='r',site_id='victoria',session_id='s',arm='a',generation=1,cutoff=10,deadline=20)
        self.assertEqual(len(calls[0]['questions']),4)
        self.assertEqual(result['diagnostics']['request'],calls[0])
        self.assertEqual(result['diagnostics']['usage']['input_tokens'],123)
        self.assertEqual(result['status'],'abstain')


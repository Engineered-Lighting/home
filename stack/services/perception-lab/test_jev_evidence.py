import json
import unittest
from jev_evidence import project


class CloudProjectionTests(unittest.TestCase):
    def test_recorded_geometry_keeps_frame_local_person_association(self):
        from persisted_evidence import observation_record
        source={'site_id':'victoria','camera_id':'den','arm':'a','generation':1,
            'observed_at':1,'available_at':2,'valid_until':3,'status':'completed',
            'result':{'objects':[{'label':'person','instance_id':'PRIVATE_PERSON'},
                                 {'label':'book','instance_id':'PRIVATE_BOOK'}],
                'boxes':[{'label':'person','instance_id':'PRIVATE_PERSON','x':10,'y':20,'w':100,'h':200},
                         {'label':'book','instance_id':'PRIVATE_BOOK','x':50,'y':90,'w':40,'h':20}],
                'pose_instances':[{'layout':'coco17','instance_id':'PRIVATE_PERSON','joints':[
                    {'joint_id':9,'pixel':[60,100],'score':1.2,'status':'supported'}]}],
                'quality':{'image_width':640,'image_height':360}}}
        record=observation_record(1,'perception.output',source,
                                  {'id':'session','site_id':'victoria','camera_id':'den'})
        output=project([record])
        person,book=output[0]['objects']
        self.assertEqual(person['instance_ref'],output[0]['pose_instances'][0]['instance_ref'])
        self.assertNotEqual(person['instance_ref'],book['instance_ref'])
        self.assertEqual(book['pixel_box'],{'x':50,'y':90,'w':40,'h':20})
        self.assertNotIn('PRIVATE',json.dumps(output))
        self.assertEqual(project(output),output)  # Actual cloud policy requires idempotence.
        self.assertEqual(source['result']['objects'][0]['instance_id'],'PRIVATE_PERSON')

    def test_invalid_geometry_and_ambiguous_association_abstain(self):
        source={'objects':[{'label':'person','instance_id':'same'},
                           {'label':'person','instance_id':'same'}],
            'boxes':[{'label':'person','instance_id':'same','x':639,'y':0,'w':20,'h':10}],
            'pose_instances':[{'layout':'coco17','instance_id':'same','joints':[
                {'joint_id':9,'pixel':[700,100],'score':.9,'status':'supported'}]}],
            'quality':{'image_width':640,'image_height':360}}
        result=project([source])[0]
        self.assertTrue(all('pixel_box' not in o for o in result['objects']))
        self.assertNotIn('instance_ref',result['pose_instances'][0])
        self.assertEqual(result['pose_instances'][0]['joints'][0]['status'],'uncertain')
        source.pop('quality')
        self.assertIsNone(project([source])[0]['pose_instances'][0]['joints'][0]['pixel'])

    def test_detector_dimensions_require_verified_recorded_frame(self):
        from persisted_evidence import observation_record
        session={'id':'session','site_id':'victoria','camera_id':'den'}
        value={'site_id':'victoria','camera_id':'den','arm':'a','generation':1,
            'observed_at':1,'available_at':2,'valid_until':3,'status':'completed','frame_id':'f',
            'result':{'schema_version':1,'coordinate_space':'source_pixels','frame_id':'f','quality':{}},
            'arm_snapshot':{'frame':{'id':'f','width':640,'height':360},'validation':[]}}
        result=observation_record(1,'perception.output',value,session)
        self.assertEqual(result['quality'],{'image_width':640,'image_height':360})
        self.assertEqual(value['result']['quality'],{})
        value['arm_snapshot']['frame']['id']='another-frame'
        self.assertEqual(observation_record(1,'perception.output',value,session)['quality'],{})
        value['arm_snapshot']['frame']['id']='f'
        value['arm_snapshot']['validation']=['overlay_source_frame_not_verified']
        self.assertEqual(observation_record(1,'perception.output',value,session)['quality'],{})

    def test_nested_identity_media_labels_and_private_attributes_cannot_escape(self):
        source={'id':'e1','site_id':'victoria','arm':'a','identity_hypothesis':'PRIVATE',
            'objects':[{'label':'book','confidence':.8,'owner':'PRIVATE'},{'label':'PRIVATE'}],
            'pose_instances':[{'layout':'coco17','identity':'PRIVATE','joints':[
                {'joint_id':9,'pixel':[100,200],'score':.9,'status':'supported','notes':'PRIVATE'}]}],
            'quality':{'image_width':640,'image_height':360,'private_log':'PRIVATE'},
            'ha':{'secret':'PRIVATE'},'relations':[{'zone':'PRIVATE'}],
            'human_labels':['PRIVATE'],'image':'PRIVATE'}
        output=project([source])
        self.assertNotIn('PRIVATE',json.dumps(output))
        self.assertEqual(output[0]['objects'],[{'label':'book','confidence':.8}])
        self.assertEqual(output[0]['pose_instances'][0]['joints'][0]['pixel'],[100,200])
        self.assertEqual(source['identity_hypothesis'],'PRIVATE')

    def test_unknown_or_invalid_joint_is_not_supported(self):
        output=project([{'pose_instances':[{'layout':'coco17','joints':[
            {'joint_id':0,'pixel':[float('nan'),2],'status':'supported','score':.8}]}]}])
        self.assertIsNone(output[0]['pose_instances'][0]['joints'][0]['pixel'])
        self.assertEqual(output[0]['pose_instances'][0]['joints'][0]['status'],'uncertain')

    def caption(self, **changes):
        return {'id':'9','site_id':'victoria','camera_id':'den','session_id':'s','arm':'b','generation':2,
            'observed_at':85,'available_at':100,'valid_until':115,'evidence_roots':['caption:k'],
            'source_kind':'caption.receipt','frame_id':'pilot-frame','local_text':'PRIVATE caption text',
            'caption_digest':'PRIVATE','model_revision':'PRIVATE','quality':{'private_log':'PRIVATE'},
            'historical_caption':{'activity':'reading','interpretation':'forged','version':'caption-projection-2:0123456789ab',
                                  'rule':'PRIVATE','text':'PRIVATE'},**changes}

    def test_caption_projection_is_bounded_idempotent_and_correlated(self):
        output=project([self.caption()])
        self.assertEqual(output[0]['historical_caption'],{'schema':'caption-activity-1','activity':'reading',
            'interpretation':'historical_model_interpretation','version':'caption-projection-2:0123456789ab'})
        self.assertEqual(output[0]['quality'],{'metric_depth':'not_supplied',
            'correlated_with':{'relation':'same_camera_visual','camera_id':'den'}})
        self.assertNotIn('PRIVATE',json.dumps(output))
        self.assertEqual(project(output),output)
        for changes,field,value in (({'historical_caption':{'activity':'watching_tv'}},'activity','unavailable'),
                                    ({'historical_caption':{'activity':'reading','version':'free text'}},'version','unknown'),
                                    ({'historical_caption':'reading'},'activity','unavailable')):
            projected=project([self.caption(**changes)])
            self.assertEqual(projected[0]['historical_caption'][field],value)
            self.assertEqual(project(projected),projected)
        # Only a caption receipt may carry the field; a worker result cannot.
        self.assertNotIn('historical_caption',project([self.caption(source_kind='perception.output')])[0])

    def room(self, method):
        from persisted_evidence import observation_record
        track={'id':'a:t1','track_id':'PRIVATE_TRACK','arm':'A','pos':[1.2,2.5,0.0],'uncertainty_m':.35,'method':method,
               'observed_at':99,'available_at':99.5,'valid_until':101,'identity':None}
        value={'site_id':'victoria','camera_id':'den','generation':1,'arm':'a','track_id':'PRIVATE_TRACK',
            'frame_id':'f1','capture_epoch':'e1','observed_at':99,'available_at':99.5,'valid_until':101,
            'coordinate_frame':'den-map','calibration_version':'cal','map_version':'map','evidence_roots':['root'],
            'status':'completed','result':{'tracks':[track],'relations':[{'zone_id':'desk-1','relation':'inside','track_id':'PRIVATE_TRACK'}],
                'quality':{'method':method,'surface_only':method=='stereo_surface','independent_confirmation':False}}}
        return observation_record(7,'room.observation',value,{'id':'s','site_id':'victoria','camera_id':'den'})

    def test_rgb_floor_contact_is_an_estimate_distinct_from_stereo(self):
        rgb,stereo=(project([self.room(m)],zone_bindings={'desk-1':'desk'})[0] for m in ('rgb_floor_contact','stereo_surface'))
        self.assertEqual(rgb['quality']['metric_depth'],'rgb_floor_estimate')
        self.assertEqual(stereo['quality']['metric_depth'],'validated_surface')
        self.assertEqual(rgb['tracks'][0]['method'],'rgb_floor_contact')
        self.assertEqual(rgb['tracks'][0]['uncertainty_m'],.35)
        self.assertEqual(rgb['relations'],[{'zone_type':'desk','relation':'inside','track_ref':rgb['tracks'][0]['track_ref']}])
        self.assertNotIn('PRIVATE',json.dumps(rgb))
        self.assertEqual(project([rgb],zone_bindings={'desk-1':'desk'}),[rgb])
        # A record mixing methods claims only the weaker estimate.
        mixed=self.room('stereo_surface');mixed['tracks'].append({**mixed['tracks'][0],'track_id':'other','method':'rgb_floor_contact'})
        self.assertEqual(project([mixed])[0]['quality']['metric_depth'],'rgb_floor_estimate')
        self.assertNotIn('tracks',project([self.room('monocular_guess')])[0])


if __name__ == '__main__':
    unittest.main()

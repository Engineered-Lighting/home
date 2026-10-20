from copy import deepcopy
import unittest
from live_detector_app import validate_config,TrialLease,CHECKPOINT,PREPROCESSING
from reasoning import digest


def provision():
    value={'schema_version':1,'capture_config':'/private/capture.json','camera_env':'/private/camera.env',
        'lease_file':'/private/lease.json','receiver_clock':{'validation_id':'measured','uncertainty_seconds':.4},'arms':{}}
    for arm,device,ip in [('a','victoria-den-jetson','192.168.1.168'),('b','la-ai','192.168.0.100')]:
        value['arms'][arm]={'origin':'https://'+ip+':8791','token_file':'/private/'+arm+'.token','ca_file':'/private/'+arm+'.pem',
            'profile':{'checkpoint':'yolo26s','artifact_sha256':CHECKPOINT,'sha256':CHECKPOINT,'precision':'fp32',
                'preprocessing':deepcopy(PREPROCESSING),'task':'detect','runtime_version':'8.4.0','profile_id':arm,
                'model_version':'pinned','engine_epoch':'trial-'+arm,'device_id':device,'runtime_revision':'exact-runtime'}}
    return value


class LiveDetectorProvisioningTests(unittest.TestCase):
    def test_exact_pair_and_fixed_hosts(self):
        original=provision()
        self.assertEqual(validate_config(original),original)
        for path,value in [('origin','https://example.com'),('origin','https://192.168.0.100/execute')]:
            changed=deepcopy(original);changed['arms']['b'][path]=value
            with self.assertRaises(ValueError):validate_config(changed)
        for field,value in [('precision','fp16'),('artifact_sha256','a'*64),('device_id','victoria-den-jetson')]:
            changed=deepcopy(original);changed['arms']['b']['profile'][field]=value
            with self.assertRaises(ValueError):validate_config(changed)

    def test_readiness_expires_without_extending_or_loading(self):
        profiles={k:v['profile'] for k,v in provision()['arms'].items()}
        value={'schema_version':1,'ready_at':100,'expires_at':220,'profiles_sha256':digest(profiles),
            'gate_references':{'technical':'t','health_resource':'h','workload_authorization':'a'}}
        now=[101]
        lease=TrialLease('unused',profiles,read=lambda _:value,clock=lambda:now[0])
        self.assertTrue(lease.ready())
        now[0]=220;self.assertFalse(lease.ready())
        now[0]=101;value['expires_at']=221;self.assertFalse(lease.ready())
        value['expires_at']=220;value['profiles_sha256']='changed';self.assertFalse(lease.ready())

    def test_optional_occupancy_reader_block_is_exact(self):
        # Plan S1.2: the Victoria-LAN reader of the bound occupancy entity.
        reader={'origin':'https://victoria-jetson.example.ts.net','credential_file':'/private/occupancy.token'}
        for block in (reader,{**reader,'hold_seconds':5,'api':'perception_lab_source','ca_file':'/private/ca.pem'}):
            value={**provision(),'occupancy_reader':block}
            self.assertEqual(validate_config(value),value)
        for bad in ({**reader,'origin':'http://192.168.1.60:8123'},{**reader,'hold_seconds':2},{**reader,'hold_seconds':16},
                    {**reader,'entity':'person.owner'},{**reader,'credential_file':'occupancy.token'},{**reader,'extra':1},
                    {'origin':reader['origin']},None):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                validate_config({**provision(),'occupancy_reader':bad})

    def test_missing_or_unreadable_admission_is_not_ready(self):
        def denied(_):raise PermissionError()
        self.assertFalse(TrialLease('unused',{},read=denied).ready())


if __name__=='__main__':unittest.main()

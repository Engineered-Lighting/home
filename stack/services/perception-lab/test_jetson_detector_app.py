from copy import deepcopy
import unittest
from jetson_detector_app import CHECKPOINT,PREPROCESSING,validate_config,validate_limits
from host_home import JETSON_HOME


class TrialGuards(unittest.TestCase):
    def config(self):
        return dict(schema_version=1,checkpoint_path=JETSON_HOME + '/perception-lab/model-cache/yolo26s/yolo26s.pt',
            ledger_root=JETSON_HOME + '/perception-lab/worker-ledgers/yolo26s',
            expires_at=1100,maximum_requests=120,gate_references=dict(technical='review1',health_resource='trial1',workload_authorization='owner1'),
            grants=[dict(site_id='victoria',cameras=['den'],token_sha256='a'*64)],
            manifest=dict(device_id='victoria-den-jetson',device='cuda:0',checkpoint='yolo26s',sha256=CHECKPOINT,
                runtime_version='8.4.0',precision='fp32',format='pt',task='detect',preprocessing=deepcopy(PREPROCESSING),engine_epoch='trial-1'))

    def test_exact_limits_fail_closed(self):
        limits={'memory.max':str(2*1024**3),'memory.swap.max':'0','pids.max':'64','cpu.max':'100000 100000'}
        validate_limits(limits)
        for key,value in [('memory.max','max'),('memory.swap.max','1024'),('pids.max','max'),('cpu.max','200000 100000')]:
            with self.subTest(key=key),self.assertRaises(ValueError):validate_limits({**limits,key:value})

    def test_no_cross_host_precision_or_unbounded_trial(self):
        validate_config(self.config(),1000)
        for path in ['/tmp/elsewhere',JETSON_HOME + '/perception-lab/model-cache/../private/file']:
            config=self.config();config['checkpoint_path']=path
            with self.assertRaises(ValueError):validate_config(config,1000)

    def test_early_contract_guards_before_any_model_import(self):
        for field,value in [('expires_at',999),('expires_at',1300),('maximum_requests',241),('maximum_requests',True)]:
            config=self.config();config[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):validate_config(config,1000)
        for field,value in [('device_id','echo'),('precision','fp16'),('sha256','b'*64),('checkpoint','yolo26s-seg')]:
            config=self.config();config['manifest'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):validate_config(config,1000)


if __name__=='__main__':unittest.main()

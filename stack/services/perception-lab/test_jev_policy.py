import unittest
import test_paired_runtime as fixtures
from jev_policy import LabJevPolicy
from jev_evidence import project


class PolicyTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def test_session_budget_survives_reenable_and_gate_reconstruction(self):
        gate=self.setup_policy();self.policy['max_session_calls']=1
        gate(self.context,self.packet,'send')
        self.policy['revision']='reenabled'
        self.context['request_id']='r2'
        self.store.db.execute('INSERT INTO lab_reasoning_requests VALUES(?,?,?)',('r2',self.session['id'],'dispatching'))
        gate=LabJevPolicy(self.store,lambda:self.policy,lambda:self.switch)
        with self.assertRaisesRegex(ValueError,'session budget'):gate(self.context,self.packet,'send')

    def setup_policy(self):
        self.now=100;self.store.clock=lambda:self.now
        self.policy={'schema_version':1,'enabled':True,'scope':'household_structured','revision':'lab1',
            'generation':1,'expires_at':200,'site_id':'victoria','camera_ids':['den'],
            'model_selector':'jev-1.13','max_calls_per_minute':2}
        self.switch={'state':'on','received_at':100}
        gate=LabJevPolicy(self.store,lambda:self.policy,lambda:self.switch)
        self.store.db.execute('CREATE TABLE lab_reasoning_requests(id TEXT PRIMARY KEY,session_id TEXT,state TEXT)')
        self.context={'request_id':'r1','site_id':'victoria','session_id':self.session['id'],'arm':'a','generation':1}
        evidence=project([{**self.context,'id':'e1','camera_id':'den','objects':[{'label':'book'}]}])
        self.packet={'model':'jev-1.13','state':{'site_id':'victoria','evidence':evidence}}
        self.store.db.execute('INSERT INTO lab_reasoning_requests VALUES(?,?,?)',('r1',self.session['id'],'dispatching'))
        return gate

    def test_admit_once_and_recheck_revocation_before_publish(self):
        gate=self.setup_policy()
        self.assertTrue(gate(self.context,self.packet,'send')['allowed'])
        with self.assertRaises(ValueError):gate(self.context,self.packet,'send')
        self.assertTrue(gate(self.context,self.packet,'publish')['allowed'])
        self.policy['generation']=2
        with self.assertRaises(ValueError):gate(self.context,self.packet,'publish')

    def test_unknown_prior_session_blocks_new_egress(self):
        gate=self.setup_policy()
        self.store.db.execute('INSERT INTO lab_reasoning_requests VALUES(?,?,?)',('old','other','indeterminate'))
        with self.assertRaisesRegex(ValueError,'Unresolved'):gate(self.context,self.packet,'send')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM lab_jev_egress').fetchone()[0],0)

    def test_stale_toggle_and_nested_data_are_rejected(self):
        gate=self.setup_policy();self.switch['received_at']=94
        with self.assertRaises(ValueError):gate(self.context,self.packet,'send')
        self.switch['received_at']=100
        self.packet['state']['evidence'][0]['identity_hypothesis']='private'
        with self.assertRaises(ValueError):gate(self.context,self.packet,'send')

    def test_rate_budget_survives_authorizer_reconstruction(self):
        gate=self.setup_policy();gate(self.context,self.packet,'send')
        self.store.db.execute('INSERT INTO lab_jev_egress VALUES(?,?,?,?,?)',('other','hash',100,'lab1',1))
        gate=LabJevPolicy(self.store,lambda:self.policy,lambda:self.switch)
        self.context['request_id']='r2'
        self.store.db.execute('INSERT INTO lab_reasoning_requests VALUES(?,?,?)',('r2',self.session['id'],'dispatching'))
        with self.assertRaisesRegex(ValueError,'rate budget'):gate(self.context,self.packet,'send')

    def test_fresh_local_switch_read_is_not_rejected_as_future(self):
        gate=self.setup_policy()
        def read_switch():
            self.now+=.001
            return {'state':'on','received_at':self.now}
        gate.toggle=read_switch
        self.assertTrue(gate(self.context,self.packet,'send')['allowed'])

    def test_caption_egress_requires_the_exact_projection_grant(self):
        gate=self.setup_policy()
        caption=project([{**self.context,'id':'c1','camera_id':'den','source_kind':'caption.receipt',
            'observed_at':90,'available_at':99,'valid_until':110,'historical_caption':{'activity':'reading'}}])
        self.packet['state']['evidence']+=caption
        # Off unless the household policy grants exactly this projection schema.
        for grant in (None,'caption-activity-2'):
            if grant:self.policy['historical_caption']=grant
            with self.assertRaisesRegex(ValueError,'Historical caption egress not permitted'):gate(self.context,self.packet,'send')
        self.policy['historical_caption']=True
        with self.assertRaisesRegex(ValueError,'policy unavailable'):gate(self.context,self.packet,'send')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM lab_jev_egress').fetchone()[0],0)
        self.policy['historical_caption']='caption-activity-1'
        self.assertTrue(gate(self.context,self.packet,'send')['allowed'])
        # A visual-only packet never needs the caption grant.
        del self.policy['historical_caption']
        self.context['request_id']='r2';self.packet['state']['evidence']=self.packet['state']['evidence'][:1]
        self.store.db.execute('INSERT INTO lab_reasoning_requests VALUES(?,?,?)',('r2',self.session['id'],'dispatching'))
        self.assertTrue(gate(self.context,self.packet,'send')['allowed'])


if __name__ == '__main__':
    unittest.main()

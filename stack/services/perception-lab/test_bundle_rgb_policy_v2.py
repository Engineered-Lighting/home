"""Explicit bounded input-window successor; legacy policy remains unchanged."""
from copy import deepcopy
import unittest
import test_bundle_rgb_input as fixture
from bundle_rgb_input import policy


class BundledPolicyV2Tests(unittest.TestCase):
    def test_new_explicit_24_input_policy_is_opt_in(self):
        value=dict(schema='home-lab-bundled-rgb-input-policy-2',version=2,max_captures=24,max_retained_bytes=40*1024**2)
        self.assertEqual(policy(value),value)
        copied=policy(value);copied['max_captures']=1
        self.assertEqual(value['max_captures'],24)

    def test_legacy_default_and_eight_input_ceiling_remain(self):
        value=deepcopy(fixture.POLICY)
        self.assertEqual((value['schema'],value['version'],value['max_captures']),('home-lab-bundled-rgb-input-policy-1',1,8))
        self.assertEqual(policy(value),value)
        value['max_captures']=9
        with self.assertRaises(ValueError):policy(value)

    def test_schema_version_count_and_byte_caps_are_closed(self):
        base=dict(schema='home-lab-bundled-rgb-input-policy-2',version=2,max_captures=24,max_retained_bytes=40*1024**2)
        for change in ({'schema':'home-lab-bundled-rgb-input-policy-1'},{'version':1},{'version':True},{'max_captures':25},
                       {'max_captures':True},{'max_captures':0},{'max_retained_bytes':40*1024**2+1},{'extra':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):policy({**base,**change})


if __name__=='__main__':unittest.main()

"""Independent forged background-need leases; no delivered background claim."""
from copy import deepcopy
import unittest
import test_live_blocking_authority as authority
from live_test_helpers import fact


class BlockingBackgroundTests(unittest.TestCase):
    system=authority.BlockingAuthorityTests.system
    publish=authority.BlockingAuthorityTests.publish
    start=authority.BlockingAuthorityTests.start
    tick=authority.BlockingAuthorityTests.tick
    post_commit=authority.BlockingAuthorityTests.post_commit

    def test_requested_background_has_exact_identity_and_bounded_positive_lease(self):
        self.system()
        presence=fact('authored-background-presence')
        presence.update(kind='person_presence',value='present')
        presence['head']['key']['predicate']='person_presence'
        self.publish(presence);self.start()
        honest=self.tick(prepare=True)
        decision=next(p['payload'] for p in honest['publications'] if p['payload_type']=='home-live-shadow-decision-6')
        self.assertEqual(decision['background']['status'],'requested',decision)
        for change in ({'valid_until':self.now},{'valid_until':self.now+4},{'goal_id':'background:forged'}):
            with self.subTest(change=change):
                forged=deepcopy(honest)
                next(p['payload'] for p in forged['publications'] if p['payload_type']=='home-live-shadow-decision-6')['background'].update(change)
                status,result=self.post_commit(forged)
                self.assertEqual(status,409,result)
                self.assertEqual(result['error']['code'],'invalid_background',result)
        status,result=self.post_commit(honest)
        self.assertEqual(status,200,result)


if __name__=='__main__':unittest.main()

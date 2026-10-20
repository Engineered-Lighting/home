"""Authored producer/journal boundary cases for the explicit repair profile."""
import copy
import tempfile
import unittest
from pathlib import Path

from journal import Journal, PREFIX
from live_conservative_test_helpers import conservative_fixture
from live_delivery_test_helpers import delivery_geometry
from live_test_helpers import NOW, PROFILE, SCOPE, LiveHttp, event, fact, publication_ref, receipt_resolver


class BlockingIngressTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix='fictional-blocking-')
        self.addCleanup(self.work.cleanup)
        service, grants, _ = conservative_fixture()
        self.now = NOW + .2
        self.journal = Journal(Path(self.work.name)/'controlled.sqlite', grants=grants, profile=PROFILE,
            live_config=service, live_receipt_resolver=receipt_resolver, clock=lambda:self.now,live_wall_clock=lambda:self.now,live_monotonic=lambda:self.now-NOW)
        self.addCleanup(self.journal.close)
        self.server = LiveHttp(self.journal)
        self.addCleanup(self.server.close)

    def post(self, payload):
        return self.server.request('POST',PREFIX+'/events',event(payload))

    def accepted(self, payload):
        status,receipt=self.post(payload)
        self.assertEqual(status,200,receipt)
        return receipt

    def test_semantic_child_cannot_outlive_its_original_work_component(self):
        parent=delivery_geometry();parent['parts']['work']['valid_until']=NOW+2
        saved=self.accepted(parent)
        child=fact('long-child');child['available_at']=NOW+.3
        child['parent_refs']=[dict(publication_ref(saved),component_id='parts.work')]
        self.now=NOW+.3
        status,result=self.post(child)
        self.assertEqual(status,409,result)
        self.assertEqual(result['error']['code'],'invalid_clock')

    def test_spatial_replacement_keeps_semantics_only_until_original_component_lease(self):
        parent=delivery_geometry();parent['parts']['work']['valid_until']=NOW+2
        old=self.accepted(parent)
        child=fact('short-child');child.update(available_at=NOW+.3,valid_until=NOW+1.5)
        child['parent_refs']=[dict(publication_ref(old),component_id='parts.work')]
        self.now=NOW+.3;self.accepted(child)
        newer=delivery_geometry(2);newer['head']['supersedes']=publication_ref(old)
        self.now=NOW+1.2;self.accepted(newer)
        def ids():
            status,result=self.server.request('GET',PREFIX+'/snapshot?site_id='+SCOPE['site_id']+'&room_id='+SCOPE['room_id'])
            self.assertEqual(status,200,result)
            return {p['id'] for p in result['items']}
        self.assertIn(child['id'],ids(),'spatial ACK before interaction replacement must not invent a semantic gap')
        self.now=NOW+1.5
        self.assertNotIn(child['id'],ids(),'replacement cannot refresh the original exclusive lease')


if __name__=='__main__':
    unittest.main()

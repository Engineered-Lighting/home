"""Actual local graph creation/Stop refusals; no worker/native/model calls."""
from copy import deepcopy
import threading
import time
import unittest
from unittest.mock import patch

import test_lab_stereo_desk_runtime as stereo
import lab_live_runtime as runtime
from lab_desk_person_bridge import DeskPersonBridgeSource


class DeskPersonCompositionTests(unittest.TestCase):
    def setUp(self):
        self.f=stereo.LabStereoDeskRuntimeTests()
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        producer=deepcopy(self.f.producer)
        seed=dict(original=dict(event=self.f.f.row),source_generation=1,
            source_position=dict(capture_epoch='synthetic-camera-epoch',capture_seq=7,revision=0),publication_ref=None)
        a=self.f.f.qualification(seed)['association']
        producer['semantic_policy']=dict(schema='home-lab-person-semantic-admission-policy-1',version=1,
            base_policy=deepcopy(producer['semantic_policy']),bridge=dict(source_id=self.f.policy['source_id'],
            method_ref=deepcopy(a['method_ref']),qualification_ref=deepcopy(a['assessment_ref']),
            receipt_store_id='authored-person-witness',receipt_component_id='person-work-association',max_bindings=1))
        self.comp=runtime.LabRuntimeComposition(producer,store=self.f.f.store,preflight=lambda:True,
            control_clock=self.f.control_clock,desk_capability=self.f.capability,clock=lambda:self.f.f.now,
            monotonic=lambda:0.)
        self.addCleanup(self.cleanup_graph)

    def cleanup_graph(self):
        g=self.comp.graph
        if g is not None and not g.finished:
            withdrawal=g.withdraw('authored_create_cleanup')
            g.finalize('authored_create_cleanup',drain_receipt=dict(schema='home-acquisition-drain-1',version=1,
                session_ref=deepcopy(self.f.scope),configuration_digest=g.configuration_digest,
                withdrawal_receipt=withdrawal['barrier_receipt'],recorder_status='incomplete',worker_status='settled',
                terminal_cursor=withdrawal['input_cursor'] or 0))

    def test_existing_registry_owner_refuses_before_archive_or_server(self):
        incumbent=object()
        self.f.f.store._qualified_desk_person_bridges={self.f.scope['session_id']:incumbent}
        with self.assertRaisesRegex(ValueError,'owner_already_exists'):
            self.comp.create()
        self.assertIsNone(self.comp.graph)
        self.assertFalse(self.comp.originals is not None)
        self.assertFalse(self.f.path.joinpath('stereo-journal.sqlite').exists())
        self.assertFalse(self.f.path.joinpath('stereo-originals.jsonl').exists())
        self.assertIs(self.f.f.store._qualified_desk_person_bridges[self.f.scope['session_id']],incumbent)

    def test_postcreate_bind_failure_closes_only_new_graph_and_preserves_other_owner(self):
        incumbent=object()
        self.f.f.store._qualified_desk_person_bridges={'unrelated-session':incumbent}
        with patch.object(DeskPersonBridgeSource,'bind',side_effect=ValueError('authored_bind_failed')):
            with self.assertRaisesRegex(ValueError,'authored_bind_failed'):
                self.comp.create()
        graph=self.comp.graph
        self.assertIsNotNone(graph)
        self.assertTrue(graph.finished)
        self.assertFalse(graph.server_thread.is_alive())
        self.assertEqual(graph.server.fileno(),-1)
        with self.assertRaises(Exception):graph.journal.db.execute('SELECT 1')
        self.assertIs(self.f.f.store._qualified_desk_person_bridges['unrelated-session'],incumbent)
        self.assertNotIn(self.f.scope['session_id'],self.f.f.store._qualified_desk_person_bridges)
        self.assertEqual(self.comp.failed_creation_cleanup['finalization']['status'],'unconfirmed')
        self.assertFalse(self.comp.failed_creation_cleanup['worker_started'])

    def test_quiesce_does_not_wait_for_contended_store_and_later_unregisters(self):
        graph=self.comp.create();source=self.comp.desk_person_bridge_source
        entered,release=threading.Event(),threading.Event()
        def hold():
            with self.f.f.store.lock:
                entered.set();release.wait(2)
        thread=threading.Thread(target=hold)
        thread.start();self.assertTrue(entered.wait(1))
        try:
            start=time.monotonic();result=graph.quiesce('authored_stop_contention');elapsed=time.monotonic()-start
            self.assertLess(elapsed,1.,'Stop must not await the bounded test lock holder')
            self.assertEqual(result['status'],'unconfirmed')
            self.assertTrue(source.quiesced)
            self.assertIs(self.f.f.store._qualified_desk_person_bridges[self.f.scope['session_id']],source)
        finally:
            release.set();thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(graph.quiesce('authored_stop_contention')['status'],'quiescent')
        self.assertNotIn(self.f.scope['session_id'],self.f.f.store._qualified_desk_person_bridges)


if __name__=='__main__':unittest.main()

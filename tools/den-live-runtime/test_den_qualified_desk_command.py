"""One selected offline SAME-Journal desk/person/dwell/command integration.

Execute only through the reviewed bounded software-native coordinator. Import
and collection are inert. Camera, model, commissioning/path assessment and
motor feedback are authored; actual production admission, temporal reduction,
commit projection, compiler, authority, outbox and canonical encoders run.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]


def load(name, relative):
    path=ROOT/relative
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QualifiedDeskCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Explicit pinned environment required. Verifies originals only;
        # native processes begin after the sole accepted worker commit.
        import test_room_command_session as canonical
        canonical.RoomCommandSessionTests.setUpClass()

    def test_same_journal_fresh_qualified_desk_dwell_to_canonical_software_move(self):
        evidence=Path(os.environ['DEN_QUALIFIED_DESK_EVIDENCE_DIR'])
        allowed=(ROOT/'artifacts/den-e2e-desk-command-20261018T235057Z-fbe624b5').resolve()
        self.assertTrue(evidence.is_absolute() and evidence.is_dir() and evidence.resolve().is_relative_to(allowed))
        self.assertFalse(any(path.is_symlink() for path in (evidence,*evidence.parents)))
        self.assertFalse(any(evidence.iterdir()),'fresh coordinator-selected evidence directory required')
        downstream=load('qualified_desk_downstream','tools/fixtures/den-qualified-desk-command-runtime.py')
        upstream=load('qualified_desk_upstream','tools/fixtures/den-qualified-desk-source-runtime.py')
        holder={}
        def on_graph(owner,graph,worker):
            self.assertNotIn('trace',holder)
            self.assertEqual(int(graph.journal._meta('cursor')),0)
            trace=downstream.CommandTrace(self,owner=owner,graph=graph,worker=worker,evidence=evidence)
            holder['trace']=trace
            return trace.recorder
        owner=None
        try:
            owner=upstream.Fixture(self,on_graph=on_graph)
            trace=holder['trace']
            positive=owner.run_positive()
            commit=positive['commit']
            self.assertIs(trace.graph,owner.graph)
            self.assertIs(trace.graph.journal,owner.graph.journal)
            self.assertIs(owner.graph.store,owner.f.store)
            checkpoint=commit['checkpoint']
            self.assertEqual(checkpoint['schema'],'home-live-worker-checkpoint-12')
            relations=list(checkpoint['state']['engagement']['reducer']['state']['relations'].values())
            supported=[r for r in relations if r['state']=='supported']
            self.assertEqual(len(supported),1,relations)
            relation=supported[0]
            self.assertGreaterEqual(relation['enter_dwell'],5)
            self.assertEqual(relation['chosen_clause']['qualification'],'qualified_pc_input')
            self.assertIsInstance(relation['refinement'],dict,relation)
            self.assertEqual(relation['refinement']['value'],'computer_work',relation)
            self.assertTrue(checkpoint['state']['pc_input_originals'])
            decisions=[e['payload'] for e in commit['publications']
                       if e['payload_type']=='home-live-shadow-decision-12']
            self.assertEqual(len(decisions),1)
            decision=decisions[0]
            self.assertEqual(decision['execution']['status'],'virtual',decision)
            self.assertEqual(decision['room_output']['task']['fixture_id'],'fixture-1')
            self.assertGreater(decision['execution']['readback'][0]['brightness_fraction'],0)
            self.assertGreaterEqual(len(positive['steps']),6,'fresh originals must earn actual dwell')
            self.assertTrue(positive['bridge_records'],'actual retained independent bridge required')
            self.assertTrue(positive['jev_outputs'],'actual queued per-person JEV required')
            for record in positive['bridge_records']:
                original=record['original']
                self.assertEqual(original['scope'],owner.source.scope)
                self.assertTrue(original['qualified_original_refs'])
                self.assertTrue(original['work_parent_ref'])
            trace_receipt=trace.accept_and_trace(commit)
            root_receipt=dict(schema='den-qualified-desk-command-authored-integration-1',version=1,
                status='passed',source=deepcopy(positive),command=trace_receipt,
                source_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
                    'tools/fixtures/den-qualified-desk-source-runtime.py',
                    'tools/fixtures/den-qualified-desk-command-runtime.py',
                    'tools/den-live-runtime/test_den_qualified_desk_command.py')},
                real_qualification=False,physical_permission=False,physical_truth=False,
                continuous_service=False,host_operations=0,real_model_calls=0,
                spotlight_semantic_CCT_mapping=False,full_combined_replay=False)
            (evidence/'joined-positive.json').write_text(json.dumps(root_receipt,indent=2)+'\n',encoding='utf8')
        finally:
            if 'trace' in holder:
                cleanup=holder['trace'].close(stop_source=owner.close if owner is not None else None)
                self.assertFalse(cleanup['errors'])
            elif owner is not None:
                owner.close()


if __name__=='__main__':unittest.main()

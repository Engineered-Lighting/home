"""Pinned subset sibling references; no Journal, server, child or native I/O."""
from copy import deepcopy
import json
import unittest

from journal import JournalError, digest
from schema_registry import (SchemaRegistry, LIVE_PROFILE, LIVE_ROOM_SUBSET_SCHEMAS,
    LIVE_ROOM_SUBSET_VERSIONS, LIVE_PC_SUPPORT_SCHEMAS)


class RoomSubsetRegistryTests(unittest.TestCase):
    def test_actual_intent2_resolves_through_cut12_and_every_pinned_sibling(self):
        registry = SchemaRegistry((), profile=LIVE_PROFILE)
        pin = lambda name: dict(id='authored-'+name, digest='a'*64)
        identity = dict(site_id='authored-site', room_id='authored-room',
            session_id='authored-session', domain_epoch='authored-domain')
        intent = dict(schema='home-live-intent-event-2', version=2,
            id='authored-inspection', **identity, manifest_ref=pin('manifest'),
            actor_ref='authored-owner', origin='human_declaration', truth_role='runtime_intent',
            kind='clear_person_hazards', target=None, set_at=100, available_at=100,
            valid_until=105, reply_to=None,
            value=dict(hazard_refs=[pin('hazard')], policy_ref=pin('policy'),
                applicability_ref=pin('applicability'), procedure_ref=pin('procedure'),
                room_assessment_ref=pin('assessment'), inspection_at=100))
        publication = dict(schema='home-context-publication-1', producer_id='authored-owner',
            stream_epoch='authored-stream', cursor=1, context_seq=0, committed_at=100,
            payload_type=intent['schema'], payload_version='2.0', payload=intent,
            payload_digest=digest(intent), id=intent['id'])
        cut = dict(schema='home-live-input-cut-12', version=12, stream_epoch='authored-stream',
            cursor=1, items=[publication], revocation_generation=0, source_generations={},
            service_clock=dict(schema='home-live-service-clock-1', **identity,
                stream_epoch='authored-stream', anchor_epoch='authored-anchor',
                authority_generation=0, utc_at=100, uncertainty_s=0, qualified=True),
            pc_input_originals=[])
        # Direct old lookup already succeeds before the repair. The complete
        # new cut must also resolve its actual intent2 through the sibling path.
        registry.validate(intent)
        registry.validate(cut)

        shared = {'home-live-pc-support-policy-1', 'home-live-room-output-1',
            'home-live-room-snapshot-1', 'home-live-person-lifecycle-1', 'home-live-intent-event-2'}
        self.assertEqual(len(LIVE_ROOM_SUBSET_VERSIONS), 10)
        for name in LIVE_ROOM_SUBSET_VERSIONS:
            path = LIVE_ROOM_SUBSET_SCHEMAS/(name+'.schema.json')
            self.assertIn(path, registry.documents)
            if name in shared:
                # Add sibling aliases while preserving the predecessor's
                # canonical name-to-schema lookup for all five shared leaves.
                self.assertEqual(registry.schemas[name][1], LIVE_PC_SUPPORT_SCHEMAS/path.name)
                self.assertEqual(registry.documents[path], registry.schemas[name][0])
            def check_refs(node):
                if isinstance(node, dict):
                    ref = node.get('$ref', '')
                    if ref and not ref.startswith('#'):
                        self.assertIn(path.parent/ref.split('#', 1)[0], registry.documents)
                    for value in node.values(): check_refs(value)
                elif isinstance(node, list):
                    for value in node: check_refs(value)
            check_refs(json.loads(path.read_bytes()))

        changed = deepcopy(cut)
        changed['items'][0]['payload']['truth_role'] = 'forged-authority'
        changed['items'][0]['payload_digest'] = digest(changed['items'][0]['payload'])
        with self.assertRaises(JournalError) as raised:
            registry.validate(changed)
        self.assertEqual(raised.exception.code, 'invalid_payload')

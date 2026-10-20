"""The live v5 pre-send budget guard (reasoning.build_jev_request budget=True
via JevProvider): the oldest window records are trimmed, the semantic state is
rebuilt so the policy's recompute still matches, and a packet that cannot fit
the hard cap is refused locally with nothing admitted or sent. Temp stores and
a fake transport; no network."""
import json
from unittest.mock import patch

import reasoning
from jev_provider import JevProvider, ProviderFailure
from jev_wire import TOKENS_PER_BYTE_HI, estimate_tokens
from test_jev_policy_v6 import BINDINGS, V6Case, respond
from live_inputs import live_records
from reasoning import build_jev_request, canonical, causal_evidence
from jev_evidence import project


class BudgetTests(V6Case):
    def provider(self, transport, authorize=None):
        return JevProvider(model_selector='jev-1.13.0', response_version='jev-1.13.0', authorize=authorize or self.gate,
                           credential=lambda: 'fixture', transport=transport, clock=self.store.clock, ha_bindings=BINDINGS,
                           allow_recent_history=True, semantic=True)

    def window(self, cutoff=100.0, window=10):
        rows = live_records(self.store, self.store.get_session(self.sid), 1, cutoff, window_seconds=window, retain_history=True)
        return causal_evidence(rows, site_id='victoria', session_id=self.sid, arm='a', generation=1, cutoff=cutoff,
                               window_seconds=window, retain_history=True)

    def evaluate(self, provider, rid='b1', cutoff=100.0, window=10):
        self.store.db.execute('INSERT OR REPLACE INTO lab_reasoning_requests VALUES(?,?,?)', (rid, self.sid, 'dispatching'))
        return provider.evaluate(self.window(cutoff, window), request_id=rid, site_id='victoria', session_id=self.sid,
                                 arm='a', generation=1, cutoff=cutoff, deadline=cutoff + 10, window_seconds=window)

    def untrimmed(self):
        records = project(self.window(), ha_bindings=BINDINGS)
        packet, provenance = build_jev_request(records, model='jev-1.13.0', site_id='victoria', session_id=self.sid, arm='a',
                                               generation=1, cutoff=100.0, window_seconds=10, retain_history=True,
                                               allow_recent_history=True, semantic=True)
        return packet, provenance

    def test_under_the_soft_cap_the_packet_is_unchanged(self):
        packet, provenance = self.untrimmed()
        records = project(self.window(), ha_bindings=BINDINGS)
        guarded, guarded_provenance = build_jev_request(records, model='jev-1.13.0', site_id='victoria', session_id=self.sid,
                                                        arm='a', generation=1, cutoff=100.0, window_seconds=10,
                                                        retain_history=True, allow_recent_history=True, semantic=True, budget=True)
        self.assertLess(estimate_tokens(len(canonical(packet).encode())), reasoning.V5_SOFT_TOKENS)
        self.assertEqual(canonical(guarded), canonical(packet))
        self.assertEqual(guarded_provenance, {**provenance, 'budget_trimmed': 0})
        self.assertNotIn('budget_trimmed', provenance, 'the guard is opt-in; replay of older packets is unchanged')

    def test_an_oversized_window_is_trimmed_oldest_first_and_passes_the_policy(self):
        packet, _ = self.untrimmed()
        full = estimate_tokens(len(canonical(packet).encode()))
        evidence = packet['state']['evidence']
        frames = [r for r in evidence if r.get('source_kind') == 'perception.output']
        latest = max(r['observed_at'] for r in frames)
        bodies = []

        def transport(body, *_):
            bodies.append(json.loads(body))
            return respond(body)
        # Caps scaled to this fixture: the full window is over the soft cap.
        with patch.object(reasoning, 'V5_SOFT_TOKENS', int(full*.6)), patch.object(reasoning, 'V5_HARD_TOKENS', int(full*.8)):
            result = self.evaluate(self.provider(transport))
            soft = reasoning.V5_SOFT_TOKENS
        self.assertEqual(result['status'], 'ready', 'send and publish both admitted: the recompute matched')
        sent = bodies[0]['state']
        trimmed = result['provenance']['budget_trimmed']
        self.assertGreater(trimmed, 0)
        self.assertEqual(result['diagnostics']['budget_trimmed'], trimmed)
        self.assertEqual(len(sent['evidence']), len(evidence) - trimmed)
        self.assertLessEqual(estimate_tokens(len(canonical(bodies[0]).encode())), soft)
        self.assertEqual(result['diagnostics']['estimated_tokens'], estimate_tokens(len(canonical(bodies[0]).encode())))
        kept = {r['id'] for r in sent['evidence']}
        dropped = [r for r in evidence if r['id'] not in kept]
        self.assertTrue(all(r['observed_at'] < latest for r in dropped if r in frames), 'the latest frame is never dropped')
        self.assertTrue(all(d['observed_at'] <= k['observed_at'] for d in dropped for k in sent['evidence']), 'oldest first')
        self.assertLess(trimmed, len(frames) - 2, 'trims what the cap needs, not the whole window')
        self.assertIn(latest, [r['observed_at'] for r in sent['evidence']])
        # The semantic block describes exactly the sent evidence.
        self.assertEqual([o['evidence_id'] for o in sent['semantic']['observations']
                          if o['evidence_id'] in kept], [o['evidence_id'] for o in sent['semantic']['observations']])
        self.assertEqual(result['provenance']['evidence_ids'], [r['id'] for r in sent['evidence']])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM lab_jev_egress').fetchone()[0], 1)
        event = json.loads(self.store.db.execute("SELECT payload FROM events WHERE kind='reasoning.request'").fetchone()[0])
        self.assertEqual(event['request'], bodies[0])
        self.assertNotIn('budget_trimmed', json.dumps(bodies[0]), 'recorded locally, never sent')

    def test_a_packet_that_cannot_fit_is_refused_and_nothing_is_sent(self):
        calls, grants = [], []

        def authorize(*args, **kw):
            grants.append(args[2])
            return self.gate(*args, **kw)
        # Even the latest frame alone is over this hard cap.
        with patch.object(reasoning, 'V5_SOFT_TOKENS', 50), patch.object(reasoning, 'V5_HARD_TOKENS', 100):
            with self.assertRaises(ProviderFailure) as caught:
                self.evaluate(self.provider(lambda *a: calls.append(a), authorize))
        self.assertEqual((caught.exception.reason, caught.exception.indeterminate), ('request_over_budget', False))
        self.assertEqual((calls, grants), ([], []))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM lab_jev_egress').fetchone()[0], 0)
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM events WHERE kind='reasoning.request'").fetchone())

    def test_production_caps_and_ratio(self):
        self.assertEqual((reasoning.V5_SOFT_TOKENS, reasoning.V5_HARD_TOKENS, TOKENS_PER_BYTE_HI), (22000, 26000, .70))
        # The failed request of session 0decd6f3 (53,631 bytes) is over the hard cap.
        self.assertGreater(estimate_tokens(53631), reasoning.V5_HARD_TOKENS)


if __name__ == '__main__':
    import unittest
    unittest.main()

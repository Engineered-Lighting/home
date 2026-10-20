"""computer_active_v2: HASS.Agent input-recency buckets beside the kept v1 fact."""
import json
import unittest

from ha_ingest import HAIngestor
from live_inputs import live_records
from test_ha_ingest import DERIVED, HAIngestTests, LAST_ACTIVE, SESSION, iso

RECENCY = {'session_entity': SESSION, 'unlocked_values': ['Unlocked'], 'last_active_entity': LAST_ACTIVE}


class ComputerInputRecencyTests(unittest.TestCase):
    setUp = HAIngestTests.setUp
    tearDown = HAIngestTests.tearDown

    def source(self, derived=None):
        return HAIngestor(self.store, site_id='victoria', camera_id='den', source_id='pc-ha', entities=[SESSION, LAST_ACTIVE],
                          derived=derived or {**DERIVED, 'computer_input_recency': RECENCY})

    def derive(self, states, observed_at=399):
        self.snapshots = getattr(self, 'snapshots', 0)+1
        self.store.clock = lambda: 400.0
        event_id = self.source().ingest(self.session['id'], snapshot_id=f'pc{self.snapshots}', observed_at=observed_at, states=states)
        return next(e for e in self.store.list_events(self.session['id']) if e['id'] == event_id)['payload']['ha']

    def test_buckets_lock_and_the_v1_clock_skew_rule(self):
        cases = [({SESSION: 'Unlocked', LAST_ACTIVE: iso(389)}, 'under_60s'),
                 ({SESSION: 'Unlocked', LAST_ACTIVE: iso(399-60)}, 'under_60s'),       # exactly 60 s
                 ({SESSION: 'Unlocked', LAST_ACTIVE: iso(399-61)}, '1_to_5_min'),
                 ({SESSION: 'Unlocked', LAST_ACTIVE: iso(399-300)}, '1_to_5_min'),     # exactly 5 min
                 ({SESSION: 'Unlocked', LAST_ACTIVE: iso(399-301)}, 'over_5_min'),
                 ({SESSION: 'Unlocked', LAST_ACTIVE: iso(404)}, 'under_60s'),          # 5 s skew is tolerated
                 ({SESSION: 'Unlocked', LAST_ACTIVE: iso(404.5)}, None),               # future beyond skew
                 ({SESSION: 'Locked', LAST_ACTIVE: iso(389)}, 'locked'),
                 ({SESSION: 'LoggedOff', LAST_ACTIVE: iso(0)}, 'locked'),
                 ({SESSION: 'Locked', LAST_ACTIVE: iso(405)}, None),                   # same skew rule as v1
                 ({SESSION: 'Locked', LAST_ACTIVE: 'unknown'}, None),
                 ({SESSION: 'Unknown', LAST_ACTIVE: iso(389)}, None),                  # never read as a lock
                 ({SESSION: 'unavailable', LAST_ACTIVE: iso(389)}, None),
                 ({LAST_ACTIVE: iso(389)}, None),
                 ({SESSION: 'Unlocked'}, None),
                 ({SESSION: 'Unlocked', LAST_ACTIVE: iso(389, '')}, None)]           # naive timestamp
        for states, expected in cases:
            with self.subTest(states=states):
                facts = self.derive(states)
                fact = facts['derived.computer_input_recency']
                self.assertEqual((fact['state'], fact['status']), (expected, 'unknown' if expected is None else 'known'))
                self.assertEqual(fact['provenance'], {'inputs': [SESSION, LAST_ACTIVE], 'rule': 'computer_active_v2'})
                self.assertNotIn('origin', fact)
                # v1 stays, unchanged, beside v2.
                self.assertEqual(facts['derived.computer_active']['provenance']['rule'], 'computer_active_v1')

    def test_v2_rule_takes_no_idle_bound_and_v1_still_requires_one(self):
        for derived in ({'computer_input_recency': {**RECENCY, 'max_idle_seconds': 60}},
                        {'computer_input_recency': {k: v for k, v in RECENCY.items() if k != 'unlocked_values'}},
                        {'computer_input_recency': {**RECENCY, 'session_entity': 'sensor.other'}},
                        {'computer_active': RECENCY}):
            with self.subTest(derived=derived), self.assertRaises(ValueError):
                self.source(derived)
        self.assertEqual(self.source({'computer_input_recency': RECENCY}).derived['derived.computer_input_recency']['rule'],
                         'computer_active_v2')

    def test_projection_sends_only_the_bucket_without_raw_inputs(self):
        from jev_evidence import project
        stamp = iso(250)
        self.store.clock = lambda: 400.0
        self.source().ingest(self.session['id'], snapshot_id='pc-a', observed_at=399, states={SESSION: 'Unlocked', LAST_ACTIVE: stamp})
        bindings = {'derived.computer_active': 'computer_active', 'derived.computer_input_recency': 'computer_input_recency'}
        records = live_records(self.store, self.session, 1, 400)
        projected = project(records, ha_bindings=bindings)
        for record in projected:
            self.assertEqual(record['ha'], {'computer_active': 'on', 'computer_input_recency': '1_to_5_min'})  # v1 fixture idle bound is 300 s
            self.assertNotIn('correlated_with', record['quality'])
            text = json.dumps(record)
            for private in ('formd', 'sessionstate', 'lastactive', 'derived.', stamp, 'Unlocked', 'computer_active_v2', 'provenance'):
                self.assertNotIn(private, text)
        self.assertEqual(project(projected, ha_bindings=bindings), projected)
        with self.assertRaisesRegex(ValueError, 'Derived HA binding'):
            project(records, ha_bindings={'derived.computer_input_recency': 'computer_active'})


if __name__ == '__main__':
    unittest.main()

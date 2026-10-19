"""Dispatcher receipt -> supervisor result; authored receipts, no I/O."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'stack/services/home-context'))
from room_fixture_command_join import canonical_dispatch_outcome


class RoomCommandOutcomeTests(unittest.TestCase):
    def test_pending_and_unknown_effects_preserve_exact_original_instead_of_refusal(self):
        originals = [
            dict(state='unconfirmed', pending_retention=True, active_call=True,
                 namespace_blocked=True),
            dict(state='unknown', record=dict(state='unknown', adapter_disposition='UNCERTAIN')),
            dict(state='attempting', record=dict(state='attempting', adapter_disposition=None)),
            dict(state='prepared', record=dict(state='prepared', adapter_disposition=None)),
        ]
        for original in originals:
            with self.subTest(state=original['state']):
                original.update(schema='home-fixture-canonical-dispatch-status-1', version=1,
                                request_id='original-request', physical_permission=False)
                untouched = deepcopy(original)
                result = canonical_dispatch_outcome('original-request', original, exact_retry=True)
                self.assertEqual(result['status'], 'unconfirmed')
                self.assertEqual(result['request_id'], 'original-request')
                self.assertEqual(result['dispatch'], untouched)
                self.assertTrue(result['exact_retry'])
                self.assertEqual(original, untouched)
        refused = dict(state='refused', request_id='original-request',
                       record=dict(state='refused', adapter_disposition=None))
        admitted = dict(state='terminal', request_id='original-request',
                        record=dict(state='terminal', adapter_disposition='ADMITTED'))
        self.assertEqual(canonical_dispatch_outcome('original-request', refused)['status'], 'refused')
        self.assertEqual(canonical_dispatch_outcome('original-request', admitted)['status'], 'dispatched')


if __name__ == '__main__': unittest.main()

"""Existing factory/ASGI/collector originals -> private assessment input."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
for folder in ('stack/services/perception-lab', 'stack/services/home-context'):
    sys.path.insert(0, str(ROOT / folder))
import test_lab_assessment_collect as collection_tests
from lab_assessment_collect import read_results, read_verified_attempts


class VerifiedAssessmentAttemptTests(unittest.TestCase):
    def test_verified_private_attempts_preserve_originals_binding_and_unknown_qualification(self):
        fixture = collection_tests.ExistingApiCollectionTests('test_actual_factory_asgi_late_association_collects_private_successor')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.assertTrue(fixture.run_collection()['complete'])
        before = fixture.output.read_bytes()
        expected = next(r['value']['candidate'] for r in fixture.records() if r['kind'] == 'attempt')
        loaded = read_verified_attempts(fixture.output)
        self.assertEqual(set(loaded), {'binding', 'attempts', 'summary', 'collection_sha256'})
        self.assertEqual(loaded['summary'], read_results(fixture.output))
        self.assertEqual(loaded['attempts'], [expected])
        self.assertEqual(loaded['binding']['schema'], 'home-lab-assessment-binding-2')
        self.assertEqual(loaded['collection_sha256'], hashlib.sha256(before).hexdigest())
        self.assertIsNone(loaded['attempts'][0]['error_bound_m'])
        self.assertIsNone(loaded['attempts'][0]['person_ref'])
        self.assertTrue(loaded['attempts'][0]['candidate_only'])
        loaded['attempts'][0]['error_bound_m'] = 0
        self.assertIsNone(read_verified_attempts(fixture.output)['attempts'][0]['error_bound_m'])
        self.assertEqual(fixture.output.read_bytes(), before)
        self.assertEqual(fixture.f.store.get_session(fixture.f.session['id'])['state'], 'recording')


if __name__ == '__main__': unittest.main()

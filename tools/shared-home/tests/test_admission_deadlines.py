"""Explicit legacy budgets do not enlarge the Victoria snapshot pilot."""
from pathlib import Path
import tempfile
import unittest

from home_shared.admission import AdmissionController, AdmissionLedger, AdmissionError, ResourceManifest
from test_admission import FakeVerifier, manifest_fixture


def fixture():
    value = manifest_fixture()
    value.update(version=3, max_deadline_seconds=300,
                 consumer_deadline_seconds={'fake-echo': 120, 'fake-victoria': 15},
                 consumer_policy_sha256={'fake-echo': 'a'*64, 'fake-victoria': 'b'*64})
    return value


class DeadlineTests(unittest.TestCase):
    def test_old_version_unchanged_new_budget_explicit_and_immutable(self):
        old = ResourceManifest.from_dict(manifest_fixture())
        self.assertEqual(old.consumer_deadline_seconds['fake-echo'], 15)
        data = fixture()
        new = ResourceManifest.from_dict(data)
        self.assertEqual(new.consumer_deadline_seconds['fake-echo'], 120)
        data['consumer_deadline_seconds']['fake-echo'] = 300
        self.assertEqual(new.consumer_deadline_seconds['fake-echo'], 120)
        with self.assertRaises(TypeError):
            new.consumer_deadline_seconds['fake-echo'] = 300
        self.assertNotEqual(new.digest, ResourceManifest.from_dict(data).digest)
        with self.assertRaises(TypeError):
            new.consumer_policy_sha256['fake-echo'] = 'c'*64

    def test_policy_digests_are_complete_and_change_manifest_identity(self):
        data = fixture()
        first = ResourceManifest.from_dict(data)
        data['consumer_policy_sha256']['fake-echo'] = 'c'*64
        self.assertNotEqual(first.digest, ResourceManifest.from_dict(data).digest)
        for policies in ({}, {'fake-echo': 'a'*64}, {'fake-echo': 'latest', 'fake-victoria': 'b'*64}):
            data = fixture()
            data['consumer_policy_sha256'] = policies
            with self.subTest(policies=policies), self.assertRaises(AdmissionError):
                ResourceManifest.from_dict(data)

    def test_complete_map_and_bounds_required(self):
        for mapping in ({}, {'fake-echo': 120}, {'fake-echo': 120, 'fake-victoria': 16},
                        {'fake-echo': 301, 'fake-victoria': 15},
                        {'fake-echo': 120, 'fake-victoria': 15, 'guest': 1}):
            data = fixture()
            data['consumer_deadline_seconds'] = mapping
            with self.subTest(mapping=mapping), self.assertRaises(AdmissionError):
                ResourceManifest.from_dict(data)
        for limit in (0, True, -1, float('nan'), float('inf'), 10**400):
            data = fixture()
            data['consumer_deadline_seconds']['fake-echo'] = limit
            with self.subTest(limit=limit), self.assertRaises(AdmissionError):
                ResourceManifest.from_dict(data)
        data = fixture()
        data['max_deadline_seconds'] = 301
        with self.assertRaises(AdmissionError):
            ResourceManifest.from_dict(data)
        data = fixture()
        data['version'] = 2
        with self.assertRaises(AdmissionError):
            ResourceManifest.from_dict(data)

    def test_controller_enforces_consumer_budget_and_preserves_duplicate_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            with AdmissionLedger(Path(directory) / 'ledger.sqlite') as ledger:
                controller = AdmissionController(ledger, ResourceManifest.from_dict(fixture()),
                                                 FakeVerifier(), clock=lambda: 1000)
                controller.enable('test-epoch')
                first = controller.submit('chat-1', 'echo', 'fake-echo', 'a'*64, 'interactive', 1120)
                duplicate = controller.submit('chat-1', 'echo', 'fake-echo', 'a'*64, 'interactive', 1300)
                self.assertEqual(first.deadline, duplicate.deadline)
                self.assertEqual(duplicate.deadline, 1120)
                controller.submit('snapshot-1', 'victoria', 'fake-victoria', 'b'*64, 'background', 1015)
                for site, consumer, deadline in [('echo', 'fake-echo', 1121),
                                                 ('victoria', 'fake-victoria', 1016)]:
                    with self.subTest(site=site), self.assertRaises(AdmissionError):
                        controller.submit('bad-'+site, site, consumer, 'c'*64, 'interactive', deadline)
                self.assertIsNone(ledger.get('bad-echo'))
                self.assertIsNone(ledger.get('bad-victoria'))


if __name__ == '__main__':
    unittest.main()

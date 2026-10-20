"""The actual existing-API publisher's private terminal chain is indispensable."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
for relative in ('stack/services/home-context', 'stack/services/perception-lab'):
    if str(ROOT / relative) not in sys.path:
        sys.path.append(str(ROOT / relative))
import test_lab_existing_api_desk_runtime as existing
from lab_live_runtime import _desk_archive_binding


class ExistingApiStereoArchiveTests(unittest.TestCase):
    def test_actual_raw_nose_original_requires_the_full_terminal_chain_before_ack(self):
        fixture = existing.ExistingApiDeskRuntimeTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        source = fixture.source
        result = source.create().step()
        self.assertEqual(result['status'], 'admitted_desk', result)
        path = Path(source.producer['original_archive']['filename'])
        bodies = [json.loads(row)['body'] for row in path.read_text(encoding='utf-8').splitlines()]
        originals = [json.loads(row['original_json']) for row in bodies if row['kind'] == 'original']
        original = next(row for row in originals if row['schema'] == 'home-original-lab-stereo-nose-2')
        module = source.original_module
        binding = dict(session_id=source.scope['session_id'], method_ref=source.composition.join.method,
                       model_ref=source.composition.join.model)
        desk = _desk_archive_binding(source.scope, source.policy, source.composition.join.binding_digest)

        def writer(name):
            value = module.LabOriginalArchive(source.path / name, binding=binding, desk_binding=desk,
                min_free_bytes=1, stereo_media_resolver=source.media.resolve_retained)
            self.addCleanup(value.finalize)
            return value

        retained = writer('actual-existing-api-raw-nose.jsonl')
        ack = retained(deepcopy(original))
        self.assertEqual(ack, {'status': 'retained', 'digest': module.original_digest(original)})
        self.assertEqual(retained.finalize()['status'], 'finalized')
        recovered = module.read_lab_originals(retained.path, expected_digests=[ack['digest']],
            expected_desk_binding=desk, stereo_media_resolver=source.media.resolve_retained)
        self.assertTrue(recovered[ack['digest']] == original, 'exact raw support and Store events must round-trip')

        missing = deepcopy(original)
        missing['support']['assessment_originals'].pop('terminal')
        changed = deepcopy(original)
        changed['support']['assessment_originals']['terminal']['payload']['binding']['experiment_generation'] += 1
        for index, invalid in enumerate((missing, changed)):
            # A fresh writer prevents a same-ID retry check masking absent
            # original-chain validation. All binary refs remain unchanged.
            rejected = writer('invalid-existing-api-original-' + str(index) + '.jsonl')
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'lab_stereo_nose_original_binding_refused'):
                rejected(invalid)


if __name__ == '__main__':
    unittest.main()

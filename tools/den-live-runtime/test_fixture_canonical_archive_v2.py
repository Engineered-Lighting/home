"""Explicit declaration4 source inventory bounds; no native or network I/O."""
from copy import deepcopy
import unittest

from fixture_canonical_archive import validate_binding
from test_fixture_canonical_archive import CanonicalArchiveFixture
from room_command_factory import TRUSTED_TASK_SOURCE_INVENTORY


class CanonicalArchiveBindingV2Tests(unittest.TestCase):
    def binding(self, count, version):
        value = deepcopy(CanonicalArchiveFixture(self).binding)
        value.update(schema='home-fixture-canonical-archive-binding-' + str(version), version=version)
        value['source_pins'] = {'source/' + str(i) + '.py': '34' * 32 for i in range(count)}
        return value

    def test_legacy_bound_stays_128(self):
        value = self.binding(128, 1)
        self.assertEqual(validate_binding(value), value)
        with self.assertRaisesRegex(ValueError, 'canonical_archive_source_pins'):
            validate_binding(self.binding(129, 1))

    def test_explicit_successor_accepts_actual_complete_inventory(self):
        value = self.binding(1, 2)
        value['source_pins'] = {name: '34' * 32 for name in TRUSTED_TASK_SOURCE_INVENTORY}
        self.assertEqual(len(value['source_pins']), 139)
        self.assertEqual(validate_binding(value), value)
        value.update(schema='home-fixture-canonical-archive-binding-1', version=1)
        with self.assertRaisesRegex(ValueError, 'canonical_archive_source_pins'):
            validate_binding(value)

    def test_successor_bound_and_schema_pair_remain_closed(self):
        value = self.binding(160, 2)
        self.assertEqual(validate_binding(value), value)
        with self.assertRaisesRegex(ValueError, 'canonical_archive_source_pins'):
            validate_binding(self.binding(161, 2))
        for schema, version in (('home-fixture-canonical-archive-binding-1', 2),
                                ('home-fixture-canonical-archive-binding-2', 1),
                                ('home-fixture-canonical-archive-binding-2', True),
                                ('home-fixture-canonical-archive-binding-3', 3)):
            changed = deepcopy(value)
            changed.update(schema=schema, version=version)
            with self.assertRaisesRegex(ValueError, 'canonical_archive_binding_version'):
                validate_binding(changed)


if __name__ == '__main__':
    unittest.main()

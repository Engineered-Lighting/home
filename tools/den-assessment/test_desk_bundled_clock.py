"""Authored clock-format compatibility; proposals never qualify real geometry."""
from copy import deepcopy
import unittest

import desk_enclosure as consumer
import test_desk_distinct_parts as distinct


FAMILY = 'existing-api-bundled-rgb-1'


class DeskBundledClockTests(unittest.TestCase):
    def setUp(self):
        self.candidate, self.assessment, self.anchor, self.binding = distinct.inputs()
        self.hazards = [{'id': 'unresolved-other-person'}]

    def evaluate(self, **options):
        self.binding['anchor_original_ref']['digest'] = consumer.digest(self.anchor)
        return consumer.evaluate_desk_enclosure(self.candidate, binding=self.binding,
            assessment=self.assessment, anchor=self.anchor, now=100.5,
            hazards=self.hazards, **options)

    def bundled(self):
        self.anchor['clock'].update(original_unit='unix_s', original_value='100.000000000')

    def test_historical_default_keeps_integer_clock_and_same_result(self):
        original = self.evaluate()
        self.assertEqual(original['status'], 'eligible_for_admission_review')
        self.assertEqual(self.evaluate(original_family=None), original)
        self.anchor['clock']['original_unit'] = 'device_ns'
        self.assertEqual(self.evaluate()['status'], 'eligible_for_admission_review')

    def test_decimal_clock_requires_explicit_family(self):
        self.bundled()
        result = self.evaluate()
        self.assertEqual(result['reason'], 'clock_qualification_unavailable')
        self.assertIsNone(result['geometry_proposal'])

    def test_explicit_family_preserves_proposal_only_and_unresolved_hazards(self):
        self.bundled()
        result = self.evaluate(original_family=FAMILY)
        self.assertEqual(result['status'], 'eligible_for_admission_review')
        self.assertEqual(result['geometry_proposal']['observed_at'], 100)
        self.assertEqual(result['inherited_hazards'], self.hazards)
        self.assertIsNone(result['hazard_clearance'])
        self.assertIsNone(result['operational_geometry'])
        for key in ('journal_authority', 'engagement_authority', 'beam_authority', 'physical_authority'):
            self.assertFalse(result[key])

    def test_new_family_never_reinterprets_old_integer_units(self):
        result = self.evaluate(original_family=FAMILY)
        self.assertEqual(result['reason'], 'clock_qualification_unavailable')
        self.assertIsNone(result['geometry_proposal'])
        self.bundled()
        self.assertEqual(self.evaluate(original_family='jpeg-renamed')['reason'],
                         'unsupported_original_clock_family')

    def test_non_decimal_or_changed_public_time_refuses(self):
        self.bundled()
        original = deepcopy(self.anchor)
        for value in ('NaN', 'Infinity', '1e2', '-100', '+100', ' 100', '100 ', '100.1',
                      '100.' + '0' * 19, '0' * 40, 100):
            with self.subTest(value=value):
                self.anchor = deepcopy(original)
                self.anchor['clock']['original_value'] = value
                result = self.evaluate(original_family=FAMILY)
                self.assertEqual(result['reason'], 'clock_qualification_unavailable')
                self.assertIsNone(result['geometry_proposal'])

    def test_explicit_family_still_requires_exact_independent_mapping(self):
        self.bundled()
        self.anchor['clock']['mapping_digest'] = 'e' * 64
        self.assertEqual(self.evaluate(original_family=FAMILY)['reason'],
                         'clock_qualification_unavailable')

    def test_legacy_v1_cannot_opt_into_new_family(self):
        self.candidate, self.assessment, self.anchor, self.binding = distinct.legacy.inputs()
        self.bundled()
        result = self.evaluate(original_family=FAMILY)
        self.assertEqual(result['reason'], 'unsupported_original_clock_family')
        self.assertIsNone(result['geometry_proposal'])

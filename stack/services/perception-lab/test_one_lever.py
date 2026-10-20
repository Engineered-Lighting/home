"""One-lever recipes: a shared evidence baseline, and arms that differ only at the declared lever."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import evidence_policy as policy
from profile_resolver import RegisteredPairs, recipe_digest

CATALOG = json.loads(Path(__file__).with_name('experiments.json').read_text(encoding='utf-8'))
FAMILY_KEY = {'ha': 'ha_evidence', 'caption': 'caption_evidence'}


class CatalogLeverTests(unittest.TestCase):
    def setUp(self):
        self.catalog = deepcopy(CATALOG)
        self.defaults = policy.validate_catalog(self.catalog)

    def recipe(self, key):
        return next(r for r in self.catalog['experiments'] if r['id'] == key)

    def test_baseline_declares_every_shared_family(self):
        self.assertEqual(self.defaults, {'ha': 'granted', 'ambient': 'granted', 'caption': 'withheld',
                                         'qwen_scene': 'granted', 'temporal_encoder': 'withheld',
                                         'spatial': 'not_provisioned'})

    def test_all_19_recipes_differ_only_at_their_lever_and_share_every_other_family(self):
        self.assertEqual(len(self.catalog['experiments']), 19)
        for recipe in self.catalog['experiments']:
            with self.subTest(recipe=recipe['id']):
                grants = policy.validate_recipe(recipe, self.defaults)
                varied = set(policy.lever_families(recipe['lever']))
                for family, arms in grants.items():
                    if family not in varied:
                        self.assertEqual(arms['a'], arms['b'], family)
                # The shared baseline reaches both arms of every recipe.
                self.assertEqual(grants['ha'], {'a': 'granted', 'b': 'granted'})
                self.assertEqual(grants['ambient'], {'a': 'granted', 'b': 'granted'})
                # The continuous Qwen scene joins the baseline for both arms; its
                # coverage stays not_provisioned until the perceiver is configured.
                self.assertEqual(grants['qwen_scene'], {'a': 'granted', 'b': 'granted'})
                self.assertEqual(grants['spatial'], {'a': 'not_provisioned', 'b': 'not_provisioned'})
                # V-JEPA is outside the baseline: only its on/off lever grants it.
                self.assertEqual(grants['temporal_encoder'],
                                 {'a': 'withheld_by_experiment', 'b': 'granted'} if recipe['id'] == 'vjepa-onoff'
                                 else {'a': 'not_in_baseline', 'b': 'not_in_baseline'})
                self.assertEqual(len(recipe['lever']['paths']) > 1, 'coupled' in recipe['lever'])

    def test_only_the_caption_and_vjepa_recipes_vary_an_evidence_family(self):
        varied = {r['id']: policy.lever_families(r['lever']) for r in self.catalog['experiments']}
        self.assertEqual({k: v for k, v in varied.items() if v}, {'qwen-jev': ['caption'], 'vjepa-onoff': ['temporal_encoder']})
        grants = policy.recipe_grants(self.recipe('qwen-jev'), self.defaults)
        self.assertEqual(grants['caption'], {'a': 'withheld_by_experiment', 'b': 'granted'})
        # Outside the lever the baseline withholds captions from both arms alike.
        self.assertEqual(policy.recipe_grants(self.recipe('detection'), self.defaults)['caption'],
                         {'a': 'not_in_baseline', 'b': 'not_in_baseline'})

    def test_validator_refuses_an_undeclared_second_difference(self):
        recipe = deepcopy(self.recipe('detection'))
        recipe['b']['sampling_hz'] = 4
        with self.assertRaisesRegex(ValueError, 'differ at'):
            policy.validate_recipe(recipe, self.defaults)
        recipe = deepcopy(self.recipe('detection'))
        recipe['b']['ha_evidence'] = False
        with self.assertRaisesRegex(ValueError, 'differ at'):
            policy.validate_recipe(recipe, self.defaults)
        # An explicit value equal to the baseline default is not a difference.
        recipe = deepcopy(self.recipe('detection'))
        recipe['a']['ha_evidence'] = True
        recipe['b']['source_policy'] = {'ambient': True}
        policy.validate_recipe(recipe, self.defaults)

    def test_validator_refuses_malformed_or_idle_levers(self):
        base = self.recipe('detection')
        for lever in (None, {}, {'paths': [], 'note': 'x'}, {'paths': ['detector']}, {'paths': ['detector'], 'note': ''},
                      {'paths': ['detector', 'pose'], 'note': 'x'}, {'paths': ['detector'], 'coupled': 'x', 'note': 'x'},
                      {'paths': ['detector', 'detector'], 'coupled': 'x', 'note': 'x'}, {'paths': ['pose'], 'note': 'x'},
                      {'paths': ['detector'], 'note': 'x', 'source': 'detector'}):
            recipe = deepcopy(base)
            if lever is None:
                recipe.pop('lever')
            else:
                recipe['lever'] = lever
            with self.subTest(lever=lever), self.assertRaises(ValueError):
                policy.validate_recipe(recipe, self.defaults)

    def test_an_ambient_ablation_is_one_lever_and_withholds_only_that_arm(self):
        recipe = deepcopy(self.recipe('detection'))
        recipe['b']['detector'] = recipe['a']['detector']
        recipe['a']['source_policy'] = {'ambient': False}
        recipe['lever'] = {'paths': ['source_policy.ambient'], 'note': 'Ambient context withheld from A.'}
        grants = policy.validate_recipe(recipe, self.defaults)
        self.assertEqual(grants['ambient'], {'a': 'withheld_by_experiment', 'b': 'granted'})
        self.assertEqual(grants['ha'], {'a': 'granted', 'b': 'granted'})
        # Unprovisioned or legacy-keyed families cannot be granted through source_policy.
        for value in ({'spatial': True}, {'temporal_encoder': True}, {'ha': False}, {'ambient': 'no'}, {}, ['ambient']):
            broken = deepcopy(recipe)
            broken['a']['source_policy'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy.validate_recipe(broken, self.defaults)

    def test_baseline_must_keep_the_resolver_legacy_defaults(self):
        for family, default in (('ha', 'withheld'), ('caption', 'granted')):
            catalog = deepcopy(self.catalog)
            catalog['baseline']['families'][family]['default'] = default
            with self.subTest(family=family), self.assertRaises(ValueError):
                policy.validate_catalog(catalog)
        catalog = deepcopy(self.catalog)
        catalog['baseline']['families'].pop('spatial')
        with self.assertRaises(ValueError):
            policy.validate_catalog(catalog)

    def test_recipe_digest_covers_the_lever_and_the_baseline_digest_is_stable(self):
        recipe = deepcopy(self.recipe('placement-same'))
        before = recipe_digest(recipe)
        recipe['lever']['note'] += ' '
        self.assertNotEqual(recipe_digest(recipe), before)
        self.assertEqual(policy.baseline_digest(self.catalog), policy.baseline_digest(deepcopy(CATALOG)))

    def test_legacy_source_policy_derives_from_the_switches_and_must_agree(self):
        derived = policy.source_policy(None, ha={'a': True, 'b': False}, caption={'a': False, 'b': True})
        self.assertEqual(derived['ha'], {'a': 'granted', 'b': 'withheld_by_experiment'})
        self.assertEqual(derived['caption'], {'a': 'not_in_baseline', 'b': 'granted'})
        self.assertEqual(derived['ambient'], {'a': 'granted', 'b': 'granted'})
        with self.assertRaises(ValueError):
            policy.source_policy({**derived, 'ha': {'a': 'granted', 'b': 'granted'}}, ha={'a': True, 'b': False})
        with self.assertRaises(ValueError):
            policy.source_policy({**derived, 'ambient': {'a': 'maybe', 'b': 'granted'}},
                                 ha={'a': True, 'b': False}, caption={'a': False, 'b': True})


class ResolverLeverTests(unittest.TestCase):
    def setUp(self):
        self.recipe = next(r for r in CATALOG['experiments'] if r['id'] == 'placement-same')
        self.profiles = {arm: {'profile_id': arm, 'model_version': 'm', 'artifact_sha256': 'a'*64, 'engine_epoch': 'e'} for arm in ('a', 'b')}
        self.binding = {'recipe_sha256': recipe_digest(self.recipe), 'profiles': self.profiles}

    def test_resolver_returns_family_grants_lever_and_baseline_digest(self):
        resolver = RegisteredPairs(site_id='victoria', camera_ids=['den'], bindings={self.recipe['id']: self.binding},
            readiness=lambda *_: {'state': 'ready', 'recipe_sha256': self.binding['recipe_sha256'], 'profiles': self.profiles})
        result = resolver({'site_id': 'victoria', 'camera_id': 'den'}, self.recipe['id'])
        self.assertEqual(result['lever'], self.recipe['lever'])
        self.assertEqual(result['baseline_sha256'], policy.baseline_digest(CATALOG))
        self.assertEqual(result['source_policy']['ambient'], {'a': 'granted', 'b': 'granted'})
        self.assertEqual(result['source_policy']['ha'], {'a': 'granted', 'b': 'granted'})
        # Consistent with the switches the runtime already applies.
        policy.source_policy(result['source_policy'], ha=result['ha_evidence'], caption=result['caption_evidence'])

    def test_catalog_load_refuses_a_recipe_whose_arms_differ_beyond_its_lever(self):
        catalog = deepcopy(CATALOG)
        next(r for r in catalog['experiments'] if r['id'] == 'placement-same')['b']['sampling_hz'] = 4
        original = Path.read_text
        read = lambda path, *a, **k: json.dumps(catalog) if path.name == 'experiments.json' else original(path, *a, **k)
        with patch.object(Path, 'read_text', read), self.assertRaisesRegex(ValueError, 'differ at'):
            RegisteredPairs(site_id='victoria', camera_ids=['den'], bindings={}, readiness=lambda *_: None)


class ActivationRecordTests(unittest.TestCase):
    import test_paired_runtime as fixtures
    import test_service_runtime as composition
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown
    receipt = fixtures.PairedRuntimeTests.receipt
    runtime = composition.CompositionTests.runtime

    def test_activation_records_the_lever_baseline_digest_and_family_grants(self):
        recipe = next(r for r in CATALOG['experiments'] if r['id'] == self.session['experiment_id'])
        binding = {'recipe_sha256': recipe_digest(recipe), 'profiles': self.profiles}
        resolver = RegisteredPairs(site_id='victoria', camera_ids=['den'], bindings={recipe['id']: binding},
                                   readiness=lambda *_: {'state': 'ready', **binding})
        runtime, _ = self.runtime(resolver)
        try:
            self.assertEqual(runtime.start_session(self.session)['inference'], 'ready')
            self.runners.append(runtime.runners[self.session['id']])
            activated = next(e['payload'] for e in self.store.list_events(self.session['id']) if e['kind'] == 'experiment.activated')
            self.assertEqual(activated['lever'], recipe['lever'])
            self.assertEqual(activated['baseline_sha256'], policy.baseline_digest(CATALOG))
            self.assertEqual(activated['source_policy']['ambient'], {'a': 'granted', 'b': 'granted'})
            runner = runtime.runners[self.session['id']]
            self.assertEqual((runner.source_policy, runner.lever_families), (activated['source_policy'], []))
        finally:
            runtime.close()


class ActivationHistoryTests(unittest.TestCase):
    def test_recorded_source_policy_must_agree_with_the_switches(self):
        from experiment_history import history

        class Store:
            import threading
            lock = threading.Lock()

            def __init__(self, rows):
                self.db = self
                self.rows = rows

            def execute(self, *args):
                return self

            def fetchall(self):
                return [(json.dumps(r),) for r in self.rows]
        session = {'id': 's', 'site_id': 'victoria', 'camera_id': 'den', 'started_at': 0, 'ended_at': 10}
        change = {'site_id': 'victoria', 'camera_id': 'den', 'generation': 1, 'effective_at': 1,
                  'ha_evidence': {'a': True, 'b': True}, 'caption_evidence': {'a': False, 'b': False},
                  'source_policy': policy.source_policy(None, ha={'a': True, 'b': True})}
        self.assertEqual(history(Store([change]), session, [])[1], 'recorded_activation')
        change['source_policy'] = {**change['source_policy'], 'ha': {'a': 'granted', 'b': 'withheld_by_experiment'}}
        with self.assertRaises(ValueError):
            history(Store([change]), session, [])


if __name__ == '__main__':
    unittest.main()

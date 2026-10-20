"""The 2026-09-28 recipe revisions are explicit, documented and within live bounds."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from jev_provider import history_windows
from managed_detector_app import check_reasoning_windows
from profile_resolver import RegisteredPairs, recipe_digest

PATH = Path(__file__).with_name('experiments.json')
RECIPES = json.loads(PATH.read_text())['experiments']
BY_ID = {r['id']: r for r in RECIPES}


class RecipeRevisionTests(unittest.TestCase):
    def test_every_setup_is_named_described_and_revision_noted(self):
        self.assertEqual(len(RECIPES), 20)
        names = [r['setup_name'] for r in RECIPES]
        self.assertEqual(len(set(names)), 20)
        self.assertEqual(BY_ID['placement-same']['setup_name'], 'Detection · Jetson vs LA')
        self.assertEqual(BY_ID['placement-best']['setup_name'], 'Detection + pose · Jetson vs LA')
        self.assertIn('Despite its id', BY_ID['placement-best']['description'])
        for recipe in RECIPES:
            with self.subTest(recipe=recipe['id']):
                if recipe['id']=='apartment-viewing':
                    self.assertEqual(recipe['setup_name'],'Apartment live analysis')
                    self.assertTrue(recipe['description'].strip())
                    self.assertTrue(recipe['revision'].startswith('2026-10-18: explicit opt-in bounded Apartment viewing'))
                    continue
                self.assertRegex(recipe['setup_name'], r'^\S.* · .+ vs .+$')
                self.assertTrue(recipe['description'].strip())
                # vjepa-onoff was added after the 2026-09-28 catalog revision.
                self.assertTrue(recipe['revision'].startswith('2026-09-29: added' if recipe['id'] == 'vjepa-onoff' else '2026-09-28: '))
        # The file stays ASCII (JSON escapes), so every host decodes one digest.
        PATH.read_bytes().decode('ascii')

    def test_placement_semantics_are_unchanged_except_shared_ha(self):
        same, best = BY_ID['placement-same'], BY_ID['placement-best']
        for arm, device in (('a', 'jetson'), ('b', 'la')):
            self.assertEqual({k: same[arm][k] for k in ('detector', 'pose', 'device', 'sampling_hz', 'precision', 'history', 'reasoner')},
                             {'detector': 'yolo26s', 'pose': None, 'device': device, 'sampling_hz': 2, 'precision': 'fp32',
                              'history': 10, 'reasoner': None})
            self.assertEqual({k: best[arm][k] for k in ('detector', 'pose', 'device', 'sampling_hz', 'precision', 'history')},
                             {'detector': 'yolo26s', 'pose': 'rtmpose-m-body7-cpu', 'device': device, 'sampling_hz': 1,
                              'precision': 'fp32', 'history': 10})
            self.assertIs(same[arm]['ha_evidence'], True)
            self.assertIs(best[arm]['ha_evidence'], True)
        self.assertIn('ha_evidence false -> true', same['revision'])
        self.assertIn('semantics unchanged', best['revision'])

    def test_vjepa_onoff_is_placement_best_plus_the_temporal_encoder_grant_on_b(self):
        from evidence_policy import lever_families, validate_catalog, validate_recipe
        best, vjepa = BY_ID['placement-best'], BY_ID['vjepa-onoff']
        self.assertEqual(vjepa['a'], best['a'])
        self.assertEqual({k: v for k, v in vjepa['b'].items() if k != 'source_policy'}, best['b'])
        self.assertEqual(vjepa['b']['source_policy'], {'temporal_encoder': True})
        grants = validate_recipe(vjepa, validate_catalog(json.loads(PATH.read_text())))
        self.assertEqual(grants['temporal_encoder'], {'a': 'withheld_by_experiment', 'b': 'granted'})
        # The one evidence family varied; the placement paths are placement-best's.
        self.assertEqual(lever_families(vjepa['lever']), ['temporal_encoder'])
        self.assertEqual(set(vjepa['lever']['paths']) - set(best['lever']['paths']), {'source_policy.temporal_encoder'})
        for family, arms in grants.items():
            if family != 'temporal_encoder':
                self.assertEqual(arms['a'], arms['b'], family)

    def test_precision_history_and_caption_revisions(self):
        for recipe in RECIPES:
            for arm in ('a', 'b'):
                with self.subTest(recipe=recipe['id'], arm=arm):
                    self.assertEqual(recipe[arm]['precision'], 'fp32')
                    self.assertIs(recipe[arm]['caption_evidence'], recipe['id'] == 'qwen-jev' and arm == 'b')
            if recipe['id'] not in {'placement-same', 'placement-best', 'vjepa-onoff', 'apartment-viewing'}:
                self.assertIn('precision fp16 -> fp32', recipe['revision'])
        self.assertEqual((BY_ID['history']['a']['history'], BY_ID['history']['b']['history']), (0, 10))
        self.assertIn('20 s -> 10 s', BY_ID['history']['revision'])

    def test_every_recipe_fits_the_live_jev_history_bound_and_longer_windows_still_fail(self):
        check_reasoning_windows(RECIPES, {r['id'] for r in RECIPES}, SimpleNamespace(check_windows=history_windows))
        longer = deepcopy(BY_ID['history'])
        longer['b']['history'] = 20
        with self.assertRaisesRegex(ValueError, r'Recipe history cannot run live JEV: .*b=20s is outside the 0-10s'):
            check_reasoning_windows([longer], {'history'}, SimpleNamespace(check_windows=history_windows))

    def test_a_binding_reviewed_against_the_prior_recipe_is_refused(self):
        prior = deepcopy(BY_ID['placement-same'])
        for key in ('setup_name', 'description', 'revision'):
            del prior[key]
        for arm in ('a', 'b'):
            del prior[arm]['caption_evidence']
            prior[arm]['ha_evidence'] = False
        profiles = {arm: {'profile_id': arm} for arm in ('a', 'b')}
        with self.assertRaisesRegex(ValueError, 'recipe revision differs'):
            RegisteredPairs(site_id='victoria', camera_ids=['den'], readiness=lambda *_: None,
                            bindings={'placement-same': {'recipe_sha256': recipe_digest(prior), 'profiles': profiles}})
        RegisteredPairs(site_id='victoria', camera_ids=['den'], readiness=lambda *_: None,
                        bindings={'placement-same': {'recipe_sha256': recipe_digest(BY_ID['placement-same']), 'profiles': profiles}})


if __name__ == '__main__':
    unittest.main()

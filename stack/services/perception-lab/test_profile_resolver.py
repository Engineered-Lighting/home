import json
from pathlib import Path
import unittest
from profile_resolver import RegisteredPairs, recipe_digest


class RegisteredPairTests(unittest.TestCase):
    def setUp(self):
        self.recipe=json.loads(Path(__file__).with_name("experiments.json").read_text())["experiments"][0]
        self.profiles={arm:{"profile_id":arm,"model_version":"fixture","artifact_sha256":"a"*64,"engine_epoch":"test"} for arm in ("a","b")}
        self.binding={"recipe_sha256":recipe_digest(self.recipe),"profiles":self.profiles}
        self.session={"site_id":"victoria","camera_id":"den"}

    def resolver(self,callback,binding=None):
        return RegisteredPairs(site_id="victoria",camera_ids=["den"],
            bindings={self.recipe["id"]:binding or self.binding},readiness=callback)

    def test_exact_recipe_and_pair_reach_readonly_check_without_mutating_binding(self):
        def check(session,recipe,profiles):
            self.assertEqual(recipe,self.recipe)
            return {"state":"ready","recipe_sha256":recipe_digest(recipe),"profiles":profiles}
        resolver=self.resolver(check)
        result=resolver(self.session,self.recipe["id"])
        self.assertEqual(result["profiles"],self.profiles)
        result["profiles"]["a"]["engine_epoch"]="changed"
        self.assertEqual(resolver(self.session,self.recipe["id"])["profiles"],self.profiles)

    def test_wrong_source_unbound_recipe_and_revision_never_inspect_workers(self):
        def forbidden(*args):self.fail("unexpected worker inspection")
        resolver=self.resolver(forbidden)
        self.assertEqual(resolver({**self.session,"site_id":"echo"},self.recipe["id"])["reason"],"pair_source_not_provisioned")
        self.assertEqual(resolver(self.session,"not-registered")["reason"],"unknown_experiment")
        other=next(key for key in resolver.recipes if key!=self.recipe["id"])
        self.assertEqual(resolver(self.session,other)["reason"],"experiment_pair_not_provisioned")
        with self.assertRaises(ValueError):self.resolver(forbidden,{**self.binding,"recipe_sha256":"f"*64})

    def test_stale_epoch_or_unconfirmed_dependencies_block_pair(self):
        for callback,reason in (
            (lambda *_:{"state":"blocked"},"pair_requirements_unmet"),
            (lambda *_:{"state":"ready","recipe_sha256":"a"*64,"profiles":self.profiles},"pair_readiness_binding_changed"),
            (lambda *_:{"state":"ready",**self.binding,"profiles":{}},"pair_readiness_binding_changed")):
            self.assertEqual(self.resolver(callback)(self.session,self.recipe["id"])["reason"],reason)

    def test_caption_evidence_comes_from_the_recipe_and_is_withheld_when_absent(self):
        resolver=self.resolver(lambda *_:{"state":"ready","recipe_sha256":self.binding["recipe_sha256"],"profiles":self.profiles})
        recipe=resolver.recipes[self.recipe["id"]]
        recipe["a"]["caption_evidence"],recipe["b"]["caption_evidence"]=False,True
        self.assertEqual(resolver(self.session,self.recipe["id"])["caption_evidence"],{"a":False,"b":True})
        for arm in ("a","b"):recipe[arm].pop("caption_evidence")
        self.assertEqual(resolver(self.session,self.recipe["id"])["caption_evidence"],{"a":False,"b":False})

    def test_catalog_never_calls_readiness_or_claims_loaded_pair_is_ready(self):
        from service_runtime import ServiceRuntime
        def forbidden(*args):self.fail("catalog inspected live workers")
        resolver=self.resolver(forbidden)
        runtime=ServiceRuntime.__new__(ServiceRuntime)
        runtime.recorders={};runtime.closed=False;runtime.resolve_profiles=resolver;runtime.recording_only=False
        catalog=runtime.capabilities()["experiments"]
        self.assertEqual(len(catalog),19)
        bound=next(row for row in catalog if row["id"]==self.recipe["id"])
        self.assertEqual(bound["status"],"unverified")
        self.assertEqual(bound["readiness_reason"],"pair_requires_runtime_check")
        self.assertTrue(all(row["status"]=="blocked" for row in catalog if row["id"]!=self.recipe["id"]))
        bound["a"]["detector"]="changed"
        self.assertEqual(resolver.recipes[self.recipe["id"]],self.recipe)


if __name__=="__main__":unittest.main()

"""Explicit recipe-to-worker bindings; inspection never prepares or loads models.

The provisioned readiness callback is read-only. Execution still needs admission
at dispatch; a ready status is not a reservation or authorization lease.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def recipe_digest(recipe):
    return hashlib.sha256(json.dumps(recipe,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


class RegisteredPairs:
    def __init__(self, *, site_id, camera_ids, bindings, readiness):
        if not callable(readiness):raise ValueError("read-only readiness resolver required")
        self.site_id,self.camera_ids=site_id,frozenset(camera_ids)
        self.bindings=deepcopy(bindings)
        self.readiness=readiness
        catalog=json.loads(Path(__file__).with_name("experiments.json").read_text(encoding="utf-8"))
        from evidence_policy import validate_catalog, baseline_digest
        # Every recipe must differ between arms only at its declared lever.
        self.defaults=validate_catalog(catalog)
        self.baseline_sha256=baseline_digest(catalog)
        self.recipes={r["id"]:r for r in catalog["experiments"]}
        for key,binding in self.bindings.items():
            if key not in self.recipes or set(binding)!={"recipe_sha256","profiles"}:
                raise ValueError("known recipe and exact pair binding required")
            if binding["recipe_sha256"]!=recipe_digest(self.recipes[key]):
                raise ValueError("recipe revision differs from reviewed worker binding")
            if set(binding["profiles"])!={"a","b"}:
                raise ValueError("two explicitly provisioned profiles required")

    def catalog(self):
        """Static provisioning facts only: no readiness callback or model work."""
        return [{**deepcopy(recipe),"status":"unverified" if key in self.bindings else "blocked",
                 "readiness_reason":"pair_requires_runtime_check" if key in self.bindings else "experiment_pair_not_provisioned"}
                for key,recipe in self.recipes.items()]

    def __call__(self, session, experiment):
        if session.get("site_id")!=self.site_id or session.get("camera_id") not in self.camera_ids:
            return {"state":"blocked","reason":"pair_source_not_provisioned"}
        if experiment not in self.recipes:
            return {"state":"blocked","reason":"unknown_experiment"}
        if experiment not in self.bindings:
            return {"state":"blocked","reason":"experiment_pair_not_provisioned"}
        binding=deepcopy(self.bindings[experiment])
        # The callback must inspect the complete recipe (HA, maps, reasoning,
        # precision, sampling), not just check whether model files are present.
        try:
            status=self.readiness(deepcopy(session),deepcopy(self.recipes[experiment]),deepcopy(binding["profiles"]))
        except Exception:
            return {"state":"blocked","reason":"pair_readiness_unavailable"}
        if not isinstance(status,dict) or status.get("state")!="ready":
            return {"state":"blocked","reason":"pair_requirements_unmet"}
        if status.get("recipe_sha256")!=binding["recipe_sha256"] or status.get("profiles")!=binding["profiles"]:
            return {"state":"blocked","reason":"pair_readiness_binding_changed"}
        from evidence_policy import recipe_grants
        recipe=self.recipes[experiment]
        return {"state":"ready","profiles":binding["profiles"],"recipe_sha256":binding["recipe_sha256"],
                # Reviewed with the recipe digest (the lever) and recorded with
                # the activation (the baseline digest and resolved family grants).
                "source_policy":recipe_grants(recipe,self.defaults),"lever":deepcopy(recipe["lever"]),
                "baseline_sha256":self.baseline_sha256,
                "ha_evidence":{arm:self.recipes[experiment][arm].get("ha_evidence",True) for arm in ("a","b")},
                # Absent means withheld: captions never silently enter a baseline.
                "caption_evidence":{arm:self.recipes[experiment][arm].get("caption_evidence",False) for arm in ("a","b")},
                "reasoning_windows":{arm:self.recipes[experiment][arm]["history"] for arm in ("a","b")},
                "sampling_hz":{arm:self.recipes[experiment][arm]["sampling_hz"] for arm in ("a","b")},
                "spatial_policy":{arm:{"localization":self.recipes[experiment][arm]["localization"],
                    "room_relations":self.recipes[experiment][arm]["room_map"]} for arm in ("a","b")}}

"""Fictional public-path profile/scene fixtures; no household data or services."""
import copy
import hashlib

from journal import digest
from live_test_helpers import MANIFEST, METHOD, PROFILE, SCOPE, person, reference
from live_worker_test_helpers import describe, worker_fixture

HUMAN_TOKEN = "synthetic-scene-owner-token"
HUMAN_ACTOR = "synthetic-scene-owner"


def scene_worker_fixture():
    service, grants, config = worker_fixture()
    examples, profiles = [], []
    for name, level, kelvin in (("anonymous", .4, 3000), ("owner", .7, 3500)):
        example = {"schema": "home-approved-lighting-example-1", "version": 1, "id": "synthetic-" + name + "-example",
                   "approved": True, "profile_id": "synthetic-" + name + "-profile",
                   "nominal": {"brightness_fraction": level, "cct_kelvin": kelvin,
                               "fixture_family_ref": reference("synthetic-fixture-family")},
                   "requirements_ref": reference("synthetic-task-requirements")}
        profile = {"schema": "home-approved-profile-1", "version": 1, "id": example["profile_id"], "approved": True,
                   "example_refs": [{"id": example["id"], "digest": digest(example)}]}
        examples.append(example)
        profiles.append(profile)
    baseline = {"profile_ref": {"id": profiles[0]["id"], "digest": digest(profiles[0])},
                "example_ref": {"id": examples[0]["id"], "digest": digest(examples[0])}}
    scene_policy = {"source_gap_hold_s": 10, "catalog": {"profiles": profiles, "examples": examples,
                    "anonymous_baseline": baseline,
                    "background": {"approval_ref": reference("synthetic-background-approval"),
                                   "requirements_ref": reference("synthetic-background-requirements"),
                                   "nominal": {"brightness_fraction": .2, "cct_kelvin": 3000,
                                               "fixture_family_ref": reference("synthetic-fixture-family")},
                                   "fade_in_s": 1, "methods": [copy.deepcopy(METHOD)]}}}
    config["scene_policy"] = scene_policy
    description = describe(config)
    manifest = service["manifest"]
    manifest.update(schema="home-live-ingress-manifest-3", version=3,
                    scene_interface_digest=description["scene_interface_digest"])
    manifest["worker"].update(config_digest=description["config_digest"], scene_policy=scene_policy,
                              scene_schema_pins=description["scene_schema_pins"])
    service.update(schema="home-live-ingress-config-3", version=3)
    service["manifest_ref"] = {"id": "synthetic-scene-manifest", "digest": digest(manifest)}
    MANIFEST.update(service["manifest_ref"])
    config.update(manifest_ref=copy.deepcopy(service["manifest_ref"]), live_manifest=copy.deepcopy(manifest))
    from schema_registry import LIVE_SCENE_VERSIONS, schema_digest
    for grant in grants:
        for binding in grant["live_bindings"]:
            binding["manifest_ref"] = copy.deepcopy(service["manifest_ref"])
        if "worker_commit" in grant["operations"]:
            grant["payload_types"] += list(LIVE_SCENE_VERSIONS)
            grant["revisions"].append("1.0")
            grant["schema_pins"] = {name: schema_digest(name, profile=PROFILE) for name in grant["payload_types"]}
    reader_types = ["home-live-fact-1", "home-live-geometry-1", "home-live-engagement-state-1",
                    "home-live-shadow-decision-2", "home-live-intent-event-1"]
    human = {"principal_id": HUMAN_ACTOR, "producer_id": HUMAN_ACTOR,
             "token_sha256": hashlib.sha256(HUMAN_TOKEN.encode()).hexdigest(),
             "expected_actor_ref": HUMAN_ACTOR, "scopes": [{"site_id": SCOPE["site_id"], "room_id": SCOPE["room_id"]}],
             "operations": ["read", "intent"], "payload_types": reader_types, "revisions": ["synthetic-r1", "1.0"],
             "source_ids": list(config["source_pins"]), "sensor_roots": [],
             "schema_pins": {name: schema_digest(name, profile=PROFILE) for name in reader_types},
             "live_bindings": [{"manifest_ref": copy.deepcopy(service["manifest_ref"]), "source_id": name,
                                "capabilities": ["read"]} for name in config["source_pins"]]}
    grants.append(human)
    return service, grants, config


def intent(event_id, person_ref=None, kind="bind_profile", at=1800000000.2, value=None):
    # Full immutable owner example records have stable literal configuration.
    example = {"schema": "home-approved-lighting-example-1", "version": 1, "id": "synthetic-owner-example", "approved": True,
               "profile_id": "synthetic-owner-profile", "nominal": {"brightness_fraction": .7, "cct_kelvin": 3500,
               "fixture_family_ref": reference("synthetic-fixture-family")}, "requirements_ref": reference("synthetic-task-requirements")}
    profile = {"schema": "home-approved-profile-1", "version": 1, "id": "synthetic-owner-profile", "approved": True,
               "example_refs": [{"id": example["id"], "digest": digest(example)}]}
    selected = {"profile_ref": {"id": profile["id"], "digest": digest(profile)},
                "example_ref": {"id": example["id"], "digest": digest(example)}}
    return {"schema": "home-live-intent-event-1", "version": 1, "id": event_id, **SCOPE,
            "manifest_ref": copy.deepcopy(MANIFEST), "actor_ref": HUMAN_ACTOR,
            "origin": "human_declaration", "truth_role": "runtime_intent", "kind": kind,
            "target": copy.deepcopy(person_ref or person()), "set_at": at, "available_at": at,
            "valid_until": at + 900, "value": (selected if value is None else value) if kind == "bind_profile" else None,
            "reply_to": None}

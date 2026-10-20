"""Explicit, additive profile/scene authority for live commissioning version three.

The service authenticates human intent, preserves its receipt and independently
checks the worker's attributable settlement. It acquires no identity or lighting.
"""
from __future__ import annotations

import json

from journal import JournalError, bounded_json, canonical, digest, finite, identifier
from live_ingress_validation import _array, _exact, _nonnegative, _person, _ref, _refs, SCOPE_KEYS
from live_worker import (INPUTS, MODULES, OUTPUTS, POLICY_KEYS, SCHEMAS, LiveWorkerBoundary,
                         reject, validate_worker_grant, validate_worker_policy)


INTENT = "home-live-intent-event-1"
CORRECTION_INTENT = "home-live-intent-event-2"
SCENE_STATE_VERSION = "home-live-scene-state-1.0.0"
CORRECTION_STATE_VERSION = "home-live-scene-state-2.0.0"
SCENE_OUTPUTS = frozenset({"home-live-engagement-state-1", "home-live-shadow-decision-2"})
SCENE_SCHEMAS = SCENE_OUTPUTS | {INTENT, "home-live-worker-checkpoint-2", "home-live-worker-commit-2", "home-live-input-cut-2"}


def nominal(value):
    _exact(value, {"brightness_fraction", "cct_kelvin", "fixture_family_ref"})
    if (not finite(value["brightness_fraction"]) or not 0 <= value["brightness_fraction"] <= 1
            or not finite(value["cct_kelvin"]) or not 1500 <= value["cct_kelvin"] <= 6500):
        raise ValueError("bounded nominal command units required")
    _ref(value["fixture_family_ref"])


def validate_scene_policy(policy, methods):
    _exact(policy, {"catalog", "source_gap_hold_s"})
    if policy["source_gap_hold_s"] != 10:
        raise ValueError("retain the commissioned ten-second source-gap hold")
    catalog = policy["catalog"]
    _exact(catalog, {"profiles", "examples", "anonymous_baseline", "background"})
    for name in ("profiles", "examples"):
        _array(catalog[name], 16 if name == "profiles" else 32, nonempty=True)
        if len({record.get("id") for record in catalog[name]}) != len(catalog[name]):
            raise ValueError("immutable catalog ids must be unique")
    for profile in catalog["profiles"]:
        _exact(profile, {"schema", "version", "id", "approved", "example_refs"})
        if profile["schema"] != "home-approved-profile-1" or type(profile["version"]) is not int or profile["version"] != 1 or profile["approved"] is not True:
            raise ValueError("explicitly approved profile record required")
        identifier(profile["id"], "profile_id")
        _refs(profile["example_refs"], nonempty=True)
        _array(profile["example_refs"], 16, nonempty=True)
    for example in catalog["examples"]:
        _exact(example, {"schema", "version", "id", "approved", "profile_id", "nominal", "requirements_ref"})
        if example["schema"] != "home-approved-lighting-example-1" or type(example["version"]) is not int or example["version"] != 1 or example["approved"] is not True:
            raise ValueError("explicitly approved lighting example required")
        identifier(example["id"], "example_id")
        identifier(example["profile_id"], "profile_id")
        nominal(example["nominal"])
        _ref(example["requirements_ref"])
        profiles = [profile for profile in catalog["profiles"] if profile["id"] == example["profile_id"]]
        if len(profiles) != 1 or {"id": example["id"], "digest": digest(example)} not in profiles[0]["example_refs"]:
            raise ValueError("example digest must belong to its approved profile")
    for profile in catalog["profiles"]:
        if any(not any(ref == {"id": example["id"], "digest": digest(example)} and example["profile_id"] == profile["id"]
                       for example in catalog["examples"]) for ref in profile["example_refs"]):
            raise ValueError("every approved profile example must resolve exactly")
    _exact(catalog["anonymous_baseline"], {"profile_ref", "example_ref"})
    resolve_selection(catalog, **catalog["anonymous_baseline"])
    background = catalog["background"]
    _exact(background, {"approval_ref", "requirements_ref", "nominal", "fade_in_s", "methods"})
    _ref(background["approval_ref"])
    _ref(background["requirements_ref"])
    nominal(background["nominal"])
    _nonnegative(background["fade_in_s"], positive=True)
    if background["fade_in_s"] > 30:
        raise ValueError("bounded smooth background transition required")
    _refs(background["methods"], nonempty=True)
    _array(background["methods"], 16, nonempty=True)
    if any(method not in methods for method in background["methods"]):
        raise ValueError("presence method must resolve to a commissioned source")
    bounded_json(policy, 65536)


def resolve_selection(catalog, profile_ref, example_ref):
    _ref(profile_ref)
    _ref(example_ref)
    profiles = [record for record in catalog["profiles"] if profile_ref == {"id": record["id"], "digest": digest(record)}]
    examples = [record for record in catalog["examples"] if example_ref == {"id": record["id"], "digest": digest(record)}]
    if len(profiles) != 1 or len(examples) != 1 or example_ref not in profiles[0]["example_refs"] or examples[0]["profile_id"] != profiles[0]["id"]:
        raise JournalError("unapproved_profile_example", "profile and example must resolve to exact approved immutable records", 409)
    return profiles[0], examples[0]


def validate_scene_worker_policy(manifest, registry):
    if not registry.scene_interface_digest or manifest["scene_interface_digest"] != registry.scene_interface_digest:
        raise ValueError("exact additive scene interface pin required")
    policy = manifest["worker"]
    _exact(policy, POLICY_KEYS | {"scene_policy", "scene_schema_pins"})
    base = dict(manifest)
    base["worker"] = {key: policy[key] for key in POLICY_KEYS}
    validate_worker_policy(base, registry)
    if policy["scene_schema_pins"] != registry.scene_schema_pins:
        raise ValueError("exact scene schema pins required")
    validate_scene_policy(policy["scene_policy"], [method for source in manifest["sources"].values() for method in source["method_refs"]])


def validate_scene_grant(grant, config):
    private = SCENE_SCHEMAS - SCENE_OUTPUTS - {INTENT}
    if grant.payload_types & private and "worker_commit" not in grant.operations:
        raise ValueError("private scene state requires the commissioned worker capability")
    if "home-live-shadow-decision-2" in grant.payload_types and "home-live-geometry-1" not in grant.payload_types:
        raise ValueError("complete scene decisions need separate geometry authority")
    if "intent" in grant.operations:
        if (grant.operations != frozenset({"read", "intent"}) or grant.expected_actor_ref is None
                or INTENT not in grant.payload_types or "1.0" not in grant.revisions
                or grant.expected_worker_id is not None
                or any(set(json.loads(binding)["capabilities"]) != {"read"} for binding in grant.live_bindings)):
            raise ValueError("human scene intents require an independent attributed read/intent grant")


def validate_scene_worker_grant(grant, config):
    # Retain every original private-worker check and add the new exact closure.
    validate_worker_grant(grant, config)
    if not SCENE_SCHEMAS <= grant.payload_types or "1.0" not in grant.revisions:
        raise ValueError("scene worker requires the complete explicit scene contract closure")


class LiveSceneWorkerBoundary(LiveWorkerBoundary):
    schema_types = SCHEMAS | SCENE_SCHEMAS
    output_types = SCENE_OUTPUTS
    input_types = INPUTS | {INTENT}
    decision_type = "home-live-shadow-decision-2"
    checkpoint_type = "home-live-worker-checkpoint-2"
    commit_type = "home-live-worker-commit-2"
    cut_type = "home-live-input-cut-2"
    version = 2
    module_names = MODULES | {"app/src/home-live-scene-state.js"}
    max_input_receipts = 256
    scene_state_version = SCENE_STATE_VERSION
    supports_brightness = False
    intent_types = frozenset({INTENT})
    external_intent_kinds = frozenset()

    def __init__(self, ingress):
        super().__init__(ingress)
        self.scene_policy = self.policy["scene_policy"]
        self.j.db.executescript("""
            CREATE TABLE IF NOT EXISTS live_scene_intents(
                cursor INTEGER PRIMARY KEY,producer_id TEXT NOT NULL,event_id TEXT NOT NULL,
                actor_ref TEXT NOT NULL,event TEXT NOT NULL,publication TEXT NOT NULL,application_result TEXT,settled_at REAL,
                UNIQUE(producer_id,event_id));
        """)

    def configuration_description(self, nested):
        description = super().configuration_description(nested)
        description.update(version="home-live-context-worker-2.0.0", scene_policy=self.scene_policy,
                           scene_schema_pins=self.policy["scene_schema_pins"], scene_interface_digest=self.j.schemas.scene_interface_digest)
        return description

    def _checkpoint(self, cp, commit, next_seq, old, now):
        if cp["scene_schema_pins"] != self.policy["scene_schema_pins"]:
            reject("checkpoint_conflict", "scene checkpoint pins differ from the commissioned stage")
        return super()._checkpoint(cp, commit, next_seq, old, now)

    def catalog(self, grant, site, room):
        self.ingress._read_scope(grant, site, room)
        if INTENT not in grant.payload_types and self.decision_type not in grant.payload_types:
            reject("forbidden", "scene catalog requires independently granted scene or intent reads", 403)
        return {"schema": "home-live-scene-catalog-1", "version": 1,
                "manifest_ref": self.ingress.config["manifest_ref"], "catalog": self.scene_policy["catalog"]}

    def intent_readable(self, publication, revision, grant):
        return (publication["payload_type"] in self.intent_types and publication["payload_type"] in grant.payload_types and revision in grant.revisions
                and ("worker_commit" in grant.operations or publication["payload"]["actor_ref"] == grant.expected_actor_ref))

    def _check_intent_capacity(self):
        if self.j.db.execute("SELECT count(*) FROM live_scene_intents").fetchone()[0] >= 128:
            reject("journal_full", "durable intent budget exhausted; explicit new session required", 507)

    def intent(self, payload, grant):
        scope = self.ingress.identity
        intent_type = payload.get("schema") if isinstance(payload, dict) else None
        revision = "2.0" if intent_type == CORRECTION_INTENT else "1.0"
        grant.require("intent", payload.get("site_id") if isinstance(payload, dict) else None,
                      payload.get("room_id") if isinstance(payload, dict) else None, payload_type=intent_type, revision=revision)
        if (not isinstance(payload, dict) or intent_type not in self.intent_types or grant.expected_actor_ref is None
                or payload.get("actor_ref") != grant.expected_actor_ref or payload.get("manifest_ref") != self.ingress.config["manifest_ref"]
                or any(payload.get(key) != scope[key] for key in SCOPE_KEYS)):
            reject("forbidden", "intent requires the exact authenticated actor and current live manifest scope", 403)
        self.j.schemas.validate(payload)
        if intent_type == CORRECTION_INTENT:
            if not self.supports_brightness or payload["truth_role"] != "runtime_intent" or payload["reply_to"] is not None:
                reject("invalid_intent", "successor correction capability is not commissioned", 400)
            if payload["kind"] == "set_brightness":
                if payload["origin"] != "human_preference":
                    reject("invalid_intent", "brightness requires an attributed human preference", 400)
                _person(payload["target"], scope)
                self._brightness_value(payload["value"])
            elif payload["kind"] in self.external_intent_kinds:
                if payload["kind"] != "clear_person_hazards" or payload["origin"] != "human_declaration" or payload["target"] is not None:
                    reject("invalid_intent", "unsupported external intent variant", 400)
            else:
                reject("invalid_intent", "unsupported successor intent kind", 400)
        elif (payload["kind"] not in {"bind_profile", "unbind_profile"} or payload["origin"] not in {"human_declaration", "human_preference"}
                or payload["truth_role"] != "runtime_intent" or payload["reply_to"] is not None):
            reject("invalid_intent", "only attributed profile binding variants are implemented", 400)
        if intent_type == INTENT:
            _person(payload["target"], scope)
            if payload["kind"] == "bind_profile":
                resolve_selection(self.scene_policy["catalog"], **payload["value"])
        def accept():
            old = self.j.db.execute("SELECT event,publication FROM live_scene_intents WHERE producer_id=? AND event_id=?",
                                    (grant.producer_id, payload["id"])).fetchone()
            if old is not None:
                saved = json.loads(old["event"])
                if saved["payload"] != payload:
                    reject("identity_conflict", "intent retry changed its immutable payload")
                return self.j._receipt(saved)
            if self.j.db.execute("SELECT 1 FROM live_scene_intents WHERE event_id=?", (payload["id"],)).fetchone():
                reject("intent_id_conflict", "intent id is already reserved in this room session")
            if not 0 <= payload["set_at"] <= payload["available_at"] <= self.j.clock() < payload["valid_until"] or payload["valid_until"] - payload["set_at"] > 86400:
                reject("invalid_clock", "intent needs actual causal availability and bounded exclusive validity", 400)
            self._check_intent_capacity()
            event = self.j._command_event(payload, grant)
            if self.j._revoked(scope["site_id"], scope["room_id"], grant.producer_id, revision):
                reject("revoked", "intent origin or room authority has been revoked")
            receipt, publication = self.ingress._record(event)
            self.j.db.execute("INSERT INTO live_scene_intents VALUES(?,?,?,?,?,?,NULL,NULL)",
                              (receipt["cursor"], grant.producer_id, payload["id"], payload["actor_ref"], canonical(event), canonical(publication)))
            return receipt
        return self.j._transaction(accept)

    def lookup_intent(self, event_id, grant, site, room):
        self.ingress._read_scope(grant, site, room)
        grant.require("read", site, room, payload_type=INTENT, revision="1.0")
        identifier(event_id, "event_id")
        with self.j.lock:
            rows = self.j.db.execute("SELECT * FROM live_scene_intents WHERE event_id=?", (event_id,)).fetchall()
            matches = [row for row in rows if "worker_commit" in grant.operations or
                       row["actor_ref"] == grant.expected_actor_ref and row["producer_id"] == grant.producer_id]
            if len(matches) != 1:
                reject("not_found", "unique intent not found in the authenticated actor scope", 404)
            row = matches[0]
            original = json.loads(row["event"])
            grant.require("read", site, room, payload_type=original["payload_type"], revision=original["revision"])
            result = {"event": original["payload"], "receipt": self.j._receipt(original)}
            if row["application_result"] is not None:
                result["application_result"] = json.loads(row["application_result"])
            return result

    def additional_inputs(self, grant):
        for kind in self.intent_types:
            grant.require("read", self.ingress.identity["site_id"], self.ingress.identity["room_id"], payload_type=kind,
                          revision="2.0" if kind == CORRECTION_INTENT else "1.0")
        return [json.loads(row[0]) for row in self.j.db.execute("SELECT publication FROM live_scene_intents WHERE application_result IS NULL ORDER BY cursor")]

    def current_people(self, publications, t, old=None):
        people, ambiguous = {}, set()
        for publication in publications.values():
            payload = publication["payload"]
            if publication["payload_type"] not in INPUTS or not self.ingress._current_payload(payload, t):
                continue
            if self.ingress.config["version"] in {7, 9, 10, 11, 12, 13}:
                head=self.j.db.execute("SELECT body FROM live_heads WHERE cursor=?",(publication["cursor"],)).fetchone()
                source=self.j.db.execute("SELECT generation FROM live_sources WHERE source_id=?",(payload["source_id"],)).fetchone()
                if head is None or head["body"] is None or source is None or source["generation"] != payload["source_generation"]:
                    continue
                from live_lifecycle import commissioned_method
                absent=any(p["payload_type"] == "home-live-person-lifecycle-1"
                    and p["payload"]["target_person_ref"] == payload["person_ref"]
                    and p["payload"]["disposition"] in {"absence_qualified","departure_qualified"}
                    and commissioned_method(p["payload"], self.policy["lifecycle_policy"]) is not None
                    and p["payload"]["observed_at"] > payload["observed_at"]
                    and self.ingress._current_payload(p["payload"],t)
                    for p in publications.values())
                if absent:
                    continue
            association = payload["association"]
            for person in association["candidate_person_refs"]:
                ambiguous.add(canonical(person))
            if (payload["status"] == "supplied" and association["person_status"] == "qualified"
                    and not (payload.get("kind") == "person_presence" and payload.get("value") == "observed_absent")
                    and not (payload.get("kind") == "negative_engagement" and payload.get("value") == "observed_departure")):
                key = canonical(payload["person_ref"])
                people.setdefault(key, set()).add(payload["source_id"])
        if self.supports_brightness and old:
            for selection in old["state"]["scene_and_corrections"]["state"]["selections"]:
                gap = selection["gap_since"] if selection["gap_since"] is not None else min(t, selection["scene"]["valid_until"])
                for p, until in self._continuity_proofs(publications, selection["scene"], t, gap):
                    people.setdefault(canonical(selection["scene"]["person_ref"]), set()).add(p["payload"]["source_id"])
        if self.supports_brightness:
            for p in publications.values():
                l = p["payload"]
                if p["payload_type"] == "home-live-person-lifecycle-1" and l["disposition"] == "association_reset" and max(l["available_at"], p["committed_at"]) <= t < l["valid_until"]:
                    ambiguous.update(people if l["target_person_ref"] is None else [canonical(l["target_person_ref"])])
        return {key: value for key, value in people.items() if key not in ambiguous}

    def settle(self, commit, grant, publications, decision, engagements, old):
        cp, t = commit["checkpoint"], decision["t"]
        snapshot = cp["state"]["scene_and_corrections"]
        scene_state = snapshot["state"]
        if (snapshot["identity"] != self.ingress.identity or scene_state["t"] != t or scene_state["seq"] != cp["state"]["semantic_seq"]
                or snapshot["version"] != self.scene_state_version
                or snapshot["config_digest"] != digest({"version": self.scene_state_version, "identity": self.ingress.identity,
                                                        "scene_policy": self.scene_policy, "max_output_age_s": self.policy["max_output_age_s"],
                                                        **({"lifecycle_policy": self.policy.get("lifecycle_policy")} if self.supports_brightness else {})})
                or scene_state["authority_generation"] != cp["authority_generation"]
                or scene_state["source_generations"] != cp["state"]["engagement"]["source_generations"]):
            reject("checkpoint_conflict", "scene state must match its semantic clock, scope and authority")
        people = self.current_people(publications, t, old=old)
        results = decision["intent_results"]
        if results != cp["state"]["intent_settlements"]:
            reject("invalid_application_result", "decision and checkpoint must expose the same terminal settlements")
        if self.supports_brightness:
            self._validate_scene_continuity(commit, publications, decision, engagements, old)
            self._validate_corrections(commit, publications, decision, engagements, old, people)
        reported_ids = set()
        for result in results:
            if result["id"] in reported_ids or result["decision_ref"] != decision["id"]:
                reject("invalid_application_result", "one result must reference its enclosing committed decision")
            reported_ids.add(result["id"])
            inputs = [p for p in publications.values() if p["payload_type"] in self.intent_types and p["id"] == result["id"]]
            if len(inputs) != 1:
                reject("invalid_application_result", "intent settlement requires one actually delivered causal intent")
            p = inputs[0]
            row = self.j.db.execute("SELECT * FROM live_scene_intents WHERE cursor=?", (p["cursor"],)).fetchone()
            if row is None or json.loads(row["publication"]) != p:
                reject("invalid_application_result", "intent settlement must preserve its original durable publication")
            intent = p["payload"]
            external = intent["schema"] == CORRECTION_INTENT and intent["kind"] in self.external_intent_kinds
            if external:
                self.validate_external_intent_result(intent, result, commit=commit, publications=publications,
                    decision=decision, engagements=engagements, old=old)
            if not external and result["status"] == "applied" and (canonical(intent["target"]) not in people
                    or not max(intent["available_at"], p["committed_at"]) <= t < intent["valid_until"]):
                reject("invalid_application_result", "applied intent requires a current qualified person and actual available validity")
            terminal = {"schema": "home-intent-application-result-1", "version": 1, **result}
            if row["application_result"] is not None and json.loads(row["application_result"]) != terminal:
                reject("application_result_conflict", "terminal intent result and original decision are immutable")
            self.j.db.execute("UPDATE live_scene_intents SET application_result=?,settled_at=? WHERE cursor=?", (canonical(terminal), t, p["cursor"]))
        unsettled = {p["id"] for p in publications.values() if p["payload_type"] in self.intent_types}
        if unsettled != reported_ids:
            reject("invalid_application_result", "every delivered pending intent requires an explicit terminal result")
        applied_by_person = {}
        for result in results:
            if result["status"] == "applied":
                declaration = next((p["payload"] for p in publications.values() if p["payload_type"] == INTENT and p["id"] == result["id"]), None)
                if declaration is None:
                    continue
                applied_by_person[canonical(declaration["target"])] = declaration
        for person, declaration in applied_by_person.items():
            bindings = [binding for binding in scene_state["bindings"] if canonical(binding["person_ref"]) == person]
            if (declaration["kind"] == "bind_profile" and (len(bindings) != 1 or bindings[0]["intent_id"] != declaration["id"])
                    or declaration["kind"] == "unbind_profile" and bindings):
                reject("invalid_application_result", "applied declaration must have its actual binding effect in the same atomic checkpoint")
        old_bindings = old["state"]["scene_and_corrections"]["state"]["bindings"] if old else []
        old_selections = old["state"]["scene_and_corrections"]["state"]["selections"] if old else []
        ambiguous = {canonical(person) for publication in publications.values() if publication["payload_type"] in INPUTS
                     and self.ingress._current_payload(publication["payload"], t)
                     for person in publication["payload"]["association"]["candidate_person_refs"]}
        current_people = [json.loads(key) for key in people]
        for binding in scene_state["bindings"]:
            _, example = resolve_selection(self.scene_policy["catalog"], binding["profile_ref"], binding["example_ref"])
            prior = next((b for b in old_bindings if b["intent_id"] == binding["intent_id"]), None)
            if (canonical(binding["person_ref"]) not in people and prior is None or binding["authority_generation"] != cp["authority_generation"]
                    or not binding["bound_at"] <= t < binding["valid_until"]
                    or canonical(binding["person_ref"]) in ambiguous
                    or any(str(person["pid"]) == str(binding["person_ref"]["pid"]) and person["track_epoch"] != binding["person_ref"]["track_epoch"] for person in current_people)
                    or not binding["source_generations"]
                    or any(scene_state["source_generations"].get(source) != generation for source, generation in binding["source_generations"].items())):
                reject("invalid_profile_binding", "personalization requires current person and matching source/room generations")
            rows = self.j.db.execute("SELECT event,application_result,settled_at FROM live_scene_intents WHERE event_id=?", (binding["intent_id"],)).fetchall()
            matches = [row for row in rows if (p := json.loads(row["event"])["payload"])["kind"] == "bind_profile"
                       and p["target"] == binding["person_ref"] and p["value"] == {"profile_ref": binding["profile_ref"], "example_ref": binding["example_ref"]}]
            if len(matches) != 1 or matches[0]["application_result"] is None or json.loads(matches[0]["application_result"])["status"] != "applied":
                reject("invalid_profile_binding", "personal binding requires its exact prior applied human declaration")
            declaration = json.loads(matches[0]["event"])["payload"]
            if binding["bound_at"] != matches[0]["settled_at"] or binding["valid_until"] != declaration["valid_until"]:
                reject("invalid_profile_binding", "binding cannot change its original applied time or declared expiry")
            if prior is None and binding["intent_id"] not in reported_ids:
                reject("invalid_profile_binding", "new binding requires a newly consumed applied declaration")
            if prior is not None and any(binding[key] != prior[key] for key in binding if key != "bound_episode_id"):
                reject("invalid_profile_binding", "retained personalization cannot rewrite its original binding")
            if prior is not None and prior["bound_episode_id"] is not None and binding["bound_episode_id"] != prior["bound_episode_id"]:
                reject("invalid_profile_binding", "personalization cannot transfer to another activity episode")
            if binding["bound_episode_id"] is not None and any(r["person_ref"] == binding["person_ref"]
                    and (self.ingress.config["version"] not in {7, 9, 10, 11, 12, 13} or any(s["scene"]["episode_id"] == binding["bound_episode_id"] and s["scene"]["person_ref"] == binding["person_ref"] and s["scene"]["subject_ref"] == r["subject_ref"] for s in old_selections))
                    and (r["state"] == "unknown" and r["episode_id"] is None or r["episode_id"] is not None and r["episode_id"] != binding["bound_episode_id"]) for r in engagements):
                reject("invalid_profile_binding", "ended activity binding cannot transfer to another episode")
            if prior is None and set(binding["source_generations"]) != people[canonical(binding["person_ref"])]:
                reject("invalid_profile_binding", "new binding must preserve every current person-source dependency")
        for selection in scene_state["selections"]:
            scene = selection["scene"]
            if not scene["selected_at"] <= t < scene["valid_until"] <= t + self.policy["max_output_age_s"]:
                reject("invalid_scene", "scene lease must be current and bounded by the commissioned output age")
            _, example = resolve_selection(self.scene_policy["catalog"], scene["profile_ref"], scene["example_ref"])
            correction = next((c for c in scene_state.get("corrections", []) if self._same_scene(c, scene)), None) if self.supports_brightness else None
            expected_nominal = dict(example["nominal"])
            if correction:
                expected_nominal["brightness_fraction"] = correction["brightness_fraction"]
            if (scene["nominal"] != expected_nominal or selection["requirements_ref"] != example["requirements_ref"]
                    or scene["correction_refs"] != ([correction["intent_ref"]["id"]] if correction else [])):
                reject("invalid_scene", "selected nominal commands must preserve the immutable approved example")
            relation = [r for r in engagements if r["person_ref"] == scene["person_ref"] and r["subject_ref"] == scene["subject_ref"] and r["episode_id"] == scene["episode_id"]]
            if len(relation) != 1 or relation[0]["state"] not in {"supported", "suspended", "ending", "candidate", "conflicted"}:
                reject("invalid_scene", "scene selection requires its actual shared engagement episode")
            bindings = [b for b in scene_state["bindings"] if b["person_ref"] == scene["person_ref"] and (self.ingress.config["version"] not in {7, 9, 10, 11, 12, 13} or b["bound_episode_id"] in {None,scene["episode_id"]})]
            expected = {"profile_ref": bindings[0]["profile_ref"], "example_ref": bindings[0]["example_ref"]} if bindings else self.scene_policy["catalog"]["anonymous_baseline"]
            if any(scene[key] != expected[key] for key in expected):
                reject("invalid_scene", "selection cannot invent personalization outside a current explicit binding")
            if bindings and scene["valid_until"] > bindings[0]["valid_until"]:
                reject("invalid_scene", "personal composition cannot outlive its explicit profile binding")
            if relation[0]["state"] in {"supported", "ending"} and scene["valid_until"] > relation[0]["valid_until"]:
                reject("invalid_scene", "supported composition cannot outlive its actual engagement basis")
            if selection["key"] != digest([scene["person_ref"], scene["subject_ref"], scene["episode_id"]]):
                reject("invalid_scene", "scene selection key must bind the exact shared episode")
            prior = next((selected for selected in old_selections if selected["key"] == selection["key"]), None)
            if self.ingress.config["version"] in {7, 9, 10, 11, 12, 13} and relation[0]["state"] == "ending" and prior is None:
                reject("invalid_scene", "ending can retain a prior scene but cannot start a new one")
            changed_by_human = any(result["status"] == "applied" and any(p["payload_type"] == INTENT and p["id"] == result["id"]
                                        and p["payload"]["target"] == scene["person_ref"] for p in publications.values()) for result in results)
            if prior is not None and not changed_by_human:
                # Expiry alone may remove personal composition and use the
                # separately approved anonymous baseline, without inheriting it.
                expired_binding = any(b["person_ref"] == scene["person_ref"] and t >= b["valid_until"] for b in old_bindings)
                withdrawn_authority = old["authority_generation"] != cp["authority_generation"] or any(
                    old["state"]["engagement"]["source_generations"].get(source) != scene_state["source_generations"].get(source)
                    for source in prior["support_source_ids"])
                brightness_changed = self.supports_brightness and correction is not None and correction["applied_at"] == t
                mutable = {"valid_until", "nominal", "correction_refs"} if brightness_changed else {"valid_until"}
                if not expired_binding and not withdrawn_authority and any(scene[key] != prior["scene"][key] for key in scene if key not in mutable):
                    reject("scene_continuity_conflict", "motion, refinement and expiry ticks cannot select another composition")
            if relation[0]["state"] not in {"supported", "ending"}:
                if prior is None or selection["gap_since"] is None or not selection["gap_since"] <= t < selection["gap_since"] + self.scene_policy["source_gap_hold_s"]:
                    reject("invalid_scene", "uncertain continuity can only retain an existing scene for the bounded source gap")
                expected_gap = prior["gap_since"] if prior["gap_since"] is not None else min(t, prior["scene"]["valid_until"])
                if selection["gap_since"] != expected_gap:
                    reject("invalid_scene", "uncertain scene continuity cannot refresh its original source-gap anchor")
                if scene["valid_until"] > selection["gap_since"] + self.scene_policy["source_gap_hold_s"]:
                    reject("invalid_scene", "held scene cannot extend its commissioned source-gap horizon")
        if decision["scene"]["status"] == "selected" and not any(selection["scene"] == decision["scene"] for selection in scene_state["selections"]):
            reject("invalid_scene", "public selected scene must match its exact private scene state")
        eligible = [engagement for engagement in engagements if engagement["state"] in {"supported", "ending"}
                    and engagement["episode_id"] is not None and engagement["valid_until"] > t]
        if len(eligible) > 1 and (decision["scene"]["status"] != "unavailable" or decision["goal"]["status"] != "unavailable"):
            reject("concurrent_scene_unresolved", "concurrent engagements cannot silently select a primary lighting beneficiary")
        if "bindings" in decision and decision["bindings"] != scene_state["bindings"]:
            reject("invalid_profile_binding", "public binding status must match the durable scene snapshot")
        if len({result["id"] for result in scene_state["settlements"]}) != len(scene_state["settlements"]):
            reject("invalid_application_result", "private settlements must retain unique intent ids")
        for result in scene_state["settlements"]:
            rows = self.j.db.execute("SELECT event,application_result FROM live_scene_intents WHERE event_id=?", (result["id"],)).fetchall()
            if len(rows) != 1 or rows[0]["application_result"] is None:
                reject("invalid_application_result", "private settlement needs its immutable durable intent result")
            original, terminal = json.loads(rows[0]["event"])["payload"], json.loads(rows[0]["application_result"])
            if result["digest"] != digest(original) or any(result[key] != terminal[key] for key in ("id", "status", "reason")):
                reject("application_result_conflict", "private settlement cannot rewrite its original intent or application result")
        self._goal(decision, publications, engagements, t)

    def validate_external_intent_result(self, intent, result, *, commit, publications, decision, engagements, old):
        reject("invalid_application_result", "external intent requires an independent commissioned delivery validator")

    def _continuity_proofs(self, publications, scene, t, gap_since):
        proofs = []
        if not self.supports_brightness:
            return proofs
        generations = {row["source_id"]: row["generation"] for row in self.j.db.execute("SELECT source_id,generation FROM live_sources")}
        for p in publications.values():
            l = p["payload"]
            if p["payload_type"] != "home-live-person-lifecycle-1" or l["disposition"] != "continuity_qualified":
                continue
            method = next((m for m in (self.policy.get("lifecycle_policy") or {}).get("continuity_methods", [])
                if all(m[k] == l[k] for k in ("method_ref", "qualification_ref", "applicability_ref"))), None)
            if (method is None or l["target_person_ref"] != scene["person_ref"] or l["target_subject_ref"] != scene["subject_ref"]
                    or any(l[k] != self.ingress.identity[k] for k in SCOPE_KEYS)
                    or not max(l["available_at"], p["committed_at"]) <= t < l["valid_until"]
                    or l["observed_at"] > t or l["source_generation"] != generations.get(l["source_id"])
                    or not 0 <= l["window"]["start_at"] <= gap_since or not l["window"]["start_at"] <= l["window"]["end_at"] <= l["observed_at"]):
                continue
            if self.ingress.config["version"] in {7, 9, 10, 11, 12, 13}:
                from live_lifecycle import commissioned_method
                if any(other["payload_type"] == "home-live-person-lifecycle-1"
                       and other["payload"]["target_person_ref"] == l["target_person_ref"]
                       and other["payload"]["disposition"] in {"absence_qualified", "departure_qualified"}
                       and other["payload"]["observed_at"] > l["observed_at"]
                       and commissioned_method(other["payload"], self.policy["lifecycle_policy"]) is not None
                       and self.ingress._current_payload(other["payload"], t)
                       for other in publications.values()):
                    continue
            until = min(l["valid_until"], l["window"]["start_at"] + method["max_gap_s"], gap_since + self.scene_policy["source_gap_hold_s"])
            if t < until:
                proofs.append((p, until))
        return proofs

    def _validate_scene_continuity(self, commit, publications, decision, engagements, old):
        scene, goal, t = decision["scene"], decision["goal"], decision["t"]
        continuity = decision["scene_continuity"]
        _exact(continuity, {"status", "reason", "valid_until"})
        positive = scene["status"] == "selected" and any(r["person_ref"] == scene["person_ref"] and r["subject_ref"] == scene["subject_ref"]
            and r["episode_id"] == scene["episode_id"] and r["state"] in {"supported", "ending"} for r in engagements)
        expected = "current" if positive and goal["status"] == "requested" else "suspended"
        if scene["status"] == "selected" and not positive and goal["status"] == "requested":
            selections = commit["checkpoint"]["state"]["scene_and_corrections"]["state"]["selections"]
            selected = next((s for s in selections if s["scene"] == scene), None)
            prior = old and next((s for s in old["state"]["scene_and_corrections"]["state"]["selections"]
                                 if s["scene"]["scene_id"] == scene["scene_id"]), None)
            if selected is None or prior is None or selected["gap_since"] is None:
                reject("invalid_scene_continuity", "held delivery requires the exact previously committed scene")
            proofs = self._continuity_proofs(publications, scene, t, selected["gap_since"])
            if not proofs or scene["valid_until"] > max(until for _, until in proofs):
                reject("invalid_scene_continuity", "held delivery requires its current measured continuity proof and bounded lease")
            expected = "held"
        reason = "current_supported_episode" if expected == "current" else "qualified_semantic_continuity" if expected == "held" else scene.get("reason", "supported_episode_required")
        until = scene["valid_until"] if scene["status"] == "selected" else t
        if continuity != {"status": expected, "reason": reason, "valid_until": until}:
            reject("invalid_scene_continuity", "public continuity must match the same committed scene and goal")

    @staticmethod
    def _brightness_value(value):
        _exact(value, {"scene_id", "episode_id", "subject_ref", "brightness_fraction"})
        identifier(value["scene_id"], "scene_id")
        identifier(value["episode_id"], "episode_id")
        _exact(value["subject_ref"], {"subject_id", "subject_epoch"})
        for key, item in value["subject_ref"].items():
            identifier(item, key)
        if not finite(value["brightness_fraction"]) or not 0 <= value["brightness_fraction"] <= 1:
            reject("invalid_intent", "brightness is an absolute finite fraction", 400)

    @staticmethod
    def _same_scene(correction, scene):
        return all(correction[k] == scene[k] for k in ("scene_id", "episode_id", "person_ref", "subject_ref"))

    @staticmethod
    def _intent_reference(publication):
        return {**{k: publication[k] for k in ("stream_epoch", "cursor", "producer_id", "id", "payload_digest")}, "component_id": None}

    def _validate_corrections(self, commit, publications, decision, engagements, old, people):
        """Independent authority check; no JS execution or trusted worker assertions."""
        cp, t = commit["checkpoint"], decision["t"]
        state = cp["state"]["scene_and_corrections"]["state"]
        previous = old["state"]["scene_and_corrections"]["state"] if old else {"corrections": [], "settlements": [], "selections": []}
        corrections = state["corrections"]
        _array(corrections, 128)
        if len({c["scene_id"] for c in corrections}) != len(corrections):
            reject("invalid_scene_correction", "one active correction is allowed per scene")
        results = {r["id"]: r for r in decision["intent_results"]}
        settlements = {r["id"]: r for r in state["settlements"]}
        if any(settlements.get(r["id"]) != r for r in previous["settlements"]):
            reject("application_result_conflict", "scene settlement history cannot be dropped or rewritten")
        candidates = []
        for p in publications.values():
            i = p["payload"]
            if p["payload_type"] != CORRECTION_INTENT or i["kind"] != "set_brightness":
                continue
            self._brightness_value(i["value"])
            selection = next((s for s in state["selections"] if all(s["scene"][k] == i["value"][k] for k in ("scene_id", "episode_id", "subject_ref"))
                              and s["scene"]["person_ref"] == i["target"]), None)
            reason = ("scene_correction_expired" if t >= i["valid_until"] else
                      "qualified_current_person_required" if canonical(i["target"]) not in people else
                      "current_scene_required" if selection is None else None)
            candidates.append((p, selection, reason))
        for p, selection, reason in candidates:
            i = p["payload"]
            if reason is None and any(other[2] is None and other[1] is selection and other[0]["cursor"] > p["cursor"] for other in candidates):
                reason = "superseded_scene_correction"
            result = results.get(i["id"])
            expected = {"status": "rejected" if reason else "applied", "reason": reason or "explicit_scene_brightness_set"}
            if result is None or any(result[k] != value for k, value in expected.items()):
                reject("invalid_application_result", "brightness result must follow current scene, availability and delivered order")
            if settlements.get(i["id"]) != {"id": i["id"], "digest": digest(i), **expected}:
                reject("invalid_application_result", "brightness settlement must preserve its exact original command")
            active = next((c for c in corrections if c["intent_ref"]["id"] == i["id"]), None)
            if reason is None and (active is None or active["applied_at"] != t):
                reject("invalid_application_result", "applied brightness requires its effect in this same atomic checkpoint")
            if reason is not None and active is not None:
                reject("invalid_application_result", "rejected correction cannot have an active effect")
        for c in corrections:
            _exact(c, {"intent_ref", "person_ref", "subject_ref", "episode_id", "scene_id", "brightness_fraction", "applied_at", "authority_generation", "source_generations"})
            ref = c["intent_ref"]
            _exact(ref, {"stream_epoch", "cursor", "producer_id", "id", "payload_digest", "component_id"})
            row = self.j.db.execute("SELECT * FROM live_scene_intents WHERE cursor=?", (ref["cursor"],)).fetchone()
            if row is None:
                reject("invalid_scene_correction", "correction needs its exact durable original publication")
            p = json.loads(row["publication"])
            i = p["payload"]
            if (ref != self._intent_reference(p) or p["payload_type"] != CORRECTION_INTENT or i["kind"] != "set_brightness"
                    or c["person_ref"] != i["target"] or any(c[k] != i["value"][k] for k in ("scene_id", "episode_id", "subject_ref", "brightness_fraction"))
                    or not finite(c["applied_at"]) or not max(i["available_at"], p["committed_at"]) <= c["applied_at"] < i["valid_until"]
                    or c["applied_at"] > t or c["authority_generation"] != cp["authority_generation"]
                    or not c["source_generations"] or any(state["source_generations"].get(s) != g for s, g in c["source_generations"].items())):
                reject("invalid_scene_correction", "correction cannot change attribution, timing, value or authority")
            selection = next((s for s in state["selections"] if self._same_scene(c, s["scene"])), None)
            if selection is None or c["applied_at"] < selection["scene"]["selected_at"]:
                reject("invalid_scene_correction", "correction cannot transfer to another scene or episode")
            prior = next((p for p in previous["corrections"] if p["intent_ref"] == ref), None)
            if prior is not None:
                if c != prior or row["application_result"] is None or json.loads(row["application_result"])["status"] != "applied" or row["settled_at"] != c["applied_at"]:
                    reject("invalid_scene_correction", "retained corrections must preserve their original committed effect")
            elif (c["intent_ref"]["id"] not in results or results[c["intent_ref"]["id"]]["status"] != "applied"
                    or publications.get(ref["cursor"]) != p or c["applied_at"] != t
                    or set(c["source_generations"]) != people.get(canonical(c["person_ref"]), set())):
                reject("invalid_scene_correction", "new correction requires a newly delivered applied command and its full source dependencies")
        for prior in previous["corrections"]:
            still_selected = any(self._same_scene(prior, s["scene"]) for s in state["selections"])
            same_authority = prior["authority_generation"] == cp["authority_generation"] and all(state["source_generations"].get(s) == g for s, g in prior["source_generations"].items())
            if still_selected and same_authority and not any(c["scene_id"] == prior["scene_id"] for c in corrections):
                reject("invalid_scene_correction", "same-scene correction cannot expire with the command application window")

    def _goal(self, decision, publications, engagements, t):
        goal, scene = decision["goal"], decision["scene"]
        if goal["status"] == "requested":
            if scene["status"] != "selected" or any(goal[key] != scene[key] for key in ("person_ref", "subject_ref", "episode_id", "scene_id", "nominal")):
                reject("invalid_scene", "task need must name the selected scene and its nominal intent")
            _, example = resolve_selection(self.scene_policy["catalog"], scene["profile_ref"], scene["example_ref"])
            if goal["requirements_ref"] != example["requirements_ref"] or not t < goal["valid_until"] <= scene["valid_until"]:
                reject("invalid_scene", "task need cannot outlive or replace the approved scene requirements")
            if not any(engagement["state"] in {"supported", "ending"} and engagement["episode_id"] == goal["episode_id"]
                       and engagement["person_ref"] == goal["person_ref"] and engagement["subject_ref"] == goal["subject_ref"] for engagement in engagements) and not (
                           self.supports_brightness and decision["scene_continuity"]["status"] == "held"):
                reject("invalid_scene", "held uncertain composition cannot authorize a new task goal")
            if any(reference not in decision["input_cut"]["geometry_refs"] for reference in goal["geometry_refs"]):
                reject("invalid_reference", "task geometry must be part of the exact decision cut")
            for reference in goal["geometry_refs"]:
                publication = self._reference(reference, publications, current=True, t=t)
                geometry = publication["payload"]
                if publication["payload_type"] != "home-live-geometry-1" or geometry["person_ref"] != goal["person_ref"] or geometry["subject_ref"] != goal["subject_ref"]:
                    reject("invalid_reference", "task geometry cannot silently select another person or subject")
        background = decision["background"]
        if background["status"] == "requested":
            approved = self.scene_policy["catalog"]["background"]
            if self.ingress.config["version"] in {7, 9, 10, 11, 12, 13}:
                expected_id = "background:" + digest([self.ingress.identity, approved["approval_ref"]])[:48]
                if (background["goal_id"] != expected_id
                        or not t < background["valid_until"] <= t + self.policy["max_output_age_s"]):
                    reject("invalid_background", "background need requires its exact identity and bounded positive lease")
            if any(background[key] != approved[key] for key in ("approval_ref", "requirements_ref", "nominal", "fade_in_s")) or not background["evidence_refs"]:
                reject("invalid_background", "background need requires the separate approved presence policy")
            for reference in background["evidence_refs"]:
                publication = self._reference(reference, publications, supplied=True, current=True, t=t)
                fact = publication["payload"]
                if publication["payload_type"] != "home-live-fact-1" or fact["kind"] != "person_presence" or fact["value"] != "present" or fact["method_ref"] not in approved["methods"]:
                    reject("invalid_background", "background need requires fresh admitted presence, never an activity label")
                expiry = min(fact["valid_until"], fact["observed_at"] + self.ingress.sources[fact["source_id"]]["max_age_s"]) - fact["clock"]["uncertainty_s"]
                if background["valid_until"] > expiry:
                    reject("invalid_background", "background need cannot refresh presence expiry")

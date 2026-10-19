"""Atomic, private worker authority for the opt-in live engagement stage.

The public capture journal remains the only source of runtime observations.
This module acquires no inputs and contains no reducer or physical transport.
"""
from __future__ import annotations

import json

from journal import JournalError, bounded_json, canonical, digest, finite, identifier
from live_ingress_validation import SCOPE_KEYS, _array, _exact, _integer, _nonnegative, _ref, _refs, _sha


OUTPUTS = frozenset({"home-live-engagement-state-1", "home-live-shadow-decision-1"})
SCHEMAS = OUTPUTS | {"home-live-worker-checkpoint-1", "home-live-worker-commit-1", "home-live-input-cut-1"}
INPUTS = frozenset({"home-live-fact-1", "home-live-geometry-1"})
POLICY_KEYS = {"worker_id", "producer_id", "producer_epoch", "revision", "config_digest", "source_pins", "schema_pins",
               "max_output_age_s", "semantic_interval_s", "engagement_policy"}
RECEIPT_KEYS = {"cursor", "id", "producer_id", "payload_digest", "committed_at", "received_at"}
MODULES = frozenset({"tools/home-live-context-worker.cjs", "tools/home-context-shadow-worker.cjs",
                     "tools/home-live-context-contract.cjs", "app/src/home-engagement-reducer.js",
                     "app/src/home-live-engagement-profile.js", "app/src/home-lighting-target-contract.js"})


def reject(code="invalid_commit", message="live worker input does not match its pinned causal contract", status=409):
    raise JournalError(code, message, status)


def validate_worker_policy(manifest, registry):
    """Pure validation before SQLite opens; config1 never acquires this authority."""
    if (not getattr(registry, "worker_interface_digest", None)
            or manifest["worker_interface_digest"] != registry.worker_interface_digest):
        raise ValueError("exact additive worker interface pin required")
    policy = manifest["worker"]
    _exact(policy, POLICY_KEYS)
    for key in ("worker_id", "producer_id", "producer_epoch", "revision"):
        identifier(policy[key], key)
    _sha(policy["config_digest"])
    expected_sources = {source: digest(value) for source, value in manifest["sources"].items()}
    if policy["source_pins"] != expected_sources or policy["schema_pins"] != registry.worker_schema_pins:
        raise ValueError("exact live source and worker schema pins required")
    _nonnegative(policy["max_output_age_s"], positive=True)
    if policy["max_output_age_s"] > 3 or type(policy["semantic_interval_s"]) not in {int, float} or policy["semantic_interval_s"] != 1:
        raise ValueError("bounded output lease and one-second semantic schedule required")
    engagement = policy["engagement_policy"]
    _exact(engagement, {"support_methods", "refinement_methods", "negative_methods", "allow_experimental"})
    if type(engagement["allow_experimental"]) is not bool:
        raise ValueError("explicit experimental support policy required")
    methods = [method for source in manifest["sources"].values() for method in source["method_refs"]]
    for key in ("support_methods", "refinement_methods", "negative_methods"):
        _refs(engagement[key], nonempty=key == "support_methods")
        if any(method not in methods for method in engagement[key]):
            raise ValueError("engagement method must resolve to a commissioned input source")


def validate_worker_grant(grant, config):
    policy, sources = config["manifest"]["worker"], config["manifest"]["sources"]
    if (grant.operations != frozenset({"read", "worker_commit"})
            or grant.expected_worker_id != policy["worker_id"] or grant.expected_consumer_id != policy["worker_id"]
            or grant.producer_id != policy["producer_id"] or grant.producer_epoch != policy["producer_epoch"]
            or policy["revision"] not in grant.revisions
            or not INPUTS | SCHEMAS <= grant.payload_types or set(grant.source_ids) != set(sources)):
        raise ValueError("one explicitly pinned private live worker grant required")
    bindings = [json.loads(value) for value in grant.live_bindings]
    if (len(bindings) != len(sources)
            or any(not any(binding["source_id"] == source and set(binding["capabilities"]) == {"read", "worker_commit"}
                           for binding in bindings) for source in sources)):
        raise ValueError("worker needs distinct read/commit authority for every pinned input source")


class LiveWorkerBoundary:
    schema_types = SCHEMAS
    output_types = OUTPUTS
    input_types = INPUTS
    decision_type = "home-live-shadow-decision-1"
    checkpoint_type = "home-live-worker-checkpoint-1"
    commit_type = "home-live-worker-commit-1"
    cut_type = "home-live-input-cut-1"
    version = 1
    module_names = MODULES
    max_input_receipts = 128

    def __init__(self, ingress):
        self.ingress, self.j = ingress, ingress.j
        self.policy = ingress.manifest["worker"]
        self.j.db.executescript("""
            CREATE TABLE IF NOT EXISTS live_worker_deliveries(principal_id TEXT NOT NULL,consumer_id TEXT NOT NULL,
                cursor INTEGER NOT NULL,delivered_at REAL NOT NULL,publication TEXT NOT NULL,
                PRIMARY KEY(principal_id,consumer_id,cursor));
            CREATE TABLE IF NOT EXISTS live_worker_current(key TEXT PRIMARY KEY,payload_type TEXT NOT NULL,
                authority_generation INTEGER NOT NULL,valid_until REAL NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS live_worker_cuts(principal_id TEXT NOT NULL,consumer_id TEXT NOT NULL,
                cursor INTEGER NOT NULL,authority_generation INTEGER NOT NULL,cursors TEXT NOT NULL,
                PRIMARY KEY(principal_id,consumer_id));
        """)

    def validate_payload(self, payload):
        if payload.get("schema") not in self.schema_types:
            reject("unsupported_schema", "closed live worker type required", 400)

    def _worker(self, grant, site, room):
        self.ingress._read_scope(grant, site, room)
        grant.require("worker_commit", site, room, producer_id=self.policy["producer_id"])
        if (grant.expected_worker_id != self.policy["worker_id"] or grant.expected_consumer_id != self.policy["worker_id"]
                or grant.producer_epoch != self.policy["producer_epoch"]):
            reject("forbidden", "worker does not match the sole commissioned room authority", 403)

    def readable(self, publication, revision, grant):
        if (publication["payload_type"] not in grant.payload_types or revision not in grant.revisions
                or not any(self.ingress._capability(grant, source, "read") for source in self.ingress.sources)):
            return False
        # A full decision has a separately authorized geometry-bearing dialect,
        # even while this milestone admits unavailable delivery sections only.
        return publication["payload_type"] != self.decision_type or "home-live-geometry-1" in grant.payload_types

    def additional_inputs(self, grant):
        return []

    def prune_deliveries(self):
        """Keep restoration bodies only for the latest cut and durable checkpoint.

        These bounded private copies may outlive source-body retention; they do
        not grant fresh support. Complete original history belongs in the causal
        archive. Exact commit retries use durable identity receipts instead.
        """
        j, keep = self.j, set()
        for row in j.db.execute("SELECT principal_id,consumer_id,cursors FROM live_worker_cuts"):
            keep.update((row["principal_id"], row["consumer_id"], cursor) for cursor in json.loads(row["cursors"]))
        for row in j.db.execute("SELECT worker_id,checkpoint FROM workers"):
            checkpoint = json.loads(row["checkpoint"])
            receipts = checkpoint.get("state", {}).get("engagement", {}).get("input_receipts", [])
            principals = {grant.principal_id for grant in j.grants
                          if grant.expected_worker_id == row["worker_id"] and "worker_commit" in grant.operations}
            keep.update((principal, row["worker_id"], receipt["cursor"])
                        for principal in principals for receipt in receipts)
        for row in j.db.execute("SELECT principal_id,consumer_id,cursor FROM live_worker_deliveries").fetchall():
            key = (row["principal_id"], row["consumer_id"], row["cursor"])
            if key not in keep:
                j.db.execute("DELETE FROM live_worker_deliveries WHERE principal_id=? AND consumer_id=? AND cursor=?", key)
        count, size = j.db.execute("SELECT count(*),coalesce(sum(length(CAST(publication AS BLOB))),0) FROM live_worker_deliveries").fetchone()
        if count > j.limits.max_receipts or size > j.limits.max_publication_bytes:
            reject("journal_full", "bounded worker restoration-body budget exhausted", 507)

    def current(self, grant):
        now, generation = self.j.clock(), self.j.revocation_generation(self.ingress.identity["site_id"], self.ingress.identity["room_id"])
        return [publication for row in self.j.db.execute("SELECT * FROM live_worker_current ORDER BY rowid")
                if row["authority_generation"] == generation and now < row["valid_until"]
                and self.readable(publication := json.loads(row["body"]), self.policy["revision"], grant)]

    def cut_metadata(self):
        return {}

    def private_cut_material(self, items):
        return {}

    def qualified_pc_support(self, facts, checkpoint, t):
        return False

    def commit_body_limit(self):
        if self.ingress.config['version'] == 11:
            return min(self.policy['manual_policy']['resource_limits']['worker_commit_bytes'], self.j.limits.max_manual_worker_commit_bytes)
        return self.j.limits.max_worker_commit_bytes

    def derived_event_limit(self, event):
        if self.ingress.config['version'] == 11 and event.get('payload_type') == 'home-live-shadow-decision-10':
            return min(self.policy['manual_policy']['resource_limits']['derived_event_bytes'], self.j.limits.max_manual_derived_event_bytes)
        return self.j.limits.max_body_bytes

    def validate_control_checkpoint(self, cp, commit):
        return None

    def input_cut(self, grant, site, room, consumer_id=None):
        self._worker(grant, site, room)
        consumer = consumer_id or grant.expected_consumer_id
        if consumer != grant.expected_worker_id:
            reject("forbidden", "input cut consumer must match the worker grant", 403)
        def read():
            j = self.j
            self.ingress._prune()
            now, items = j.clock(), []
            for row in j.db.execute("SELECT body FROM live_heads WHERE body IS NOT NULL ORDER BY cursor"):
                publication = json.loads(row["body"])
                if publication["payload_type"] not in self.input_types:
                    continue
                saved = j.db.execute("SELECT revision FROM publications WHERE cursor=?", (publication["cursor"],)).fetchone()
                revision = saved["revision"] if saved is not None else None
                if revision is None:
                    original = j.db.execute("SELECT event FROM receipts WHERE producer_id=? AND event_id=? AND json_extract(receipt,'$.cursor')=? AND json_extract(receipt,'$.payload_digest')=?",
                                            (publication["producer_id"], publication["id"], publication["cursor"], publication["payload_digest"])).fetchone()
                    revision = json.loads(original["event"]).get("revision") if original else None
                if not isinstance(revision, str):
                    reject("reference_unavailable", "current producer head lacks its immutable revision receipt")
                if not self.ingress._readable(publication, revision, grant):
                    continue
                items.append(publication)
            items.extend(self.additional_inputs(grant))
            items.sort(key=lambda publication: publication["cursor"])
            for publication in items:
                j.db.execute("INSERT OR IGNORE INTO live_worker_deliveries VALUES(?,?,?,?,?)",
                             (grant.principal_id, consumer, publication["cursor"], now, canonical(publication)))
            cursor = int(j._meta("cursor"))
            j._delivered(grant, consumer, site, room, cursor)
            authority = j.revocation_generation(site, room)
            j.db.execute("INSERT INTO live_worker_cuts VALUES(?,?,?,?,?) ON CONFLICT(principal_id,consumer_id) DO UPDATE SET cursor=excluded.cursor,authority_generation=excluded.authority_generation,cursors=excluded.cursors",
                         (grant.principal_id, consumer, cursor, authority, canonical([p["cursor"] for p in items])))
            self.prune_deliveries()
            result = {"schema": self.cut_type, "version": self.version, "stream_epoch": j._meta("epoch"),
                      "cursor": cursor, "items": items, "revocation_generation": j.revocation_generation(site, room),
                      "source_generations": {row["source_id"]: row["generation"] for row in j.db.execute("SELECT source_id,generation FROM live_sources")}}
            result.update(self.ingress.service_clock_sample())
            result.update(self.cut_metadata())
            result.update(self.private_cut_material(items))
            j.schemas.validate(result)
            return result
        return self.j._transaction(read)

    def checkpoint(self, worker_id, grant, site, room):
        self._worker(grant, site, room)
        grant.require("read", site, room, payload_type=self.checkpoint_type)
        if worker_id != grant.expected_worker_id:
            reject("forbidden", "checkpoint belongs to another worker", 403)
        def read():
            row = self.j.db.execute("SELECT * FROM workers WHERE worker_id=? AND site_id=? AND room_id=?", (worker_id, site, room)).fetchone()
            result = {"schema": "home-context-worker-checkpoint-1", "stream_epoch": self.j._meta("epoch"),
                    "worker_id": worker_id, "input_cursor": row["input_cursor"] if row else 0,
                    "context_seq": row["context_seq"] if row else 0,
                    "checkpoint": json.loads(row["checkpoint"]) if row else None,
                    "revocation_generation": self.j.revocation_generation(site, room)}
            if self.ingress.config["version"] in {7, 9, 10, 11, 12, 13}:
                result.update(self.ingress.service_clock_sample())
            return result
        return self.j._transaction(read)

    def lookup(self, commit_id, grant, site, room):
        self._worker(grant, site, room)
        identifier(commit_id, "commit_id")
        with self.j.lock:
            row = self.j.db.execute("SELECT receipt FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?",
                                    (grant.producer_id, self.j._meta("epoch"), "worker-commit/" + commit_id)).fetchone()
            if row is None:
                reject("not_found", "worker commit has no durable receipt", 404)
            return json.loads(row["receipt"])

    def _receipts(self, commit, grant, t):
        j, result = self.j, {}
        receipts = commit["input_receipts"]
        if not isinstance(receipts, list) or len(receipts) > self.max_input_receipts:
            reject()
        for claimed in receipts:
            if not isinstance(claimed, dict) or set(claimed) != (RECEIPT_KEYS | {"received_at_lower_bound","clock_uncertainty_s"} if self.ingress.config["version"] in {7, 9, 10, 11, 12, 13} else RECEIPT_KEYS):
                reject("invalid_input_receipt")
            cursor = claimed["cursor"]
            if type(cursor) is not int or not 0 < cursor <= commit["input_cursor"] or cursor in result:
                reject("invalid_input_receipt")
            saved = j.db.execute("SELECT delivered_at,publication FROM live_worker_deliveries WHERE principal_id=? AND consumer_id=? AND cursor=?",
                                 (grant.principal_id, commit["worker_id"], cursor)).fetchone()
            if saved is None:
                reject("unseen_cursor", "input was not returned through the worker's public causal cut")
            publication = json.loads(saved["publication"])
            if (any(claimed[key] != publication[key] for key in ("id", "producer_id", "payload_digest", "committed_at"))
                    or not finite(claimed["received_at"]) or not publication["committed_at"] <= saved["delivered_at"] <= claimed["received_at"] <= t
                    or claimed["received_at"] > j.clock()):
                reject("invalid_input_receipt")
            if self.ingress.config["version"] in {7, 9, 10, 11, 12, 13}:
                lower,error=claimed["received_at_lower_bound"],claimed["clock_uncertainty_s"]
                if (not finite(lower) or not finite(error) or not 0 <= error <= 1 or lower > claimed["received_at"]
                    or abs((claimed["received_at"]-lower)/2-error)>1e-6
                    or claimed["received_at"]-lower > self.ingress.manifest["service_clock_policy"]["max_round_trip_s"]+2*self.ingress.manifest["service_clock_policy"]["max_anchor_divergence_s"]+2*self.ingress.manifest["service_clock_policy"]["uncertainty_s"]):
                    reject("invalid_input_receipt","bounded original first-consumer receipt interval required")
            payload = publication["payload"]
            if (publication["stream_epoch"] != commit["stream_epoch"] or publication["payload_type"] not in self.input_types
                    or any(payload[key] != self.ingress.identity[key] for key in SCOPE_KEYS)):
                reject("invalid_input_receipt")
            result[cursor] = publication
        return result

    def _reference(self, reference, publications, *, supplied=False, current=False, t=None):
        if (not isinstance(reference, dict) or set(reference) != {"stream_epoch", "cursor", "producer_id", "id", "payload_digest", "component_id"}
                or reference["component_id"] is not None or reference["cursor"] not in publications):
            reject("invalid_reference")
        publication = publications[reference["cursor"]]
        if any(reference[key] != publication[key] for key in ("stream_epoch", "cursor", "producer_id", "id", "payload_digest")):
            reject("invalid_reference")
        payload = publication["payload"]
        lifecycle = self.ingress.config["version"] >= 6 and publication["payload_type"] == "home-live-person-lifecycle-1"
        if supplied and payload.get("status") != "supplied":
            if lifecycle:
                from live_lifecycle import commissioned_method
                if commissioned_method(payload, self.policy["lifecycle_policy"]) is None:
                    reject("invalid_reference", "lifecycle support requires its exact commissioned qualification")
            else:
                reject("invalid_reference", "positive support must name supplied evidence")
        source = self.j.db.execute("SELECT generation FROM live_sources WHERE source_id=?", (payload["source_id"],)).fetchone()
        if source is None or source["generation"] != payload["source_generation"]:
            reject("source_generation_conflict", "input source generation was withdrawn before commit")
        if current:
            head = self.j.db.execute("SELECT body FROM live_heads WHERE cursor=?", (publication["cursor"],)).fetchone()
            if head is None or head["body"] is None or not self.ingress._current_payload(payload, t):
                reject("stale_reference", "positive support must remain current at the frozen decision cut")
        return publication

    def _checkpoint(self, cp, commit, next_seq, old, now):
        self.validate_control_checkpoint(cp, commit)
        j = self.j
        if (cp["worker_id"] != self.policy["worker_id"] or cp["scope"] != self.ingress.identity
                or cp["manifest_ref"] != self.ingress.config["manifest_ref"]
                or cp["source_pins"] != self.policy["source_pins"] or cp["schema_pins"] != self.policy["schema_pins"]
                or cp["config_digest"] != self.policy["config_digest"] or cp["input_cursor"] != commit["input_cursor"]
                or cp["authority_generation"] != j.revocation_generation(commit["site_id"], commit["room_id"])
                or cp["state"]["seq"] != next_seq):
            reject("checkpoint_conflict", "checkpoint pins, counters or authority differ from the atomic commit")
        if (not finite(cp["last_committed_at"]) or cp["last_committed_at"] > now
                or old and (cp["last_committed_at"] <= old["last_committed_at"] if self.ingress.config["version"] in {7, 9, 10, 11, 12, 13} else cp["last_committed_at"] < old["last_committed_at"])
                or cp["state"]["semantic_seq"] != (old["state"]["semantic_seq"] if old else 0) + 1):
            reject("checkpoint_conflict", "semantic sequence and causal worker time must progress once per tick")
        nested = cp["state"]["engagement"]
        source_generations = {row["source_id"]: row["generation"] for row in j.db.execute("SELECT source_id,generation FROM live_sources")}
        if nested["source_generations"] != source_generations:
            reject("source_generation_conflict", "private checkpoint must bind every current source generation")
        description = self.configuration_description(nested)
        if set(nested["module_hashes"]) != self.module_names or digest(description) != self.policy["config_digest"]:
            reject("checkpoint_conflict", "checkpoint module identity does not match the frozen worker configuration")
        snapshot = nested["reducer"]
        reducer = snapshot["state"]
        if (snapshot["profile"] != "home-live-shadow-1" or snapshot["identity"] != self.ingress.identity
                or reducer["seq"] != cp["state"]["semantic_seq"] or reducer["t"] != cp["last_committed_at"]
                or nested["stream_epoch"] != commit["stream_epoch"] or nested["input_receipts"] != commit["input_receipts"]
                or cp["state"]["root_ledger"] != {"times": reducer["root_times"], "bindings": reducer["root_bindings"]}):
            reject("checkpoint_conflict", "private engagement mirrors must match the exact reducer snapshot and input receipts")
        bindings = [{"person_ref": relation["person_ref"], "subject_ref": relation["subject_ref"]} for relation in reducer["relations"].values()]
        if sorted(map(canonical, bindings)) != sorted(map(canonical, cp["state"]["identity_bindings"])):
            reject("checkpoint_conflict", "identity bindings cannot diverge from epoch-qualified reducer relationships")
        scope = canonical([self.ingress.identity[key] for key in ("site_id", "room_id", "session_id", "domain_epoch")])
        if set(reducer["root_times"]) != set(reducer["root_bindings"]):
            reject("root_time_conflict", "every retained original root requires its immutable time and physical binding")
        for root, observed in reducer["root_times"].items():
            original = j.db.execute("SELECT observed_at,sensor_digest FROM original_roots WHERE scope=? AND observation_root=?", (scope, root)).fetchone()
            if (original is None or original["observed_at"] != observed
                    or original["sensor_digest"] != digest(sorted(reducer["root_bindings"][root]))):
                reject("root_time_conflict", "checkpoint cannot rewrite an admitted original observation root")
        if old:
            prior = old["state"]["engagement"]
            for root, observed in prior["reducer"]["state"]["root_times"].items():
                if reducer["root_times"].get(root) != observed or reducer["root_bindings"].get(root) != prior["reducer"]["state"]["root_bindings"][root]:
                    reject("root_time_conflict", "same-domain checkpoints cannot discard immutable original-root bindings")
            changed = {source for source, generation in source_generations.items() if prior["source_generations"].get(source) != generation}
            if changed:
                for previous in prior["reducer"]["state"]["relations"].values():
                    for current in reducer["relations"].values():
                        if current["person_ref"] == previous["person_ref"] and current["subject_ref"] == previous["subject_ref"]:
                            if previous["chosen_clause"] and changed.intersection(previous["chosen_clause"]["source_ids"]):
                                if current["episode_id"] is not None or current["enter_dwell"] != 0 or current["refine_dwell"] != 0:
                                    reject("source_generation_conflict", "withdrawn source continuity cannot transfer dwell, refinement or an episode")
                            if previous["refinement"] and changed.intersection(previous["refinement"]["source_ids"]) and current["refinement"] is not None:
                                reject("source_generation_conflict", "withdrawn refinement evidence cannot preserve a claimed activity")
        return cp["last_committed_at"]

    def configuration_description(self, nested):
        return {"version": "home-live-context-worker-1.0.0", "profile": "home-live-shadow-1",
                       "identity": self.ingress.identity,
                       **{key: self.policy[key] for key in ("worker_id", "producer_id", "producer_epoch", "revision", "source_pins", "schema_pins", "engagement_policy", "max_output_age_s", "semantic_interval_s")},
                       "module_hashes": nested["module_hashes"], "worker_interface_digest": self.j.schemas.worker_interface_digest}

    def _outputs(self, commit, grant, publications, seq, t):
        cp, decision, engagements = commit["checkpoint"], None, []
        lifecycle_mode = self.ingress.config["version"] >= 6
        events = commit["publications"]
        if not isinstance(events, list) or not 1 <= len(events) <= 16:
            reject()
        for event in events:
            self.j._event(event, max_bytes=self.derived_event_limit(event))
            payload = event["payload"]
            if (event["payload_type"] not in self.output_types or event["producer_id"] != grant.producer_id
                    or event["stream_epoch"] != self.policy["producer_epoch"] or event["revision"] != self.policy["revision"]
                    or any(event[key] != self.ingress.identity[key] for key in SCOPE_KEYS)
                    or payload["manifest_ref"] != self.ingress.config["manifest_ref"]
                    or event["observed_at"] != (payload.get("observed_through") if payload.get("observed_through") is not None else t)
                    or event["available_at"] != t or not t < event["valid_until"] <= t + self.policy["max_output_age_s"]):
                reject("invalid_commit", "worker publications must share their frozen scope, time and bounded lease")
            if event["id"] != payload["id"] or event["payload_type"] == "home-live-engagement-state-1" and event["valid_until"] != payload["valid_until"]:
                reject("invalid_commit", "worker output identity and validity must match its publication envelope")
            grant.require("worker_commit", event["site_id"], event["room_id"], payload_type=event["payload_type"], revision=event["revision"])
            if payload["semantic_seq"] != cp["state"]["semantic_seq"]:
                reject("invalid_commit", "output semantic sequence differs from checkpoint")
            if event["payload_type"] == self.decision_type:
                if decision is not None:
                    reject("invalid_commit", "exactly one shadow decision is required")
                decision = payload
            else:
                engagements.append(payload)
        if decision is None or decision["seq"] != seq or decision["t"] != t or decision["worker_id"] != grant.expected_worker_id:
            reject("invalid_commit", "one next-sequence decision must match its checkpoint")
        if cp["state"]["engagement"]["last_decision"] != {"id": decision["id"], "t": t, "seq": seq}:
            reject("checkpoint_conflict", "checkpoint last decision must match this transaction's output")
        cut = decision["input_cut"]
        expected = {"stream_epoch": commit["stream_epoch"], "input_cursor": commit["input_cursor"],
                    "revocation_generation": cp["authority_generation"], "input_receipts": commit["input_receipts"],
                    "geometry_refs": cut["geometry_refs"], **({"source_generations":cp["state"]["engagement"]["source_generations"]} if self.ingress.config["version"] in {7, 9, 10, 11, 12, 13} else {})}
        expected.update(self.cut_metadata())
        if any(cut[key] != value for key, value in expected.items()) or cut["cut_digest"] != digest(expected):
            reject("invalid_commit", "decision input cut must match the complete immutable receipt cut")
        if self.ingress.config["version"] in {7, 9, 10, 11, 12, 13}:
            expected_refs=[{key:p[key] for key in ("stream_epoch","cursor","producer_id","id","payload_digest")} | {"component_id":None}
                for p in publications.values() if p["payload_type"] == "home-live-geometry-1"]
            if sorted(map(canonical,expected_refs)) != sorted(map(canonical,cut["geometry_refs"])):
                reject("invalid_reference","raw geometry cut must be independently complete")
        for reference in cut["geometry_refs"]:
            publication = self._reference(reference, publications, current=True, t=t)
            if publication["payload_type"] != "home-live-geometry-1":
                reject("invalid_reference", "geometry cut can contain only separately authorized geometry")
        for engagement in engagements:
            if (engagement["watermark"] != {"stream_epoch": commit["stream_epoch"], "input_cursor": commit["input_cursor"],
                                              "semantic_seq": cp["state"]["semantic_seq"], "input_cut_digest": cut["cut_digest"]}
                    or engagement["available_at"] != t or engagement["valid_until"] <= t):
                reject("invalid_commit", "engagement must name its exact semantic causal cut")
            support = engagement["support"]
            if support is not None:
                facts = [self._reference(ref, publications, supplied=True, current=engagement["state"] in {"candidate", "supported", "ending"}, t=t)
                         for ref in support["evidence_refs"]]
                if self.ingress.config["version"] in {7, 9, 10, 11, 12, 13} and not self.policy["engagement_policy"]["allow_experimental"] and any(p["payload_type"] == "home-live-fact-1" for p in facts) and not self.qualified_pc_support(facts, cp, t):
                    reject("invalid_commit", "operator experimental admission is disabled")
                from live_lifecycle import qualified_negative
                def matching_support(p):
                    if lifecycle_mode and p["payload_type"] == "home-live-person-lifecycle-1":
                        return engagement["state"] == "ending" and qualified_negative(p["payload"], self.policy["lifecycle_policy"], engagement["person_ref"], engagement["subject_ref"])
                    return (p["payload_type"] == "home-live-fact-1" and p["payload"]["person_ref"] == engagement["person_ref"]
                            and p["payload"]["subject_ref"] == engagement["subject_ref"])
                if not facts or any(not matching_support(p) for p in facts):
                    reject("invalid_reference", "engagement support must belong to the same person and stable subject")
                roots = {root for publication in facts for root in publication["payload"]["observation_roots"]}
                if set(support["observation_roots"]) != roots:
                    reject("invalid_reference", "engagement roots must preserve exact original observation ancestry")
                horizon = min(min(p["payload"]["valid_until"], p["payload"]["observed_at"] + self.ingress.sources[p["payload"]["source_id"]]["max_age_s"])
                              - p["payload"]["clock"]["uncertainty_s"] for p in facts)
                if support["basis_valid_until"] > horizon:
                    reject("invalid_reference", "support cannot refresh original evidence expiry")
                if engagement["state"] in {"candidate", "supported"}:
                    if not any(p["payload"]["method_ref"] in self.policy["engagement_policy"]["support_methods"]
                               and (p["payload"]["kind"] == "object_interaction" and p["payload"]["value"] is True
                                    or p["payload"]["kind"] == "hand_material_relation" and p["payload"]["value"] in {"supported_holding", "contact", "manipulation"}) for p in facts) and not self.qualified_pc_support(facts, cp, t):
                        reject("invalid_reference", "positive engagement cannot be created by proximity, object visibility or activity words")
                    for publication in publications.values():
                        negative = publication["payload"]
                        if (lifecycle_mode and publication["payload_type"] == "home-live-person-lifecycle-1"
                                and qualified_negative(negative, self.policy["lifecycle_policy"], engagement["person_ref"], engagement["subject_ref"])
                                and self.ingress._current_payload(negative, t)):
                            reject("invalid_commit", "fresh qualified lifecycle contradiction blocks positive support")
                        if (publication["payload_type"] == "home-live-fact-1" and negative["person_ref"] == engagement["person_ref"]
                                and negative["subject_ref"] == engagement["subject_ref"] and negative["status"] == "supplied"
                                and negative["method_ref"] in self.policy["engagement_policy"]["negative_methods"]
                                and self.ingress._current_payload(negative, t)
                                and (negative["kind"] == "negative_engagement" or negative["kind"] == "object_interaction" and negative["value"] is False
                                     or negative["kind"] == "hand_material_relation" and negative["value"] == "not_interacting")):
                            reject("invalid_commit", "fresh observed contradiction blocks every positive support clause")
                if engagement["state"] == "ending" and not all(
                        lifecycle_mode and p["payload_type"] == "home-live-person-lifecycle-1" and qualified_negative(p["payload"], self.policy["lifecycle_policy"], engagement["person_ref"], engagement["subject_ref"])
                        or p["payload_type"] == "home-live-fact-1" and p["payload"]["method_ref"] in self.policy["engagement_policy"]["negative_methods"]
                        and (p["payload"]["kind"] == "negative_engagement" or p["payload"]["kind"] == "object_interaction" and p["payload"]["value"] is False
                             or p["payload"]["kind"] == "hand_material_relation" and p["payload"]["value"] == "not_interacting") for p in facts):
                    reject("invalid_reference", "observed exit requires fresh positively supplied negative evidence")
                if engagement["state"] in {"candidate", "supported", "ending"} and (support["basis_valid_until"] <= t or engagement["valid_until"] > support["basis_valid_until"]):
                    reject("expired", "positive engagement expires with its selected support clause")
            elif engagement["state"] in {"candidate", "supported"}:
                reject("invalid_reference", "positive state requires an attributable support clause")
            for alternative in engagement["alternatives"]:
                for reference in alternative["evidence_refs"]:
                    alternative_publication = self._reference(reference, publications, supplied=True, current=True, t=t)
                    alternative_input = alternative_publication["payload"]
                    if lifecycle_mode and alternative_publication["payload_type"] == "home-live-person-lifecycle-1":
                        from live_lifecycle import qualified_negative
                        if not qualified_negative(alternative_input, self.policy["lifecycle_policy"], engagement["person_ref"], engagement["subject_ref"]):
                            reject("invalid_reference", "lifecycle alternative must be a commissioned negative for its actual relationship")
                    elif alternative_input["person_ref"] != engagement["person_ref"] or alternative_input["subject_ref"] != engagement["subject_ref"]:
                        reject("invalid_reference", "alternative must remain scoped to its actual relationship")
            refinement = engagement["refinement"]
            refinement_facts = [self._reference(reference, publications, supplied=True, current=refinement["status"] == "supported", t=t)["payload"]
                                for reference in refinement["evidence_refs"]]
            if any(fact["person_ref"] != engagement["person_ref"] or fact["subject_ref"] != engagement["subject_ref"] for fact in refinement_facts):
                reject("invalid_reference", "activity refinement must preserve the same relationship association")
            if refinement["status"] == "supported":
                if (engagement["state"] != "supported" or not any(fact["kind"] == "activity_refinement"
                        and fact["value"] == refinement["activity"] and fact["method_ref"] in self.policy["engagement_policy"]["refinement_methods"] for fact in refinement_facts)
                        or refinement["activity"] == "reading" and not any(fact["kind"] == "material_state" and fact["value"] == "open"
                             and fact["method_ref"] in self.policy["engagement_policy"]["refinement_methods"] for fact in refinement_facts)):
                    reject("invalid_reference", "supported refinement requires its admitted observed distinction and use evidence")
        relations = list(cp["state"]["engagement"]["reducer"]["state"]["relations"].values())
        if len(relations) != len(engagements):
            reject("invalid_commit", "publish every retained reducer relationship in the same transaction")
        for relation in relations:
            matches = [value for value in engagements if value["person_ref"] == relation["person_ref"] and value["subject_ref"] == relation["subject_ref"]]
            if len(matches) != 1 or any(matches[0][key] != relation[key] for key in ("state", "episode_id")):
                reject("invalid_commit", "engagement output cannot diverge from its checkpoint relationship")
            output = matches[0]
            if output["support"] is not None:
                chosen = relation["chosen_clause"]
                if (chosen is None or output["support"]["clause_id"] != chosen["id"]
                        or sorted(reference["id"] for reference in output["support"]["evidence_refs"]) != sorted(chosen["fact_ids"])
                        or output["support"]["basis_valid_until"] != chosen["valid_until"]):
                    reject("invalid_commit", "public supporting clause must match its durable reducer selection")
                if self.ingress.config["version"] >= 6:
                    rows = [publications[ref["cursor"]] for ref in output["support"]["evidence_refs"]]
                    if any(p["payload_type"] == "home-live-person-lifecycle-1" for p in rows):
                        from live_lifecycle import qualified_negative
                        if not rows or any(p["payload_type"] != "home-live-person-lifecycle-1" or not qualified_negative(p["payload"], self.policy["lifecycle_policy"], relation["person_ref"], relation["subject_ref"]) for p in rows):
                            reject("invalid_reference", "one qualified lifecycle clause cannot mix unrelated support")
                        payloads = [p["payload"] for p in rows]
                        roots = sorted({r for p in payloads for r in p["observation_roots"]})
                        expected_clause = {"id": "clause:" + digest({"kind": "negative_engagement", "ids": sorted(p["id"] for p in payloads)})[:24],
                            "kind": "negative_engagement", "fact_ids": sorted(p["id"] for p in payloads),
                            "source_ids": sorted({p["source_id"] for p in payloads}),
                            "sensor_roots": sorted({r for p in payloads for r in p["sensor_roots"]}),
                            "observation_roots": roots, "progression_roots": roots,
                            "parent_evidence_ids": sorted({r["id"] for p in payloads for r in p["basis_refs"]}),
                            "observed_at": max(p["observed_at"] for p in payloads),
                            "valid_until": min(min(p["valid_until"], p["observed_at"] + self.ingress.sources[p["source_id"]]["max_age_s"]) - p["clock"]["uncertainty_s"] for p in payloads),
                            "qualification": "qualified_lifecycle", "progression_start_at": min(p["observed_at"] for p in payloads)}
                        if chosen != expected_clause:
                            reject("invalid_reference", "qualified lifecycle clause must preserve its exact original roots, timestamps and basis")
        reference = decision["engagement_ref"]
        if len(engagements) != 1:
            if reference is not None:
                reject("invalid_reference", "empty or concurrent state has no implicit primary beneficiary")
        else:
            selected = engagements[0]
            expected_ref = {"commit_id": commit["id"], "producer_id": grant.producer_id, "id": selected["id"], "payload_digest": digest(selected)}
            if reference != expected_ref:
                reject("invalid_reference", "decision must bind its exact same-transaction engagement output")
        return decision, engagements

    def _private_inputs(self, cp, publications):
        cache = cp["state"]["engagement"]["reducer"]["state"]["cache"]
        facts = [publication for publication in publications.values() if publication["payload_type"] == "home-live-fact-1"]
        received = {receipt["cursor"]: receipt["received_at"] for receipt in cp["state"]["engagement"]["input_receipts"]}
        for fact in cache.values():
            matches = [publication for publication in facts if publication["payload"]["id"] == fact["id"]]
            if len(matches) != 1:
                reject("invalid_reference", "private reducer facts need unambiguous delivered producer identity")
            publication = matches[0]
            original = json.loads(canonical(publication["payload"]))
            original["available_at"] = max(original["available_at"], publication["committed_at"], received[publication["cursor"]])
            source = self.ingress.sources[original["source_id"]]
            original["valid_until"] = min(original["valid_until"], original["observed_at"] + source["max_age_s"]) - original["clock"]["uncertainty_s"]
            if canonical(fact) != canonical(original):
                reject("invalid_reference", "private reducer facts must preserve observations with explicit causal availability and reduced clock expiry")
        if self.ingress.config["version"] >= 6:
            lifecycle_cache = cp["state"]["engagement"]["reducer"]["state"]["lifecycle_cache"]
            if not isinstance(lifecycle_cache, dict) or len(lifecycle_cache) > 128:
                reject("invalid_reference", "bounded standalone lifecycle cache required")
            for key, payload in lifecycle_cache.items():
                matches = [p for p in publications.values() if p["payload_type"] == "home-live-person-lifecycle-1" and p["id"] == key]
                if len(matches) != 1 or payload.get("id") != key:
                    reject("invalid_reference", "private lifecycle proof must preserve its unique delivered publication")
                publication = matches[0]
                original = json.loads(canonical(publication["payload"]))
                original["available_at"] = max(original["available_at"], publication["committed_at"], received[publication["cursor"]])
                source = self.ingress.sources[original["source_id"]]
                original["valid_until"] = min(original["valid_until"], original["observed_at"] + source["max_age_s"]) - original["clock"]["uncertainty_s"]
                if (canonical(payload) != canonical(original) or not original["available_at"] <= cp["last_committed_at"] < original["valid_until"]
                        or cp["state"]["engagement"]["source_generations"].get(original["source_id"]) != original["source_generation"]):
                    reject("invalid_reference", "private lifecycle proof cannot change original scope, clock, lineage or expiry")
        expected_originals = {canonical(reference) for publication in publications.values() for reference in publication["payload"].get("original_receipt_refs", [])}
        if {canonical(reference) for reference in cp["state"]["original_receipts"]} != expected_originals:
            reject("invalid_reference", "private original receipts must equal the exact delivered input lineage")

    def commit(self, commit, grant):
        if not isinstance(commit, dict):
            reject("invalid_commit", "typed commit object required", 400)
        self._worker(grant, commit.get("site_id"), commit.get("room_id"))
        self.j.schemas.validate(commit)
        if commit["schema"] != self.commit_type or commit["worker_id"] != grant.expected_worker_id or commit["producer_id"] != grant.producer_id:
            reject("invalid_commit")
        bounded_json(commit, self.commit_body_limit())
        def apply():
            j = self.j
            marker = "worker-commit/" + commit["id"]
            retry = j.db.execute("SELECT digest,receipt FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?",
                                 (grant.producer_id, commit["stream_epoch"], marker)).fetchone()
            if retry:
                if retry["digest"] != digest(commit):
                    reject("identity_conflict", "worker commit retry changed its original bytes")
                return json.loads(retry["receipt"])
            if commit["stream_epoch"] != j._meta("epoch"):
                reject("reset_required", "worker journal epoch changed")
            self.ingress._prune()
            row = j.db.execute("SELECT * FROM workers WHERE worker_id=? AND site_id=? AND room_id=?",
                               (commit["worker_id"], commit["site_id"], commit["room_id"])).fetchone()
            previous, seq = (row["input_cursor"], row["context_seq"]) if row else (0, 0)
            old = json.loads(row["checkpoint"]) if row else None
            if (commit["expected_input_cursor"] != previous or commit["expected_context_seq"] != seq
                    or not previous <= commit["input_cursor"] <= int(j._meta("cursor"))):
                reject("checkpoint_conflict", "worker cursor or sequence advanced concurrently")
            delivered = j.db.execute("SELECT delivered FROM consumers WHERE principal_id=? AND consumer_id=? AND site_id=? AND room_id=?",
                                     (grant.principal_id, commit["worker_id"], commit["site_id"], commit["room_id"])).fetchone()
            if delivered is None or commit["input_cursor"] > delivered["delivered"]:
                reject("unseen_cursor", "worker cannot consume a cursor outside its delivered public cut")
            cut = j.db.execute("SELECT * FROM live_worker_cuts WHERE principal_id=? AND consumer_id=?",
                               (grant.principal_id, commit["worker_id"])).fetchone()
            if cut is None or cut["cursor"] != commit["input_cursor"] or cut["authority_generation"] != j.revocation_generation(commit["site_id"], commit["room_id"]):
                reject("revocation_conflict", "worker must commit its complete latest atomic input cut")
            cp = commit["checkpoint"]
            t = self._checkpoint(cp, commit, seq + 1, old, j.clock())
            publications = self._receipts(commit, grant, t)
            if (set(json.loads(cut["cursors"])) != set(publications) if self.ingress.config["version"] in {7, 9, 10, 11, 12, 13} else not set(json.loads(cut["cursors"])) <= set(publications)):
                reject("invalid_input_receipt", "complete current observations, including alternatives, must remain in the decision cut")
            self._private_inputs(cp, publications)
            decision, engagements = self._outputs(commit, grant, publications, seq + 1, t)
            self.settle(commit, grant, publications, decision, engagements, old)
            if row is None and j.db.execute("SELECT count(*) FROM workers").fetchone()[0] >= j.limits.max_workers:
                reject("journal_full", "worker authority budget exhausted", 507)
            for event in commit["publications"]:
                if j._receipt(event) is not None:
                    reject("output_identity_reused", "a new commit cannot reuse a durable output identity")
            receipts = []
            j.db.execute("DELETE FROM live_worker_current")
            for event in commit["publications"]:
                receipt, publication = self.ingress._record(event, max_bytes=self.derived_event_limit(event))
                receipts.append(receipt)
                payload = event["payload"]
                key = canonical([event["payload_type"], payload.get("person_ref"), payload.get("subject_ref")])
                j.db.execute("INSERT INTO live_worker_current VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET authority_generation=excluded.authority_generation,valid_until=excluded.valid_until,body=excluded.body",
                             (key, event["payload_type"], cp["authority_generation"], event["valid_until"], canonical(publication)))
            if j.db.execute("SELECT coalesce(sum(length(CAST(body AS BLOB))),0) FROM live_worker_current").fetchone()[0] > j.limits.max_state_bytes:
                reject("journal_full", "current derived worker output budget exhausted", 507)
            j.db.execute("INSERT INTO workers VALUES(?,?,?,?,?,?) ON CONFLICT(worker_id,site_id,room_id) DO UPDATE SET input_cursor=excluded.input_cursor,context_seq=excluded.context_seq,checkpoint=excluded.checkpoint",
                         (commit["worker_id"], commit["site_id"], commit["room_id"], commit["input_cursor"], seq + 1, canonical(cp)))
            self.prune_deliveries()
            result = {"schema": "home-context-worker-commit-receipt-1", "id": commit["id"], "worker_id": commit["worker_id"],
                      "stream_epoch": j._meta("epoch"), "input_cursor": commit["input_cursor"], "context_seq": seq + 1,
                      "cursor": int(j._meta("cursor")), "receipts": receipts, "status": "accepted", "commit_digest": digest(commit)}
            if j.db.execute("SELECT count(*) FROM receipts").fetchone()[0] >= j.limits.max_receipts:
                reject("journal_full", "durable commit identity budget exhausted", 507)
            j.db.execute("INSERT INTO receipts(producer_id,source_epoch,event_id,digest,receipt,event) VALUES(?,?,?,?,?,?)",
                         (grant.producer_id, commit["stream_epoch"], marker, digest(commit), canonical(result), canonical({"schema": "home-context-worker-commit-tombstone-1", "site_id": commit["site_id"], "room_id": commit["room_id"]})))
            return result
        return self.j._transaction(apply)

    def settle(self, commit, grant, publications, decision, engagements, old):
        """A later commissioned stage may add atomic, bounded state settlements."""
        return None

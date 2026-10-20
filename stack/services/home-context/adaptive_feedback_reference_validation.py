"""Native compact feedback carrier; expansion is a private semantic view.

This profile preserves the E observation, authority, intent and installation
rules.  It changes only the checkpoint's repeated-original representation.
"""
import copy
import hashlib
import json

from journal import JournalError, canonical, digest, finite
from adaptive_feedback_validation import (
    AdaptiveFeedbackProfile, REVISION_FIELDS, SCOPE_KEYS, _same_scope,
    _publication_ref, component_id,
    original_family_clocks, validate_checkpoint_frontier,
    validate_authority,
    validate_configuration as validate_feedback_configuration,
    validate_feedback,
)


REFERENCE_PROFILE = "adaptive-book-feedback-reference-synthetic-1"
REFERENCE_MODE = "feedback_reference"
ROLE_FIELDS = ("pending_inputs", "geometry_inputs", "retained_inputs", "committed_output_inputs")
MASK_ROLES = frozenset(ROLE_FIELDS[:3])
MASK_FAMILIES = frozenset({"home-geometry-4", "home-fact-batch-3"})
EXTERNAL_FAMILIES = frozenset({"home-lighting-control-decision-3", "home-lighting-installed-boundary-1",
                               "home-lighting-policy-decision-3", "home-lighting-settled-exposure-1"})
PROJECTION_FIELDS = ("installed_at", "requests", "targets", "environment_heads", "support_bounds")


def _error(message):
    raise JournalError("invalid_reference", message, 409)


def dependency_pool(checkpoint):
    """Check the compact union without treating a digest as original authority."""
    try:
        if checkpoint["schema"] != "home-context-worker-checkpoint-8" or checkpoint["version"] != 8:
            _error("reference checkpoint8 required")
        descriptors = checkpoint["dependency_receipts"]
        if not isinstance(descriptors, list) or len(descriptors) > 96:
            _error("reference original pool exceeds its unchanged bound")
        indexed, cursors = {}, set()
        for item in descriptors:
            if set(item) != {"source_ref", "received_at", "eligible_at", "digest"}:
                _error("closed dependency descriptor required")
            source, parent = item["source_ref"], item["source_ref"]["publication_ref"]
            if (set(source) != {"publication_ref", "publication_id", "producer_id", "producer_epoch", "payload_type", "payload_version", "component"}
                    or set(parent) != {"journal_epoch", "cursor", "payload_digest"} or source["component"] is not None
                    or parent["journal_epoch"] != checkpoint["journal_epoch"] or type(parent["cursor"]) is not int
                    or not 0 < parent["cursor"] <= checkpoint["input_cursor"]
                    or not finite(item["received_at"]) or not finite(item["eligible_at"])
                    or not 0 <= item["received_at"] <= checkpoint["last_t"] <= 120
                    or not item["received_at"] <= item["eligible_at"] <= 120
                    or item["digest"] != digest({k: v for k, v in item.items() if k != "digest"})
                    or item["digest"] in indexed or parent["cursor"] in cursors):
                _error("reference dependency origin, clock, digest or uniqueness differs")
            indexed[item["digest"]] = item
            cursors.add(parent["cursor"])
        used = set()
        for role in ROLE_FIELDS:
            for entry in checkpoint[role]:
                if set(entry) - {"dependency_digest", "component_ids"} or "dependency_digest" not in entry:
                    _error("closed ordered role reference required")
                item = indexed.get(entry["dependency_digest"])
                if item is None or entry["dependency_digest"] in used:
                    _error("role reference is absent or repeated in the original union")
                if "component_ids" in entry:
                    ids = entry["component_ids"]
                    if (role not in MASK_ROLES or item["source_ref"]["payload_type"] not in MASK_FAMILIES
                            or not isinstance(ids, list) or not ids or len(ids) > 16
                            or any(not isinstance(value, str) for value in ids) or ids != sorted(set(ids))):
                        _error("component masks are limited to unchanged ordinary geometry/fact roles")
                if role == "committed_output_inputs" and item["source_ref"]["payload_type"] not in {
                        "home-lighting-policy-decision-3", "home-lighting-settled-exposure-1"}:
                    _error("committed role requires an unchanged policy or settled original")
                used.add(entry["dependency_digest"])
        for item in checkpoint["referenced_outputs"]:
            if indexed.get(item["digest"]) != item or item["digest"] in used or item["source_ref"]["payload_type"] not in EXTERNAL_FAMILIES:
                _error("external output must remain an exact unique pool descriptor")
            if item["eligible_at"] > checkpoint["last_t"]:
                _error("external output requires an eligible original")
            used.add(item["digest"])
        if used != set(indexed):
            _error("role and external original union must equal the complete dependency pool")
        seen_revisions = set()
        for entry in checkpoint["revisions"]["rows"]:
            if set(entry) != {"dependency_digest"} or entry["dependency_digest"] not in indexed or entry["dependency_digest"] in seen_revisions:
                _error("ordered revision reference must be a unique mask-free pool member")
            if indexed[entry["dependency_digest"]]["source_ref"]["payload_type"] not in REVISION_FIELDS:
                _error("revision role requires its original revision family")
            seen_revisions.add(entry["dependency_digest"])
        return indexed
    except (KeyError, TypeError, ValueError, RecursionError):
        _error("malformed reference checkpoint union")


def expand_exposure_projection(pipeline):
    if pipeline is None:
        return
    context = pipeline["exposure_context"]
    if isinstance(context, dict) and context.get("representation") == "held_context_projection_1":
        held = pipeline["feedback_exposure"]["held_context"]
        if set(context) != {"representation", "digest"} or held is None or any(k not in held for k in PROJECTION_FIELDS):
            _error("held-context projection requires its complete exact parent")
        projection = {k: copy.deepcopy(held[k]) for k in PROJECTION_FIELDS}
        if digest(projection) != context["digest"]:
            _error("held-context projection digest differs")
        pipeline["exposure_context"] = projection


def hydrate_reference_checkpoint(checkpoint, originals):
    """Resolve only encoded roles/revisions; external output expansion stays separate.

    Callers authenticate originals and FIRST.  This helper proves exact supplied
    identity, bytes and clocks, and deliberately does not establish authority.
    """
    pool = dependency_pool(checkpoint)
    if not isinstance(originals, list) or len(originals) != len(pool):
        _error("exact bounded original pool required for reference hydration")
    supplied = {}
    try:
        for original in originals:
            if set(original) != {"publication", "first_received_at"}:
                _error("closed original plus genuine FIRST required")
            p = original["publication"]
            cursor = p["cursor"]
            if cursor in supplied:
                _error("hydration original cursor repeated")
            supplied[cursor] = original
        recovered = {}
        for key, descriptor in pool.items():
            ref = descriptor["source_ref"]
            original = supplied.get(ref["publication_ref"]["cursor"])
            if original is None:
                _error("referenced original is unavailable")
            p = original["publication"]
            source = {"publication_ref": {k: p[k] for k in ("journal_epoch", "cursor", "payload_digest")},
                      "publication_id": p["id"], "producer_id": p["producer_id"], "producer_epoch": p["producer_epoch"],
                      "payload_type": p["payload_type"], "payload_version": p["payload_version"], "component": None}
            clocks = original_family_clocks(p["payload"], p["committed_at"])
            if (source != ref or p["stream_epoch"] != checkpoint["journal_epoch"]
                    or not _same_scope(p["payload"], checkpoint) or digest(p["payload"]) != p["payload_digest"]
                    or p["id"] != p["payload"]["id"] or p["payload_type"] != p["payload"]["schema"]
                    or p["payload_version"] != str(p["payload"]["version"]) + ".0"
                    or original["first_received_at"] != descriptor["received_at"]
                    or p["committed_at"] > descriptor["received_at"]
                    or max(*clocks, p["committed_at"], descriptor["received_at"]) != descriptor["eligible_at"]):
                _error("reference hydration changed original identity, scope, bytes or FIRST")
            recovered[key] = {"publication": copy.deepcopy(p), "received_at": descriptor["received_at"]}
        view = copy.deepcopy(checkpoint)
        for role in ROLE_FIELDS:
            view[role] = []
            for entry in checkpoint[role]:
                row = copy.deepcopy(recovered[entry["dependency_digest"]])
                if "component_ids" in entry:
                    row["component_ids"] = copy.deepcopy(entry["component_ids"])
                    payload = row["publication"]["payload"]
                    allowed = {component_id(value, payload["schema"] == "home-geometry-4") for value in
                               (payload["objects"] if payload["schema"] == "home-geometry-4" else payload["facts"])}
                    if not set(row["component_ids"]) <= allowed:
                        _error("role mask differs from its exact original components")
                view[role].append(row)
        view["revisions"]["rows"] = [copy.deepcopy(recovered[entry["dependency_digest"]]) for entry in checkpoint["revisions"]["rows"]]
        expand_exposure_projection(view["pipeline"])
        return view
    except (KeyError, TypeError, ValueError, RecursionError):
        _error("malformed hydration original or projection")


def _commit_errors(commit):
    cp, cut = commit["checkpoint"], commit["input_cut"]
    errors = []
    if (commit["kind"] not in {"bootstrap", "semantic", "control"} or not cut["complete"]
            or any(x["runtime_epoch"] != commit["runtime_epoch"] for x in (cp, cut))
            or any(x["journal_epoch"] != commit["journal_epoch"] for x in (cp, cut))
            or cp["last_t"] != cut["as_of_t"] or cp["input_cursor"] != cut["input_cursor"]
            or commit["expected_input_cursor"] > cp["input_cursor"]
            or cp["commit_seq"] != commit["expected_commit_seq"] + 1 or commit["expected_authority"] != cut["authority"]):
        errors.append("commit_internal_frontier")
    if any(cp["authority"][k] != cut["authority"][k] for k in ("room", "sources", "registrations", "producers")):
        errors.append("commit_authority_fence")
    fence = lambda f: canonical([f[k] for k in ("person_ref", "object_ref", "association_generation", "source_id", "registration_ref")])
    pre, post = cut["authority"]["follows"], cp["authority"]["follows"]
    if len(pre) != len(post):
        errors.append("commit_follow_set")
    pipeline = cp["pipeline"] or {}
    for current in pre:
        following = next((x for x in post if fence(x) == fence(current)), None)
        if following is None:
            errors.append("commit_follow_set"); continue
        if following == current:
            continue
        owned = []
        for binding in cp["intent_authority_bindings"]:
            if fence(binding) != fence(current):
                continue
            intent = next((i for i in pipeline.get("intents", []) if i["id"] == binding["intent_id"] and i["kind"] == "dont_follow"
                           and i["effective_at"] <= binding["applied_at"] < i["valid_until"]
                           and i["person_ref"] == binding["person_ref"] and i["object_ref"] == binding["object_ref"]), None)
            if intent and any(x["intent_id"] == binding["intent_id"] and x["status"] == "applied" and x["decided_at"] == binding["applied_at"] for x in pipeline.get("intent_outcomes", [])):
                owned.append((binding, intent))
        active = [b for b, i in owned if i["valid_until"] > cp["last_t"]]
        closing = current["follow_allowed"] and not following["follow_allowed"] and following["follow_generation"] == current["follow_generation"] + 1 and any(b["baseline_follow_generation"] == current["follow_generation"] for b in active)
        opening = not current["follow_allowed"] and following["follow_allowed"] and following["follow_generation"] == current["follow_generation"] + 1 and not active and any(b["baseline_follow_generation"] + 1 == current["follow_generation"] for b, i in owned)
        if not closing and not opening:
            errors.append("commit_follow_unproven")
    return errors


def _proof_errors(proof):
    c, r, p = proof["commit"], proof["receipt"], proof["publication"]
    wire, errors = proof["request_canonical_json"], []
    if (wire != canonical(c) or len(wire.encode("utf-8")) > 262144 or hashlib.sha256(wire.encode("utf-8")).hexdigest() != r["raw_request_digest"]
            or r["id"] != c["id"] or any(r[k] != c["checkpoint"][k] for k in ("commit_seq", "semantic_seq", "control_seq", "input_cursor"))
            or r["commit_seq"] != c["expected_commit_seq"] + 1 or r["input_cursor"] != c["input_cut"]["input_cursor"]
            or any(x["journal_epoch"] != proof["journal_epoch"] for x in (c, c["input_cut"], c["checkpoint"], r, p))
            or p["stream_epoch"] != proof["journal_epoch"] or p["producer_epoch"] != r["producer_epoch"] or p["producer_id"] != r["worker_id"]
            or p["context_seq"] != r["commit_seq"] or not c["input_cut"]["as_of_t"] <= r["first_received_at"] <= p["committed_at"] <= proof["resolved_at"]):
        errors.append("worker_output_proof_join")
    errors.extend(_commit_errors(c))
    outputs = {(x["schema"], x["id"]): x for x in c["outputs"]}
    rows = r["receipts"]; cursors = [x["cursor"] for x in rows]
    if (len(outputs) != len(c["outputs"]) or len({x["id"] for x in rows}) != len(rows) or len(rows) != len(outputs)
            or cursors != sorted(set(cursors)) or r["cursor"] != max([r["input_cursor"], *cursors])):
        errors.append("worker_output_proof_outputs")
    for index, row in enumerate(rows):
        matches = [x for x in c["outputs"] if x["id"] == row["id"]]
        if (len(matches) != 1 or index >= len(c["outputs"]) or c["outputs"][index]["id"] != row["id"]
                or row["payload_digest"] != digest(matches[0]) or row["producer_id"] != p["producer_id"]
                or row["producer_epoch"] != p["producer_epoch"] or row["stream_epoch"] != p["stream_epoch"]
                or row["cursor"] <= c["input_cut"]["input_cursor"] or not r["first_received_at"] <= row["committed_at"] <= proof["resolved_at"]):
            errors.append("worker_output_proof_receipt")
    joined = [x for x in rows if x["cursor"] == p["cursor"]]
    if (outputs.get((p["payload_type"], p["id"])) != p["payload"] or len(joined) != 1
            or any(joined[0][k] != p[k] for k in ("id", "producer_id", "producer_epoch", "stream_epoch", "committed_at", "payload_digest"))):
        errors.append("worker_output_proof_publication")
    for key in ("consumer_receipt", "worker_receipt"):
        if proof[key] is not None and not p["committed_at"] <= proof[key]["received_at"] <= proof[key]["eligible_at"] <= proof["resolved_at"]:
            errors.append("worker_output_proof_delivery")
    return errors


def validate_reference(value, registry=None):
    if registry is None:
        from schema_registry import SchemaRegistry
        registry = SchemaRegistry((), profile=REFERENCE_PROFILE)
    if isinstance(value, dict) and value.get("schema") == "home-context-worker-checkpoint-response-6":
        try:
            registry.validate(value)
            cp = value["checkpoint"]
            errors = validate_reference(cp, registry)["errors"] if cp is not None else []
            if cp is not None and (cp["journal_epoch"] != value["journal_epoch"] or cp["input_cursor"] != value["input_cursor"]
                                   or any(cp[k] != value[k] for k in ("commit_seq", "semantic_seq", "control_seq"))):
                errors.append("feedback_reference_checkpoint_response")
            if cp is None and (value["input_cursor"] != 0 or value["commit_seq"] != 0 or value["semantic_seq"] != -1 or value["control_seq"] != 0):
                errors.append("feedback_reference_empty_checkpoint_response")
            follows = value["authority"]["follows"]
            scope_source = cp if cp is not None else follows[0]["person_ref"] if follows else None
            identity = {k: scope_source[k] for k in SCOPE_KEYS} if scope_source is not None else {k: "unbound-empty-response" for k in SCOPE_KEYS}
            validate_authority(value["authority"], identity)
            return {"ok": not errors, "errors": errors}
        except (JournalError, KeyError, TypeError, ValueError, RecursionError, UnicodeError):
            return {"ok": False, "errors": ["feedback_reference_checkpoint_response"]}
    result = validate_feedback(value, registry)
    if not result["ok"]:
        return result
    errors = []
    def visit(v):
        if isinstance(v, list):
            for item in v: visit(item)
            return
        if not isinstance(v, dict): return
        name = v.get("schema")
        if name == "home-context-worker-checkpoint-8":
            if v["interface_digest"] != registry.interface_digest:
                errors.append("feedback_reference_interface_pin")
            try:
                dependency_pool(v)
                expand_exposure_projection(copy.deepcopy(v["pipeline"]))
                validate_checkpoint_frontier(v)
            except JournalError as error:
                errors.append(error.code)
        if name == "home-context-worker-commit-8": errors.extend(_commit_errors(v))
        if name == "home-context-worker-output-proof-4": errors.extend(_proof_errors(v))
        if name == "home-context-worker-original-lookup-1":
            original, first = v["original"], v["worker_receipt"]
            publication = original["publication"]
            if first is not None:
                if (publication is None or first["consumer_id"] != v["worker_id"]
                        or first["digest"] != digest({k: x for k, x in first.items() if k != "digest"})
                        or not publication["committed_at"] <= first["received_at"] <= original["resolved_at"]
                        or first["eligible_at"] != max(*original_family_clocks(publication["payload"], publication["committed_at"]), publication["committed_at"], first["received_at"])):
                    errors.append("feedback_reference_worker_first")
        if name == "home-context-replay-9" and (v["interface_digest"] != registry.interface_digest or v["digest"] != digest({k: x for k, x in v.items() if k != "digest"})):
            errors.append("feedback_reference_replay_pin_digest")
        if name == "home-context-worker-checkpoint-response-6" and v["checkpoint"] is not None:
            cp = v["checkpoint"]
            if (cp["journal_epoch"] != v["journal_epoch"] or cp["input_cursor"] != v["input_cursor"]
                    or any(cp[k] != v[k] for k in ("commit_seq", "semantic_seq", "control_seq"))):
                errors.append("feedback_reference_checkpoint_response")
        for item in v.values(): visit(item)
    try:
        visit(value)
    except (KeyError, TypeError, ValueError, RecursionError, UnicodeError):
        errors.append("feedback_reference_semantic_shape")
    return {"ok": not errors, "errors": errors}


def validate_configuration(value, registry=None):
    if registry is None:
        from schema_registry import SchemaRegistry
        registry = SchemaRegistry((), profile=REFERENCE_PROFILE)
    return validate_feedback_configuration(value, registry, profile_mode=REFERENCE_MODE)


class AdaptiveFeedbackReferenceProfile(AdaptiveFeedbackProfile):
    commit_schema = "home-context-worker-commit-8"
    output_proof_schema = "home-context-worker-output-proof-4"
    output_proof_version = 4
    checkpoint_response_schema = "home-context-worker-checkpoint-response-6"
    checkpoint_response_version = 6

    def validate_payload(self, value):
        result = validate_reference(value, self.j.schemas)
        if not result["ok"]:
            raise JournalError("invalid_payload", "invalid reference feedback contract", errors=result["errors"])
        if all(key in value for key in SCOPE_KEYS) and not _same_scope(self.identity, value):
            raise JournalError("scope_mismatch", "reference feedback session identity changed")
        if "clock_profile_digest" in value and value["clock_profile_digest"] != self.clock_profile["digest"]:
            raise JournalError("clock_profile_mismatch", "reference feedback clock profile changed")
        if value.get("schema") == "home-context-worker-checkpoint-response-6":
            validate_authority(value["authority"], self.identity)
        return value

    def _hydrate_checkpoint_outputs(self, checkpoint, grant, cut=None):
        self.validate_payload(checkpoint)
        originals, size = [], 0
        claims = {r["ref"]["cursor"]: r for r in cut["receipts"]} if cut is not None else {}
        for descriptor in dependency_pool(checkpoint).values():
            p, _ = self._source_component(descriptor["source_ref"], grant, through=checkpoint["input_cursor"], historical=True)
            delivered = self.j.db.execute("SELECT received_at,eligible_at,digest FROM feedback_consumer_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",
                                          (grant.principal_id, grant.expected_consumer_id, p["cursor"])).fetchone()
            prior = self.j.db.execute("SELECT received_at,eligible_at FROM feedback_worker_first_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",
                                      (grant.principal_id, grant.expected_consumer_id, p["cursor"])).fetchone()
            first = {"consumer_id": grant.expected_consumer_id, "received_at": delivered[0], "eligible_at": delivered[1]} if delivered else None
            claim = claims.get(p["cursor"])
            own_output = p["producer_id"] == self._worker_producer and p["producer_epoch"] == grant.producer_epoch and p["payload_type"] in EXTERNAL_FAMILIES
            if (not delivered or delivered[2] != digest(first) or delivered[1] != self._eligible(p, delivered[0])
                    or not max(p["committed_at"], delivered[0]) <= descriptor["received_at"] <= min(checkpoint["last_t"], self.j.clock())
                    or descriptor["eligible_at"] != self._eligible(p, descriptor["received_at"])
                    or prior and tuple(prior) != (descriptor["received_at"], descriptor["eligible_at"])
                    or not prior and (cut is None or p["payload_type"] not in {"home-lighting-policy-decision-3", "home-lighting-settled-exposure-1"}
                        and claim != {"ref": descriptor["source_ref"]["publication_ref"], "received_at": descriptor["received_at"], "eligible_at": descriptor["eligible_at"]})):
                raise JournalError("invalid_input_receipt", "reference original lacks unchanged authenticated worker FIRST", 409)
            if own_output:
                self._admitted_reference_output(p, grant)
            size += len(canonical(p).encode("utf-8"))
            if size > min(self.j.limits.max_publication_bytes, 11 * 1024 * 1024):
                raise JournalError("invalid_reference", "reference hydration original bytes exceed the unchanged bound", 409)
            originals.append({"publication": p, "first_received_at": descriptor["received_at"]})
        view = hydrate_reference_checkpoint(checkpoint, originals)
        return self._expand_referenced_outputs(view, grant, cut)

    def _stored_checkpoint_view(self, checkpoint, grant):
        return self._hydrate_checkpoint_outputs(checkpoint, grant)

    def _phase_transition_wire(self, commit, old, grant):
        current = {**commit, "checkpoint": self._hydrate_checkpoint_outputs(commit["checkpoint"], grant, commit["input_cut"])}
        prior = self._hydrate_checkpoint_outputs(old, grant) if old is not None else None
        return self._phase_transition(current, prior)

    def lookup_worker_original(self, grant, site, room, *, cursor, consumer_id):
        grant.require("read", site, room)
        self._scope_check(site, room); self._consumer(grant, consumer_id)
        worker_grants = [g for g in self.j.grants if "worker_commit" in g.operations
                         and (site, room) in g.scopes and g.producer_id == self._worker_producer
                         and g.principal_id == grant.principal_id and g.producer_id == grant.producer_id
                         and g.producer_epoch == grant.producer_epoch
                         and g.expected_consumer_id == grant.expected_consumer_id
                         and g.expected_worker_id == grant.expected_worker_id]
        if not worker_grants or grant.expected_worker_id != self._worker_producer or consumer_id != self._worker_producer:
            raise JournalError("forbidden", "worker FIRST lookup requires the configured processing principal and fixed worker reader", 403)
        def read():
            row = self.j.db.execute("SELECT body FROM publications WHERE cursor=? AND site_id=? AND room_id=?", (cursor, site, room)).fetchone()
            if row is None:
                raise JournalError("not_found", "worker original is unavailable in the granted room", 404)
            publication = json.loads(row[0]) if row else None
            if publication is not None:
                self._ref(_publication_ref(publication), grant, self.identity, int(self.j._meta("cursor")), allow_revoked=True)
            delivered = self.j.db.execute("SELECT received_at,eligible_at,digest FROM feedback_consumer_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",
                                          (grant.principal_id, consumer_id, cursor)).fetchone()
            original = {"schema": "home-context-original-lookup-1", "version": 1, **self.identity,
                        "clock_profile_digest": self.clock_profile["digest"], "journal_epoch": self.j.stream_epoch,
                        "resolved_at": self.j.clock(), "publication": publication,
                        "receipt": {"consumer_id": consumer_id, "received_at": delivered[0], "eligible_at": delivered[1], "digest": delivered[2]} if delivered else None}
            self.validate_payload(original)
            processed = self.j.db.execute("SELECT received_at,eligible_at FROM feedback_worker_first_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",
                                          (grant.principal_id, consumer_id, cursor)).fetchone() if publication is not None else None
            first = None
            if processed:
                clock_row = {"consumer_id": self._worker_producer, "received_at": processed[0], "eligible_at": processed[1]}
                first = {**clock_row, "digest": digest(clock_row)}
            result = {"schema": "home-context-worker-original-lookup-1", "version": 1,
                      "worker_id": self._worker_producer, "original": original, "worker_receipt": first}
            self.validate_payload(result)
            return result
        return self.j._transaction(read)

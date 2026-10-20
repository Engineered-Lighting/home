"""Finite, owner-local Lab HA/JEV join for existing live-fact admission.

This source-preparation adapter opens no camera, model, network or journal. Its
caller supplies one actual Journal, its authenticated grant, and immutable Lab
events. Two separately commissioned semantic sources retain the physical roots
of the original modalities. Their source epoch is the semantic collector epoch;
the original camera epoch, HA snapshot and relative JEV clocks remain unchanged
in the private original receipt.

The historical v1 policy remains non-supporting. The explicit v2 policy consumes
config12's fixed PC proof policy and an actual current work publication; it never
creates association or geometry. Its fixed private retainer must acknowledge the
complete original before journal admission. JEV attribution additionally needs a
request-time frozen work reference and matching original consumed visual roots.
The opt-in v3 policy uses the incumbent persisted-observation root projection;
it preserves original event bodies and keeps HA's snapshot-root contract.
Policy4 retains that proof meaning only for config13's assessed-room successor.
No geometry, model invocation or engagement logic lives here.
"""
from copy import deepcopy
from decimal import Decimal
import threading

from journal import Journal, canonical, digest, finite, identifier
from live_capture import _original_digest as _capture_digest
from live_ingress_validation import validate_config, validate_clock
from schema_registry import LIVE_PROFILE, SchemaRegistry
from persisted_evidence import observation_record
from validation import ACTIVITIES


BINDING_KEYS = {"source_id", "method_ref", "association_method_ref", "model_ref",
                "receipt_store_id", "receipt_component_id", "clock_uncertainty_s",
                "kind_sensor_roots"}
POLICY_KEYS = {"schema", "version", "arm", "generation", "ha_source_id",
               "ha_entities", "wire", "response_version", "ha", "jev", "max_events"}


def _original_digest(value):
    return _capture_digest(value, limit=2 * 1024 * 1024)


class SemanticRefusal(ValueError):
    """A bounded category, never a model response or private source content."""


class LabLiveSemanticAdmission:
    """Publish scoped facts through the actual existing Journal.

    Compose resolve_receipt into the journal's fixed original resolver before
    opening it. originals are private and returned only through original_receipt;
    there is no route or raw-data browser projection. This finite object does
    not restore after process loss or grant association/restart continuity.
    """

    def __init__(self, session, *, live_config, revision, policy, original_retainer=None):
        self.config = validate_config(live_config, SchemaRegistry((), profile=LIVE_PROFILE))
        self.scope = self.config["manifest"]["identity"]
        self.v4 = isinstance(policy, dict) and policy.get("schema") == "home-lab-semantic-admission-policy-4"
        self.v3 = isinstance(policy, dict) and policy.get("schema") == "home-lab-semantic-admission-policy-3"
        self.persisted_roots = self.v3 or self.v4
        self.v2 = self.persisted_roots or isinstance(policy, dict) and policy.get("schema") == "home-lab-semantic-admission-policy-2"
        policy_version = 4 if self.v4 else 3 if self.v3 else 2 if self.v2 else 1
        if (not isinstance(session, dict) or session.get("id") != self.scope["session_id"]
                or session.get("site_id") != self.scope["site_id"]
                or not isinstance(session.get("camera_id"), str)
                or not finite(session.get("started_at")) or session["started_at"] < 0
                or not isinstance(policy, dict) or set(policy) != POLICY_KEYS | ({"pc_support_policy_digest"} if self.v2 else set()) | ({"root_lineage"} if self.persisted_roots else set())
                or policy["schema"] != "home-lab-semantic-admission-policy-" + str(policy_version)
                or type(policy["version"]) is not int or policy["version"] != policy_version
                or self.persisted_roots and policy["root_lineage"] != "persisted-observation-record-1"
                or policy["arm"] not in {"a", "b"}
                or type(policy["generation"]) is not int or policy["generation"] < 1
                or type(policy["max_events"]) is not int or not 1 <= policy["max_events"] <= 24
                or policy["wire"] != "den-facts-7"
                or policy["response_version"] != "jev-1.13.0"
                or policy["ha_entities"] != {"active": "derived.computer_active",
                                              "recency": "derived.computer_input_recency"}):
            raise ValueError("exact_lab_semantic_configuration_required")
        identifier(policy["ha_source_id"], "ha_source_id")
        self.session, self.policy = deepcopy(session), deepcopy(policy)
        self.revision = identifier(revision, "revision")
        for family in ("ha", "jev"):
            b = self.policy[family]
            if not isinstance(b, dict) or set(b) != BINDING_KEYS:
                raise ValueError("closed_semantic_source_binding_required")
            source = self.config["manifest"]["sources"].get(b["source_id"])
            kinds = {"ha.observation"} if family == "ha" else {"ha.observation", "perception.output"}
            if (source is None or b["method_ref"] not in source["method_refs"]
                    or b["association_method_ref"] not in source["association_method_refs"]
                    or b["model_ref"] is not None and b["model_ref"] not in source["model_refs"]
                    or family == "ha" and b["model_ref"] is not None
                    or family == "jev" and b["model_ref"] is None
                    or b["receipt_store_id"] not in source["receipt_store_ids"]
                    or b["receipt_component_id"] not in source["receipt_component_ids"]
                    or source["capture_clock"]["original_unit"] != "unix_s"
                    or source["capture_clock"]["epoch_origin_s"] != 0
                    or not finite(b["clock_uncertainty_s"])
                    or not 0 <= b["clock_uncertainty_s"] <= source["capture_clock"]["max_uncertainty_s"]
                    or not isinstance(b["kind_sensor_roots"], dict)
                    or set(b["kind_sensor_roots"]) != kinds):
                raise ValueError("commissioned_semantic_source_required")
            for roots in b["kind_sensor_roots"].values():
                if (not isinstance(roots, list) or not roots or len(roots) != len(set(roots))
                        or not set(roots) <= set(source["sensor_roots"])):
                    raise ValueError("commissioned_semantic_physical_roots_required")
            if family == "jev" and not (set(b["kind_sensor_roots"]["ha.observation"])
                                        - set(b["kind_sensor_roots"]["perception.output"])):
                raise ValueError("independent_device_root_required")
        if policy["ha"]["source_id"] == policy["jev"]["source_id"]:
            raise ValueError("separate_semantic_source_bindings_required")
        if (policy["ha"]["kind_sensor_roots"]["ha.observation"]
                != policy["jev"]["kind_sensor_roots"]["ha.observation"]):
            raise ValueError("original_ha_root_binding_must_agree_across_consumers")
        self.binding_digest = digest([self.config, self.session, self.policy, self.revision])
        self.pc_policy, self.pc_binding = None, None
        if self.v2:
            from live_pc_support import validate_pc_support_policy
            if (self.config["version"] != (13 if self.v4 else 12) or not callable(original_retainer)
                    or policy["pc_support_policy_digest"] != digest(self.config["manifest"]["worker"]["pc_support_policy"])):
                raise ValueError("fixed_pc_successor_and_original_retainer_required")
            self.pc_policy = validate_pc_support_policy(self.config["manifest"]["worker"]["pc_support_policy"],
                                                       self.config["manifest"]["sources"])
            matches = [b for b in self.pc_policy["bindings"] if b["source_id"] == policy["ha"]["source_id"]
                       and b["method_ref"] == policy["ha"]["method_ref"]]
            if (len(matches) != 1 or matches[0]["ha_source_id"] != policy["ha_source_id"]
                    or matches[0]["association_method_ref"] != policy["ha"]["association_method_ref"]
                    or matches[0]["association_method_ref"] != policy["jev"]["association_method_ref"]
                    or matches[0]["proof_component_id"] != policy["ha"]["receipt_component_id"]
                    or matches[0]["input_sensor_root"] not in policy["ha"]["kind_sensor_roots"]["ha.observation"]):
                raise ValueError("exact_semantic_pc_support_binding_required")
            self.pc_binding = deepcopy(matches[0])
        elif original_retainer is not None:
            raise ValueError("historical_semantic_policy_has_no_retainer_port")
        self.original_retainer = original_retainer
        self.lock = threading.RLock()
        self.originals, self.records, self.prepared, self.identities, self.heads = {}, {}, {}, {}, {}
        self.pending, self.authority, self.progress = None, None, {}
        self.requests, self.quiesced = {}, False

    @property
    def source_ids(self):
        return tuple(self.policy[family]["source_id"] for family in ("ha", "jev"))

    def quiesce(self):
        """Stop new work; retain a pending exact retry for supervisor settlement."""
        self.quiesced = True
        if not self.lock.acquire(False):
            return {"status": "unconfirmed", "pending": list(self.pending) if self.pending is not None else None,
                    "source_ids": list(self.source_ids)}
        try:
            return {"status": "unconfirmed" if self.pending is not None else "quiescent",
                    "pending": list(self.pending) if self.pending is not None else None,
                    "source_ids": list(self.source_ids)}
        finally:
            self.lock.release()

    def _work(self, publication, received_at):
        b = self.pc_binding
        p = publication.get("payload", {})
        a, work = p.get("association", {}), p.get("parts", {}).get("work", {})
        return (publication.get("schema") == "home-context-publication-1"
                and publication.get("payload_type") == "home-live-geometry-1"
                and publication.get("payload_digest") == digest(p)
                and finite(publication.get("committed_at")) and publication["committed_at"] <= received_at
                and all(p.get(k) == v for k, v in self.scope.items())
                and p.get("manifest_ref") == self.config["manifest_ref"]
                and p.get("source_id") == b["geometry_source_id"] and p.get("method_ref") == b["geometry_method_ref"]
                and p.get("subject_ref") == b["subject_ref"] and isinstance(p.get("person_ref"), dict)
                and p.get("status") == work.get("status") == "supplied"
                and a.get("person_status") == a.get("subject_status") == "qualified"
                and a.get("method_ref") == b["association_method_ref"]
                and a.get("candidate_person_refs") == a.get("candidate_subject_refs") == []
                and finite(p.get("available_at")) and p["available_at"] <= received_at
                and finite(p.get("valid_until")) and finite(work.get("valid_until"))
                and received_at < min(p["valid_until"], work["valid_until"]))

    @staticmethod
    def _work_ref(publication):
        return {k: publication[k] for k in ("stream_epoch", "cursor", "producer_id", "id", "payload_digest")} | {"component_id": "parts.work"}

    def current_work_parent(self, journal, grant, received_at):
        """Select one actual scoped head; absence/ambiguity never chooses a face."""
        with self.lock:
            self._check(journal, received_at)
            if not self.v2 or self.quiesced:
                return None
            items = journal.snapshot(grant, self.scope["site_id"], self.scope["room_id"])["items"]
            selected = [p for p in items if self._work(p, received_at)]
            return self._work_ref(selected[0]) if len(selected) == 1 else None

    def _parent(self, ref, journal, grant, received_at):
        keys = {"stream_epoch", "cursor", "producer_id", "id", "payload_digest", "component_id"}
        if not isinstance(ref, dict) or set(ref) != keys or ref["component_id"] != "parts.work":
            raise SemanticRefusal("exact_current_work_parent_reference_required")
        selected = [p for p in journal.snapshot(grant, self.scope["site_id"], self.scope["room_id"])["items"]
                    if self._work(p, received_at) and self._work_ref(p) == ref]
        if len(selected) != 1:
            raise SemanticRefusal("selected_work_parent_unavailable")
        return deepcopy(selected[0])

    def remember_request(self, event, *, received_at, journal, grant, work_parent_ref=None):
        """Freeze the exact optional work head before the corresponding reply."""
        with self.lock:
            self._check(journal, received_at)
            if not self.v2:
                raise SemanticRefusal("request_association_requires_explicit_v2")
            self._events([event])
            p = event["payload"]
            if (event.get("kind") != "reasoning.request" or p.get("arm") != self.policy["arm"]
                    or p.get("generation") != self.policy["generation"]
                    or p.get("status") != "admitted_before_transport"
                    or not isinstance(p.get("request_id"), str) or not p["request_id"]
                    or not finite(p.get("available_at")) or p["available_at"] > received_at):
                raise SemanticRefusal("original_jev_request_binding_required")
            rid, fingerprint = p["request_id"], _original_digest(event)
            old = self.requests.get(rid)
            if old is not None:
                if old["request_digest"] != fingerprint or old["work_parent_ref"] != work_parent_ref:
                    raise SemanticRefusal("jev_request_association_changed")
                return deepcopy(old)
            if self.quiesced or len(self.requests) >= self.policy["max_events"]:
                raise SemanticRefusal("semantic_request_budget_or_quiesce")
            if work_parent_ref is not None:
                self._parent(work_parent_ref, journal, grant, received_at)
            result = {"request_event_id": event["id"], "request_digest": fingerprint,
                      "first_consumer_received_at": received_at, "work_parent_ref": deepcopy(work_parent_ref)}
            self.requests[rid] = result
            return deepcopy(result)

    def resolve_receipt(self, store_id, receipt_id):
        with self.lock:
            return deepcopy(self.records[(store_id, receipt_id)])

    def original_receipt(self, store_id, receipt_id):
        with self.lock:
            return deepcopy(self.originals[(store_id, receipt_id)])

    def _check(self, journal, received_at):
        if not isinstance(journal, Journal):
            raise SemanticRefusal("actual_journal_authority_required")
        if self.authority is None:
            self.authority = journal
        if self.authority is not journal or journal.live_config != self.config:
            raise SemanticRefusal("semantic_journal_binding_changed")
        if digest([self.config, self.session, self.policy, self.revision]) != self.binding_digest:
            raise SemanticRefusal("semantic_configuration_changed")
        now = journal.clock()
        if not finite(received_at) or not finite(now) or not 0 <= received_at <= now:
            raise SemanticRefusal("actual_consumer_receipt_time_required")
        return now

    def _events(self, events):
        if (not isinstance(events, (list, tuple)) or not 1 <= len(events) <= 128
                or len(__import__("json").dumps(events, allow_nan=False).encode()) > 2 * 1024 * 1024):
            raise SemanticRefusal("bounded_original_events_required")
        indexed = {}
        for e in events:
            if (not isinstance(e, dict) or type(e.get("id")) is not int or not 1 <= e["id"] <= 9007199254740991
                    or e.get("session_id") != self.session["id"] or not isinstance(e.get("payload"), dict)):
                raise SemanticRefusal("original_semantic_event_scope_required")
            p = e["payload"]
            if any(k in p and p[k] != self.session[k] for k in ("site_id", "camera_id")):
                raise SemanticRefusal("original_semantic_event_scope_required")
            if p.get("session_id", self.session["id"]) != self.session["id"]:
                raise SemanticRefusal("original_semantic_event_scope_required")
            fp = _original_digest(e)
            if e["id"] in indexed and _original_digest(indexed[e["id"]]) != fp:
                raise SemanticRefusal("semantic_event_identity_conflict")
            old = self.identities.get(e["id"])
            if old is not None and old != fp:
                raise SemanticRefusal("semantic_event_identity_conflict")
            indexed[e["id"]] = e
        return indexed

    def _lease(self, payload):
        keys = ("observed_at", "available_at", "valid_until")
        if (not all(finite(payload.get(k)) for k in keys)
                or not 0 <= payload["observed_at"] <= payload["available_at"] < payload["valid_until"]):
            raise SemanticRefusal("original_semantic_clock_required")
        return tuple(payload[k] for k in keys)

    def _ha(self, e):
        p = e["payload"]
        if (e.get("kind") != "ha.observation" or (p.get("quality") or {}).get("source_id") != self.policy["ha_source_id"]
                or not isinstance(p.get("ha"), dict) or not isinstance(p.get("snapshot_key"), str)
                or p.get("evidence_roots") != [p["snapshot_key"]]
                or p.get("snapshot_digest") != digest({k: p[k] for k in ("observed_at", "ha", "acquisition_duration_s")})):
            raise SemanticRefusal("original_configured_pc_snapshot_required")
        self._lease(p)
        # A reconciled HA snapshot can contain PC data and camera-derived
        # Frigate occupancy. Keep that original mixed root correlated; do not
        # mint an independent PC-only root for the entire retained snapshot.
        if any(isinstance(f, dict) and f.get("origin") == "same_camera_visual" for f in p["ha"].values()):
            if not set(self.policy["jev"]["kind_sensor_roots"]["perception.output"]) <= set(
                    self.policy["ha"]["kind_sensor_roots"]["ha.observation"]):
                raise SemanticRefusal("mixed_ha_snapshot_camera_binding_required")
        return p

    def _roots(self, event):
        """Project validated v3 visual originals exactly as incumbent reasoning.

        This is consumed provenance, not a replacement for raw event bodies or
        frame/source validation. HA always keeps its strict original contract.
        """
        payload = event["payload"]
        if not self.persisted_roots or event["kind"] == "ha.observation":
            return payload.get("evidence_roots")
        result = payload.get("result")
        if (event["kind"] != "perception.output"
                or not all(isinstance(payload.get(key), str) and payload[key]
                           for key in ("capture_epoch", "frame_id"))
                or not isinstance(result, dict) or result.get("frame_id") != payload["frame_id"]):
            raise SemanticRefusal("original_semantic_visual_frame_required")
        record = observation_record(event["id"], event["kind"], payload, self.session)
        if record is None:
            raise SemanticRefusal("original_semantic_observation_record_required")
        return record["evidence_roots"]

    def publish_pc(self, events, *, received_at, journal, grant, association=None, work_parent_ref=None):
        """Select the configured HA head, never the newest unrelated HA source."""
        with self.lock:
            now = self._check(journal, received_at)
            if association is not None:
                raise SemanticRefusal("qualified_association_consumer_contract_not_closed")
            if work_parent_ref is not None and not self.v2:
                raise SemanticRefusal("qualified_association_consumer_contract_not_closed")
            indexed = self._events(events)
            choices = [e for e in indexed.values() if e.get("kind") == "ha.observation"
                       and (e["payload"].get("quality") or {}).get("source_id") == self.policy["ha_source_id"]
                       and finite(e["payload"].get("observed_at"))]
            if not choices:
                return {"status": "unavailable", "reason": "configured_pc_source_missing", "receipts": []}
            e = max(choices, key=lambda e: (e["payload"]["observed_at"], e["id"]))
            p = self._ha(e)
            active, recent = (p["ha"].get(self.policy["ha_entities"][k], {}) for k in ("active", "recency"))
            known = active.get("status") == recent.get("status") == "known"
            known = (known and recent.get("state") in {"under_15s", "15_to_60s", "1_to_5_min", "over_5_min", "locked"}
                     and (active.get("provenance") or {}).get("rule") == "computer_active_v1"
                     and (recent.get("provenance") or {}).get("rule") == "computer_active_v3")
            state = active.get("state")
            value = ("active_unattributed" if known and state == "on" else
                     "observed_inactive" if known and state == "off" else None)
            cached = self.prepared.get(("ha", e["id"]))
            parent = None
            if cached is not None:
                if work_parent_ref is not None and cached.get("work_parent_ref") != work_parent_ref:
                    raise SemanticRefusal("semantic_parent_identity_changed")
            elif work_parent_ref is not None:
                parent = self._parent(work_parent_ref, journal, grant, received_at)
            if self.v2 and known and state == "on" and recent.get("state") == "under_15s" and parent is not None:
                value = "attributed_recent"
            return self._publish("ha", e, [e], [e], received_at, now, journal, grant,
                                 kind="pc_input_interaction", value=value,
                                 available=p["available_at"], expiry=p["valid_until"], parent=parent)

    def publish_jev(self, events, *, output_id, received_at, journal, grant, association=None):
        """Require original admitted request, completed delivery and actual output.

        This finite first slice handles only original camera+HA facts-wire input.
        Additional admitted source kinds refuse rather than being silently dropped.
        """
        with self.lock:
            now = self._check(journal, received_at)
            if association is not None:
                raise SemanticRefusal("qualified_association_consumer_contract_not_closed")
            indexed = self._events(events)
            out = indexed.get(output_id)
            if out is None or out.get("kind") != "reasoning.output":
                raise SemanticRefusal("original_jev_output_required")
            p = out["payload"]
            rid = p.get("request_id")
            chain = [e for e in indexed.values() if e.get("kind") in {"reasoning.request", "reasoning.delivery"}
                     and e["payload"].get("request_id") == rid]
            requests = [e for e in chain if e["kind"] == "reasoning.request"]
            deliveries = [e for e in chain if e["kind"] == "reasoning.delivery"]
            if len(requests) != 1 or len(deliveries) != 1 or not isinstance(rid, str) or not rid:
                raise SemanticRefusal("exact_jev_request_delivery_required")
            req, delivery = requests[0]["payload"], deliveries[0]["payload"]
            provenance, judgment = p.get("provenance", {}), p.get("judgment", {})
            packet = req.get("request")
            if (any(e["payload"].get("arm") != self.policy["arm"]
                    or e["payload"].get("generation") != self.policy["generation"] for e in [*chain, out])
                    or req.get("status") != "admitted_before_transport"
                    or req.get("wire_version") != self.policy["wire"]
                    or req.get("timeline_verified") not in {"full", "partial"}
                    or delivery.get("state") != "completed" or delivery.get("status") not in {"ready", "abstain"}
                    or delivery.get("current") is not True
                    or p.get("run_id") != "jev" + self.policy["arm"].upper()
                    or p.get("provider_status") != delivery["status"]
                    or p.get("status") not in {"ready", "abstain"}
                    or judgment.get("model_version") != self.policy["response_version"]
                    or provenance.get("response_version") != self.policy["response_version"]
                    or not isinstance(packet, dict) or req.get("packet_sha256") != digest(packet)
                    or provenance.get("input_digest") != digest(packet)
                    or (judgment.get("diagnostics") or {}).get("request") != packet
                    or packet.get("model") != self.policy["response_version"]
                    or (packet.get("state") or {}).get("schema") != self.policy["wire"]
                    or any(provenance.get(k) != v for k, v in {
                        "request_id": rid, "session_id": self.session["id"], "site_id": self.session["site_id"],
                        "arm": self.policy["arm"], "generation": self.policy["generation"],
                        "wire_version": req.get("wire_version"), "evidence_ids": req.get("evidence_ids"),
                        "timeline_ids": req.get("timeline_ids"), "evidence_digest": req.get("evidence_digest")}.items())):
                raise SemanticRefusal("original_jev_chain_binding_mismatch")
            cutoff = provenance.get("observed_at")
            available = self.session["started_at"] + p.get("available_at_s", float("nan"))
            end = self.session["started_at"] + p.get("source_end_s", float("nan"))
            if (not all(finite(v) for v in (cutoff, available, end, req.get("available_at"), delivery.get("available_at"), provenance.get("deadline")))
                    or not 0 <= end <= cutoff <= req["available_at"] <= available == delivery["available_at"] <= received_at
                    or not available <= provenance["deadline"]
                    or not finite(p.get("valid_until"))):
                raise SemanticRefusal("original_jev_clock_binding_mismatch")
            ids = req.get("evidence_ids")
            timeline_ids = req.get("timeline_ids")
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 16 or len(ids) != len(set(ids))
                    or not isinstance(timeline_ids, list) or len(timeline_ids) > 64
                    or len(timeline_ids) != len(set(timeline_ids))
                    or any(not isinstance(i, str) or not i.isdecimal() or int(i) not in indexed
                           for i in [*ids, *timeline_ids])):
                raise SemanticRefusal("complete_jev_original_evidence_required")
            evidence = [indexed[int(i)] for i in ids]
            all_evidence = [indexed[int(i)] for i in dict.fromkeys([*ids, *timeline_ids])]
            for e in all_evidence:
                q = e["payload"]
                self._lease(q)
                if q["available_at"] > cutoff or q["observed_at"] > cutoff:
                    raise SemanticRefusal("jev_future_evidence_not_causal")
                if e.get("kind") == "ha.observation":
                    self._ha(e)
                elif (e.get("kind") != "perception.output" or q.get("status") != "completed"
                      or q.get("arm") != self.policy["arm"] or q.get("generation") != self.policy["generation"]):
                    raise SemanticRefusal("jev_additional_source_contract_not_closed")
                if self.persisted_roots:
                    # Reject malformed canonical originals even without an
                    # attributed work parent; source validation precedes this.
                    self._roots(e)
            visual = [e for e in evidence if e["kind"] == "perception.output"]
            latest = max(visual, key=lambda e: (e["payload"]["observed_at"], e["id"]), default=None)
            if latest is None or latest["payload"]["observed_at"] != end:
                raise SemanticRefusal("jev_original_visual_clock_required")
            # Historical inputs retain their actual old times. Only required
            # current PC and latest visual support bound the resulting lease.
            pc = [e for e in evidence if e["kind"] == "ha.observation"]
            pc_head = max(pc, key=lambda e: (e["payload"]["observed_at"], str(e["id"])), default=None)
            devices = packet["state"].get("devices", {})
            for field, entity, concrete in (
                    ("pc_input", self.policy["ha_entities"]["recency"], {"under_15s", "15_to_60s", "1_to_5_min", "over_5_min", "locked"}),
                    ("pc_active", self.policy["ha_entities"]["active"], {"on", "off"})):
                word = devices.get(field)
                if word in concrete and (pc_head is None or pc_head["payload"]["valid_until"] <= cutoff
                        or pc_head["payload"]["ha"].get(entity, {}).get("status") != "known"
                        or pc_head["payload"]["ha"].get(entity, {}).get("state") != word):
                    raise SemanticRefusal("jev_pc_packet_original_mismatch")
            expiry = min(p["valid_until"], latest["payload"]["valid_until"],
                         *(e["payload"]["valid_until"] for e in pc))
            activity = judgment.get("activity")
            camera_now = packet["state"].get("now", {})
            value = (activity if p["status"] == "ready" and activity in ACTIVITIES
                     and camera_now.get("people_in_view") == "one"
                     and camera_now.get("camera_evidence") == "current" else None)
            originals = sorted([*all_evidence, *chain, out], key=lambda e: e["id"])
            parent, request_binding = None, None
            if self.v2:
                request_binding = self.requests.get(rid)
                if request_binding is not None:
                    if (request_binding["request_event_id"] != requests[0]["id"]
                            or request_binding["request_digest"] != _original_digest(requests[0])):
                        raise SemanticRefusal("jev_request_association_changed")
                    ref = request_binding["work_parent_ref"]
                    if ref is not None and ("jev", out["id"]) not in self.prepared:
                        parent = self._parent(ref, journal, grant, received_at)
                        support = parent["payload"]
                        visual_roots = {root for row in visual for root in self._roots(row)}
                        if (parent["committed_at"] > cutoff or support["available_at"] > cutoff
                                or support["observed_at"] > cutoff
                                or not visual_roots.intersection(support["observation_roots"])
                                or pc_head is None or devices.get("pc_active") != "on"
                                or devices.get("pc_input") != "under_15s"):
                            raise SemanticRefusal("jev_original_cutoff_association_unavailable")
                        # Every consumed current visual root must remain an
                        # original of this same associated cut. One matching
                        # frame cannot attribute a later/different person.
                        if not set(self._roots(latest)) <= set(support["observation_roots"]):
                            raise SemanticRefusal("jev_consumed_visual_association_mismatch")
            return self._publish("jev", out, originals, all_evidence, received_at, now, journal, grant,
                                 kind="activity_refinement", value=value, available=available, expiry=expiry,
                                 parent=parent, request_binding=request_binding)

    def _pc_material(self, body, event, parent, received_at):
        """Construct only the already commissioned proof; validation is separate."""
        from live_pc_support import _instant
        b, p = self.pc_binding, event["payload"]
        clock = next(c for c in self.pc_policy["clock_qualifications"] if c["id"] == b["qualification_ref"]["id"])
        input_at = float(_instant(p["ha"][b["lastactive_entity"]]["state"]) + Decimal(str(clock["offset_s"])))
        root = "pc-input:" + digest([b["device_id"], b["input_clock_epoch"], input_at])[:40]
        until = min(input_at + b["max_input_age_s"] - clock["uncertainty_s"], clock["valid_until"])
        original = {"schema": "home-original-pc-input-1", "version": 1,
            "id": "lab-pc-original:" + digest([self.scope, body["source_id"], event["id"]]),
            "scope": deepcopy(self.scope), **{k: deepcopy(body[k]) for k in
                ("source_id", "source_generation", "method_ref", "person_ref", "subject_ref")},
            **{k: deepcopy(b[k]) for k in ("association_method_ref", "qualification_ref", "input_rule_ref",
                "device_id", "workstation_id", "input_clock_epoch")},
            "input_root": root, "input_at": input_at, "input_valid_until": until,
            "association_ref": self._work_ref(parent), "observed_at": p["observed_at"],
            "available_at": received_at, "ha_event": deepcopy(event)}
        own_times = {root: input_at, **{r: p["observed_at"] for r in p["evidence_roots"]}}
        own_bindings = {root: [b["input_sensor_root"]], **{r: deepcopy(self.policy["ha"]["kind_sensor_roots"]["ha.observation"])
                                                       for r in p["evidence_roots"]}}
        body["observation_root_times"].update(own_times)
        body["sensor_root_bindings"].update(own_bindings)
        body["valid_until"] = min(body["valid_until"], until)
        body["available_at"] = received_at
        body["reason"] = "qualified_pc_input_attribution"
        body["assertion_basis"] = "inferred"
        return original, own_times, own_bindings

    def _retain(self, family, cached):
        if not self.v2:
            return
        wrapper = cached["retention_record"]
        retained = self.original_retainer(deepcopy(wrapper))
        if retained != {"status": "retained", "digest": _original_digest(wrapper)}:
            raise SemanticRefusal("durable_semantic_original_receipt_required")

    def _publish(self, family, event, originals, evidence, received_at, now, journal, grant,
                 *, kind, value, available, expiry, parent=None, request_binding=None):
        b = self.policy[family]
        source = self.config["manifest"]["sources"][b["source_id"]]
        identity = (family, event["id"])
        fingerprint = _original_digest(originals)
        cached = self.prepared.get(identity)
        if cached is not None:
            if cached["fingerprint"] != fingerprint:
                raise SemanticRefusal("semantic_event_identity_conflict")
        elif self.pending is not None and self.pending != identity:
            raise SemanticRefusal("semantic_pending_exact_retry_required")
        else:
            if self.quiesced:
                raise SemanticRefusal("semantic_source_quiesced")
            previous = self.progress.get(family)
            if previous is not None and event["id"] <= previous[0]:
                raise SemanticRefusal("semantic_original_progression_regressed")
            if len(self.prepared) >= self.policy["max_events"]:
                raise SemanticRefusal("semantic_event_budget_exhausted")
            times, bindings = {}, {}
            for e in evidence:
                p, physical = e["payload"], b["kind_sensor_roots"].get(e["kind"])
                roots = self._roots(e)
                if not physical or not isinstance(roots, list) or not roots:
                    raise SemanticRefusal("original_semantic_roots_required")
                for root in roots:
                    identifier(root, "original_semantic_root")
                    if root in times and (times[root] != p["observed_at"] or bindings[root] != physical):
                        raise SemanticRefusal("semantic_original_root_conflict")
                    times[root], bindings[root] = p["observed_at"], deepcopy(physical)
            observed = max(times.values())
            if previous is not None and observed < previous[1]:
                raise SemanticRefusal("semantic_original_progression_regressed")
            if not observed <= available <= received_at or expiry - observed > source["max_age_s"]:
                raise SemanticRefusal("semantic_original_lease_invalid")
            if now + b["clock_uncertainty_s"] >= expiry:
                return {"status": "expired", "reason": "original_support_expired", "receipts": []}
            key = {"source_id": b["source_id"], "payload_type": "home-live-fact-1", "person_ref": None,
                   "subject_ref": None, "predicate": kind, "method_ref": deepcopy(b["method_ref"])}
            original = {"schema": "home-original-lab-semantic-1", "version": 1,
                        "events": deepcopy(originals), "session": deepcopy(self.session),
                        "policy_digest": digest(self.policy), "first_consumer_received_at": received_at}
            oid = "lab-semantic-original:" + digest([self.scope, b["source_id"], event["id"]])
            ref = {"store_id": b["receipt_store_id"], "receipt_id": oid, "digest": _original_digest(original),
                   "component_id": b["receipt_component_id"], "observed_at": observed, "available_at": available,
                   "clock_mapping_id": source["capture_clock"]["mapping_id"]}
            clock = {"status": "commissioned", **{k: source["capture_clock"][k] for k in
                     ("mapping_id", "mapping_digest", "source_epoch", "original_unit")},
                     "original_value": format(Decimal(str(observed)), "f"), "uncertainty_s": b["clock_uncertainty_s"]}
            validate_clock(clock, source["capture_clock"], observed)
            body = {"schema": "home-live-fact-1", "version": 1,
                    "id": "lab-semantic:" + digest([self.scope, b["source_id"], event["id"]]),
                    **deepcopy(self.scope), "manifest_ref": deepcopy(self.config["manifest_ref"]),
                    "source_id": b["source_id"], "source_generation": source["initial_generation"],
                    "person_ref": None, "subject_ref": None,
                    "association": {"person_status": "not_applicable", "subject_status": "not_applicable",
                        "association_id": "lab-semantic-unattributed:" + digest([self.scope, b["source_id"]]),
                        "generation": self.policy["generation"], "method_ref": deepcopy(b["association_method_ref"]),
                        "candidate_person_refs": [], "candidate_subject_refs": []},
                    "head": {"key": key, "position": {"capture_epoch": source["capture_epoch"],
                        "capture_seq": event["id"], "revision": 0}, "supersedes": deepcopy(self.heads.get(canonical(key)))},
                    "observed_at": observed, "available_at": available, "valid_until": expiry,
                    "sensor_roots": sorted({r for roots in bindings.values() for r in roots}),
                    "observation_roots": sorted(times), "observation_root_times": deepcopy(times),
                    "sensor_root_bindings": deepcopy(bindings), "parent_refs": [], "original_receipt_refs": [ref],
                    "clock": clock, "method_ref": deepcopy(b["method_ref"]), "model_ref": deepcopy(b["model_ref"]),
                    "layer": "observation" if family == "ha" else "interpretation", "kind": kind,
                    "status": "supplied" if value is not None else "unobservable", "value": value,
                    "assertion_basis": "measured" if family == "ha" else "inferred", "probability": None,
                    "conflicting_refs": [], "reason": "qualified_per_observation_evidence" if value is not None else "missing_applicability"}
            if self.v2:
                original.update(schema="home-original-lab-semantic-2", version=2,
                                request_binding=deepcopy(request_binding))
            if parent is not None:
                support = parent["payload"]
                for root, time in support["observation_root_times"].items():
                    physical = support["sensor_root_bindings"][root]
                    if root in times and (times[root] != time or set(bindings[root]) != set(physical)):
                        raise SemanticRefusal("semantic_parent_original_root_conflict")
                    body["observation_root_times"][root] = time
                    body["sensor_root_bindings"][root] = deepcopy(physical)
                body.update(person_ref=deepcopy(support["person_ref"]), subject_ref=deepcopy(support["subject_ref"]),
                            association=deepcopy(support["association"]), parent_refs=[self._work_ref(parent)],
                            available_at=max(available, parent["committed_at"], support["available_at"]),
                            valid_until=min(expiry, support["valid_until"], support["parts"]["work"]["valid_until"]))
                key.update(person_ref=deepcopy(body["person_ref"]), subject_ref=deepcopy(body["subject_ref"]))
                body["head"]["supersedes"] = deepcopy(self.heads.get(canonical(key)))
            own_times, own_bindings = deepcopy(times), deepcopy(bindings)
            if family == "ha" and value == "attributed_recent":
                original, own_times, own_bindings = self._pc_material(body, event, parent, received_at)
                oid = original["id"]
                ref.update(receipt_id=oid, digest=digest(original), observed_at=original["observed_at"],
                           available_at=original["available_at"])
            else:
                ref["digest"] = _original_digest(original)
            body["observation_roots"] = sorted(body["observation_root_times"])
            body["sensor_roots"] = sorted({r for roots in body["sensor_root_bindings"].values() for r in roots})
            body["observed_at"] = max(body["observation_root_times"].values())
            body["clock"]["original_value"] = format(Decimal(str(body["observed_at"])), "f")
            validate_clock(body["clock"], source["capture_clock"], body["observed_at"])
            record = {"ref": deepcopy(ref), "capture_epoch": source["capture_epoch"],
                      "capture_seq": event["id"], "observation_root_times": own_times,
                      "sensor_root_bindings": own_bindings}
            if family == "ha" and value == "attributed_recent":
                record["pc_input_original"] = deepcopy(original)
                from live_pc_support import validate_pc_input_fact
                validate_pc_input_fact(body, source, self.pc_policy, [record], [parent], now=now)
            envelope = {"schema": "home-context-event-1", "schema_version": "1.0", "id": body["id"],
                        "producer_id": source["producer_id"], "stream_epoch": source["producer_epoch"],
                        **deepcopy(self.scope), **{k: body[k] for k in ("observed_at", "available_at", "valid_until")},
                        "payload_type": "home-live-fact-1", "payload_version": "1.0", "revision": self.revision, "payload": body}
            store_key = (b["receipt_store_id"], oid)
            self.originals[store_key] = original
            self.records[store_key] = record
            cached = {"fingerprint": fingerprint, "event": envelope, "receipt": None,
                      "original_observed_at": observed,
                      "append_attempted": False, "cancelled_before_append": False,
                      "work_parent_ref": self._work_ref(parent) if parent is not None else None}
            if self.v2:
                cached["retention_record"] = {"schema": "home-original-lab-admission-1", "version": 1,
                    "session_id": self.session["id"], "family": family, "source_id": b["source_id"],
                    "method_ref": deepcopy(b["method_ref"]), "model_ref": deepcopy(b["model_ref"]),
                    "original_ref": deepcopy(ref), "receipt_record": deepcopy(record),
                    "original": deepcopy(original), "first_consumer_received_at": received_at}
            self.prepared[identity] = cached
            for e in originals:
                self.identities[e["id"]] = _original_digest(e)
        if cached["receipt"] is None:
            if cached["cancelled_before_append"]:
                return {"status": "quiesced", "supports_engagement": False, "receipts": []}
            self.pending = identity
            self._retain(family, cached)
            if self.quiesced and not cached["append_attempted"]:
                cached["cancelled_before_append"] = True
                self.pending = None
                return {"status": "quiesced", "supports_engagement": False, "receipts": []}
            cached["append_attempted"] = True
            receipt = journal.append_event(deepcopy(cached["event"]), grant)
            cached["receipt"] = deepcopy(receipt)
            self.progress[family] = (event["id"], cached["original_observed_at"] if self.v2 else cached["event"]["payload"]["observed_at"])
            self.heads[canonical(cached["event"]["payload"]["head"]["key"])] = {
                **{k: receipt[k] for k in ("stream_epoch", "cursor", "producer_id", "id", "payload_digest")}, "component_id": None}
            self.pending = None
        elif journal.append_event(deepcopy(cached["event"]), grant) != cached["receipt"]:
            # Even an exact local retry must pass the current actual grant;
            # cached acknowledgement is never a replacement authorizer.
            raise SemanticRefusal("semantic_original_journal_receipt_changed")
        supported = cached["event"]["payload"].get("value") == "attributed_recent"
        return {"status": "admitted_attributed" if cached["event"]["payload"]["person_ref"] is not None else "admitted_unattributed",
                "supports_engagement": supported,
                "receipts": [deepcopy(cached["receipt"])]}

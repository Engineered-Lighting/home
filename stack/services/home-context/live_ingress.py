"""Initial live producer profile. No acquisition, inference or lighting authority."""
from __future__ import annotations

import json

from journal import JournalError, LIVE_PROFILE, bounded_json, canonical, digest, identifier
from live_ingress_validation import validate_capture, validate_clock

TYPES = frozenset({"home-live-fact-1", "home-live-geometry-1"})
LIFECYCLE_TYPE = "home-live-person-lifecycle-1"
SCOPE = ("site_id", "room_id", "session_id", "domain_epoch")


def receipt_catalog_resolver(records, *, pc_proof_components=()):
    """Bounded metadata provider for controlled CLI sessions, never a file/URL resolver.

    Live acquisition composition supplies the same fixed-store callback against
    its original receipt owner; the HTTP caller can never choose that provider.
    """
    if not isinstance(records, list) or len(records) > 2048:
        raise ValueError("bounded explicit original-receipt catalog required")
    catalog = {}
    for record in records:
        if (not isinstance(record, dict)
                or set(record) != ({"ref", "capture_epoch", "capture_seq", "observation_root_times", "sensor_root_bindings"}
                    | ({"pc_input_original"} if record.get("ref", {}).get("component_id") in pc_proof_components else set()))
                or not isinstance(record["ref"], dict)
                or set(record["ref"]) != {"store_id", "receipt_id", "digest", "component_id", "observed_at", "available_at", "clock_mapping_id"}):
            raise ValueError("closed original-receipt metadata required")
        bounded_json(record, 65536)
        key = (identifier(record["ref"]["store_id"], "store_id"), identifier(record["ref"]["receipt_id"], "receipt_id"))
        if key in catalog:
            raise ValueError("duplicate original-receipt identity")
        catalog[key] = canonical(record)
    def resolve(store_id, receipt_id):
        return json.loads(catalog[(store_id, receipt_id)])
    return resolve


def validate_grants(grants, config):
    """Pure authority preflight; malformed grants must never open a ledger."""
    sources, identity = config["manifest"]["sources"], config["manifest"]["identity"]
    for grant in grants:
        worker_enabled, scene_enabled, delivery_enabled = config["version"] >= 2, config["version"] >= 3, config["version"] in {4, 5, 6, 7, 9, 10, 11, 12, 13}
        stable_enabled = config["version"] == 5
        lifecycle_enabled = config["version"] >= 6
        from live_worker import SCHEMAS as worker_schemas, OUTPUTS as worker_outputs
        from schema_registry import LIVE_SCENE_VERSIONS
        scene_schemas = set(LIVE_SCENE_VERSIONS)
        from schema_registry import LIVE_DELIVERY_VERSIONS
        from schema_registry import LIVE_STABLE_DELIVERY_VERSIONS
        from schema_registry import LIVE_CONSERVATIVE_DELIVERY_VERSIONS
        manual_enabled = config["version"] == 11
        room_clarification_enabled = config["version"] in {10, 11}
        subset_enabled = config["version"] == 13
        pc_enabled = config["version"] in {12, 13}
        room_enabled = config["version"] in {9, 10, 11, 12, 13}
        conservative_enabled = config["version"] in {7, 9, 10, 11, 12, 13}
        from schema_registry import LIVE_ROOM_VERSIONS
        from schema_registry import LIVE_ROOM_CLARIFICATION_VERSIONS, LIVE_ROOM_MANUAL_VERSIONS
        shared_manual_types = {"home-live-person-lifecycle-1", "home-live-intent-event-2", "home-live-intent-event-3"}
        if not manual_enabled and grant.payload_types & (set(LIVE_ROOM_MANUAL_VERSIONS)-shared_manual_types):
            raise ValueError("manual control requires explicit config11 commissioning")
        if manual_enabled and grant.payload_types & (set(LIVE_ROOM_CLARIFICATION_VERSIONS)-shared_manual_types):
            raise ValueError("manual grants cannot mix predecessor room worker authority")
        shared_room_types = {"home-live-room-output-1", "home-live-room-snapshot-1", "home-live-person-lifecycle-1", "home-live-intent-event-2"}
        from schema_registry import LIVE_PC_SUPPORT_VERSIONS
        from schema_registry import LIVE_ROOM_SUBSET_VERSIONS
        if not subset_enabled and grant.payload_types & (set(LIVE_ROOM_SUBSET_VERSIONS)-shared_room_types-{"home-live-pc-support-policy-1"}):
            raise ValueError("assessed room subset requires explicit config13 commissioning")
        if subset_enabled and grant.payload_types & (set(LIVE_PC_SUPPORT_VERSIONS)-shared_room_types-{"home-live-pc-support-policy-1"}):
            raise ValueError("room subset grants cannot mix predecessor PC worker authority")
        if not pc_enabled and grant.payload_types & (set(LIVE_PC_SUPPORT_VERSIONS)-shared_room_types):
            raise ValueError("PC support requires explicit config12 commissioning")
        if pc_enabled and grant.payload_types & (set(LIVE_ROOM_VERSIONS)-shared_room_types):
            raise ValueError("PC support grants cannot mix predecessor room worker authority")
        if not room_clarification_enabled and grant.payload_types & (set(LIVE_ROOM_CLARIFICATION_VERSIONS)-shared_room_types):
            raise ValueError("room clarification requires explicit config10 commissioning")
        if room_clarification_enabled and grant.payload_types & (set(LIVE_ROOM_VERSIONS)-shared_room_types):
            raise ValueError("room clarification grants cannot mix predecessor worker authority")
        if not room_enabled and grant.payload_types & (set(LIVE_ROOM_VERSIONS)-{"home-live-person-lifecycle-1","home-live-intent-event-2"}):
            raise ValueError("room authority requires explicit config9 commissioning")
        if room_enabled and grant.payload_types & (set(LIVE_CONSERVATIVE_DELIVERY_VERSIONS)-{"home-live-person-lifecycle-1","home-live-intent-event-2"}):
            raise ValueError("room grants cannot mix historical delivery authority")
        if not conservative_enabled and grant.payload_types & (set(LIVE_CONSERVATIVE_DELIVERY_VERSIONS) - {"home-live-intent-event-2", "home-live-person-lifecycle-1"}):
            raise ValueError("conservative delivery requires explicit version-seven commissioning")
        if conservative_enabled and grant.payload_types & {"home-live-shadow-decision-5", "home-live-worker-checkpoint-5", "home-live-worker-commit-5", "home-live-input-cut-5"}:
            raise ValueError("version-seven grants cannot mix historical delivery authority")
        from schema_registry import LIVE_LIFECYCLE_VERSIONS
        if not lifecycle_enabled and grant.payload_types & set(LIVE_LIFECYCLE_VERSIONS):
            raise ValueError("lifecycle and correction authority require explicit version-six commissioning")
        if lifecycle_enabled and grant.payload_types & (set(LIVE_STABLE_DELIVERY_VERSIONS) | set(LIVE_DELIVERY_VERSIONS)):
            raise ValueError("version-six grants cannot mix historical delivery authority")
        if not stable_enabled and not lifecycle_enabled and grant.payload_types & set(LIVE_STABLE_DELIVERY_VERSIONS):
            raise ValueError("stable delivery types require explicit version-five commissioning")
        if stable_enabled and grant.payload_types & set(LIVE_DELIVERY_VERSIONS):
            raise ValueError("version-five grants cannot mix historical delivery authority")
        if not delivery_enabled and grant.payload_types & set(LIVE_DELIVERY_VERSIONS):
            raise ValueError("live delivery types require explicit version-four commissioning")
        if not scene_enabled and grant.payload_types & scene_schemas:
            raise ValueError("live profile and scene types require explicit version-three commissioning")
        if not worker_enabled and grant.payload_types & worker_schemas:
            raise ValueError("producer-only live configuration cannot grant future worker types")
        if grant.payload_types & (worker_schemas - worker_outputs) and "worker_commit" not in grant.operations:
            raise ValueError("private worker types require the sole scoped worker capability")
        if "home-live-shadow-decision-1" in grant.payload_types and "home-live-geometry-1" not in grant.payload_types:
            raise ValueError("complete live decisions require separately authorized geometry reads")
        if scene_enabled:
            from live_scene import validate_scene_grant
            validate_scene_grant(grant, config)
        if subset_enabled:
            from room_subset_worker import validate_room_subset_grant
            validate_room_subset_grant(grant, config)
        elif pc_enabled:
            from pc_support_worker import validate_pc_grant
            validate_pc_grant(grant, config)
        elif manual_enabled:
            from room_manual import validate_manual_grant
            validate_manual_grant(grant, config)
        elif room_clarification_enabled:
            from room_clarification import validate_room_clarification_grant
            validate_room_clarification_grant(grant, config)
        elif room_enabled:
            from room_delivery import validate_room_grant
            validate_room_grant(grant, config)
        elif conservative_enabled:
            from live_delivery import validate_conservative_delivery_grant
            validate_conservative_delivery_grant(grant, config)
        elif lifecycle_enabled:
            from live_delivery import validate_lifecycle_delivery_grant
            validate_lifecycle_delivery_grant(grant, config)
        elif stable_enabled:
            from live_delivery import validate_stable_delivery_grant
            validate_stable_delivery_grant(grant, config)
        elif delivery_enabled:
            from live_delivery import validate_delivery_grant
            validate_delivery_grant(grant, config)
        allowed = {"read", "event", "revoke", "worker_commit"} if worker_enabled else {"read", "event", "revoke"}
        if scene_enabled:
            allowed.add("intent")
        if not grant.operations <= allowed or not grant.live_bindings:
            raise ValueError("initial live grants allow only scoped ingress, reads and revocation")
        if grant.clock_mappings or grant.registration_bindings or grant.adaptive_subjects:
            raise ValueError("live qualification must use its pinned manifest, not legacy bindings")
        for encoded in grant.live_bindings:
            binding = json.loads(encoded)
            source = sources.get(binding["source_id"])
            if (source is None or binding["manifest_ref"] != config["manifest_ref"]
                    or binding["source_id"] not in grant.source_ids
                    or (identity["site_id"], identity["room_id"]) not in grant.scopes):
                raise ValueError("live grant must bind an exact configured manifest and source")
            caps = set(binding["capabilities"])
            if (caps & {"capture", "control"} and ("event" not in grant.operations or grant.producer_id != source["producer_id"])
                    or "read" in caps and "read" not in grant.operations
                    or "revoke" in caps and "revoke" not in grant.operations
                    or "worker_commit" in caps and (not worker_enabled or "worker_commit" not in grant.operations)
                    or "capture" in caps and not set(source["sensor_roots"]) <= grant.sensor_roots):
                raise ValueError("live capabilities require matching operation, producer and physical-source authority")
        if "worker_commit" in grant.operations:
            if subset_enabled:
                from room_subset_worker import validate_room_subset_worker_grant
                validate_room_subset_worker_grant(grant, config)
            elif pc_enabled:
                from pc_support_worker import validate_pc_worker_grant
                validate_pc_worker_grant(grant, config)
            elif manual_enabled:
                from room_manual import validate_manual_worker_grant
                validate_manual_worker_grant(grant, config)
            elif room_clarification_enabled:
                from room_clarification import validate_room_clarification_worker_grant
                validate_room_clarification_worker_grant(grant, config)
            elif room_enabled:
                from room_delivery import validate_room_worker_grant
                validate_room_worker_grant(grant, config)
            elif conservative_enabled:
                from live_delivery import validate_conservative_delivery_worker_grant
                validate_conservative_delivery_worker_grant(grant, config)
            elif lifecycle_enabled:
                from live_delivery import validate_lifecycle_delivery_worker_grant
                validate_lifecycle_delivery_worker_grant(grant, config)
            elif stable_enabled:
                from live_delivery import validate_stable_delivery_worker_grant
                validate_stable_delivery_worker_grant(grant, config)
            elif delivery_enabled:
                from live_delivery import validate_delivery_worker_grant
                validate_delivery_worker_grant(grant, config)
            elif scene_enabled:
                from live_scene import validate_scene_worker_grant
                validate_scene_worker_grant(grant, config)
            else:
                from live_worker import validate_worker_grant
                validate_worker_grant(grant, config)


class LiveIngressProfile:
    def __init__(self, journal):
        self.j = journal
        self.config = journal.live_config
        self.manifest = self.config["manifest"]
        self.identity = self.manifest["identity"]
        self.sources = self.manifest["sources"]
        self.producer_types = TYPES | {LIFECYCLE_TYPE} if self.config["version"] >= 6 else TYPES
        self.limits = self.manifest["limits"]
        journal.db.executescript("""
            CREATE TABLE IF NOT EXISTS live_sources(source_id TEXT PRIMARY KEY,generation INTEGER NOT NULL,
                barrier_at REAL,barrier_uncertainty REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS live_heads(key TEXT PRIMARY KEY,source_id TEXT NOT NULL,
                capture_epoch TEXT NOT NULL,capture_seq INTEGER NOT NULL,revision INTEGER NOT NULL,
                generation INTEGER NOT NULL,cursor INTEGER NOT NULL,reference TEXT NOT NULL,
                valid_until REAL NOT NULL,body TEXT);
            CREATE TABLE IF NOT EXISTS live_original_receipts(key TEXT PRIMARY KEY,binding_digest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS live_head_dependencies(head_key TEXT NOT NULL,parent_cursor INTEGER NOT NULL,
                PRIMARY KEY(head_key,parent_cursor));
            CREATE TABLE IF NOT EXISTS live_identity_slots(key TEXT PRIMARY KEY,source_id TEXT NOT NULL,
                generation INTEGER NOT NULL,capture_seq INTEGER NOT NULL,track_epoch TEXT NOT NULL,subject TEXT);
            CREATE TABLE IF NOT EXISTS live_retired_identities(key TEXT PRIMARY KEY);
        """)
        def initialize():
            journal._set_meta("profile", LIVE_PROFILE)
            journal._set_meta("live-config", digest(self.config))
            for source, policy in self.sources.items():
                journal.db.execute("INSERT OR IGNORE INTO live_sources VALUES(?,?,NULL,0)",
                                   (source, policy["initial_generation"]))
        journal._transaction(initialize)
        self.worker = None
        if self.config["version"] >= 2:
            if self.config["version"] == 13:
                from room_subset_worker import RoomSubsetWorkerBoundary
                self.worker = RoomSubsetWorkerBoundary(self)
            elif self.config["version"] == 12:
                from pc_support_worker import PcSupportWorkerBoundary
                self.worker = PcSupportWorkerBoundary(self)
            elif self.config["version"] == 11:
                from room_manual import RoomManualLiveDeliveryWorkerBoundary
                self.worker = RoomManualLiveDeliveryWorkerBoundary(self)
            elif self.config["version"] == 10:
                from room_clarification import RoomClarificationLiveDeliveryWorkerBoundary
                self.worker = RoomClarificationLiveDeliveryWorkerBoundary(self)
            elif self.config["version"] == 9:
                from room_worker import RoomLiveDeliveryWorkerBoundary
                self.worker = RoomLiveDeliveryWorkerBoundary(self)
            elif self.config["version"] in {7, 9, 10, 11, 12, 13}:
                from live_delivery import ConservativeLiveDeliveryWorkerBoundary
                self.worker = ConservativeLiveDeliveryWorkerBoundary(self)
            elif self.config["version"] >= 6:
                from live_delivery import LifecycleLiveDeliveryWorkerBoundary
                self.worker = LifecycleLiveDeliveryWorkerBoundary(self)
            elif self.config["version"] == 5:
                from live_delivery import StableLiveDeliveryWorkerBoundary
                self.worker = StableLiveDeliveryWorkerBoundary(self)
            elif self.config["version"] == 4:
                from live_delivery import LiveDeliveryWorkerBoundary
                self.worker = LiveDeliveryWorkerBoundary(self)
            elif self.config["version"] == 3:
                from live_scene import LiveSceneWorkerBoundary
                self.worker = LiveSceneWorkerBoundary(self)
            else:
                from live_worker import LiveWorkerBoundary
                self.worker = LiveWorkerBoundary(self)

    def _capability(self, grant, source, capability):
        return any((b := json.loads(encoded))["source_id"] == source
                   and b["manifest_ref"] == self.config["manifest_ref"]
                   and capability in b["capabilities"] for encoded in grant.live_bindings)

    def _authorize(self, event, grant, operation="event"):
        # Scope/type authorization precedes identity lookup and schema diagnostics.
        if not isinstance(event, dict):
            raise JournalError("forbidden", "explicit scoped producer envelope required", 403)
        grant.require(operation, event.get("site_id"), event.get("room_id"),
                      payload_type=event.get("payload_type"), revision=event.get("revision"),
                      producer_id=event.get("producer_id"))
        payload = event.get("payload")
        if not isinstance(payload, dict) or any(event.get(k) != self.identity[k] for k in SCOPE):
            raise JournalError("forbidden", "input is outside the configured live scope", 403)
        if operation == "revoke":
            if event.get("payload_type") != "home-context-revocation-1":
                raise JournalError("unsupported_schema", "typed revocation route required")
            return
        if event.get("payload_type") not in self.producer_types:
            raise JournalError("unsupported_schema", "type is not admitted by the initial live profile")
        source_id = payload.get("source_id")
        source = self.sources.get(source_id) if isinstance(source_id, str) else None
        capability = "control" if payload.get("status") == "withdrawn" else "capture"
        if (source is None or source_id not in grant.source_ids or not self._capability(grant, source_id, capability)
                or source["producer_id"] != event.get("producer_id")
                or source["producer_epoch"] != event.get("stream_epoch")
                or payload.get("manifest_ref") != self.config["manifest_ref"]):
            raise JournalError("forbidden", "input lacks its exact live source authority", 403)
        if capability == "capture" and not set(payload.get("sensor_roots", [])) <= grant.sensor_roots:
            raise JournalError("forbidden", "physical source is outside the configured grant", 403)

    def validate_payload(self, payload):
        if self.worker and payload.get("schema") in self.worker.schema_types:
            return self.worker.validate_payload(payload)
        if payload.get("schema") not in {*self.producer_types, "home-context-revocation-1"}:
            raise JournalError("unsupported_schema", "live ingress type required")

    def _publication_ref(self, publication):
        return {k: publication[k] for k in ("stream_epoch", "cursor", "producer_id", "id", "payload_digest")} | {"component_id": None}

    def _resolve_parent(self, ref, grant, event, *, full=False):
        j = self.j
        if ref["stream_epoch"] != j._meta("epoch"):
            raise JournalError("invalid_reference", "reference has an incompatible journal epoch")
        row = j.db.execute("SELECT body,revision FROM publications WHERE cursor=?", (ref["cursor"],)).fetchone()
        if not row:
            raise JournalError("reference_unavailable", "required original publication is no longer retained", 409)
        publication = json.loads(row[0])
        grant.require("read", event["site_id"], event["room_id"], payload_type=publication["payload_type"], revision=row["revision"])
        if any(ref[k] != publication[k] for k in ("id", "producer_id", "payload_digest")):
            raise JournalError("invalid_reference", "exact publication reference required")
        parent = publication["payload"]
        if (publication["payload_type"] not in TYPES or any(parent.get(k) != event[k] for k in SCOPE)
                or not self._capability(grant, parent.get("source_id"), "read")
                or publication["payload_type"] not in grant.payload_types
                or parent.get("status") != "supplied"
                or parent.get("manifest_ref") != self.config["manifest_ref"]):
            raise JournalError("forbidden", "required parent is outside the input authority", 403)
        current = j.db.execute("SELECT body FROM live_heads WHERE cursor=?", (publication["cursor"],)).fetchone()
        if not current or current[0] is None or not self._current(publication, j.clock()):
            raise JournalError("reference_unavailable", "required parent is not current", 409)
        if parent["available_at"] > event["available_at"] or publication["committed_at"] > event["available_at"]:
            raise JournalError("invalid_clock", "derived availability cannot precede its required parent")
        component = ref["component_id"]
        if component is not None:
            if publication["payload_type"] != "home-live-geometry-1" or component not in {"parts." + name for name in parent["parts"]}:
                raise JournalError("invalid_reference", "registered parent component required")
            part = parent["parts"][component.removeprefix("parts.")]
            if part["status"] != "supplied" or part["valid_until"] <= j.clock() + (event["payload"]["clock"]["uncertainty_s"] if self.config["version"] in {7, 9, 10, 11, 12, 13} else 0):
                raise JournalError("reference_unavailable", "required parent component is unavailable", 409)
        return publication if full else parent

    def _part_support(self, payload, parents):
        if payload["schema"] != "home-live-geometry-1":
            if self.config["version"] in {7, 9, 10, 11, 12, 13}:
                for ref, parent in zip(payload["parent_refs"], parents):
                    component = ref["component_id"]
                    support = parent["parts"][component.removeprefix("parts.")] if component is not None else parent
                    if payload["valid_until"] > support["valid_until"] or self.j.clock() + payload["clock"]["uncertainty_s"] >= support["valid_until"]:
                        raise JournalError("invalid_clock", "semantic validity cannot extend original component support", 409)
            return
        if payload["status"] == "supplied" and (payload["association"]["person_status"] != "qualified"
                or payload["parts"]["work"]["status"] == "supplied" and payload["association"]["subject_status"] != "qualified"):
            raise JournalError("invalid_association", "supplied geometry requires its qualified person and work association")
        selected = {canonical(ref): parent for ref, parent in zip(payload["parent_refs"], parents)}
        for part in payload["parts"].values():
            if part["status"] != "supplied":
                continue
            observations = []
            for ref in part["support_refs"]:
                if "store_id" in ref:
                    observations.append(ref["observed_at"])
                else:
                    parent = selected[canonical(ref)]
                    component = ref["component_id"]
                    support = parent["parts"][component.removeprefix("parts.")] if component is not None else parent
                    observations.append(support["observed_at"])
                    if part["valid_until"] > support["valid_until"]:
                        raise JournalError("invalid_clock", "part validity cannot extend its selected support", 409)
            if (not observations or max(observations) != part["observed_at"]
                    or payload["status"] == "supplied" and self.j.clock() + payload["clock"]["uncertainty_s"] >= part["valid_until"]):
                raise JournalError("invalid_clock", "part freshness must come from its actual current support", 409)

    def _invalidate_dependents(self, cursors):
        """Withdraw transitive current claims; preserve their durable head fences."""
        pending, visited = list(cursors), set()
        while pending:
            cursor = pending.pop()
            if cursor in visited:
                continue
            visited.add(cursor)
            for row in self.j.db.execute("SELECT h.key,h.cursor FROM live_heads h JOIN live_head_dependencies d ON d.head_key=h.key WHERE d.parent_cursor=? AND h.body IS NOT NULL", (cursor,)).fetchall():
                self.j.db.execute("UPDATE live_heads SET body=NULL WHERE key=?", (row["key"],))
                pending.append(row["cursor"])

    def _identity_fence(self, payload):
        """Scope current heads without becoming another tracker or engagement reducer."""
        person = payload["person_ref"]
        if person is None:
            return
        j, source = self.j, payload["source_id"]
        subject = payload["subject_ref"]
        slot_key = canonical([source, person["pid"]])
        track_key = canonical([source, person["pid"], person["track_epoch"]])
        subject_key = canonical([source, person, subject]) if subject is not None else None
        if j.db.execute("SELECT 1 FROM live_retired_identities WHERE key=? OR key=?", (track_key, subject_key)).fetchone():
            raise JournalError("stale_head", "retired anonymous identities cannot regain current authority", 409)
        old = j.db.execute("SELECT * FROM live_identity_slots WHERE key=?", (slot_key,)).fetchone()
        sequence = payload["head"]["position"]["capture_seq"]
        old_subject = json.loads(old["subject"]) if old and old["subject"] is not None else None
        changed_track = bool(old and old["track_epoch"] != person["track_epoch"])
        changed_subject = bool(old and subject is not None and old_subject is not None and subject != old_subject)
        if old and payload["source_generation"] == old["generation"]:
            if sequence <= old["capture_seq"] and (changed_track or changed_subject):
                raise JournalError("stale_head", "association continuity cannot be replaced by an older or competing capture", 409)
            if sequence < old["capture_seq"]:
                return  # A late compatible interpretation cannot regress continuity.
        if old and (changed_track or changed_subject):
            if changed_track:
                retired = canonical([source, person["pid"], old["track_epoch"]])
            else:
                retired = canonical([source, person, old_subject])
            j.db.execute("INSERT OR IGNORE INTO live_retired_identities VALUES(?)", (retired,))
            cursors = []
            for head in j.db.execute("SELECT key,cursor FROM live_heads WHERE source_id=?", (source,)).fetchall():
                key = json.loads(head["key"])
                old_person = key["person_ref"]
                if (old_person is not None and old_person["pid"] == person["pid"]
                        and (changed_track or key["subject_ref"] == old_subject)):
                    j.db.execute("UPDATE live_heads SET body=NULL WHERE key=?", (head["key"],))
                    cursors.append(head["cursor"])
            self._invalidate_dependents(cursors)
        keep_subject = subject if subject is not None else old_subject if old and not changed_track else None
        j.db.execute("INSERT INTO live_identity_slots VALUES(?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET generation=excluded.generation,capture_seq=excluded.capture_seq,track_epoch=excluded.track_epoch,subject=excluded.subject",
                     (slot_key, source, payload["source_generation"], sequence, person["track_epoch"], canonical(keep_subject) if keep_subject is not None else None))
        if (j.db.execute("SELECT count(*) FROM live_identity_slots").fetchone()[0] > self.limits["max_heads"]
                or j.db.execute("SELECT count(*) FROM live_retired_identities").fetchone()[0] > 2 * self.limits["max_heads"]):
            raise JournalError("journal_full", "anonymous continuity ledger budget exhausted", 507)

    def _bind_lineage(self, payload, records, parents):
        j = self.j
        times, physical = {}, {}
        for record in records:
            key = canonical([record["ref"]["store_id"], record["ref"]["receipt_id"]])
            binding = digest(record)
            prior = j.db.execute("SELECT binding_digest FROM live_original_receipts WHERE key=?", (key,)).fetchone()
            if prior and prior[0] != binding:
                raise JournalError("receipt_conflict", "original receipt metadata is immutable", 409)
            j.db.execute("INSERT OR IGNORE INTO live_original_receipts VALUES(?,?)", (key, binding))
        for record in [*records, *parents]:
            for root, observed in record["observation_root_times"].items():
                sensors = sorted(record["sensor_root_bindings"][root])
                if root in times and (times[root] != observed or physical[root] != sensors):
                    raise JournalError("root_conflict", "original ancestry must agree across all support", 409)
                times[root], physical[root] = observed, sensors
        if times != payload["observation_root_times"] or physical != {r: sorted(v) for r, v in payload["sensor_root_bindings"].items()}:
            raise JournalError("root_conflict", "published ancestry must exactly match original support", 409)
        j._bind_original_roots({**self.identity, "payload": payload})
        if (j.db.execute("SELECT count(*) FROM original_roots").fetchone()[0] > self.limits["max_original_roots"]
                or j.db.execute("SELECT count(*) FROM live_original_receipts").fetchone()[0] > self.limits["max_receipt_bindings"]):
            raise JournalError("journal_full", "immutable live lineage budget exhausted", 507)

    def append_event(self, event, grant):
        self._authorize(event, grant)
        def accept():
            j = self.j
            retry = j._receipt(event)
            if retry:
                return retry
            j._event(event)
            p = event["payload"]
            if p["id"] != event["id"] or any(p[k] != event[k] for k in ("observed_at", "available_at", "valid_until")):
                raise JournalError("invalid_envelope", "live identity and clocks must equal the original envelope")
            if not p["observed_at"] <= p["available_at"] < p["valid_until"]:
                raise JournalError("invalid_clock", "exclusive causal validity interval required")
            source_id = p["source_id"]
            if j._revoked(event["site_id"], event["room_id"], source_id, event["revision"]):
                raise JournalError("revoked", "source or revision is revoked", 409)
            if p["schema"] == LIFECYCLE_TYPE:
                from live_lifecycle import append_lifecycle
                return append_lifecycle(self, event, grant)
            if p["status"] == "withdrawn":
                return self._barrier(event)
            source = self.sources[source_id]
            source_state = j.db.execute("SELECT * FROM live_sources WHERE source_id=?", (source_id,)).fetchone()
            if p["source_generation"] != source_state["generation"]:
                raise JournalError("source_generation_conflict", "fresh capture must match the current source generation", 409)
            if source_state["barrier_at"] is not None and p["observed_at"] - p["clock"]["uncertainty_s"] <= source_state["barrier_at"] + source_state["barrier_uncertainty"]:
                raise JournalError("prebarrier_capture", "recovery requires a qualified post-barrier exposure", 409)
            pc_positive = (self.config["version"] in {12, 13} and p["schema"] == "home-live-fact-1"
                and p.get("status") == "supplied" and p.get("kind") == "pc_input_interaction" and p.get("value") == "attributed_recent")
            pc_components = {b["proof_component_id"] for b in self.manifest["worker"]["pc_support_policy"]["bindings"]
                if b["source_id"] == source_id and b["method_ref"] == p["method_ref"]} if pc_positive else set()
            records = validate_capture(p, source, j.live_receipt_resolver, pc_proof_components=pc_components)
            if p["status"] == "supplied" and not self._current_payload(p, j.clock()):
                raise JournalError("expired", "supplied geometry or evidence is not current", 409)
            resolved_parents = [self._resolve_parent(ref, grant, event, full=pc_positive) for ref in p["parent_refs"]]
            parents = [parent["payload"] for parent in resolved_parents] if pc_positive else resolved_parents
            self._part_support(p, parents)
            if pc_positive:
                from live_pc_support import validate_pc_input_fact
                try:
                    validate_pc_input_fact(p, source, self.manifest["worker"]["pc_support_policy"], records, resolved_parents, now=j.clock())
                except ValueError as error:
                    raise JournalError("invalid_pc_support", str(error), 409) from None
            self._bind_lineage(p, records, parents)
            self._identity_fence(p)
            key = canonical(p["head"]["key"])
            old = j.db.execute("SELECT * FROM live_heads WHERE key=?", (key,)).fetchone()
            position = p["head"]["position"]
            if position["capture_epoch"] != source["capture_epoch"]:
                raise JournalError("capture_epoch_conflict", "capture epoch must match its commissioned source", 409)
            if old and p["source_generation"] == old["generation"] and (position["capture_seq"], position["revision"]) <= (old["capture_seq"], old["revision"]):
                raise JournalError("stale_head", "capture progression cannot regress or be redelivered under a new identity", 409)
            expected = json.loads(old["reference"]) if old else None
            if p["head"]["supersedes"] != expected:
                raise JournalError("head_conflict", "exact prior head reference required", 409)
            if old is None and j.db.execute("SELECT count(*) FROM live_heads").fetchone()[0] >= min(self.limits["max_heads"], j.limits.max_state_items):
                raise JournalError("journal_full", "durable live head budget exhausted", 507)
            receipt, publication = self._record(event)
            if old:
                association = p["association"]
                ordinary_geometry = (self.config["version"] in {7, 9, 10, 11, 12, 13} and p["schema"] == "home-live-geometry-1"
                    and not association["candidate_person_refs"] and not association["candidate_subject_refs"]
                    and association["person_status"] in {"qualified", "not_applicable"})
                # Immutable earlier observations may still support independently
                # leased semantics. Only their own original horizon applies.
                if not ordinary_geometry:
                    self._invalidate_dependents([old["cursor"]])
            horizon = min(p["valid_until"], p["observed_at"] + source["max_age_s"]) - p["clock"]["uncertainty_s"]
            j.db.execute("INSERT INTO live_heads VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET capture_epoch=excluded.capture_epoch,capture_seq=excluded.capture_seq,revision=excluded.revision,generation=excluded.generation,cursor=excluded.cursor,reference=excluded.reference,valid_until=excluded.valid_until,body=excluded.body",
                         (key, source_id, position["capture_epoch"], position["capture_seq"], position["revision"], p["source_generation"], publication["cursor"], canonical(self._publication_ref(publication)), horizon, canonical(publication) if horizon > j.clock() else None))
            j.db.execute("DELETE FROM live_head_dependencies WHERE head_key=?", (key,))
            for ref in p["parent_refs"]:
                j.db.execute("INSERT OR IGNORE INTO live_head_dependencies VALUES(?,?)", (key, ref["cursor"]))
            state_bytes = j.db.execute("SELECT coalesce(sum(length(CAST(body AS BLOB))),0) FROM live_heads").fetchone()[0]
            if state_bytes > j.limits.max_state_bytes:
                raise JournalError("journal_full", "live head body budget exhausted", 507)
            self._prune()
            return receipt
        return self.j._transaction(accept)

    def _record(self, event, *, max_bytes=None):
        j = self.j
        self._prune()
        if j.db.execute("SELECT count(*) FROM receipts").fetchone()[0] >= j.limits.max_receipts:
            raise JournalError("journal_full", "durable receipt budget exhausted", 507)
        cursor = int(j._meta("cursor")) + 1
        now = j.clock()
        publication = {"schema": "home-context-publication-1", "id": event["id"], "producer_id": event["producer_id"],
                       "stream_epoch": j._meta("epoch"), "cursor": cursor, "context_seq": cursor, "committed_at": now,
                       "payload_type": event["payload_type"], "payload_version": event["payload_version"],
                       "payload": event["payload"], "payload_digest": digest(event["payload"])}
        body = bounded_json(publication, j.limits.max_body_bytes if max_bytes is None else max_bytes)
        source = event["payload"].get("source_id", event["producer_id"])
        j.db.execute("INSERT INTO publications VALUES(?,?,?,?,?,?,?)", (cursor, event["site_id"], event["room_id"], source, event["revision"], event["payload_type"], body))
        receipt = {"schema": "home-context-receipt-1", "id": event["id"], "producer_id": event["producer_id"],
                   "stream_epoch": j._meta("epoch"), "cursor": cursor, "committed_at": now,
                   "payload_digest": publication["payload_digest"], "status": "accepted"}
        # No receipt lookup is allowed to become an untyped geometry read route.
        j.db.execute("INSERT INTO receipts(producer_id,source_epoch,event_id,digest,receipt,event) VALUES(?,?,?,?,?,?)",
                     (event["producer_id"], event["stream_epoch"], event["id"], digest(event), canonical(receipt), canonical(j._tombstone(event))))
        j._set_meta("cursor", cursor)
        j._observe_publication(event, receipt, publication)
        return receipt, publication

    def _barrier(self, event):
        j, p = self.j, event["payload"]
        control, source = p["control"], self.sources[p["source_id"]]
        validate_clock(control["clock"], source["control_clock"], p["observed_at"])
        current = j.db.execute("SELECT generation FROM live_sources WHERE source_id=?", (p["source_id"],)).fetchone()[0]
        if (control["asserted_at"] != p["observed_at"] or control["expected_source_generation"] != current
                or control["next_source_generation"] != current + 1 or p["source_generation"] != current + 1
                or control["expected_authority_generation"] != j.revocation_generation(event["site_id"], event["room_id"])):
            raise JournalError("source_generation_conflict", "source and room generations must compare-and-swap", 409)
        for ref in control["retired_refs"]:
            head = j.db.execute("SELECT source_id,reference FROM live_heads WHERE cursor=?", (ref["cursor"],)).fetchone()
            if not head or head["source_id"] != p["source_id"] or json.loads(head["reference"]) != ref:
                raise JournalError("invalid_reference", "withdrawal may retire only its own exact heads")
        receipt, _ = self._record(event)
        j.db.execute("UPDATE live_sources SET generation=?,barrier_at=?,barrier_uncertainty=? WHERE source_id=?",
                     (current + 1, p["observed_at"], control["clock"]["uncertainty_s"], p["source_id"]))
        retired = [row[0] for row in j.db.execute("SELECT cursor FROM live_heads WHERE source_id=?", (p["source_id"],))]
        j.db.execute("UPDATE live_heads SET body=NULL WHERE source_id=?", (p["source_id"],))
        self._invalidate_dependents(retired)
        j._set_meta("revocation:" + canonical([event["site_id"], event["room_id"]]), control["expected_authority_generation"] + 1)
        self._prune()
        return receipt

    def _current_payload(self, payload, now):
        source = self.sources[payload["source_id"]]
        error = payload["clock"]["uncertainty_s"]
        return payload["observed_at"] <= now and now + error < min(payload["valid_until"], payload["observed_at"] + source["max_age_s"])

    def _current(self, publication, now):
        p = publication["payload"]
        return p.get("status") != "withdrawn" and self._current_payload(p, now)

    def _prune(self):
        j, now = self.j, self.j.clock()
        floor = int(j._meta("retained_after"))
        tail = int(j._meta("cursor"))
        old = j.db.execute("SELECT max(cursor) FROM publications WHERE json_extract(body,'$.committed_at')<?", (now - j.limits.retention_seconds,)).fetchone()[0]
        floor = max(floor, old or 0, tail - j.limits.max_publications)
        j.db.execute("DELETE FROM publications WHERE cursor<=?", (floor,))
        size = j.db.execute("SELECT coalesce(sum(length(CAST(body AS BLOB))),0) FROM publications").fetchone()[0]
        if size > j.limits.max_publication_bytes:
            for cursor, length in j.db.execute("SELECT cursor,length(CAST(body AS BLOB)) FROM publications ORDER BY cursor").fetchall():
                if size <= j.limits.max_publication_bytes:
                    break
                floor, size = max(floor, cursor), size - length
            j.db.execute("DELETE FROM publications WHERE cursor<=?", (floor,))
        j._set_meta("retained_after", floor)
        # Positions, references and immutable lineage remain; expired bodies don't.
        expired = [row[0] for row in j.db.execute("SELECT cursor FROM live_heads WHERE valid_until<=? AND body IS NOT NULL", (now,))]
        j.db.execute("UPDATE live_heads SET body=NULL WHERE valid_until<=?", (now,))
        self._invalidate_dependents(expired)

    def _readable(self, publication, revision, grant):
        intent_types = ({"home-live-intent-event-1", "home-live-intent-event-2", "home-live-intent-event-3", "home-live-intent-event-4"} if self.config["version"] == 11
                        else {"home-live-intent-event-1", "home-live-intent-event-2", "home-live-intent-event-3"} if self.config["version"] == 10
                        else {"home-live-intent-event-1", "home-live-intent-event-2"} if self.config["version"] >= 6 else {"home-live-intent-event-1"})
        if self.config["version"] >= 3 and publication["payload_type"] in intent_types:
            return self.worker.intent_readable(publication, revision, grant)
        if self.worker and publication["payload_type"] in self.worker.output_types:
            return self.worker.readable(publication, revision, grant)
        return (publication["payload_type"] in grant.payload_types and revision in grant.revisions
                and self._capability(grant, publication["payload"].get("source_id"), "read"))

    def _read_scope(self, grant, site, room):
        grant.require("read", site, room)
        if (site, room) != (self.identity["site_id"], self.identity["room_id"]):
            raise JournalError("forbidden", "read is outside this live journal scope", 403)

    def service_clock_sample(self):
        monitor=getattr(self.j,"live_service_clock",None)
        if monitor is None:
            return {}
        sample=monitor.sample()
        return {"service_clock":{"schema":"home-live-service-clock-1",**self.identity,
            "stream_epoch":self.j._meta("epoch"),"authority_generation":self.j.revocation_generation(self.identity["site_id"],self.identity["room_id"]),
            "anchor_epoch":sample["anchor_epoch"],"utc_at":sample.get("sampled_wall_at") if sample["qualified"] else None,
            "uncertainty_s":sample["uncertainty_s"],"qualified":sample["qualified"]}}

    def snapshot(self, grant, site, room, consumer_id=None):
        self._read_scope(grant, site, room)
        def read():
            j = self.j
            self._prune()
            items = []
            for row in j.db.execute("SELECT body FROM live_heads WHERE body IS NOT NULL ORDER BY cursor"):
                publication = json.loads(row[0])
                receipt = j.db.execute("SELECT event FROM receipts WHERE json_extract(receipt,'$.cursor')=?", (publication["cursor"],)).fetchone()
                revision = json.loads(receipt[0])["revision"]
                if self._readable(publication, revision, grant):
                    items.append(publication)
            if self.worker:
                items.extend(self.worker.current(grant))
                items.sort(key=lambda publication: publication["cursor"])
            cursor = int(j._meta("cursor"))
            j._delivered(grant, consumer_id or grant.principal_id, site, room, cursor)
            return {"schema": "home-context-snapshot-1", "stream_epoch": j._meta("epoch"), "cursor": cursor,
                    "items": items, "revocation_generation": j.revocation_generation(site, room), **self.service_clock_sample()}
        return self.j._transaction(read)

    def page(self, grant, site, room, *, after=0, stream_epoch=None, limit=128, consumer_id=None):
        self._read_scope(grant, site, room)
        if type(limit) is not int or not 1 <= limit <= 128:
            raise JournalError("invalid_query", "limit must be 1..128")
        def read():
            j = self.j
            self._prune()
            tail = j._epoch_cursor(stream_epoch, after)
            items, used, next_cursor = [], 2048, after
            for row in j.db.execute("SELECT body,revision FROM publications WHERE cursor>? ORDER BY cursor", (after,)):
                publication = json.loads(row[0])
                if not self._readable(publication, row[1], grant):
                    next_cursor = publication["cursor"]
                    continue
                size = len(row[0].encode("utf-8"))
                if len(items) >= limit or used + size > j.limits.max_response_bytes:
                    if not items and used + size > j.limits.max_response_bytes:
                        raise JournalError("response_too_large", "page budget cannot contain the next visible publication", 507)
                    break
                items.append(publication)
                used += size
                next_cursor = publication["cursor"]
            else:
                next_cursor = tail
            j._delivered(grant, consumer_id or grant.principal_id, site, room, next_cursor)
            return {"schema": "home-context-page-1", "stream_epoch": j._meta("epoch"), "after": after,
                    "items": items, "next_cursor": next_cursor, "revocation_generation": j.revocation_generation(site, room), **self.service_clock_sample()}
        return self.j._transaction(read)

    def revoke(self, event, grant):
        self._authorize(event, grant, "revoke")
        def accept():
            j = self.j
            retry = j._receipt(event)
            if retry:
                return retry
            j._event(event)
            p = event["payload"]
            sources = list(self.sources) if p["source_id"] == "*" else [p["source_id"]]
            if any(s not in self.sources or not self._capability(grant, s, "revoke") for s in sources):
                raise JournalError("forbidden", "revocation is outside configured source authority", 403)
            if p["revision"] != "*" and p["revision"] not in grant.revisions:
                raise JournalError("forbidden", "revocation revision is outside configured authority", 403)
            receipt, _ = self._record(event)
            j.db.execute("INSERT OR IGNORE INTO revocations VALUES(?,?,?,?)", (event["site_id"], event["room_id"], p["source_id"], p["revision"]))
            control = getattr(self.worker, 'control_authority', None)
            if control is not None and p['source_id'] == '*':
                state = control.state()
                if state['control_active'] or state['control_generation'] == 0:
                    control.retire('room-revoke:'+digest(event)[:48], 'room_authority_revoked')
            for source in sources:
                # Conservatively withdraw current source heads; history remains attributable.
                retired = [row[0] for row in j.db.execute("SELECT cursor FROM live_heads WHERE source_id=?", (source,))]
                j.db.execute("UPDATE live_heads SET body=NULL WHERE source_id=?", (source,))
                if self.config["version"] in {7, 9, 10, 11, 12, 13}:
                    j.db.execute("UPDATE live_sources SET generation=generation+1,barrier_at=?,barrier_uncertainty=0 WHERE source_id=?", (j.clock(),source))
                self._invalidate_dependents(retired)
            j._set_meta("revocation:" + canonical([event["site_id"], event["room_id"]]), j.revocation_generation(event["site_id"], event["room_id"]) + 1)
            self._prune()
            return receipt
        return self.j._transaction(accept)

    def unavailable(self, *args, **kwargs):
        raise JournalError("unsupported_schema", "capability is not implemented by the initial live ingress stage", 403)

    def worker_commit(self, commit, grant):
        if not self.worker:
            return self.unavailable()
        return self.worker.commit(commit, grant)

    def checkpoint(self, worker_id, grant, site, room):
        if not self.worker:
            return self.unavailable()
        return self.worker.checkpoint(worker_id, grant, site, room)

    def worker_input_cut(self, grant, site, room, consumer_id=None):
        if not self.worker:
            return self.unavailable()
        return self.worker.input_cut(grant, site, room, consumer_id)

    def worker_commit_lookup(self, commit_id, grant, site, room):
        if not self.worker:
            return self.unavailable()
        return self.worker.lookup(commit_id, grant, site, room)

    def intent(self, payload, grant):
        if self.config["version"] < 3:
            return self.unavailable()
        return self.worker.intent(payload, grant)

    def lookup_intent(self, event_id, grant, site, room):
        if self.config["version"] < 3:
            return self.unavailable()
        return self.worker.lookup_intent(event_id, grant, site, room)

    def scene_catalog(self, grant, site, room):
        if self.config["version"] < 3:
            return self.unavailable()
        return self.worker.catalog(grant, site, room)

    append = request_evidence = reply_evidence = lookup_request = input_cut = unavailable

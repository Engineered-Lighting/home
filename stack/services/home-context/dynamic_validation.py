"""Isolated synthetic contracts and journal profile; no acquisition or devices."""
from __future__ import annotations

import json
import math
import sys
import uuid

from journal import JournalError, bounded_json, canonical, digest, finite, identifier


SCOPE = ("site_id", "room_id", "session_id", "domain_epoch")
INPUTS = frozenset({"home-fact-batch-2", "home-geometry-3", "home-intent-event-2",
                    "home-evidence-request-2", "home-evidence-reply-2"})
OUTPUTS = frozenset({"home-engagement-state-3", "home-lighting-policy-decision-2",
                     "home-lighting-control-decision-1"})
GEOMETRY_PRIORITY = {"open": 0, "unknown": 1, "occluded": 2, "closed": 3, "withdrawn": 4}
QUOTAS = {"home-geometry-3": 600, "home-fact-batch-2": 240,
          "home-lighting-control-decision-1": 600, "home-engagement-state-3": 120,
          "home-lighting-policy-decision-2": 120, "home-intent-event-2": 32,
          "home-source-health-1": 16, "home-context-revocation-1": 8,
          "home-evidence-request-2": 8, "home-evidence-reply-2": 8}


def content_digest(value):
    return digest({key: item for key, item in value.items() if key != "digest"})


def same_scope(a, b):
    return all(a.get(key) == b.get(key) for key in SCOPE)


def object_key(value):
    return canonical([value[key] for key in (*SCOPE, "object_epoch", "oid")])


def component_id(value, geometric=False):
    return "geometry:" + digest({k: value[k] for k in ("object_ref", "person_ref", "association_generation")}) if geometric else value["id"]


def validate_dynamic(value, registry=None):
    """Return the same acceptance boundary as the normative JS validator."""
    if registry is None:
        from schema_registry import SchemaRegistry
        registry = SchemaRegistry((), profile="dynamic-book-synthetic-1")
    errors = []
    try:
        registry.validate(value)
    except (JournalError, TypeError, ValueError, UnicodeError):
        return {"ok": False, "errors": ["schema"]}

    def visit(item):
        if isinstance(item, dict):
            if finite(item.get("observed_at")) and finite(item.get("available_at")) and item["observed_at"] > item["available_at"]:
                errors.append("clock_order")
            if finite(item.get("valid_until")) and finite(item.get("available_at")) and item["valid_until"] <= item["available_at"]:
                errors.append("ttl")
            for name in ("person_ref", "object_ref"):
                if item.get(name) and not same_scope(value, item[name]):
                    errors.append(name.split("_")[0] + "_scope")
            if item.get("schema") and item is not value and all(k in item for k in SCOPE) and not same_scope(value, item):
                errors.append("nested_scope")
            if "centre_m" in item:
                u, v, n = item["u"], item["v"], item["normal"]
                dot = lambda a, b: sum(x * y for x, y in zip(a, b))
                cross = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
                if (any(abs(dot(a, a) - 1) > 1e-6 for a in (u, v, n))
                        or abs(dot(u, v)) > 1e-6 or any(abs(a - b) > 1e-6 for a, b in zip(cross, n))):
                    errors.append("patch_basis")
                points = item["vertices_uv_m"]
                area = sum(a[0] * points[(i + 1) % len(points)][1] - a[1] * points[(i + 1) % len(points)][0]
                           for i, a in enumerate(points))
                if abs(area) < 1e-8:
                    errors.append("patch_area")
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
    visit(value)
    name = value["schema"]
    if name in {"home-dynamic-geometry-snapshot-1", "home-control-input-cut-1", "home-context-replay-4"}:
        if content_digest(value) != value["digest"]:
            errors.append("digest_mismatch")
    if name == "home-fact-2" and value["capture"]["observed_at"] != value["observed_at"]:
        errors.append("capture_clock")
    if name == "home-fact-batch-2":
        if (any(f["available_at"] > value["available_at"] or f["capture"]["observed_at"] != f["observed_at"] for f in value["facts"])
                or value["observed_at"] != min(f["observed_at"] for f in value["facts"])
                or value["valid_until"] != max(f["valid_until"] for f in value["facts"])):
            errors.append("batch_clock_binding")
    if name == "home-geometry-3":
        if value["capture"]["observed_at"] != value["observed_at"]:
            errors.append("capture_clock")
        if any(o["state"] == "open" and (len(o["patches"]) != 2 or len({p["role"] for p in o["patches"]}) != 2
               or len({(p["surface_id"], p["surface_epoch"]) for p in o["patches"]}) != 2) for o in value["objects"]):
            errors.append("two_pages_required")
        if len({object_key(o["object_ref"]) for o in value["objects"]}) != len(value["objects"]):
            errors.append("duplicate_object")
    return {"ok": not errors, "errors": errors}


class DynamicProfile:
    def __init__(self, journal):
        self.j = journal
        if journal.evidence_router not in (None, "cached_synthetic_only", "synthetic_stub"):
            raise ValueError("only an explicitly composed cached synthetic dispatcher is supported")
        journal.db.executescript("""
            CREATE TABLE dynamic_cuts(cut_id TEXT PRIMARY KEY,principal_id TEXT NOT NULL,consumer_id TEXT NOT NULL,
                site_id TEXT NOT NULL,room_id TEXT NOT NULL,after_cursor INTEGER NOT NULL,cut_cursor INTEGER NOT NULL,
                as_of_t REAL NOT NULL,authority TEXT NOT NULL,delivered_cursor INTEGER NOT NULL);
            CREATE TABLE dynamic_heads(site_id TEXT NOT NULL,room_id TEXT NOT NULL,subject TEXT NOT NULL,body TEXT NOT NULL,
                PRIMARY KEY(site_id,room_id,subject));
            CREATE TABLE dynamic_deliveries(principal_id TEXT NOT NULL,consumer_id TEXT NOT NULL,cursor INTEGER NOT NULL,
                served_at REAL NOT NULL,PRIMARY KEY(principal_id,consumer_id,cursor));
            CREATE TABLE dynamic_snapshot_masks(principal_id TEXT NOT NULL,consumer_id TEXT NOT NULL,cursor INTEGER NOT NULL,
                component_ids TEXT NOT NULL,served_at REAL NOT NULL,PRIMARY KEY(principal_id,consumer_id,cursor,component_ids));
        """)

    def validate_payload(self, value):
        result = validate_dynamic(value, self.j.schemas)
        if not result["ok"]:
            raise JournalError("invalid_payload", "invalid dynamic contract", errors=result["errors"])
        return value

    def authority(self, site, room):
        key = "dynamic-authority:" + canonical([site, room])
        raw = self.j._meta(key)
        return json.loads(raw) if raw else {"generation": 0, "registration_generation": 0,
                                           "association_generation": 0, "revocation_generation": 0, "motion_allowed": True}

    def _authority_update(self, site, room, **changes):
        current = self.authority(site, room)
        current.update(changes)
        current["generation"] += 1
        self.j._set_meta("dynamic-authority:" + canonical([site, room]), canonical(current))

    @staticmethod
    def source(event):
        return event["payload"].get("capture", {}).get("source_id", event["payload"].get("source_id", event["producer_id"]))

    def _storage_budget(self, reserved=False):
        db = self.j.db
        count, size = db.execute("SELECT count(*),coalesce(sum(length(CAST(body AS BLOB))),0) FROM publications").fetchone()
        ceiling = min(self.j.limits.max_publications, 2048 if reserved else 1736)
        byte_ceiling = min(self.j.limits.max_publication_bytes, (12 if reserved else 11) * 1024 * 1024)
        pages = db.execute("PRAGMA page_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0]
        if count > ceiling or size > byte_ceiling or pages > min(self.j.limits.max_db_bytes, (48 if reserved else 47) * 1024 * 1024):
            raise JournalError("journal_full", "synthetic reservation exhausted; no evidence was evicted", 507)

    def _roots(self, event):
        payload = event["payload"]
        records = payload["facts"] if payload["schema"] == "home-fact-batch-2" else [payload]
        scope = canonical([event[k] for k in SCOPE])
        for record in records:
            cap = record["capture"]
            root = canonical([cap["source_id"], cap["capture_epoch"], cap["capture_seq"]])
            ancestry = digest([cap["source_id"], cap["capture_epoch"]])
            row = self.j.db.execute("SELECT sensor_digest,observed_at FROM original_roots WHERE scope=? AND observation_root=?", (scope, root)).fetchone()
            if row and (row[0] != ancestry or row[1] != cap["observed_at"]):
                raise JournalError("root_time_conflict", "original capture identity cannot change its observation time", 409)
            if not row:
                total = self.j.db.execute("SELECT count(*) FROM original_roots WHERE scope=? AND sensor_digest=?", (scope, ancestry)).fetchone()[0]
                if total >= 600:
                    raise JournalError("root_budget", "synthetic original capture budget exhausted", 507)
                self.j.db.execute("INSERT INTO original_roots VALUES(?,?,?,?)", (scope, root, ancestry, cap["observed_at"]))

    def _heads(self, event):
        """Admission guards only; authority changes when an original is eligible."""
        payload, site, room = event["payload"], event["site_id"], event["room_id"]
        if payload["schema"] not in {"home-geometry-3", "home-fact-batch-2"}:
            return
        authority = self.authority(site, room)
        records = payload["objects"] if payload["schema"] == "home-geometry-3" else payload["facts"]
        for item in records:
            ref = item["object_ref"]
            subject = canonical([ref[k] for k in (*SCOPE, "oid")])
            row = self.j.db.execute("SELECT body FROM dynamic_heads WHERE site_id=? AND room_id=? AND subject=?", (site, room, subject)).fetchone()
            old = json.loads(row[0]) if row else None
            cap = payload.get("capture", item.get("capture"))
            geometric = payload["schema"] == "home-geometry-3"
            fresh = {"object_epoch": ref["object_epoch"], "person_ref": item["person_ref"],
                     "association_generation": item["association_generation"],
                     "capture_epoch": cap["capture_epoch"] if geometric or not old else old["capture_epoch"],
                     "capture_seq": cap["capture_seq"] if geometric or not old else old["capture_seq"],
                     "state": item.get("state", "closed" if item.get("kind") == "closed" and item["value"] else old["state"] if old else "unknown")}
            if geometric and old and fresh["capture_epoch"] == old["capture_epoch"] and fresh["capture_seq"] < old["capture_seq"]:
                raise JournalError("capture_regression", "older capture cannot overwrite the current head", 409)
            if old and fresh["association_generation"] < old["association_generation"]:
                raise JournalError("association_regression", "obsolete association cannot overwrite the current head", 409)
        if payload["schema"] == "home-geometry-3":
            registration = payload["registration_generation"]
            if registration < authority["registration_generation"]:
                raise JournalError("registration_regression", "obsolete registration cannot overwrite current authority", 409)

    def _refresh_heads(self, site, room):
        # Each original is applied once when its parent publication is eligible.
        # Derive winners before guard mutation, so a delayed older capture is
        # handled without replaying or resurrecting obsolete authority.
        pending = self.j.db.execute("SELECT cursor FROM publications p WHERE site_id=? AND room_id=? AND payload_type IN ('home-geometry-3','home-fact-batch-2') AND json_extract(body,'$.committed_at')<=? AND json_extract(body,'$.payload.available_at')<=? AND NOT EXISTS(SELECT 1 FROM meta WHERE key='dynamic-head-applied:'||p.cursor)",
                                    (site, room, self.j.clock(), self.j.clock())).fetchall()
        if not pending:
            return
        pending_cursors = {r[0] for r in pending}
        records = self._qualified_components(site, room, self.j.clock())
        geometry_captures = {}
        for record in records:
            if record["head"]["kind"] != "geometry":
                continue
            head = record["head"]
            subject = canonical([head["object_ref"][k] for k in (*SCOPE, "oid")])
            rank = (head["observed_at"], GEOMETRY_PRIORITY[record["component"]["state"]], record["publication"]["payload_digest"])
            if subject not in geometry_captures or rank > geometry_captures[subject][0]:
                geometry_captures[subject] = (rank, head["capture"])
        subjects = {}
        for record in records:
            head, item = record["head"], record["component"]
            subject = canonical([head["object_ref"][k] for k in (*SCOPE, "oid")])
            cap = geometry_captures[subject][1] if head["kind"] != "geometry" and subject in geometry_captures else head["capture"]
            state = item["state"] if head["kind"] == "geometry" else "closed" if head["kind"] in {"closed", "negative_use"} and item["value"] or head["kind"] == "use" and not item["value"] else "unknown" if head["kind"] == "source_gap" and item["value"] else "open"
            candidate = {"object_epoch": head["object_ref"]["object_epoch"], "person_ref": head["person_ref"],
                         "association_generation": head["association_generation"], "capture_epoch": cap["capture_epoch"],
                         "capture_seq": cap["capture_seq"], "state": state, "observed_at": head["observed_at"]}
            rank = (head["observed_at"], GEOMETRY_PRIORITY[state], record["publication"]["payload_digest"])
            if subject not in subjects or rank > subjects[subject][0]:
                subjects[subject] = (rank, candidate)
        authority, changed = self.authority(site, room), False
        for subject, (_, fresh) in subjects.items():
            row = self.j.db.execute("SELECT body FROM dynamic_heads WHERE site_id=? AND room_id=? AND subject=?", (site, room, subject)).fetchone()
            old = json.loads(row[0]) if row else None
            if old and (fresh["observed_at"] < old.get("observed_at", -1) or fresh["association_generation"] < old["association_generation"]):
                continue
            guards = ("object_epoch", "person_ref", "association_generation", "capture_epoch")
            if old is None or any(fresh[k] != old[k] for k in guards) or (fresh["state"] in {"closed", "withdrawn"}) != (old["state"] in {"closed", "withdrawn"}):
                changed = True
            if old and any(r["publication"]["cursor"] in pending_cursors
                           and canonical([r["head"]["object_ref"][k] for k in (*SCOPE, "oid")]) == subject
                           and (r["head"]["person_ref"] != old["person_ref"] or r["head"]["object_ref"]["object_epoch"] != old["object_epoch"]
                                or r["head"]["association_generation"] != old["association_generation"])
                           for r in records):
                changed = True
            authority["association_generation"] = max(authority["association_generation"], fresh["association_generation"])
            self.j.db.execute("INSERT INTO dynamic_heads VALUES(?,?,?,?) ON CONFLICT(site_id,room_id,subject) DO UPDATE SET body=excluded.body", (site, room, subject, canonical(fresh)))
        for record in records:
            if record["head"]["association_generation"] > authority["association_generation"]:
                changed = True
            authority["association_generation"] = max(authority["association_generation"], record["head"]["association_generation"])
            if record["head"]["kind"] == "geometry":
                registration = record["publication"]["payload"]["registration_generation"]
                if registration > authority["registration_generation"]:
                    authority["registration_generation"] = registration
                    changed = True
            elif record["head"]["kind"] == "source_gap" and record["component"]["value"] and record["publication"]["cursor"] in pending_cursors:
                changed = True
        for cursor in pending_cursors:
            self.j._set_meta("dynamic-head-applied:" + str(cursor), 1)
        if changed:
            self._authority_update(site, room, **{k: v for k, v in authority.items() if k != "generation"})

    def append(self, event, *, context_seq=None, status="accepted"):
        j, db = self.j, self.j.db
        retry = j._receipt(event)
        if retry:
            return retry
        kind = event["payload_type"]
        if kind not in QUOTAS:
            raise JournalError("unsupported_schema", "payload is outside the closed synthetic event set")
        if db.execute("SELECT count(*) FROM publications WHERE payload_type=?", (kind,)).fetchone()[0] >= QUOTAS[kind]:
            raise JournalError("journal_full", "synthetic per-type publication reservation exhausted", 507)
        if kind in {"home-geometry-3", "home-fact-batch-2"}:
            self._roots(event)
        source = self.source(event)
        if j._revoked(event["site_id"], event["room_id"], source, event["revision"]) and kind != "home-context-revocation-1":
            raise JournalError("revoked", "source or revision is revoked", 409)
        if db.execute("SELECT count(*) FROM receipts").fetchone()[0] >= j.limits.max_receipts:
            raise JournalError("journal_full", "durable identity budget exhausted", 507)
        cursor, committed = int(j._meta("cursor")) + 1, j.clock()
        publication = {"schema": "home-context-publication-1", "id": event["id"], "producer_id": event["producer_id"],
                       "stream_epoch": j._meta("epoch"), "journal_epoch": j._meta("epoch"), "cursor": cursor,
                       "context_seq": cursor if context_seq is None else context_seq, "committed_at": committed,
                       "payload_type": kind, "payload_version": event["payload_version"],
                       "payload": event["payload"], "payload_digest": digest(event["payload"])}
        body = bounded_json(publication, j.limits.max_body_bytes)
        db.execute("INSERT INTO publications VALUES(?,?,?,?,?,?,?)", (cursor, event["site_id"], event["room_id"], source, event["revision"], kind, body))
        receipt = {"schema": "home-context-receipt-1", "id": event["id"], "producer_id": event["producer_id"],
                   "stream_epoch": j._meta("epoch"), "cursor": cursor, "committed_at": committed,
                   "payload_digest": publication["payload_digest"], "status": status}
        stored = j._tombstone(event) if kind in OUTPUTS else event
        db.execute("INSERT INTO receipts(producer_id,source_epoch,event_id,digest,receipt,event) VALUES(?,?,?,?,?,?)",
                   (event["producer_id"], event["stream_epoch"], event["id"], digest(event), canonical(receipt), canonical(stored)))
        subject = event["payload"].get("id", "room") if kind == "home-intent-event-2" else "room"
        key = canonical([event["site_id"], event["room_id"], source, kind, subject])
        db.execute("INSERT INTO state VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET revision=excluded.revision,valid_until=excluded.valid_until,body=excluded.body",
                   (key, event["site_id"], event["room_id"], source, event["revision"], event["valid_until"], body))
        j._set_meta("cursor", cursor)
        self._heads(event)
        self._refresh_heads(event["site_id"], event["room_id"])
        if kind == "home-intent-event-2":
            auth = self.authority(event["site_id"], event["room_id"])
            changes = {}
            if event["payload"]["kind"] == "association_correction":
                changes["association_generation"] = auth["association_generation"] + 1
            self._authority_update(event["site_id"], event["room_id"], **changes)
        self._storage_budget(kind in {"home-context-revocation-1", "home-evidence-reply-2"})
        return receipt

    def _qualified_components(self, site, room, t, *, eligible=None, masks=None):
        """Derive bounded causal heads from original publications, including fences.

        Publication arrival is not observation order. Future availability cannot
        retire an eligible predecessor, and expiry cannot resurrect older data.
        """
        records = []
        for row in self.j.db.execute("SELECT * FROM publications WHERE site_id=? AND room_id=? AND payload_type IN ('home-geometry-3','home-fact-batch-2')", (site, room)).fetchall():
            if eligible is not None and row["cursor"] not in eligible:
                continue
            publication = json.loads(row["body"]); body = publication["payload"]
            geometric = body["schema"] == "home-geometry-3"
            for item in body["objects"] if geometric else body["facts"]:
                clocks = body if geometric else item
                if max(publication["committed_at"], body["available_at"], clocks["observed_at"], clocks["available_at"]) > t:
                    continue
                mask = (masks or {}).get(publication["cursor"])
                if mask is not None and component_id(item, geometric) not in mask:
                    continue
                cap = clocks["capture"]
                lineage = canonical([cap["source_id"], publication["producer_id"], *[item["object_ref"][k] for k in SCOPE], item["object_ref"]["oid"], body["schema"]])
                head = {"payload_type": body["schema"], "person_ref": item["person_ref"], "object_ref": item["object_ref"],
                        "association_generation": item["association_generation"], "kind": "geometry" if geometric else item["kind"],
                        "capture": cap, **{k: clocks[k] for k in ("observed_at", "available_at", "valid_until")},
                        "publication_ref": {"journal_epoch": publication["journal_epoch"], "cursor": publication["cursor"],
                                            "payload_digest": publication["payload_digest"], "component_id": item["object_ref"]["oid"] if geometric else item["id"]}}
                records.append({"lineage": lineage, "head": head, "publication": publication,
                                "component": item, "source_id": cap["source_id"], "revision": row["revision"],
                                "payload_type": body["schema"], "site_id": site, "room_id": room})
        # Same-observed negative geometry wins conservatively; stable digest
        # breaks remaining ties without interpreting receipt order as truth.
        records.sort(key=lambda r: (r["head"]["observed_at"], GEOMETRY_PRIORITY[r["component"]["state"]] if r["head"]["kind"] == "geometry" else 0, r["publication"]["payload_digest"]))
        families = {}
        for record in records:
            lineage, head = record["lineage"], record["head"]
            key = (lineage, head["payload_type"])
            previous = families.get(key, [])
            if previous and previous[0]["head"]["observed_at"] < head["observed_at"]:
                previous = []
            if head["kind"] == "geometry":
                families[key] = [record]
            else:
                # Every interpretation of the newest original observation is
                # retained, even when one child's own expiry has elapsed.
                families[key] = previous + [record]
        return [r for family in families.values() for r in family]

    def append_event(self, event, grant):
        self.j._event(event)
        name, p = event["payload_type"], event["payload"]
        grant.require("event", event["site_id"], event["room_id"], payload_type=name, revision=event["revision"], producer_id=event["producer_id"])
        if name in OUTPUTS:
            raise JournalError("worker_commit_required", "shared outputs require an atomic worker commit")
        if name not in {"home-fact-batch-2", "home-geometry-3", "home-source-health-1"}:
            raise JournalError("typed_route_required", "typed command requires its dedicated route")
        if name in {"home-fact-batch-2", "home-geometry-3"}:
            if any(event[k] != p[k] for k in ("observed_at", "available_at", "valid_until")):
                raise JournalError("invalid_payload", "source envelope clocks must equal original body clocks")
            records = p["facts"] if name == "home-fact-batch-2" else [p]
            if any(r["capture"]["source_id"] not in grant.source_ids for r in records):
                raise JournalError("forbidden", "capture source is outside the explicit synthetic grant", 403)
            if name == "home-geometry-3":
                bounded_json(p, 4096)
                if not any(r[1] == p["capture"]["source_id"] and r[3] == p["registry_digest"] for r in grant.registration_bindings):
                    raise JournalError("forbidden", "geometry registry is outside the explicit synthetic binding", 403)
        return self.j._transaction(lambda: self.append(event))

    def _event_for(self, payload, grant=None, *, producer_id=None, valid_until=None):
        t = payload.get("observed_at", payload.get("available_at", payload.get("t", self.j.clock())))
        version = self.j.schemas.payload_versions[payload["schema"]]
        return {"schema": "home-context-event-1", "schema_version": "1.0", "id": payload["id"],
                "producer_id": producer_id or grant.producer_id, "stream_epoch": self.j._meta("epoch"),
                **{k: payload[k] for k in SCOPE}, "observed_at": t, "available_at": payload.get("available_at", t),
                "valid_until": valid_until if valid_until is not None else payload.get("valid_until", t + 3),
                "payload_type": payload["schema"], "payload_version": str(version) + ".0",
                "revision": str(version) + ".0", "payload": payload}

    def intent(self, payload, grant):
        self.validate_payload(payload)
        if payload["schema"] != "home-intent-event-2":
            raise JournalError("unsupported_schema", "intent-2 required")
        grant.require("intent", payload["site_id"], payload["room_id"], payload_type=payload["schema"], revision="2.0")
        if not grant.expected_actor_ref or payload["actor_ref"] != grant.expected_actor_ref:
            raise JournalError("forbidden", "intent actor must match the explicit grant", 403)
        event = self._event_for(payload, grant)
        self.j._event(event)
        return self.j._transaction(lambda: self.append(event))

    def _visible(self, grant, site, room, after, through, *, inputs_only=False):
        names = sorted(grant.payload_types & INPUTS if inputs_only else grant.payload_types)
        revisions = sorted(grant.revisions)
        if not names or not revisions:
            return []
        return self.j.db.execute("SELECT * FROM publications WHERE site_id=? AND room_id=? AND cursor>? AND cursor<=? AND payload_type IN (%s) AND revision IN (%s) ORDER BY cursor" %
                                 (",".join("?" for _ in names), ",".join("?" for _ in revisions)),
                                 (site, room, after, through, *names, *revisions)).fetchall()

    def _delivered_publications(self, grant, consumer, publications):
        # This is server delivery time, never a fabricated consumer receipt.
        for publication in publications:
            self.j.db.execute("INSERT OR IGNORE INTO dynamic_deliveries VALUES(?,?,?,?)",
                              (grant.principal_id, consumer, publication["cursor"], self.j.clock()))

    def page(self, grant, site, room, *, after=0, stream_epoch=None, limit=128, consumer_id=None):
        grant.require("read", site, room)
        if type(limit) is not int or not 1 <= limit <= 128:
            raise JournalError("invalid_query", "limit must be 1..128")
        def read():
            self._expire_requests(site, room)
            self._refresh_heads(site, room)
            tail = self.j._epoch_cursor(stream_epoch, after)
            rows = self._visible(grant, site, room, after, tail)
            selected, used = [], 2048
            for row in rows[:limit]:
                size = len(row["body"].encode("utf-8"))
                if used + size > self.j.limits.max_response_bytes:
                    break
                selected.append(json.loads(row["body"])); used += size
            more = len(selected) < len(rows)
            next_cursor = selected[-1]["cursor"] if more and selected else after if more else tail
            self._delivered_publications(grant, consumer_id or grant.principal_id, selected)
            self.j._delivered(grant, consumer_id or grant.principal_id, site, room, next_cursor)
            return {"schema": "home-context-page-1", "stream_epoch": self.j._meta("epoch"), "after": after,
                    "items": selected, "next_cursor": next_cursor, "authority": self.authority(site, room),
                    "revocation_generation": self.authority(site, room)["revocation_generation"]}
        return self.j._transaction(read)

    def snapshot(self, grant, site, room, consumer_id=None):
        grant.require("read", site, room)
        def read():
            now = self.j.clock()
            # Snapshot2 has no pending collection. Do not advance its global
            # cursor past an original whose components can become eligible later.
            for row in self.j.db.execute("SELECT * FROM publications WHERE site_id=? AND room_id=? AND payload_type IN ('home-geometry-3','home-fact-batch-2')", (site, room)).fetchall():
                if row["revision"] not in grant.revisions or row["payload_type"] not in grant.payload_types:
                    continue
                publication = json.loads(row["body"]); body = publication["payload"]
                geometric = body["schema"] == "home-geometry-3"
                for component in body["objects"] if geometric else body["facts"]:
                    clocks = body if geometric else component
                    if self.j._revoked(site, room, clocks["capture"]["source_id"], row["revision"]):
                        continue
                    if max(publication["committed_at"], body["available_at"], clocks["observed_at"], clocks["available_at"]) > now:
                        raise JournalError("snapshot_pending_inputs", "snapshot has pending original components; use cut/checkpoint recovery or retry after availability", 409)
            self._expire_requests(site, room)
            self._refresh_heads(site, room)
            tail = int(self.j._meta("cursor"))
            rows = self.j.db.execute("SELECT body,revision,source_id FROM state WHERE site_id=? AND room_id=? AND valid_until>?", (site, room, now)).fetchall()
            items = {json.loads(r["body"])["cursor"]: json.loads(r["body"]) for r in rows
                     if r["revision"] in grant.revisions and json.loads(r["body"])["payload_type"] in grant.payload_types
                     and json.loads(r["body"])["payload_type"] not in {"home-fact-batch-2", "home-geometry-3"}
                     and not self.j._revoked(site, room, r["source_id"], r["revision"])}
            heads, masks = [], {}
            components = self._qualified_components(site, room, now)
            for row in components:
                if (row["revision"] not in grant.revisions or row["payload_type"] not in grant.payload_types
                        or self.j._revoked(site, room, row["source_id"], row["revision"])):
                    continue
                head = row["head"]
                if row["payload_type"] == "home-fact-batch-2" and head["valid_until"] <= now:
                    continue
                # Expired and explicit negative geometry heads remain fences.
                publication = row["publication"]
                items[publication["cursor"]] = publication
                heads.append(head)
                masks.setdefault(publication["cursor"], set()).add(component_id(row["component"], row["payload_type"] == "home-geometry-3"))
            items = sorted(items.values(), key=lambda p: p["cursor"])
            heads.sort(key=lambda h: (h["publication_ref"]["cursor"], canonical(h)))
            result = {"schema": "home-context-snapshot-2", "version": 2, "stream_epoch": self.j._meta("epoch"), "cursor": tail,
                      "as_of_t": now, "items": items, "component_heads": heads,
                      "authority": self.authority(site, room), "revocation_generation": self.authority(site, room)["revocation_generation"]}
            if len(heads) > 64:
                raise JournalError("snapshot_budget", "current component selectors exceed the synthetic snapshot bound", 507)
            bounded_json(result, self.j.limits.max_response_bytes)
            consumer = consumer_id or grant.principal_id
            identifier(consumer, "consumer_id")
            for cursor, ids in masks.items():
                self.j.db.execute("INSERT OR IGNORE INTO dynamic_snapshot_masks VALUES(?,?,?,?,?)", (grant.principal_id, consumer, cursor, canonical(sorted(ids)), now))
            if self.j.db.execute("SELECT count(*) FROM dynamic_snapshot_masks").fetchone()[0] > 2048:
                raise JournalError("snapshot_budget", "snapshot selector proof reservation exhausted", 507)
            self._storage_budget()
            self._delivered_publications(grant, consumer, items)
            self.j._delivered(grant, consumer, site, room, tail)
            return result
        return self.j._transaction(read)

    def input_cut(self, grant, site, room, *, consumer_id, after, t, limit=128, cut_id=None):
        grant.require("read", site, room)
        grant.require("worker_commit", site, room)
        if consumer_id != grant.expected_worker_id:
            raise JournalError("forbidden", "cut consumer must be the configured worker", 403)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 128 or not finite(t) or not 0 <= t <= self.j.clock():
            raise JournalError("invalid_query", "bounded causal cut parameters required")
        def read():
            self._refresh_heads(site, room)
            if cut_id:
                row = self.j.db.execute("SELECT * FROM dynamic_cuts WHERE cut_id=? AND principal_id=? AND consumer_id=? AND site_id=? AND room_id=?",
                                        (cut_id, grant.principal_id, consumer_id, site, room)).fetchone()
                if not row:
                    raise JournalError("unknown_cut", "cut is outside the granted worker scope", 404)
                if t != row["as_of_t"] or not row["after_cursor"] <= after <= row["delivered_cursor"]:
                    raise JournalError("incomplete_cut", "cut continuation cannot skip receipts or change time", 409)
                frozen, authority = row["cut_cursor"], json.loads(row["authority"])
                identifier(cut_id, "cut_id")
            else:
                self._expire_requests(site, room)
                frozen = self.j._epoch_cursor(None, after)
                if self.j.db.execute("SELECT count(*) FROM dynamic_cuts").fetchone()[0] >= 1024:
                    raise JournalError("cut_budget", "synthetic fence budget exhausted", 429)
                authority, new_id = self.authority(site, room), "cut:" + str(uuid.uuid4())
                self.j.db.execute("INSERT INTO dynamic_cuts VALUES(?,?,?,?,?,?,?,?,?,?)",
                                  (new_id, grant.principal_id, consumer_id, site, room, after, frozen, t, canonical(authority), after))
                cut_id_local = new_id
            actual_id = cut_id if cut_id else cut_id_local
            rows = self._visible(grant, site, room, after, frozen, inputs_only=True)
            publications, used = [], 4096
            for row in rows[:limit]:
                size = len(row["body"].encode("utf-8"))
                if used + size > self.j.limits.max_response_bytes:
                    break
                publications.append(json.loads(row["body"])); used += size
            complete = len(publications) == len(rows)
            next_cursor = frozen if complete else publications[-1]["cursor"] if publications else after
            self.j.db.execute("UPDATE dynamic_cuts SET delivered_cursor=max(delivered_cursor,?) WHERE cut_id=?", (next_cursor, actual_id))
            self._delivered_publications(grant, consumer_id, publications)
            self.j._delivered(grant, consumer_id, site, room, next_cursor)
            self._storage_budget()
            return {"journal_epoch": self.j._meta("epoch"), "cut_id": actual_id, "cut_cursor": frozen,
                    "as_of_t": t, "authority": authority, "complete": complete,
                    "publications": publications, "next_cursor": next_cursor}
        return self.j._transaction(read)

    def checkpoint(self, worker_id, grant, site, room):
        grant.require("read", site, room)
        grant.require("worker_commit", site, room)
        if worker_id != grant.expected_worker_id:
            raise JournalError("forbidden", "worker identity does not match configured principal", 403)
        def read():
            self._refresh_heads(site, room)
            row = self.j.db.execute("SELECT * FROM workers WHERE worker_id=? AND site_id=? AND room_id=?", (worker_id, site, room)).fetchone()
            cp = json.loads(row["checkpoint"]) if row else None
            return {"schema": "home-context-worker-checkpoint-response-3", "journal_epoch": self.j._meta("epoch"),
                    "worker_id": worker_id, "input_cursor": row["input_cursor"] if row else 0,
                    "commit_seq": row["context_seq"] if row else 0, "semantic_seq": cp["semantic_seq"] if cp else -1,
                    "control_seq": cp["control_seq"] if cp else 0, "checkpoint": cp, "authority": self.authority(site, room)}
        return self.j._transaction(read)

    def _ref(self, ref, grant, scope, through, *, allow_revoked=False):
        if ref["journal_epoch"] != self.j._meta("epoch") or ref["cursor"] > through:
            raise JournalError("invalid_reference", "reference is outside the fixed journal cut", 409)
        row = self.j.db.execute("SELECT * FROM publications WHERE cursor=?", (ref["cursor"],)).fetchone()
        if not row or row["site_id"] != scope["site_id"] or row["room_id"] != scope["room_id"]:
            raise JournalError("invalid_reference", "reference is not durable in the granted room", 409)
        p = json.loads(row["body"])
        if p["payload_type"] not in grant.payload_types or row["revision"] not in grant.revisions or p["payload_digest"] != ref["payload_digest"] or not same_scope(p["payload"], scope):
            raise JournalError("invalid_reference", "reference binding or digest mismatch", 409)
        if not allow_revoked and self.j._revoked(row["site_id"], row["room_id"], row["source_id"], row["revision"]):
            raise JournalError("revoked", "referenced source is revoked", 409)
        component = ref.get("component_id")
        if component and p["payload_type"] == "home-geometry-3" and component not in {o["object_ref"]["oid"] for o in p["payload"]["objects"]}:
            raise JournalError("invalid_reference", "geometry component does not exist", 409)
        return p

    def _validate_cut(self, commit, grant, previous):
        cut, cp = commit["input_cut"], commit["checkpoint"]
        row = self.j.db.execute("SELECT * FROM dynamic_cuts WHERE cut_id=? AND principal_id=? AND consumer_id=? AND site_id=? AND room_id=?",
                                (cut["cut_id"], grant.principal_id, grant.expected_worker_id, commit["site_id"], commit["room_id"])).fetchone()
        if (not row or not cut["complete"] or row["delivered_cursor"] != row["cut_cursor"]
                or cut["input_cursor"] != row["cut_cursor"] or cut["as_of_t"] != row["as_of_t"]
                or row["after_cursor"] != previous or cut["authority"] != json.loads(row["authority"])):
            raise JournalError("incomplete_cut", "commit requires its complete original receipt fence", 409)
        if cut["authority"] != commit["expected_authority"] or cp["authority"] != cut["authority"] or cut["authority"] != self.authority(commit["site_id"], commit["room_id"]):
            raise JournalError("authority_conflict", "authority changed after the cut", 409)
        known, known_rows = {}, {}
        old_worker = self.j.db.execute("SELECT checkpoint FROM workers WHERE worker_id=? AND site_id=? AND room_id=?", (grant.expected_worker_id, commit["site_id"], commit["room_id"])).fetchone()
        if old_worker:
            prior_checkpoint = json.loads(old_worker[0])
            known = {r["ref"]["cursor"]: r for r in prior_checkpoint["receipts"]}
            known_rows = {r["publication"]["cursor"]: r for k in ("pending_inputs", "geometry_inputs", "retained_inputs") for r in prior_checkpoint[k]}
        receipts = {}
        for claimed in cut["receipts"]:
            cursor = claimed["ref"]["cursor"]
            if cursor in receipts:
                raise JournalError("invalid_input_receipt", "receipt cursor repeated", 409)
            p = self._ref(claimed["ref"], grant, commit, row["cut_cursor"], allow_revoked=True)
            payload = p["payload"]
            delivered = self.j.db.execute("SELECT served_at FROM dynamic_deliveries WHERE principal_id=? AND consumer_id=? AND cursor=?",
                                          (grant.principal_id, grant.expected_worker_id, cursor)).fetchone()
            if p["payload_type"] not in INPUTS or not delivered or not max(p["committed_at"], delivered[0]) <= claimed["received_at"] <= self.j.clock():
                raise JournalError("invalid_input_receipt", "receipt is not a causal original worker input", 409)
            eligible = max(payload.get("observed_at", payload.get("t", payload.get("available_at", p["committed_at"]))),
                           payload.get("available_at", p["committed_at"]), p["committed_at"], claimed["received_at"])
            if claimed["eligible_at"] != eligible or cursor in known and claimed["received_at"] != known[cursor]["received_at"]:
                raise JournalError("invalid_input_receipt", "original input receipt time cannot change", 409)
            receipts[cursor] = claimed
        required = {r["cursor"] for r in self._visible(grant, commit["site_id"], commit["room_id"], previous, row["cut_cursor"], inputs_only=True)}
        if not required <= set(receipts):
            raise JournalError("incomplete_cut", "a visible input receipt is missing from the cut", 409)
        eligible_refs, buffered_refs = cut["eligible_refs"], cut["buffered_refs"]
        eligible_cursors, buffered_cursors = [r["cursor"] for r in eligible_refs], [r["cursor"] for r in buffered_refs]
        if len(set(eligible_cursors + buffered_cursors)) != len(eligible_cursors + buffered_cursors) or set(eligible_cursors + buffered_cursors) != set(receipts):
            raise JournalError("invalid_input_receipt", "eligible and buffered references must partition receipts", 409)
        for cursor in sorted(receipts):
            # A journal cursor orders durable delivery, not physical or receipt
            # clocks. Complete delivery is checked above; independently buffer
            # each future input without suppressing an eligible later input.
            now_eligible = receipts[cursor]["eligible_at"] <= cut["as_of_t"]
            if (cursor in eligible_cursors) != now_eligible:
                raise JournalError("receipt_order", "input eligibility differs from its original causal clocks", 409)
        for reference in eligible_refs + buffered_refs:
            if reference["payload_digest"] != receipts[reference["cursor"]]["ref"]["payload_digest"] or reference["journal_epoch"] != cut["journal_epoch"]:
                raise JournalError("invalid_reference", "cut reference differs from its receipt", 409)
        retained = cp["pending_inputs"] + cp["geometry_inputs"] + cp["retained_inputs"]
        body_cursors = set()
        for item in retained:
            saved = item["publication"]
            if saved["cursor"] in body_cursors:
                raise JournalError("invalid_reference", "checkpoint body cursor repeated", 409)
            body_cursors.add(saved["cursor"])
            ref = {"journal_epoch": saved["journal_epoch"], "cursor": saved["cursor"], "payload_digest": saved["payload_digest"]}
            original = self._ref(ref, grant, commit, row["cut_cursor"], allow_revoked=True)
            fields = ("journal_epoch", "cursor", "id", "producer_id", "committed_at", "payload_type", "payload_version", "payload_digest", "payload")
            if saved != {k: original[k] for k in fields} or saved["cursor"] not in receipts or item["received_at"] != receipts[saved["cursor"]]["received_at"]:
                raise JournalError("invalid_reference", "checkpoint body or original receipt was changed", 409)
            if "component_ids" in item:
                payload = original["payload"]
                ids = item["component_ids"]
                allowed = {component_id(c, payload["schema"] == "home-geometry-3") for c in
                           (payload["objects"] if payload["schema"] == "home-geometry-3" else payload["facts"] if payload["schema"] == "home-fact-batch-2" else [])}
                if len(ids) != len(set(ids)) or not ids or ids != sorted(ids) or not set(ids) <= allowed:
                    raise JournalError("invalid_reference", "checkpoint component selector is not in its original publication", 409)
                if saved["cursor"] not in known_rows:
                    issued = self.j.db.execute("SELECT served_at FROM dynamic_snapshot_masks WHERE principal_id=? AND consumer_id=? AND cursor=? AND component_ids=?",
                                               (grant.principal_id, grant.expected_worker_id, saved["cursor"], canonical(ids))).fetchone()
                    if not issued or issued[0] > item["received_at"]:
                        raise JournalError("invalid_reference", "first component mask requires its authentic delivered snapshot selection", 409)
            if saved["cursor"] in known_rows and item.get("component_ids") != known_rows[saved["cursor"]].get("component_ids"):
                raise JournalError("invalid_reference", "retained component selection cannot change", 409)
        checkpoint_receipts = set()
        for claimed in cp["receipts"]:
            if claimed["ref"]["cursor"] in checkpoint_receipts:
                raise JournalError("invalid_input_receipt", "checkpoint receipt cursor repeated", 409)
            checkpoint_receipts.add(claimed["ref"]["cursor"])
            if claimed["ref"]["cursor"] not in receipts or claimed != receipts[claimed["ref"]["cursor"]]:
                raise JournalError("invalid_input_receipt", "checkpoint receipt differs from its original cut", 409)
        pending_cursors = {item["publication"]["cursor"] for item in cp["pending_inputs"]}
        if body_cursors != checkpoint_receipts:
            raise JournalError("invalid_input_receipt", "retained bodies and original checkpoint receipts must match exactly", 409)
        for cursor in buffered_cursors:
            original = self._ref(receipts[cursor]["ref"], grant, commit, row["cut_cursor"], allow_revoked=True)
            discarded_by_revocation = (not cut["authority"]["motion_allowed"]
                                       and original["payload_type"] in {"home-geometry-3", "home-fact-batch-2"})
            if not discarded_by_revocation and (cursor not in pending_cursors or cursor not in checkpoint_receipts):
                raise JournalError("invalid_input_receipt", "buffered input requires its immutable pending body and original receipt", 409)
        source_facts, source_intents = {}, {}
        for item in retained:
            payload = item["publication"]["payload"]
            if payload["schema"] == "home-fact-batch-2":
                for fact in payload["facts"]:
                    if item.get("component_ids") is None or fact["id"] in item["component_ids"]:
                        source_facts.setdefault(fact["id"], set()).add(digest(fact))
            elif payload["schema"] == "home-intent-event-2":
                source_intents.setdefault(payload["id"], set()).add(digest(payload))
        for values, sources in ((cp["facts"], source_facts), (cp["intents"], source_intents)):
            if len({v["id"] for v in values}) != len(values) or any(digest(v) not in sources.get(v["id"], set()) for v in values):
                raise JournalError("invalid_reference", "materialized checkpoint inputs must be selected original components", 409)
        return receipts

    def _geometry(self, snapshot, cut, commit, grant, receipts):
        self.validate_payload(snapshot)
        if (snapshot["digest"] != cut["geometry_snapshot_digest"] or snapshot["as_of_t"] != cut["as_of_t"]
                or snapshot["input_cursor"] != cut["input_cursor"] or snapshot["authority"] != cut["authority"]):
            raise JournalError("invalid_reference", "control geometry snapshot differs from its atomic cut", 409)
        eligible = {r["cursor"] for r in cut["eligible_refs"]}
        cp = commit["checkpoint"]
        masks = {r["publication"]["cursor"]: r["component_ids"] for k in ("pending_inputs", "geometry_inputs", "retained_inputs") for r in cp[k] if "component_ids" in r}
        current = {}
        for candidate in self._qualified_components(commit["site_id"], commit["room_id"], cut["as_of_t"], eligible=eligible, masks=masks):
            if candidate["payload_type"] != "home-geometry-3":
                continue
            key = object_key(candidate["head"]["object_ref"])
            rank = lambda r: (r["head"]["observed_at"], GEOMETRY_PRIORITY[r["component"]["state"]], r["publication"]["payload_digest"])
            if key not in current or rank(candidate) > rank(current[key]):
                current[key] = candidate
        for head in snapshot["object_heads"]:
            if head["estimate_ref"] is None:
                if head["status"] in {"fresh", "held"}:
                    raise JournalError("invalid_reference", "usable head requires an immutable estimate", 409)
                continue
            p = self._ref(head["estimate_ref"], grant, commit, cut["input_cursor"], allow_revoked=head["status"] not in {"fresh", "held"})
            payload = p["payload"]
            if payload["schema"] != "home-geometry-3" or p["cursor"] not in eligible or p["cursor"] not in receipts:
                raise JournalError("invalid_reference", "geometry must have an eligible original receipt", 409)
            selected = current.get(object_key(head["object_ref"]))
            if not selected or head["estimate_ref"] != selected["head"]["publication_ref"]:
                raise JournalError("stale_geometry", "snapshot did not select its current eligible component lineage", 409)
            matches = [o for o in payload["objects"] if o["object_ref"] == head["object_ref"]]
            if len(matches) != 1 or head["estimate"] != matches[0] or head["capture"] != payload["capture"] or snapshot["registry_digest"] != payload["registry_digest"]:
                raise JournalError("invalid_reference", "geometry body is not the immutable selected estimate", 409)
            for field in ("observed_at", "available_at", "valid_until"):
                if head[field] != payload[field]:
                    raise JournalError("invalid_reference", "geometry timing was changed", 409)
            if head["status"] in {"fresh", "held"}:
                age = cut["as_of_t"] - payload["observed_at"]
                if not 0 <= age <= 0.5 + 1e-9 or cut["as_of_t"] >= min(payload["valid_until"], payload["observed_at"] + 1):
                    raise JournalError("stale_geometry", "usable estimate exceeds freshness or TTL", 409)

    def _target(self, target, output, commit, grant):
        """Bind materialized page coordinates to durable evidence and authority."""
        cut, cp = commit["input_cut"], commit["checkpoint"]
        publication = self._ref(target["estimate_ref"], grant, commit, cut["input_cursor"])
        geometry = publication["payload"]
        if geometry["schema"] != "home-geometry-3":
            raise JournalError("invalid_reference", "target requires original page geometry", 409)
        matches = [o for o in geometry["objects"] if o["object_ref"] == target["object_ref"]]
        if len(matches) != 1 or target["estimate_ref"].get("component_id") != target["object_ref"]["oid"]:
            raise JournalError("invalid_reference", "target component differs from its original object", 409)
        original = matches[0]
        fields = ("person_ref", "object_ref", "association_generation", "patches", "uncertainty")
        if any(target[k] != original[k] for k in fields) or original["state"] != "open":
            raise JournalError("invalid_reference", "target body differs from its immutable open pages", 409)
        policy = cp["pipeline"]["policy"] if cp["pipeline"] else None
        requests = [r for r in (policy["requests"] if policy else []) if r["id"] == target["request_id"]]
        if len(requests) != 1:
            raise JournalError("invalid_reference", "target lacks a checkpointed consumer request", 409)
        request = requests[0]
        lease = request["lease"]
        if (any(request[k] != target[k] for k in ("person_ref", "object_ref", "association_generation", "requirements"))
                or request["valid_until"] <= cut["as_of_t"] or lease["valid_until"] <= cut["as_of_t"]
                or lease["authority_generation"] != cut["authority"]["generation"]
                or not cut["authority"]["motion_allowed"]):
            raise JournalError("invalid_reference", "target request authority or attribution changed", 409)
        heads = [h for h in output["geometry_snapshot"]["object_heads"] if h["object_ref"] == target["object_ref"]]
        if (len(heads) != 1 or not heads[0]["estimate"]
                or any(heads[0]["estimate"][k] != target[k] for k in ("person_ref", "object_ref", "association_generation"))
                or heads[0]["estimate"]["state"] != "open" or heads[0]["status"] == "rejected"):
            raise JournalError("invalid_reference", "current object authority differs from the target", 409)
        if target["status"] == "resolved":
            if (request["target_kind"] != "runtime_pages" or heads[0]["status"] != "fresh"
                    or heads[0]["estimate"]["state"] != "open" or target["estimate_ref"] != heads[0]["estimate_ref"]):
                raise JournalError("invalid_reference", "resolved target must use its current fresh head", 409)
        else:
            if request["target_kind"] != "declared_frozen_pages":
                raise JournalError("invalid_reference", "frozen target lacks a declared request", 409)
            intents = cp["pipeline"]["intents"]
            applicable = [i for i in intents if i["kind"] == "keep_here" and i["object_ref"] == target["object_ref"]
                          and i["person_ref"] == target["person_ref"] and lease["id"] == "declared-lease:" + i["id"]]
            if len(applicable) != 1:
                raise JournalError("invalid_reference", "frozen target lacks an attributable keep-here intent", 409)
            intent = applicable[0]
            outcomes = [o for o in cp["pipeline"].get("intent_outcomes", []) if o["intent_id"] == intent["id"]]
            if len(outcomes) != 1 or outcomes[0]["status"] != "applied" or outcomes[0]["intent_digest"] != digest(intent):
                raise JournalError("invalid_reference", "frozen target requires first applied keep-here admission", 409)
            if (lease["issued_at"] != intent["available_at"]
                    or lease["valid_until"] > min(intent["valid_until"], intent["available_at"] + 30, 120)):
                raise JournalError("invalid_reference", "frozen target exceeds its declared lifetime", 409)
            durable_intents = []
            for reference in cut["eligible_refs"]:
                original_input = self._ref(reference, grant, commit, cut["input_cursor"], allow_revoked=True)
                if original_input["payload_type"] == "home-intent-event-2" and original_input["payload"] == intent:
                    durable_intents.append(original_input)
            if len(durable_intents) != 1:
                raise JournalError("invalid_reference", "frozen intent is not an eligible durable input", 409)
            previous_row = self.j.db.execute("SELECT checkpoint FROM workers WHERE worker_id=? AND site_id=? AND room_id=?",
                                            (grant.expected_worker_id, commit["site_id"], commit["room_id"])).fetchone()
            previous = json.loads(previous_row[0]) if previous_row else None
            previous_pipeline = previous["pipeline"] if previous else None
            previous_intents = previous_pipeline["intents"] if previous_pipeline else []
            previous_heads = [h for h in (previous_pipeline["frozen_heads"] if previous_pipeline else [])
                              if h["object_ref"] == target["object_ref"]
                              and h["estimate"] and all(h["estimate"][k] == target[k] for k in fields)]
            same_declaration = any(i == intent for i in previous_intents)
            if same_declaration and len(previous_heads) == 1:
                if target["estimate_ref"] != previous_heads[0]["estimate_ref"]:
                    raise JournalError("invalid_reference", "frozen target changed its captured original reference", 409)
            elif (intent["id"] in (previous_pipeline["intent_ids"] if previous_pipeline else [])
                  or heads[0]["status"] != "fresh" or heads[0]["estimate"]["state"] != "open"
                  or target["estimate_ref"] != heads[0]["estimate_ref"]):
                raise JournalError("invalid_reference", "initial freeze requires current fresh geometry and cannot recapture invalidated authority", 409)
            saved_heads = [h for h in cp["pipeline"]["frozen_heads"] if h["object_ref"] == target["object_ref"]]
            if (len(saved_heads) != 1 or not saved_heads[0]["estimate"] or saved_heads[0]["estimate_ref"] != target["estimate_ref"]
                    or any(saved_heads[0]["estimate"][k] != target[k] for k in fields)):
                raise JournalError("invalid_reference", "frozen target lacks its immutable checkpointed head", 409)

    def _intent_ledger(self, commit, old):
        pipeline = commit["checkpoint"]["pipeline"]
        ledger = pipeline["intent_outcomes"] if pipeline else []
        prior = {o["intent_id"]: o for o in old["pipeline"]["intent_outcomes"]} if old and old["pipeline"] else {}
        current = {o["intent_id"]: o for o in ledger}
        if len(current) != len(ledger) or any(current.get(key) != value for key, value in prior.items()):
            raise JournalError("intent_result_conflict", "first admission ledger is immutable", 409)
        policy = next((o for o in commit["outputs"] if o["schema"] == "home-lighting-policy-decision-2"), None)
        if not policy:
            if current != prior:
                raise JournalError("invalid_intent_result", "only a semantic policy may admit declarations", 409)
            return
        results = {r["id"]: r for r in policy["intent_results"]}
        if len(results) != len(policy["intent_results"]) or set(results) != set(current):
            raise JournalError("invalid_intent_result", "policy and checkpoint admission ledger must agree", 409)
        if pipeline and pipeline["policy"] != policy:
            raise JournalError("invalid_intent_result", "checkpoint must preserve its committed policy", 409)
        retained = commit["checkpoint"]["retained_inputs"] + commit["checkpoint"]["pending_inputs"] + commit["checkpoint"]["geometry_inputs"]
        for key, outcome in current.items():
            result = results[key]
            if any(result[k] != outcome[k] for k in ("status", "reason", "decided_at", "decision_ref")):
                raise JournalError("intent_result_conflict", "policy changed its first admission result", 409)
            bodies = [r["publication"]["payload"] for r in retained if r["publication"]["payload_type"] == "home-intent-event-2" and r["publication"]["payload"]["id"] == key]
            if len(bodies) != 1 or digest(bodies[0]) != outcome["intent_digest"] or outcome["decided_at"] < bodies[0]["available_at"]:
                raise JournalError("invalid_intent_result", "admission requires its unchanged eligible declaration body", 409)
            if key not in prior and (outcome["decided_at"] != policy["t"] or outcome["decision_ref"] != policy["id"]):
                raise JournalError("invalid_intent_result", "first admission binds the current semantic decision", 409)

    def worker_commit(self, commit, grant):
        self.validate_payload(commit)
        if commit["schema"] != "home-context-worker-commit-3":
            raise JournalError("invalid_commit", "commit-3 required")
        grant.require("worker_commit", commit["site_id"], commit["room_id"])
        bounded_json(commit, self.j.limits.max_worker_commit_bytes)
        cp, cut = commit["checkpoint"], commit["input_cut"]
        if any(not same_scope(commit, value) for value in (cp, cut)) or any(value["runtime_epoch"] != commit["runtime_epoch"] or value["journal_epoch"] != commit["journal_epoch"] for value in (cp, cut)):
            raise JournalError("scope_mismatch", "checkpoint/cut identity differs from commit")
        def apply():
            marker = "worker-commit/" + commit["id"]
            retry = self.j.db.execute("SELECT digest,receipt FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?", (grant.producer_id, commit["journal_epoch"], marker)).fetchone()
            if retry:
                if retry["digest"] != digest(commit):
                    raise JournalError("identity_conflict", "commit retry changed its original body", 409)
                return json.loads(retry["receipt"])
            self._refresh_heads(commit["site_id"], commit["room_id"])
            if commit["journal_epoch"] != self.j._meta("epoch") or cp["interface_digest"] != self.j.schemas.interface_digest:
                raise JournalError("reset_required", "journal/interface binding changed", 409)
            row = self.j.db.execute("SELECT * FROM workers WHERE worker_id=? AND site_id=? AND room_id=?", (grant.expected_worker_id, commit["site_id"], commit["room_id"])).fetchone()
            previous, seq = (row["input_cursor"], row["context_seq"]) if row else (0, 0)
            old = json.loads(row["checkpoint"]) if row else None
            if commit["expected_input_cursor"] != previous or commit["expected_commit_seq"] != seq:
                raise JournalError("checkpoint_conflict", "worker checkpoint advanced concurrently", 409)
            sem, ctl = (old["semantic_seq"], old["control_seq"]) if old else (-1, 0)
            delta = {"bootstrap": (0, 0), "semantic": (1, 0), "control": (0, 1), "combined": (1, 1)}[commit["kind"]]
            if cp["commit_seq"] != seq + 1 or cp["semantic_seq"] != sem + delta[0] or cp["control_seq"] != ctl + delta[1] or cp["input_cursor"] != cut["input_cursor"] or cp["last_t"] != cut["as_of_t"]:
                raise JournalError("invalid_commit", "checkpoint counters or cutoff do not match the commit kind")
            if old and (not same_scope(old, commit) or cp["config_digest"] != old["config_digest"] or cp["last_t"] < old["last_t"]):
                raise JournalError("checkpoint_conflict", "checkpoint scope/configuration/clock changed", 409)
            if commit["kind"] == "bootstrap" and row:
                raise JournalError("invalid_commit", "bootstrap is valid only for a fresh worker")
            claimed = self._validate_cut(commit, grant, previous)
            counts = {name: sum(o["schema"] == name for o in commit["outputs"]) for name in OUTPUTS}
            if counts != {"home-engagement-state-3": delta[0], "home-lighting-policy-decision-2": delta[0], "home-lighting-control-decision-1": delta[1]}:
                raise JournalError("invalid_commit", "output set must match the semantic/control sequence deltas")
            self._intent_ledger(commit, old)
            output_receipts = []
            for output in commit["outputs"]:
                if output["t"] != cut["as_of_t"] or output["semantic_seq"] != cp["semantic_seq"]:
                    raise JournalError("invalid_commit", "output time/sequence differs from checkpoint")
                grant.require("worker_commit", commit["site_id"], commit["room_id"], payload_type=output["schema"], revision=str(self.j.schemas.payload_versions[output["schema"]]) + ".0")
                if output["schema"] == "home-lighting-control-decision-1":
                    bounded_json(output, 32 * 1024)
                    if output["control_seq"] != cp["control_seq"] or output["input_cut_digest"] != cut["digest"]:
                        raise JournalError("invalid_commit", "control differs from its cut/sequence")
                    self._geometry(output["geometry_snapshot"], cut, commit, grant, claimed)
                    for ref in output["consumed_refs"]:
                        if ref["cursor"] not in {r["cursor"] for r in cut["eligible_refs"]}:
                            raise JournalError("invalid_reference", "control consumed an ineligible input", 409)
                        self._ref(ref, grant, commit, cut["input_cursor"], allow_revoked=True)
                    for target in output["allocation"]["targets"]:
                        self._target(target, output, commit, grant)
                event = self._event_for(output, grant)
                self.j._event(event)
                if self.j._receipt(event):
                    raise JournalError("output_identity_reused", "new commit reused a publication identity", 409)
                output_receipts.append(self.append(event, context_seq=seq + 1))
                if output["schema"] == "home-lighting-policy-decision-2":
                    for result in output["intent_results"]:
                        rows = self.j.db.execute("SELECT producer_id,source_epoch,event_id,event,application_result FROM receipts WHERE event_id=?", (result["id"],)).fetchall()
                        matches = [r for r in rows if json.loads(r["event"]).get("payload_type") == "home-intent-event-2" and same_scope(json.loads(r["event"]), commit)]
                        if len(matches) != 1:
                            raise JournalError("invalid_intent_result", "result requires one scoped durable declaration", 409)
                        saved = matches[0]
                        if saved["application_result"]:
                            first = json.loads(saved["application_result"])
                            if any(first[k] != result[k] for k in ("status", "reason", "decision_ref")) or first["available_at"] != result["decided_at"]:
                                raise JournalError("intent_result_conflict", "first durable application result cannot change", 409)
                            continue
                        source_receipt = self.j.db.execute("SELECT receipt FROM receipts WHERE producer_id=? AND source_epoch=? AND event_id=?", (saved["producer_id"], saved["source_epoch"], saved["event_id"])).fetchone()
                        if json.loads(source_receipt[0])["cursor"] not in {r["cursor"] for r in cut["eligible_refs"]}:
                            raise JournalError("invalid_intent_result", "declaration was not eligible in this committed cut", 409)
                        application = {"schema": "home-intent-application-result-2", "version": 2,
                                       "id": "intent-result:" + digest([commit[k] for k in SCOPE] + [result["id"]]),
                                       **{k: commit[k] for k in SCOPE}, "intent_id": result["id"], "available_at": result["decided_at"],
                                       "status": result["status"], "reason": result["reason"], "decision_ref": result["decision_ref"],
                                       "truth_role": "runtime_application_result"}
                        self.validate_payload(application)
                        self.j.db.execute("UPDATE receipts SET application_result=? WHERE producer_id=? AND source_epoch=? AND event_id=?", (canonical(application), saved["producer_id"], saved["source_epoch"], saved["event_id"]))
            self.j.db.execute("INSERT INTO workers VALUES(?,?,?,?,?,?) ON CONFLICT(worker_id,site_id,room_id) DO UPDATE SET input_cursor=excluded.input_cursor,context_seq=excluded.context_seq,checkpoint=excluded.checkpoint",
                              (grant.expected_worker_id, commit["site_id"], commit["room_id"], cp["input_cursor"], seq + 1, canonical(cp)))
            result = {"schema": "home-context-worker-commit-receipt-3", "id": commit["id"], "worker_id": grant.expected_worker_id,
                      "journal_epoch": self.j._meta("epoch"), "input_cursor": cp["input_cursor"], "commit_seq": seq + 1,
                      "semantic_seq": cp["semantic_seq"], "control_seq": cp["control_seq"], "cursor": int(self.j._meta("cursor")), "receipts": output_receipts}
            self.j.db.execute("INSERT INTO receipts(producer_id,source_epoch,event_id,digest,receipt,event) VALUES(?,?,?,?,?,?)",
                              (grant.producer_id, commit["journal_epoch"], marker, digest(commit), canonical(result), canonical({"schema": "home-context-worker-commit-tombstone-3", **{k: commit[k] for k in SCOPE}})))
            self._storage_budget()
            return result
        return self.j._transaction(apply)

    def request_evidence(self, payload, grant):
        self.validate_payload(payload)
        if payload["schema"] != "home-evidence-request-2" or not 0 < payload["deadline"] - payload["available_at"] <= 3:
            raise JournalError("invalid_payload", "cached request-2 requires a deadline within three seconds")
        grant.require("evidence_request", payload["site_id"], payload["room_id"], payload_type=payload["schema"], revision="2.0")
        event = self._event_for(payload, grant, valid_until=payload["deadline"])
        self.j._event(event)
        def apply():
            retry = self.j._receipt(event)
            if retry:
                return retry
            self._expire_requests(payload["site_id"], payload["room_id"])
            if payload["deadline"] <= self.j.clock():
                raise JournalError("expired", "request deadline elapsed", 409)
            if self.j.db.execute("SELECT count(*) FROM requests WHERE terminal IS NULL").fetchone()[0] >= 1:
                raise JournalError("request_budget", "one pending synthetic request is permitted", 429)
            if self.j.db.execute("SELECT 1 FROM requests WHERE request_id=?", (payload["id"],)).fetchone():
                raise JournalError("identity_conflict", "request id already exists", 409)
            receipt = self.append(event, status="pending")
            self.j.db.execute("INSERT INTO requests VALUES(?,?,?,?,?,NULL,?,?)", (payload["id"], object_key(payload["object_ref"]), payload["site_id"], payload["room_id"], payload["deadline"], canonical(payload), canonical(receipt)))
            if self.j.evidence_router != "synthetic_stub":
                ref = self._cached_estimate(payload)
                self._terminal(payload, "supported" if ref else "unavailable", "cached_geometry" if ref else "no_compatible_cached_geometry", ref)
            return receipt
        return self.j._transaction(apply)

    def _cached_estimate(self, request):
        self._refresh_heads(request["site_id"], request["room_id"])
        authority = self.authority(request["site_id"], request["room_id"])
        if not authority["motion_allowed"]:
            return None
        rows = self._qualified_components(request["site_id"], request["room_id"], self.j.clock())
        # Select the current lineage before compatibility: a changed epoch,
        # person, association, closed state or expiry must fence older support.
        candidates = [(row, row["head"]) for row in rows if row["payload_type"] == "home-geometry-3"]
        candidates = [(row, head) for row, head in candidates if same_scope(head["object_ref"], request)
                      and head["object_ref"]["oid"] == request["object_ref"]["oid"]]
        if not candidates:
            return None
        row, head = max(candidates, key=lambda r: (r[1]["observed_at"], GEOMETRY_PRIORITY[r[0]["component"]["state"]], r[0]["publication"]["payload_digest"]))
        if self.j._revoked(row["site_id"], row["room_id"], row["source_id"], row["revision"]):
            return None
        p = row["publication"]; g = p["payload"]; obj = row["component"]
        correction = None
        for original in self.j.db.execute("SELECT body FROM publications WHERE site_id=? AND room_id=? AND payload_type='home-intent-event-2'", (request["site_id"], request["room_id"])).fetchall():
            declared = json.loads(original[0]); i = declared["payload"]
            if (i["kind"] == "association_correction" and same_scope(i, request) and i["object_ref"]["oid"] == request["object_ref"]["oid"]
                    and max(declared["committed_at"], i["available_at"]) <= self.j.clock()):
                correction = max(correction if correction is not None else -1, i["available_at"])
        if (g["registration_generation"] != authority["registration_generation"] or head["object_ref"] != request["object_ref"]
                or head["person_ref"] != request["person_ref"] or obj["state"] != "open"
                or correction is not None and g["observed_at"] <= correction
                or not 0 <= self.j.clock() - g["observed_at"] <= .5
                or self.j.clock() >= min(g["valid_until"], g["observed_at"] + 1)):
            return None
        facts = []
        for fact_row in (r for r in rows if r["payload_type"] == "home-fact-batch-2"):
            fact_head = fact_row["head"]
            if (not same_scope(fact_head["object_ref"], request) or fact_head["object_ref"]["oid"] != request["object_ref"]["oid"]
                    or fact_head["observed_at"] < g["observed_at"]
                    or self.j._revoked(fact_row["site_id"], fact_row["room_id"], fact_row["source_id"], fact_row["revision"])):
                continue
            if (fact_head["object_ref"] != request["object_ref"] or fact_head["person_ref"] != request["person_ref"]
                    or fact_head["association_generation"] != head["association_generation"]):
                return None
            facts.append(fact_row["component"])
        if any(f["kind"] in {"closed", "source_gap", "negative_use"} and f["value"] or f["kind"] == "use" and not f["value"] for f in facts):
            return None
        for kind in {f["kind"] for f in facts}:
            if len({f["value"] for f in facts if f["kind"] == kind}) > 1:
                return None
        if obj["uncertainty"]["translation_radius_m"] <= request["precision_m"] and obj["uncertainty"]["rotation_radius_rad"] <= request["orientation_rad"]:
            return dict(head["publication_ref"])
        return None

    def _terminal(self, request, status, reason, ref=None):
        reply = {"schema": "home-evidence-reply-2", "version": 2, "id": "reply:" + request["id"],
                 **{k: request[k] for k in SCOPE}, "request_id": request["id"], "available_at": self.j.clock(),
                 "status": status, "reason": reason, "estimate_ref": ref, "actual_frames": 0, "actual_model_calls": 0}
        event = self._event_for(reply, producer_id="home-context-journal", valid_until=max(self.j.clock() + 1, request["deadline"]))
        self.j._event(event)
        receipt = self.append(event, status=status)
        self.j.db.execute("UPDATE requests SET terminal=? WHERE request_id=? AND terminal IS NULL", (canonical({"payload": reply, "receipt": receipt}), request["id"]))
        return receipt

    def _expire_requests(self, site, room):
        for row in self.j.db.execute("SELECT body FROM requests WHERE site_id=? AND room_id=? AND terminal IS NULL AND deadline<=?", (site, room, self.j.clock())).fetchall():
            self._terminal(json.loads(row[0]), "expired", "deadline_elapsed")

    def reply_evidence(self, payload, grant):
        self.validate_payload(payload)
        if payload["schema"] != "home-evidence-reply-2":
            raise JournalError("unsupported_schema", "reply-2 required")
        grant.require("evidence_reply", payload["site_id"], payload["room_id"], payload_type=payload["schema"], revision="2.0")
        def apply():
            row = self.j.db.execute("SELECT * FROM requests WHERE request_id=?", (payload["request_id"],)).fetchone()
            if not row or not same_scope(json.loads(row["body"]), payload):
                raise JournalError("unknown_request", "reply is outside the request scope", 404)
            if row["terminal"]:
                terminal = json.loads(row["terminal"])
                if terminal["payload"] != payload:
                    raise JournalError("terminal_conflict", "request terminal is immutable", 409)
                return terminal["receipt"]
            request = json.loads(row["body"])
            if self.j.clock() >= request["deadline"] and payload["status"] != "expired":
                raise JournalError("expired", "late reply can terminate only as expired", 409)
            if payload["status"] == "supported":
                ref = self._cached_estimate(request)
                if not ref or payload["estimate_ref"] != ref:
                    raise JournalError("unsupported_evidence", "supported reply requires compatible committed cached geometry", 409)
            elif payload["estimate_ref"] is not None:
                raise JournalError("unsupported_evidence", "non-supported reply cannot assert geometry", 409)
            event = self._event_for(payload, grant, valid_until=max(self.j.clock() + 1, request["deadline"]))
            self.j._event(event)
            receipt = self.append(event, status=payload["status"])
            self.j.db.execute("UPDATE requests SET terminal=? WHERE request_id=?", (canonical({"payload": payload, "receipt": receipt}), payload["request_id"]))
            return receipt
        return self.j._transaction(apply)

    def lookup_request(self, request_id, grant, site, room):
        grant.require("read", site, room, payload_type="home-evidence-request-2", revision="2.0")
        def read():
            self._expire_requests(site, room)
            row = self.j.db.execute("SELECT * FROM requests WHERE request_id=? AND site_id=? AND room_id=?", (request_id, site, room)).fetchone()
            if not row:
                raise JournalError("not_found", "request not found", 404)
            terminal = json.loads(row["terminal"]) if row["terminal"] else None
            if terminal:
                grant.require("read", site, room, payload_type="home-evidence-reply-2", revision="2.0")
            return {"request": json.loads(row["body"]), "receipt": terminal["receipt"] if terminal else json.loads(row["receipt"]), "terminal_reply": terminal["payload"] if terminal else None}
        return self.j._transaction(read)

    def lookup_intent(self, event_id, grant, site, room):
        grant.require("read", site, room, payload_type="home-intent-event-2", revision="2.0")
        with self.j.lock:
            rows = self.j.db.execute("SELECT event,receipt,application_result FROM receipts WHERE event_id=?", (event_id,)).fetchall()
            rows = [r for r in rows if json.loads(r["event"]).get("payload_type") == "home-intent-event-2" and json.loads(r["event"])["site_id"] == site and json.loads(r["event"])["room_id"] == room]
            if len(rows) != 1:
                raise JournalError("not_found", "intent not found in granted scope", 404)
            row = rows[0]
            return {"intent": json.loads(row["event"])["payload"], "receipt": json.loads(row["receipt"]),
                    "application_result": json.loads(row["application_result"]) if row["application_result"] else None}

    def revoke(self, event, grant):
        self.j._event(event)
        p = event["payload"]
        if p["schema"] != "home-context-revocation-1":
            raise JournalError("unsupported_schema", "typed scoped revocation required")
        grant.require("revoke", event["site_id"], event["room_id"], payload_type=event["payload_type"], revision=event["revision"], producer_id=event["producer_id"])
        def apply():
            retry = self.j._receipt(event)
            if retry:
                return retry
            receipt = self.append(event)
            self.j.db.execute("INSERT OR IGNORE INTO revocations VALUES(?,?,?,?)", (event["site_id"], event["room_id"], p["source_id"], p["revision"]))
            self.j.db.execute("DELETE FROM state WHERE site_id=? AND room_id=? AND (?='*' OR source_id=?) AND (?='*' OR revision=?)", (event["site_id"], event["room_id"], p["source_id"], p["source_id"], p["revision"], p["revision"]))
            self.j.db.execute("DELETE FROM state WHERE site_id=? AND room_id=? AND json_extract(body,'$.payload_type') IN ('home-engagement-state-3','home-lighting-policy-decision-2','home-lighting-control-decision-1')", (event["site_id"], event["room_id"]))
            auth = self.authority(event["site_id"], event["room_id"])
            self._authority_update(event["site_id"], event["room_id"], revocation_generation=auth["revocation_generation"] + 1, motion_allowed=False)
            self.j._set_meta("revocation:" + canonical([event["site_id"], event["room_id"]]), auth["revocation_generation"] + 1)
            return receipt
        return self.j._transaction(apply)


if __name__ == "__main__":
    try:
        values = json.load(sys.stdin)
        result = [validate_dynamic(v) for v in values] if isinstance(values, list) else validate_dynamic(values)
        print(json.dumps(result, separators=(",", ":")))
    except (ValueError, TypeError, KeyError, JournalError):
        print('{"ok":false,"errors":["invalid_json"]}')
        sys.exit(1)

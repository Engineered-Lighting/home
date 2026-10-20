"""Loopback-only stdlib HTTP boundary for the local shadow journal."""
from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from journal import Journal, JournalError, Limits, PREFIX, DYNAMIC_PROFILE, ADAPTIVE_PROFILE, FEEDBACK_PROFILE, REFERENCE_PROFILE, G2_PROFILE, FEEDBACK_PROFILES, LIVE_PROFILE, bounded_json, identifier


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


class LocalServer(HTTPServer):
    allow_reuse_address = False
    request_queue_size = 8

    def __init__(self, address, journal):
        if not ipaddress.ip_address(address[0]).is_loopback:
            raise ValueError("Home context journal may bind only to a literal loopback address")
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.journal = journal
        super().__init__(address, Handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "HomeContextShadow/1"

    def log_message(self, *_):
        # Requests can contain local declarations; default access logs stay off.
        pass

    def _send(self, status, value):
        body = bounded_json(value, self.server.journal.limits.max_response_bytes).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _query(self):
        parsed = urlsplit(self.path)
        if len(self.path) > 4096 or parsed.scheme or parsed.netloc or parsed.fragment:
            raise JournalError("invalid_query", "bounded relative URL required")
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=16)
        allowed = {"site_id", "room_id", "consumer_id", "after", "stream_epoch", "limit", "wait_s"}
        if self.server.journal.profile in {DYNAMIC_PROFILE, "home-map-rehearsal-synthetic-1", ADAPTIVE_PROFILE, *FEEDBACK_PROFILES}:
            allowed |= {"t", "cut_id"}
        if self.server.journal.profile in FEEDBACK_PROFILES:
            allowed |= {"cursor", "event_id"}
        if self.server.journal.profile == G2_PROFILE and unquote(parsed.path) in {PREFIX+"/worker-control-output-proofs",PREFIX+"/worker-application-output-proofs",PREFIX+"/worker-policy-output-proofs",PREFIX+"/worker-exposure-output-proofs",PREFIX+"/worker-feedback-application-output-proofs"}:
            allowed.add("expected_family")
        if any(len(v) != 1 for v in query.values()) or set(query) - allowed:
            raise JournalError("invalid_query", "query keys must be unique and allowlisted")
        return unquote(parsed.path), {k: v[0] for k, v in query.items()}

    def _grant(self):
        headers = self.headers.get_all("Authorization", [])
        if len(headers) != 1 or not headers[0].startswith("Bearer "):
            raise JournalError("unauthorized", "explicit bearer grant required", 401)
        return self.server.journal.authenticate(headers[0][7:])

    def _body(self, limit=None):
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdigit():
            raise JournalError("invalid_body", "one bounded Content-Length required")
        size = int(lengths[0])
        if not 0 < size <= (limit or self.server.journal.limits.max_body_bytes):
            raise JournalError("payload_too_large", "body exceeds configured bound", 413)
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise JournalError("invalid_body", "application/json required", 415)
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise JournalError("invalid_body", "body was truncated")
        try:
            self._request_utf8 = raw.decode("utf-8")
            return json.loads(self._request_utf8, object_pairs_hook=_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
        except (ValueError, UnicodeError, RecursionError):
            raise JournalError("invalid_body", "bounded finite JSON required") from None

    def _scope(self, query):
        return identifier(query.get("site_id"), "site_id"), identifier(query.get("room_id"), "room_id")

    @staticmethod
    def _integer(query, key, default):
        value = query.get(key, str(default))
        if not value.isdigit() or len(value) > 16:
            raise JournalError("invalid_query", "integer cursor/limit required")
        return int(value)

    def _get(self):
        journal = self.server.journal
        path, query = self._query()
        if path == "/health":
            if query:
                raise JournalError("invalid_query", "health takes no query")
            return {"schema": "home-context-health-1", "status": "local_shadow", "actuation_enabled": False, **(journal.dynamic.service_clock_sample() if getattr(journal,"live_service_clock",None) is not None else {})}
        grant = self._grant()
        site, room = self._scope(query)
        cut_route = "/feedback-input-cut" if journal.profile in FEEDBACK_PROFILES else "/adaptive-input-cut" if journal.profile == ADAPTIVE_PROFILE else "/dynamic-input-cut"
        if path == PREFIX + cut_route and journal.dynamic:
            try:
                t = float(query.get("t", ""))
            except ValueError:
                raise JournalError("invalid_query", "finite cut time required") from None
            return journal.dynamic.input_cut(grant, site, room, consumer_id=query.get("consumer_id"),
                                             after=self._integer(query, "after", 0), t=t,
                                             limit=self._integer(query, "limit", 128), cut_id=query.get("cut_id"))
        if path == PREFIX + "/snapshot":
            return journal.snapshot(grant, site, room, query.get("consumer_id"))
        if path == PREFIX + "/live-scene-catalog" and journal.profile == LIVE_PROFILE:
            if set(query) - {"site_id", "room_id"}:
                raise JournalError("invalid_query", "scene catalog takes only its granted room scope")
            return journal.dynamic.scene_catalog(grant, site, room)
        if path == PREFIX + "/live-input-cut" and journal.profile == LIVE_PROFILE:
            if set(query) - {"site_id", "room_id", "consumer_id"}:
                raise JournalError("invalid_query", "live input cut uses the current atomic cursor only")
            return journal.dynamic.worker_input_cut(grant, site, room, query.get("consumer_id"))
        if path in (PREFIX + "/events", PREFIX + "/publications"):
            try:
                wait = float(query.get("wait_s", "0"))
            except ValueError:
                raise JournalError("invalid_query", "bounded wait_s required") from None
            if not 0 <= wait <= 10:
                raise JournalError("invalid_query", "wait_s must be 0..10")
            # This single-threaded boundary returns the current durable page.
            # wait_s is a maximum, not a requirement to block command admission.
            return journal.page(grant, site, room, after=self._integer(query, "after", 0), stream_epoch=query.get("stream_epoch"),
                                limit=self._integer(query, "limit", 128), consumer_id=query.get("consumer_id"))
        if path.startswith(PREFIX + "/evidence-requests/"):
            return journal.lookup_request(identifier(path[len(PREFIX + "/evidence-requests/"):], "request_id"), grant, site, room)
        if path.startswith(PREFIX + "/intent-events/"):
            return journal.lookup_intent(identifier(path[len(PREFIX + "/intent-events/"):], "id"), grant, site, room)
        if path.startswith(PREFIX + "/worker-commits/"):
            return journal.checkpoint(identifier(path[len(PREFIX + "/worker-commits/"):], "worker_id"), grant, site, room)
        if path.startswith(PREFIX + "/live-worker-commits/") and journal.profile == LIVE_PROFILE:
            return journal.dynamic.worker_commit_lookup(identifier(path[len(PREFIX + "/live-worker-commits/"):], "commit_id"), grant, site, room)
        if path.startswith(PREFIX + "/map-rehearsal-commits/") and journal.profile == "home-map-rehearsal-synthetic-1":
            return journal.dynamic.lookup_commit(identifier(path[len(PREFIX + "/map-rehearsal-commits/"):], "commit_id"), grant, site, room)
        if path.startswith(PREFIX + "/adaptive-commits/") and journal.profile == ADAPTIVE_PROFILE:
            return journal.dynamic.lookup_commit(identifier(path[len(PREFIX + "/adaptive-commits/"):], "commit_id"), grant, site, room)
        if journal.profile in FEEDBACK_PROFILES:
            if path == PREFIX + "/feedback-read-cut":
                if set(query)-{"site_id","room_id","consumer_id"}:
                    raise JournalError("invalid_query","read cut captures only the actual clock and fixed consumer")
                return journal.dynamic.read_cut(grant,site,room,consumer_id=query.get("consumer_id"))
            if path.startswith(PREFIX + "/feedback-read-cuts/"):
                if set(query)-{"site_id","room_id","consumer_id"}:
                    raise JournalError("invalid_query","retained read cut takes only authenticated scope and consumer")
                return journal.dynamic.read_cut(grant,site,room,consumer_id=query.get("consumer_id"),
                                                cut_id=identifier(path[len(PREFIX + "/feedback-read-cuts/"):],"cut_id"))
            if path.startswith(PREFIX + "/feedback-commits/"):
                return journal.dynamic.lookup_commit(identifier(path[len(PREFIX + "/feedback-commits/"):], "commit_id"), grant, site, room)
            if path == PREFIX + "/feedback-originals":
                return journal.dynamic.lookup_original(grant, site, room, cursor=self._integer(query,"cursor",0), consumer_id=query.get("consumer_id"))
            if path == PREFIX + "/feedback-worker-originals" and journal.profile in {REFERENCE_PROFILE, G2_PROFILE}:
                if set(query) - {"site_id", "room_id", "cursor", "consumer_id"}:
                    raise JournalError("invalid_query", "worker original accepts only exact scope, cursor and fixed consumer")
                return journal.dynamic.lookup_worker_original(grant, site, room, cursor=self._integer(query,"cursor",0), consumer_id=query.get("consumer_id"))
            if path == PREFIX + "/feedback-worker-output-proofs":
                if journal.profile == G2_PROFILE:
                    raise JournalError("not_found", "G2 requires the exact family proof route", 404)
                return journal.dynamic.lookup_output_proof(grant, site, room, cursor=self._integer(query,"cursor",0), consumer_id=query.get("consumer_id"))
            if journal.profile == G2_PROFILE:
                proof_routes = {"/worker-control-output-proofs": {"home-lighting-control-decision-3", "home-lighting-control-carrier-1", "home-lighting-installed-boundary-1"},
                                "/worker-application-output-proofs": {"home-intent-application-result-3"},
                                "/worker-policy-output-proofs": {"home-lighting-policy-decision-3"},
                                "/worker-exposure-output-proofs": {"home-lighting-settled-exposure-1"},
                                "/worker-feedback-application-output-proofs": {"home-experience-feedback-application-1"}}
                families = proof_routes.get(path[len(PREFIX):])
                if families is not None:
                    if set(query) - {"site_id", "room_id", "cursor", "consumer_id", "expected_family"}:
                        raise JournalError("invalid_query", "proof routes require exact scope, cursor and consumer")
                    expected = query.get("expected_family")
                    if expected is not None and expected not in families:
                        raise JournalError("invalid_query", "expected family differs from its dedicated proof route")
                    return journal.dynamic.lookup_output_proof(grant, site, room, cursor=self._integer(query, "cursor", 0), consumer_id=query.get("consumer_id"), expected_families={expected} if expected else families)
            if path == PREFIX + "/preference-write-origins":
                return journal.dynamic.lookup_origin(grant,site,room,event_id=query.get("event_id"))
            if path == PREFIX + "/preference-write-origin-proofs":
                return journal.dynamic.lookup_write_origin_proof(grant,site,room,event_id=query.get("event_id"))
        raise JournalError("not_found", "route is not allowlisted", 404)

    def _post(self):
        path, query = self._query()
        if query:
            raise JournalError("invalid_query", "mutation query parameters are not allowed")
        grant = self._grant()
        if path == PREFIX + "/worker-commits" and "worker_commit" in grant.operations:
            body = self._body(self.server.journal.worker_commit_body_limit())
        else:
            body = self._body()
        routes = {"events": self.server.journal.append_event, "publications": self.server.journal.append_event, "evidence-requests": self.server.journal.request_evidence,
                  "evidence-replies": self.server.journal.reply_evidence, "intent-events": self.server.journal.intent,
                  "consumer-feedback": self.server.journal.feedback, "worker-commits": self.server.journal.worker_commit,
                  "revocations": self.server.journal.revoke}
        if self.server.journal.profile == ADAPTIVE_PROFILE:
            routes["preference-snapshots"] = lambda body, scoped_grant: self.server.journal.dynamic.declaration(body, scoped_grant, family="home-preference-snapshot-1")
            routes["goal-declarations"] = lambda body, scoped_grant: self.server.journal.dynamic.declaration(body, scoped_grant, family="home-goal-declaration-1")
        if self.server.journal.profile in FEEDBACK_PROFILES:
            profile=self.server.journal.dynamic
            # D/legacy accept their existing JSON encoding. E retains its exact
            # canonical wire text so a historical proof can reproduce the bytes.
            routes["worker-commits"] = lambda body,g:profile.worker_commit(body,g,request_canonical_json=self._request_utf8)
            routes.update({"preference-snapshots":lambda body,g:profile.declaration(body,g,family="home-preference-snapshot-2"),
                           "goal-declarations":lambda body,g:profile.declaration(body,g,family="home-goal-declaration-1"),
                           "experience-contexts":lambda body,g:profile.declaration(body,g,family="home-experience-context-declaration-1"),
                           "experience-feedback":profile.feedback,
                           "preference-writes":lambda body,g:profile.preference_write(body,g,request_canonical_json=self._request_utf8),
                           "experience-producer-revocations":profile.producer_revoke,
                           "feedback-authority-revocations":profile.authority_revoke})
            if self.server.journal.profile == G2_PROFILE:
                routes.update({"geometry-constraint-sources": profile.geometry_constraint_source,
                               "feedback-assessments": profile.feedback_assessment,
                               "write-authorities": profile.write_authority,
                               "write-frontiers": profile.write_frontier,
                               "support-qualifications": profile.support_qualification})
        action = routes.get(path[len(PREFIX) + 1:]) if path.startswith(PREFIX + "/") else None
        if action is None:
            raise JournalError("not_found", "route is not allowlisted", 404)
        return action(body, grant)

    def _handle(self, action):
        try:
            self._send(200, action())
        except JournalError as error:
            self._send(error.status, error.response())
        except (ValueError, TypeError, KeyError, RecursionError):
            self._send(400, JournalError("invalid_payload", "request does not match the typed local boundary").response())
        except (BrokenPipeError, ConnectionError, TimeoutError):
            self.close_connection = True

    def do_GET(self):
        self._handle(self._get)

    def do_POST(self):
        self._handle(self._post)


def main():
    parser = argparse.ArgumentParser(description="Local shadow journal; no models, devices, or actuation")
    parser.add_argument("--database", required=True)
    parser.add_argument("--grants", required=True, help="explicit grant JSON containing token_sha256 values")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--max-db-mib", type=int, default=64)
    parser.add_argument("--profile", choices=("legacy", DYNAMIC_PROFILE, ADAPTIVE_PROFILE, LIVE_PROFILE, *sorted(FEEDBACK_PROFILES)), default="legacy")
    args = parser.parse_args()
    # Refuse exposure before opening either configuration or database.
    if not ipaddress.ip_address(args.host).is_loopback or not 1 <= args.port <= 65535 or not 1 <= args.max_db_mib <= 64:
        parser.error("literal loopback address and valid local port required")
    config_limit = 1048576 if args.profile == LIVE_PROFILE else 65536
    with open(args.grants, encoding="utf-8") as source:
        raw = source.read(config_limit + 1)
    if len(raw.encode("utf-8")) > config_limit:
        parser.error("grant configuration exceeds limit")
    config = json.loads(raw, object_pairs_hook=_pairs)
    profile_key = "adaptive_feedback_geometry_hold_2_config" if args.profile == G2_PROFILE else "adaptive_feedback_reference_config" if args.profile == REFERENCE_PROFILE else "adaptive_feedback_config" if args.profile == FEEDBACK_PROFILE else "adaptive_config" if args.profile == ADAPTIVE_PROFILE else None
    expected_keys = {"schema", "version", "grants"} | ({profile_key} if profile_key else set())
    if args.profile == LIVE_PROFILE:
        expected_keys |= {"live_config", "live_receipt_catalog"}
    if not isinstance(config, dict) or set(config) != expected_keys or config["schema"] != "home-context-grants-1" or config["version"] != 1 or not isinstance(config["grants"], list):
        parser.error("explicit home-context-grants-1 configuration required")
    options = {profile_key: config[profile_key], "clock": None} if profile_key else {}
    if options:
        started = time.monotonic()
        options["clock"] = lambda: time.monotonic() - started
    if args.profile == LIVE_PROFILE:
        from live_ingress import receipt_catalog_resolver
        utc_start, monotonic_start = time.time(), time.monotonic()
        pc_components = {b["proof_component_id"] for b in config["live_config"]["manifest"]["worker"]["pc_support_policy"]["bindings"]} if config["live_config"]["version"] in {12, 13} else set()
        options = {"live_config": config["live_config"],
                   "live_receipt_resolver": receipt_catalog_resolver(config["live_receipt_catalog"], pc_proof_components=pc_components),
                   "clock": lambda: utc_start + time.monotonic() - monotonic_start}
    journal = Journal(args.database, grants=config["grants"], limits=Limits(max_db_bytes=args.max_db_mib * 1024 * 1024), profile=args.profile, **options)
    server = LocalServer((args.host, args.port), journal)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        journal.close()


if __name__ == "__main__":
    main()

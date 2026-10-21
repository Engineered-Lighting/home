"""Explicitly authorized TypeSafe transport; construction never makes a request.

The provisioned authorizer verifies household egress policy and its kill switch.
It receives the exact packet and digest before sending and before publication.
No default credentials, policy, model selector, retry, or household permission.
HTTP contract: https://docs.typesafe.ai/api (checked 2026-09-26).
"""
from copy import deepcopy
import hashlib
import http.client
import json
import re
import time

import controller_load
from reasoning import build_jev_request, canonical, finite, plain_copy, validate_jev_response

HISTORY_LIMIT_SECONDS = 10
# 4: historical caption enum projection and RGB floor-contact spatial estimates.
# 5: shared context block (family coverage, ambient priors, digest), the
# computer_input_recency / den_audio_playback / den_light / owner_home facts;
# ambient HA facts leave device state.
# 6: opaque evidence roots/frame ids, per-fact HA grants (ungranted facts and
# empty HA records dropped), the Qwen scene record, corroboration block.
# 7: computer_input_recency also carries the computer_active_v3 buckets
# (under_15s, 15_to_60s); the semantic block gains static-scene tagging, phone
# relations and a PC-input-gated corroboration (den-semantics-6).
# 8: head-direction-1 head blocks and corroboration-3 (den-semantics-7).
PROJECTION_VERSION = "structured-semantic-8"


def history_windows(windows):
    """Live historical comparison bound. Invalid recipes are rejected, never clamped."""
    for arm, value in sorted(dict(windows).items()):
        if not finite(value) or not 0 <= value <= HISTORY_LIMIT_SECONDS:
            raise ValueError(f"Reasoning window {arm}={value!r}s is outside the 0-{HISTORY_LIMIT_SECONDS}s JEV history bound")
    return dict(windows)


class ProviderFailure(Exception):
    def __init__(self, reason, *, indeterminate=False, http_status=None):
        super().__init__(reason)
        self.reason = reason
        self.indeterminate = indeterminate
        # The provider's HTTP status code only (never its body), for diagnosis.
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None


# Statuses that, answered with a response body, show the request was refused
# before execution: malformed (400), unauthenticated (401), forbidden (403),
# unknown route (404), too large (413) or failed validation (422). /api
# documents 401 ("Missing or invalid API key") and 422 ("The request body
# failed validation"). It documents 429 (rate limited) and 529 (overloaded) as
# retryable, but not that they prove non-execution, and documents no request
# lookup or idempotency key; so 429, 5xx, 529 and other statuses stay
# indeterminate (https://docs.typesafe.ai/api, checked 2026-09-30).
REJECTED_BEFORE_EXECUTION = frozenset({400, 401, 403, 404, 413, 422})
REJECTION_BODY_LIMIT = 65536


def _rejection_body(conn, response, expires):
    """True when a bounded, non-empty response body arrived before the deadline.
    The body itself is discarded unread: it is never stored or logged."""
    size = 0
    while True:
        remaining = expires-time.monotonic()
        if remaining <= 0:
            return False
        if conn.sock is not None:
            conn.sock.settimeout(remaining)
        chunk = response.read1(min(8192, REJECTION_BODY_LIMIT+1-size))
        if not chunk:
            return size > 0
        size += len(chunk)
        if size > REJECTION_BODY_LIMIT:
            return False


def post_systemone(body, token, timeout):
    """Fixed TLS origin, no redirects/proxies/retries, bounded response body.

    Exceptions expose neither provider body nor authorization header. A transport
    failure after dispatch does not prove that paid provider execution ended.
    """
    conn = http.client.HTTPSConnection("api.typesafe.ai", timeout=timeout)
    expires = time.monotonic() + timeout
    try:
        conn.request("POST", "/v1/systemone", body=body, headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json", "Accept": "application/json",
            "Accept-Encoding": "identity",
        })
        response = conn.getresponse()
        if response.status != 200:
            status = response.status if type(response.status) is int else None
            # A documented pre-execution refusal, answered in full: a known
            # failure. Anything else (429, 5xx, a redirect, a refusal whose
            # body did not arrive) leaves execution unproven.
            try:
                refused = status in REJECTED_BEFORE_EXECUTION and _rejection_body(conn, response, expires)
            except Exception:
                refused = False
            if refused:
                raise ProviderFailure("provider_rejected_%d" % status, http_status=status)
            raise ProviderFailure("provider_http_error", indeterminate=True, http_status=status)
        if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ProviderFailure("provider_content_type")
        chunks, size = [], 0
        while True:
            remaining = expires-time.monotonic()
            if remaining <= 0:
                raise ProviderFailure("provider_transport_indeterminate", indeterminate=True)
            if conn.sock is not None:
                conn.sock.settimeout(remaining)
            chunk = response.read1(min(8192, 65537-size))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            size += len(chunk)
            if size > 65536:
                raise ProviderFailure("provider_response_too_large")
    except ProviderFailure:
        raise
    except Exception:
        raise ProviderFailure("provider_transport_indeterminate", indeterminate=True) from None
    finally:
        conn.close()


class JevProvider:
    """Single request adapter for the independent per-arm reasoning scheduler.

    Authorizer is a trusted server callback, never a client-provided grant. It
    must inspect nested evidence against the permitted cloud data scope, verify
    source permissions, and return a current signed-policy revision/generation.
    This adapter does not claim to implement the household's existing verifier.
    """
    def __init__(self, *, model_selector, response_version, authorize, credential,
                 transport=post_systemone, clock=time.time, allow_recent_history=False, semantic=False, ha_bindings=None, zone_bindings=None,
                 wire=None, shadow=None, storyline_seconds=60, monitor_side=None, questions=None, facts_key=None,
                 desk_seat=None):
        if (not isinstance(model_selector, str) or not model_selector
                or "latest" in model_selector or len(model_selector) > 128
                or not isinstance(response_version, str)
                or not re.fullmatch(r"jev-\d+\.\d+\.\d+", response_version)):
            raise ValueError("reviewed selector and exact response version required")
        if not all(callable(x) for x in (authorize, credential, transport, clock)):
            raise ValueError("provisioned authorization, credential and transport required")
        self.model = model_selector
        self.version = response_version
        self.authorize, self.credential = authorize, credential
        self.transport, self.clock = transport, clock
        if type(allow_recent_history) is not bool:raise ValueError('Explicit historical comparison setting required')
        self.allow_recent_history=allow_recent_history
        self.semantic=semantic
        self.ha_bindings=deepcopy(ha_bindings or {})
        self.zone_bindings=deepcopy(zone_bindings or {})
        # Packet wire: None is v5 (structured-semantic-7, today's default);
        # a WIRE_VERSIONS id is a facts-only v6 wire (den-facts-6, den-facts-7
        # with now.position, or den-facts-8 with the room facts), which
        # additionally needs the household's grant of that same id in
        # structured_sources at the policy.
        from jev_semantics import desk_seat_config
        from jev_wire import STORYLINE_SECONDS, WIRE_VERSION_7, WIRE_VERSIONS
        if wire not in (None, *WIRE_VERSIONS) or storyline_seconds not in STORYLINE_SECONDS:
            raise ValueError("registered packet wire required")
        self.wire, self.storyline_seconds = wire, storyline_seconds
        # The site's desk_seat region (desk-seat-1): den-facts-7 needs it,
        # den-facts-8 uses it only as its no-3D-fact position fallback, any
        # other wire ignores it; the authorizer recomputes with its own copy.
        try:
            self.desk_seat = None if desk_seat is None else desk_seat_config(desk_seat)
        except ValueError:
            raise ValueError("registered desk_seat region required") from None
        if wire == WIRE_VERSION_7 and self.desk_seat is None:
            raise ValueError("registered desk_seat region required")
        # The site's optional monitor side (head-direction-1 head_toward); the
        # authorizer recomputes with its own copy of the same config.
        from jev_semantics import MONITOR_SIDES
        if monitor_side is not None and (not isinstance(monitor_side, str) or monitor_side not in MONITOR_SIDES):
            raise ValueError("registered monitor side required")
        self.monitor_side = monitor_side
        # The v6 wire's registered question set (None: den-activity-8-facts;
        # den-activity-9-fanout is v7, fused in code by jev_wire.fuse) and
        # facts-key version (None: facts-key-1). Both only on the v6 wire.
        from jev_wire import FACTS_KEY_DEFAULT, FACTS_KEY_VERSIONS, QUESTION_SETS, QUESTION_VERSION, question_wire_ok
        questions = QUESTION_VERSION if questions is None else questions
        facts_key = FACTS_KEY_DEFAULT if facts_key is None else facts_key
        if (questions not in QUESTION_SETS or facts_key not in FACTS_KEY_VERSIONS
                or (wire is None and (questions != QUESTION_VERSION or facts_key != FACTS_KEY_DEFAULT))
                or (wire is not None and not question_wire_ok(questions, wire))):
            raise ValueError("registered question set and facts key required")
        self.question_version, self.facts_key_version = questions, facts_key
        # Optional v6 shadow beside the v5 wire: it builds and verifies v6
        # after each v5 request and never sends (jev_shadow.ShadowV6).
        self.shadow = shadow if wire is None else None
        self.rings = {}
        # The scheduler's own frame rings (facts_key): fed every tick, never
        # shared with the dispatch rings above, so what is sent is unchanged.
        self.key_rings = {}

    def facts_key(self, records, *, site_id, session_id, arm, generation, cutoff, window_seconds=10,
                  shared_context=None, context_snapshot=None):
        """The facts key (plan §6.2) of the state a request with these inputs
        would carry, plus the few words the scheduler gates on; None on the v5
        wire. Local only: nothing is admitted, recorded or sent. Built at budget
        level 0 from the same projection, causal selection and sidecar as the
        v6 build (jev_wire.prepare)."""
        if self.wire is None:
            return None
        import jev_wire as wire
        from jev_evidence import granted_bindings, project, project_context
        from reasoning import causal_evidence, shared_evidence
        block = project_context(context_snapshot)
        dropped = [f for f, v in (block or {}).get("ha_facts", {}).items() if v != "supplied"]
        bound = dict(ha_bindings=granted_bindings(self.ha_bindings, dropped), zone_bindings=self.zone_bindings)
        evidence = causal_evidence(project(records, **bound), site_id=site_id, session_id=session_id, arm=arm,
                                   generation=generation, cutoff=cutoff, window_seconds=window_seconds, retain_history=True)
        admitted = shared_evidence(project(shared_context or [], **bound), site_id=site_id, session_id=session_id, arm=arm,
                                   generation=generation, cutoff=cutoff)
        ring = self.key_rings.get((session_id, arm, generation))
        if ring is None:
            # One live ring per arm: an older session or generation is dropped.
            for stale in [k for k in self.key_rings if k[1] == arm]:
                del self.key_rings[stale]
            ring = self.key_rings[(session_id, arm, generation)] = wire.FrameRing()
        ring.update(evidence)
        local = wire.sidecar(evidence, admitted, block, ring.window(cutoff, self.storyline_seconds), cutoff,
                             self.storyline_seconds, room_facts=self.wire in wire.ROOM_WIRES)
        state = wire.build_state(local, cutoff=cutoff, storyline_seconds=self.storyline_seconds,
                                 monitor_side=getattr(self, "monitor_side", None), wire_version=self.wire,
                                 desk_seat=getattr(self, "desk_seat", None))
        return {"key": wire.facts_key(state, getattr(self, "facts_key_version", wire.FACTS_KEY_DEFAULT)),
                "people": state["now"]["people_in_view"],
                "camera": state["now"]["camera_evidence"], "cue_state": wire.debounced_state(state)}

    def _grant(self, context, packet, phase, local=None):
        try:
            # Independent copies: the authorizer cannot alter what is sent.
            if local is None:
                grant = self.authorize(plain_copy(context), plain_copy(packet), phase)
            else:
                grant = self.authorize(plain_copy(context), plain_copy(packet), phase, local=plain_copy(local))
            if (not isinstance(grant, dict) or grant.get("allowed") is not True
                    or grant.get("scope") != "household_structured"
                    or not isinstance(grant.get("revision"), str) or not grant["revision"]
                    or type(grant.get("generation")) is not int or grant["generation"] < 0
                    or type(grant.get("expires_at")) not in (float, int)
                    or not self.clock() < grant["expires_at"]):
                raise ValueError()
            return (grant["revision"], grant["generation"])
        except Exception as error:
            reasons={"Session reasoning grant revoked":"session_revoked",
                "Lab household policy unavailable":"policy_unavailable",
                "Fresh Lab kill-switch state required":"switch_unavailable",
                "Structured source permission mismatch":"source_mismatch",
                "Durable reasoning reservation required":"reservation_missing",
                "Unresolved provider execution":"execution_unknown",
                "Provider rate budget exhausted":"rate_budget",
                "Private operator file required":"private_file_unavailable",
                "Historical caption egress not permitted":"caption_not_permitted",
                "Ambient context egress not permitted":"ambient_not_permitted",
                "Owner presence egress not permitted":"presence_not_permitted",
                "Device context egress not permitted":"device_context_not_permitted",
                "Qwen scene egress not permitted":"qwen_scene_not_permitted",
                "V-JEPA scene egress not permitted":"vjepa_scene_not_permitted",
                "Packet wire not permitted":"wire_not_permitted"}
            code=reasons.get(str(error),type(error).__name__)
            # Controlled category only: never persist arbitrary exception text.
            raise ProviderFailure("household_egress_not_authorized:"+code) from None

    def evaluate(self, records, *, request_id, site_id, session_id, arm, generation,
                 cutoff, deadline, window_seconds=10, shared_context=None, context_snapshot=None, reading_context=None):
        if self.wire is not None:
            return self._evaluate_facts(records, request_id=request_id, site_id=site_id, session_id=session_id, arm=arm,
                                        generation=generation, cutoff=cutoff, deadline=deadline, window_seconds=window_seconds,
                                        shared_context=shared_context, context_snapshot=context_snapshot, reading_context=reading_context)
        started = time.perf_counter()
        try:
            if (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id)
                    or type(deadline) not in (int, float) or not 0 < deadline-self.clock() <= 15):
                raise ValueError("bounded request identity and deadline required")
            from jev_evidence import granted_bindings, project, project_context
            if self.allow_recent_history:
                history_windows({arm: window_seconds})
            # Allowlisted and digest-checked; identical for both arms of a tick.
            context = project_context(context_snapshot)
            # A fact the tick's context marks as not carried (its household
            # grant is absent, or HA is withheld) is dropped, never sent: the
            # packet still dispatches with the rest of its evidence.
            dropped = [f for f, v in (context or {}).get("ha_facts", {}).items() if v != "supplied"]
            bound = dict(ha_bindings=granted_bindings(self.ha_bindings, dropped), zone_bindings=self.zone_bindings)
            projected, projected_shared = project(records,**bound), project(shared_context or [],**bound)
            block = context
            # budget: the pre-send guard trims the oldest window records, or
            # refuses a packet whose estimate stays over the hard cap.
            packet, provenance = build_jev_request(projected, model=self.model,
                site_id=site_id, session_id=session_id, arm=arm, generation=generation,
                cutoff=cutoff, window_seconds=window_seconds, retain_history=True,allow_recent_history=self.allow_recent_history,semantic=self.semantic,
                shared_context=projected_shared, context=context, budget=True, monitor_side=self.monitor_side)
        except ValueError as error:
            # Over budget even after trimming: refused locally, nothing admitted or sent.
            raise ProviderFailure("request_over_budget" if str(error) == "request over budget" else "request_invalid") from None
        except Exception:
            # Nothing was admitted or sent: a known local failure, not unknown delivery.
            raise ProviderFailure("request_invalid") from None
        context = {"request_id": request_id, "site_id": site_id, "session_id": session_id,
                   **provenance, "response_version": self.version, "deadline": deadline}
        if provenance["status"] == "missing_evidence":
            return {"status": "missing_evidence", "provenance": context}
        if self.shadow is not None:
            # Inputs only (the same projected records v5 used); the shadow
            # builds and verifies v6 after this request, never sending it.
            try:
                self.shadow.stash(request_id, records=projected, shared=projected_shared, context=block,
                                  site_id=site_id, session_id=session_id, arm=arm, generation=generation, cutoff=cutoff,
                                  window_seconds=window_seconds, model=self.model)
            except Exception:
                pass
        grant = self._grant(context, packet, "send")
        from jev_wire import estimate_tokens
        size = len(canonical(packet).encode())
        return self._dispatch(packet, context, grant, started, None, {
            'projection_version':PROJECTION_VERSION,'question_version':provenance['question_version'],
            'packet_bytes':size,'estimated_tokens':estimate_tokens(size),'budget_trimmed':provenance['budget_trimmed'],
            'shared_context_digest':context_snapshot.get('shared_context_digest') if isinstance(context_snapshot,dict) else None})

    def _evaluate_facts(self, records, *, request_id, site_id, session_id, arm, generation,
                        cutoff, deadline, window_seconds=10, shared_context=None, context_snapshot=None, reading_context=None):
        """The v6 build: facts-only state, local sidecar bound by its digest."""
        import jev_wire as wire
        started = time.perf_counter()
        try:
            if (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id)
                    or type(deadline) not in (int, float) or not 0 < deadline-self.clock() <= 15):
                raise ValueError("bounded request identity and deadline required")
            from jev_evidence import granted_bindings, project, project_context
            if self.allow_recent_history:
                history_windows({arm: window_seconds})
            block = project_context(context_snapshot)
            dropped = [f for f, v in (block or {}).get("ha_facts", {}).items() if v != "supplied"]
            bound = dict(ha_bindings=granted_bindings(self.ha_bindings, dropped), zone_bindings=self.zone_bindings)
            ring_key = (session_id, arm, generation)
            from activity_reading import reader_key
            epoch = reader_key((reading_context or {}).get('binding'))
            epochs = getattr(self, '_reading_ring_epochs', {})
            if ring_key in epochs and epochs[ring_key] != epoch:
                self.rings.pop(ring_key, None)
            epochs[ring_key] = epoch
            self._reading_ring_epochs = epochs
            ring = self.rings.setdefault(ring_key, wire.FrameRing())
            local, packet, binding = wire.prepare(project(records, **bound), project(shared_context or [], **bound), block,
                site_id=site_id, session_id=session_id, arm=arm, generation=generation, cutoff=cutoff,
                window_seconds=window_seconds, model=self.model, storyline_seconds=self.storyline_seconds, ring=ring,
                monitor_side=self.monitor_side, question_version=self.question_version, wire_version=self.wire,
                desk_seat=self.desk_seat)
        except ValueError as error:
            # Over budget at every level: refused locally, nothing admitted or sent.
            raise ProviderFailure("request_over_budget" if str(error) == "request over budget" else "request_invalid") from None
        except Exception:
            raise ProviderFailure("request_invalid") from None
        camera = [r for r in local["records"] if r.get("source_kind") == "perception.output"]
        fresh = any(finite(r.get("valid_until")) and r["valid_until"] > cutoff for r in camera)
        size, estimate = binding.pop("packet_bytes"), binding.pop("estimated_tokens")
        context = {"request_id": request_id, "site_id": site_id, "session_id": session_id, "arm": arm,
                   "generation": generation, **binding, "window_seconds": window_seconds, "mode": "causal",
                   "fresh_evidence": fresh, "allow_recent_history": self.allow_recent_history,
                   "input_digest": hashlib.sha256(canonical(packet).encode()).hexdigest(),
                   "status": "ready" if fresh or (self.allow_recent_history and camera) else "missing_evidence",
                   "response_version": self.version, "deadline": deadline}
        if context["status"] == "missing_evidence":
            return {"status": "missing_evidence", "provenance": context}
        grant = self._grant(context, packet, "send", local)
        return self._dispatch(packet, context, grant, started, local, {
            'projection_version':wire.PROJECTION_VERSIONS[self.wire],'question_version':self.question_version,
            'wire_version':self.wire,'budget_level':binding['budget_level'],'packet_bytes':size,
            'estimated_tokens':estimate,'facts_key':wire.facts_key(packet['state'],self.facts_key_version),
            'facts_key_version':self.facts_key_version,
            'shared_context_digest':context_snapshot.get('shared_context_digest') if isinstance(context_snapshot,dict) else None})

    def _dispatch(self, packet, context, grant, started, local, diagnostics):
        try:
            token = self.credential()
            if not isinstance(token, str) or not token or len(token) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise ValueError()
        except Exception:
            raise ProviderFailure("provider_credential_unavailable") from None
        remaining = context["deadline"]-self.clock()
        if remaining <= 0:
            return {"status": "expired", "provenance": context}
        # Content-free load timings: local preparation (projection, packet and
        # egress admission) and the transport round trip.
        sending = time.perf_counter()
        controller_load.observe("reasoning_build_ms", (sending-started)*1000)
        try:
            raw = self.transport(canonical(packet).encode(), token, remaining)
        except ProviderFailure:
            raise
        except Exception:
            raise ProviderFailure("provider_transport_indeterminate", indeterminate=True) from None
        finally:
            controller_load.observe("reasoning_send_ms", (time.perf_counter()-sending)*1000)
        received = self.clock()
        if received > context["deadline"]:
            return {"status": "expired", "provenance": context}
        if self._grant(context, packet, "publish", local) != grant:
            raise ProviderFailure("authorization_changed")
        try:
            if not isinstance(raw, bytes) or len(raw) > 65536:
                raise ValueError()
            response = validate_jev_response(json.loads(raw), packet["questions"], expected_version=self.version)
        except Exception:
            raise ProviderFailure("provider_response_invalid") from None
        answer = response["answers"]["activity"]
        fusion = None
        from jev_wire import THRESHOLDS, fuse
        version = context.get("question_version")
        if local is not None and version in THRESHOLDS:
            # v7: veto gates in code (plan §4.3); a vetoed class abstains with
            # its mass moved to insufficient_evidence, never relabelled.
            fusion = fuse(response["answers"], packet["state"], version)
            answer = {**answer, "choice": fusion["activity"], "probabilities": fusion["probabilities"]}
        usage=response.get('usage') if isinstance(response.get('usage'),dict) else {}
        usage={k:usage[k] for k in ('input_tokens','output_tokens') if type(usage.get(k)) is int and usage[k]>=0}
        # Keep the provider's distribution confidence distinct from probability
        # of the chosen activity. Neither asserts calibrated household accuracy.
        return {"status": "abstain" if answer["choice"] == "insufficient_evidence" else "ready",
                "judgment": {"activity": answer["choice"], "confidence": answer["confidence"],
                             "probabilities": answer["probabilities"], "model_version": response["model"]},
                "diagnostics":{'request':packet,'answers':response['answers'],'usage':usage,
                    'usage_status':'reported' if len(usage)==2 else 'not_reported',**diagnostics,
                    **({'fusion':{'gate':fusion['gate'],'nouls':fusion['nouls']}} if fusion else {})},
                "received_at": received, "provenance": {**context,
                    "authorization_revision": grant[0], "authorization_generation": grant[1]},
                "actuation": False}

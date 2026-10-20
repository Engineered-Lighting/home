"""Shadow mode for packet v6 (den-facts-6) beside the live v5 wire.

After each live v5 request, the v5 provider hands the shadow the same
projected inputs it used. The shadow builds the v6 packet exactly as the v6
provider would (jev_wire.prepare), runs the v6 policy checks on it
(LabJevPolicy.shadow_verify: schema walk, questions, grants, sidecar
provenance, digest binding, ledger re-derivation, recompute) and records one
local 'reasoning.shadow' event with the verdict, sizes and the words-only
state. It has no transport and admits nothing: v6 never leaves the controller
in shadow mode, and v5 stays the wire until the owner switches.
"""
from collections import OrderedDict
import threading
import time

import jev_wire as wire
from reasoning import canonical

# Stashed inputs awaiting their run; far above the two arms' in-flight requests.
PENDING_ITEMS = 8


class ShadowV6:
    def __init__(self, store, verifier, *, storyline_seconds=60, monitor_side=None):
        if storyline_seconds not in wire.STORYLINE_SECONDS or not callable(getattr(verifier, 'shadow_verify', None)):
            raise ValueError('v6 shadow needs a verifier and a supported storyline window')
        self.store, self.verifier, self.storyline_seconds = store, verifier, storyline_seconds
        # The site's optional monitor side; the verifier recomputes with its own.
        self.monitor_side = monitor_side
        self.pending = OrderedDict()
        self.rings = {}
        self.lock = threading.Lock()

    def stash(self, request_id, **inputs):
        """Keep a v5 request's projected inputs (references, not copies: v5
        never mutates them) until its run; bounded, oldest dropped."""
        with self.lock:
            self.pending[request_id] = inputs
            while len(self.pending) > PENDING_ITEMS:
                self.pending.popitem(last=False)

    def run(self, request_id):
        """Build and verify v6 for a stashed request; record the verdict.
        Returns the event payload, or None when nothing was stashed."""
        with self.lock:
            inputs = self.pending.pop(request_id, None)
            if inputs is None:
                return None
            key = (inputs['session_id'], inputs['arm'], inputs['generation'])
            ring = self.rings.setdefault(key, wire.FrameRing())
            # One ring per recording arm; a new session or generation drops the old.
            for other in [k for k in self.rings if k[0] != key[0] or (k[1] == key[1] and k != key)]:
                self.rings.pop(other)
        started = time.perf_counter()
        packet, binding = None, {}
        try:
            local, packet, binding = wire.prepare(
                inputs['records'], inputs['shared'], inputs['context'], site_id=inputs['site_id'],
                session_id=inputs['session_id'], arm=inputs['arm'], generation=inputs['generation'],
                cutoff=inputs['cutoff'], window_seconds=inputs['window_seconds'], model=inputs['model'],
                storyline_seconds=self.storyline_seconds, ring=ring, monitor_side=self.monitor_side)
            built = time.perf_counter()
            context = {'request_id': request_id, 'site_id': inputs['site_id'], 'session_id': inputs['session_id'],
                       'arm': inputs['arm'], 'generation': inputs['generation'],
                       **{k: v for k, v in binding.items() if k not in ('packet_bytes', 'estimated_tokens')}}
            verdict = self.verifier.shadow_verify(context, packet, local)
        except ValueError as error:
            built = time.perf_counter()
            verdict = {'match': False, 'reason': 'request_over_budget' if str(error) == 'request over budget' else 'build_failed'}
        except Exception:
            built = time.perf_counter()
            verdict = {'match': False, 'reason': 'build_failed'}
        verified = time.perf_counter()
        payload = {'request_id': request_id, 'arm': inputs['arm'], 'generation': inputs['generation'],
                   'wire_version': wire.WIRE_VERSION, 'question_version': wire.QUESTION_VERSION,
                   'projection_version': wire.PROJECTION_VERSION, 'mode': 'shadow_not_sent',
                   'match': verdict.get('match') is True, 'reason': verdict.get('reason'),
                   'wire_granted': verdict.get('wire_granted', False),
                   'timeline_verified': verdict.get('timeline_verified'), 'rederived': verdict.get('rederived'),
                   'budget_level': binding.get('budget_level'), 'packet_bytes': binding.get('packet_bytes'),
                   'estimated_tokens': binding.get('estimated_tokens'), 'timeline_frames': len(binding.get('timeline_ids') or []),
                   'build_ms': round((built-started)*1000, 3), 'verify_ms': round((verified-built)*1000, 3),
                   'available_at': self.store.clock()}
        if packet is not None:
            # Closed-vocabulary words only (validated or refused above): the
            # state v6 would have sent, for the offline v5/v6 comparison.
            payload['state_bytes'] = len(canonical(packet['state']).encode())
            payload['facts_key'] = wire.facts_key(packet['state'])
            payload['state'] = packet['state']
        self.store.append_event(inputs['session_id'], 'reasoning.shadow', payload)
        return payload

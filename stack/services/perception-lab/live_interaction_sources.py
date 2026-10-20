"""Bounded reuse of existing accepted LA receipts, never a model scheduler.

This controller-local capability subscribes to successful FactLog completions;
it never opens the private log or calls LA. A fixed capture-owner resolver proves
the exact ring frame aliases, original clock and physical ancestry. All output
remains an uncalibrated model proposal. A later rule must independently qualify
material/person/use before it can publish a supported refinement.

`model_ref` pins the registered LA model/config manifest. `artifact_sha256` is a
separate artifact pin because those two existing digest dialects are not aliases.
Native device activity has no camera clock or implicit person attribution and is
explicitly omitted from this camera-only adapter.
"""
from copy import deepcopy
import base64
import hashlib
import json
import re
import threading
import time

import la_fact_log
import qwen_scene as scene
from journal import digest as wire_digest


SCHEMA = 'home-interaction-supplemental-1'
LA_SOURCE = la_fact_log.SOURCE_ID
_SHA = re.compile(r'[a-f0-9]{64}')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}')


def _pin(value):
    return (isinstance(value, dict) and set(value) == {'id', 'digest'}
            and isinstance(value['id'], str) and _ID.fullmatch(value['id'])
            and isinstance(value['digest'], str) and _SHA.fullmatch(value['digest']))


class ExistingReceiptSources:
    """One bounded source cache for an explicitly configured camera/model.

    `bind_capture` is composed once with LiveCaptureAdapter.measurement_input;
    it is a trusted in-process capability, not a mapping supplied by a request.
    Newer current captures may bind earlier Qwen frames only when this very
    producer already qualified them. Unknown frame aliases stay unavailable.
    """
    def __init__(self, *, site_id, camera_id, model_ref, artifact_sha256, granted,
                 identity, clock=time.time, max_receipts=24):
        if (any(not isinstance(value, str) or not _ID.fullmatch(value) for value in (site_id, camera_id))
                or not _pin(model_ref) or not isinstance(artifact_sha256, str) or not _SHA.fullmatch(artifact_sha256)
                or not all(callable(port) for port in (granted, identity, clock))
                or type(max_receipts) is not int or not 2 <= max_receipts <= 24):
            raise ValueError('fixed bounded existing LA receipt source required')
        self.site_id, self.camera_id = site_id, camera_id
        self.model_ref, self.artifact = deepcopy(model_ref), artifact_sha256
        self.granted, self.identity, self.clock = granted, identity, clock
        self.capacity, self.lock = max_receipts, threading.RLock()
        self.capture_input = None
        self.receipt_resolver = None
        self.scope = None
        self.frames, self.receipts = {}, {}
        self.fact_log = None
        self.perceiver = None
        self.retired = {}
        self.expired_frames = set()
        self.closed = False

    def bind_capture(self, capture_input, receipt_resolver):
        """Fix the original owner resolver once; never replace authority."""
        with self.lock:
            if (self.closed or self.capture_input is not None
                    or not callable(capture_input) or not callable(receipt_resolver)):
                raise ValueError('one fixed original capture capability required')
            self.capture_input = capture_input
            self.receipt_resolver = receipt_resolver

    def attach(self, fact_log):
        """Subscribe to new successful receipts; does not replay/open its files."""
        if not isinstance(fact_log, la_fact_log.FactLog):
            raise ValueError('existing controller FactLog required')
        with self.lock:
            if self.closed or self.fact_log is not None:
                raise ValueError('one active existing receipt subscription required')
            fact_log.subscribe_receipts(self.observe_la)
            self.fact_log = fact_log
        return self

    def bind_perceiver(self, perceiver):
        """Bind the existing controller LA transport, not a model scheduler.

        Exact acquisition RGB bytes join its existing ring. The existing pump
        keeps all grant, single-request, budget, clock and receipt authority.
        """
        from la_live_perceiver import LaLivePerceiver
        with self.lock:
            if (self.closed or self.perceiver is not None or not isinstance(perceiver, LaLivePerceiver)
                    or self.fact_log is not perceiver.fact_log
                    or perceiver.pump.session['site_id'] != self.site_id
                    or perceiver.pump.session['camera_id'] != self.camera_id
                    or perceiver.pump.base_profile['artifact_sha256'] != self.artifact):
                raise ValueError('one matching existing admitted LA perceiver required')
            self.perceiver = perceiver
        return self

    def offer_capture(self, jpeg, header, capture_id):
        """Offer only the original qualified RGB exposure, without waiting."""
        try:
            with self.lock:
                if self.closed or self.perceiver is None or self.capture_input is None:
                    return 'unavailable'
                if self._authority()[0] is not None:
                    return 'unavailable'
                native = self.capture_input(capture_id)
                originals = [row for row in native['original_receipts'] if row['component_id'] == 'rgb']
                receipt = json.loads(base64.b64decode(header + '=' * ((-len(header)) % 4), altchars=b'-_', validate=True))
                if (len(originals) != 1 or originals[0]['rgb_receipt'] != receipt
                        or not isinstance(jpeg, bytes) or hashlib.sha256(jpeg).hexdigest() != receipt['jpeg_sha256']):
                    return 'refused'
                return 'offered' if self.perceiver.offer_preview(jpeg, header) else 'unavailable'
        except Exception:
            return 'unavailable'

    def _authority(self):
        try:
            if self.granted() is not True:
                return 'la_grant_unavailable', None
            identity = self.identity()
            if (not isinstance(identity, dict) or set(identity) != {'engine_epoch', 'artifact_sha256', 'session_id'}
                    or any(not isinstance(identity[key], str) or not _ID.fullmatch(identity[key])
                           for key in ('engine_epoch', 'session_id'))
                    or identity['artifact_sha256'] != self.artifact):
                return 'la_identity_unavailable', None
            return None, identity
        except Exception:
            return 'la_identity_unavailable', None

    def _receipt(self, request, receipt):
        if (not isinstance(receipt, dict) or set(receipt) != la_fact_log.RECEIPT_KEYS
                or len(scene.canonical(receipt).encode()) > la_fact_log.MAX_RECEIPT_BYTES
                or receipt['schema'] != scene.RECEIPT_SCHEMA or receipt['result_schema'] != scene.SCHEMA
                or receipt['site_id'] != self.site_id or receipt['camera_id'] != self.camera_id
                or receipt['artifact_sha256'] != self.artifact or receipt['prompt_version'] != scene.PROMPT_VERSION
                or receipt['origin'] != scene.ORIGIN or receipt['text_scope'] != 'digest_only_reply_not_retained'
                or receipt['status'] != 'valid' or receipt['invalid_reason'] is not None
                or receipt['fingerprint'] != scene.fingerprint(receipt)
                or receipt['max_age_seconds'] != scene.MAX_AGE_SECONDS
                or not isinstance(request, dict) or set(request) != {'reference', 'frames', 'sent_at', 'profile'}
                or not isinstance(request.get('reference'), dict)
                or set(request['reference']) != {'request_id', 'session_id', 'site_id', 'body_sha256', 'prompt_version'}
                or not isinstance(request.get('profile'), dict) or set(request['profile']) != {'engine_epoch'}):
            raise ValueError()
        reference, frames = request['reference'], request['frames']
        if (not isinstance(frames, list) or not scene.MIN_IMAGES <= len(frames) <= scene.MAX_IMAGES
                or any(not isinstance(frame, dict) or set(frame) !=
                       {'media_id', 'frame_id', 'capture_epoch', 'observed_at'} for frame in frames)
                or any(reference.get(key) != receipt[key] for key in
                       ('request_id', 'session_id', 'site_id', 'body_sha256', 'prompt_version'))
                or request['profile']['engine_epoch'] != receipt['engine_epoch']
                or receipt['frame_ids'] != [frame['frame_id'] for frame in frames]
                or receipt['media_ids'] != [frame['media_id'] for frame in frames]
                or receipt['evidence_roots'] != ['media:' + frame['media_id'] for frame in frames]
                or len(set(receipt['frame_ids'])) != len(frames)
                or any(frame['capture_epoch'] != receipt['capture_epoch'] for frame in frames)
                or receipt['window_start'] != frames[0]['observed_at']
                or receipt['observed_at'] != frames[-1]['observed_at']):
            raise ValueError()
        stamps = [frame['observed_at'] for frame in frames]
        if (not all(scene.finite(value) and value >= 0 for value in stamps)
                or any(a >= b for a, b in zip(stamps, stamps[1:]))
                or stamps[-1] - stamps[0] > scene.MAX_SPAN_SECONDS
                or not all(scene.finite(receipt[key]) for key in
                           ('observed_at', 'generated_at', 'available_at', 'first_received_at', 'valid_until'))
                or not receipt['observed_at'] <= request['sent_at'] <= receipt['generated_at'] <= receipt['available_at']
                or receipt['first_received_at'] != receipt['available_at']
                or receipt['valid_until'] != min(receipt['observed_at'] + scene.MAX_AGE_SECONDS,
                    receipt['original_valid_until'] if receipt['original_valid_until'] is not None else float('inf'))):
            raise ValueError()
        valid, _ = scene.validate_facts(receipt['facts'], prior_supplied=receipt['prior_supplied'])
        if valid is None:
            raise ValueError()
        return {'receipt': deepcopy(receipt), 'frames': deepcopy(frames)}

    def observe_la(self, request, receipt):
        """Cache one successful first receipt, with immutable retry identity."""
        try:
            value = self._receipt(request, receipt)
            with self.lock:
                if self.closed:
                    return 'refused'
                reason, identity = self._authority()
                if reason is not None or any(receipt[key] != identity[key]
                        for key in ('engine_epoch', 'artifact_sha256', 'session_id')):
                    return 'refused'
                key = receipt['request_id']
                previous = self.receipts.get(key)
                if previous is not None:
                    # First availability is not changed by reconnect/observer retry.
                    stable = lambda r: {k: v for k, v in r.items() if k not in {'available_at', 'first_received_at'}}
                    return 'duplicate' if (stable(previous['receipt']) == stable(receipt)
                                           and previous['frames'] == value['frames']) else 'refused'
                now = self.clock()
                if not scene.finite(now) or key in self.retired:
                    return 'refused'
                if now >= receipt['valid_until']:
                    if len(self.retired) < 1024 and len(self.expired_frames) < 1024:
                        self.retired[key] = wire_digest([value['frames'], receipt['fingerprint']])
                        self.expired_frames.add(receipt['frame_ids'][-1])
                    return 'refused'
                if not scene.causal(receipt, now):
                    return 'refused'
                expired = [old for old, row in self.receipts.items() if now >= row['receipt']['valid_until']]
                # Retain bounded immutable identities so eviction cannot make a
                # changed request ID new again. Exhaustion is an explicit stop.
                if len(self.retired) + len(expired) > 1024:
                    return 'refused'
                for old in expired:
                    row = self.receipts.pop(old)
                    self.retired[old] = wire_digest([row['frames'], row['receipt']['fingerprint']])
                if len(self.receipts) >= self.capacity:
                    return 'refused'
                self.receipts[key] = value
                return 'accepted'
        except Exception:
            return 'refused'

    def _binding(self, context):
        if self.capture_input is None:
            raise ValueError('capture_binding_unavailable')
        try:
            native = self.capture_input(context['capture']['id'])
            if any(native[key] != context[key] for key in ('capture', 'capture_payload', 'original_receipts')):
                raise ValueError()
            payload, cap = native['capture_payload'], native['capture']
            scope = {key: payload[key] for key in ('site_id', 'room_id', 'session_id', 'domain_epoch')}
            if scope['site_id'] != self.site_id or self.scope is not None and self.scope != scope:
                raise ValueError()
            if native['registration']['camera_id'] != self.camera_id:
                raise ValueError()
            if not cap['available_at'] <= self.clock() < cap['effective_valid_until']:
                raise ValueError()
            originals = [row for row in native['original_receipts'] if row['component_id'] == 'rgb']
            if len(originals) != 1:
                raise ValueError()
            original = originals[0]
            rgb = original['rgb_receipt']
            position = payload['head']['position']
            if (rgb['session'] != position['capture_epoch'] or rgb['sequence'] != position['capture_seq']
                    or original['capture_epoch'] != rgb['session'] or original['capture_seq'] != rgb['sequence']
                    or type(rgb['timing_generation']) is not int or rgb['timing_generation'] < 0
                    or not _SHA.fullmatch(rgb['jpeg_sha256'])
                    or rgb['observed_at_unix_ns'] / 1_000_000_000 != original['observed_at']
                    or original['observed_at'] != payload['observed_at']
                    or original['sensor_roots'] != payload['sensor_roots']):
                raise ValueError()
            rgb_refs = [row for row in payload['original_receipt_refs'] if row['component_id'] == 'rgb']
            if len(rgb_refs) != 1:
                raise ValueError()
            record = self.receipt_resolver(rgb_refs[0]['store_id'], rgb_refs[0]['receipt_id'])
            if (record['ref'] != rgb_refs[0] or record['capture_epoch'] != rgb['session']
                    or record['capture_seq'] != rgb['sequence']
                    or not record['observation_root_times']
                    or set(record['observation_root_times']) != set(record['sensor_root_bindings'])
                    or any(root not in payload['observation_root_times']
                           or observed != original['observed_at']
                           or observed != payload['observation_root_times'][root]
                           or record['sensor_root_bindings'][root] != payload['sensor_root_bindings'][root]
                           for root, observed in record['observation_root_times'].items())):
                raise ValueError()
            rgb_roots = list(record['observation_root_times'])
            alias = f"{rgb['session']}-{rgb['timing_generation']}-{rgb['sequence']}"
            binding = {'frame_id': alias, 'media_id': 'ring-' + rgb['jpeg_sha256'],
                       'capture_epoch': rgb['session'], 'observed_at': original['observed_at'],
                       'capture_id': cap['id'], 'source_generation': cap['source_generation'],
                       'sensor_roots': deepcopy(original['sensor_roots']),
                       'observation_root_times': {root: payload['observation_root_times'][root] for root in rgb_roots},
                       'sensor_root_bindings': {root: deepcopy(payload['sensor_root_bindings'][root]) for root in rgb_roots},
                       'original_receipt_refs': deepcopy(rgb_refs),
                       'person_ref': self._person_binding(context, payload)}
            previous = self.frames.get(alias)
            if previous is not None and previous != binding:
                raise ValueError()
            if previous is None and len(self.frames) >= self.capacity:
                raise ValueError('supplemental_capacity_exhausted')
            self.frames[alias], self.scope = binding, scope
            return binding
        except Exception as error:
            reason = 'supplemental_capacity_exhausted' if str(error) == 'supplemental_capacity_exhausted' else 'capture_binding_unavailable'
            raise ValueError(reason) from None

    def _person_binding(self, context, capture_payload):
        """Read the actual graph's separately admitted current spatial result.

        This never creates a person from Qwen's word `one`. The trusted graph
        supplies its own spatial output, not a semantic input supplied remotely.
        """
        try:
            result = context['spatial']
            event, ack = result['publication'], result['receipt']
            spatial = event['payload']
            person = spatial['person_ref']
            scope = ('site_id', 'room_id', 'session_id', 'domain_epoch')
            if (result['status'] != 'supplied' or spatial['schema'] != 'home-live-geometry-1'
                    or spatial['status'] != 'supplied' or ack['status'] != 'accepted'
                    or ack['id'] != event['id'] or ack['producer_id'] != event['producer_id']
                    or ack['payload_digest'] != wire_digest(spatial)
                    or spatial['head']['position'] != capture_payload['head']['position']
                    or any(spatial[key] != capture_payload[key] for key in
                           (*scope, 'source_id', 'source_generation', 'observed_at', 'sensor_roots',
                            'observation_roots', 'observation_root_times', 'sensor_root_bindings'))
                    or spatial['association']['person_status'] != 'qualified'
                    or spatial['association']['candidate_person_refs']
                    or not isinstance(person, dict) or set(person) != {*scope, 'track_epoch', 'pid'}
                    or any(person[key] != capture_payload[key] for key in scope)
                    or not scene.finite(ack.get('committed_at'))
                    or not spatial['available_at'] <= ack['committed_at'] <= self.clock()
                    or not spatial['available_at'] <= self.clock() < spatial['valid_until']):
                return None
            return deepcopy(person)
        except Exception:
            return None

    def read(self, context):
        """Current source proposals or explicit absence, never local demand gating."""
        unavailable = lambda reason: {'status': 'unavailable', 'reason': reason, 'receipt': None}
        output = {'schema': SCHEMA, 'version': 1, 'la': unavailable('la_receipt_unavailable'),
                  'pc': {'status': 'unavailable', 'reason': 'qualified_person_device_attribution_unavailable'}}
        try:
            with self.lock:
                if self.closed:
                    output['la'] = unavailable('optional_sources_closed')
                    return output
                current = self._binding(context)
                if current['frame_id'] in self.expired_frames:
                    output['la'] = unavailable('la_receipt_stale')
                reason, identity = self._authority()
                if reason is not None:
                    self.receipts.clear()
                    output['la'] = unavailable(reason)
                    return output
                rows = sorted(self.receipts.values(), key=lambda row: row['receipt']['observed_at'], reverse=True)
                # Competing interpretations of this exact observed frame are
                # retained as alternatives. Arrival order never selects truth.
                alternatives = {}
                for row in rows:
                    receipt = row['receipt']
                    if (receipt['frame_ids'][-1] != current['frame_id']
                            or any(receipt[key] != identity[key] for key in ('engine_epoch', 'artifact_sha256', 'session_id'))
                            or not scene.causal(receipt, self.clock()) or self.clock() >= receipt['valid_until']):
                        continue
                    exact = all((binding := self.frames.get(frame['frame_id'])) is not None
                                and all(frame[key] == binding[key] for key in
                                        ('frame_id', 'media_id', 'capture_epoch', 'observed_at'))
                                and binding['source_generation'] == current['source_generation']
                                for frame in row['frames'])
                    if exact:
                        alternatives.setdefault(scene.canonical(receipt['facts']), receipt)
                if len(alternatives) > 1:
                    output['la'] = {'status': 'conflict', 'reason': 'conflicting_receipts', 'receipt': None,
                        'alternatives': [deepcopy(receipt) for receipt in list(alternatives.values())[:4]],
                        'alternative_count': len(alternatives), 'alternatives_complete': len(alternatives) <= 4}
                    return output
                for row in rows:
                    receipt = row['receipt']
                    if receipt['frame_ids'][-1] != current['frame_id']:
                        continue
                    if any(receipt[key] != identity[key] for key in ('engine_epoch', 'artifact_sha256', 'session_id')):
                        output['la'] = unavailable('la_identity_unavailable')
                        continue
                    if not scene.causal(receipt, self.clock()) or self.clock() >= receipt['valid_until']:
                        output['la'] = unavailable('la_receipt_stale')
                        continue
                    mapped = []
                    for frame in row['frames']:
                        binding = self.frames.get(frame['frame_id'])
                        if (binding is None or any(frame[key] != binding[key] for key in
                            ('frame_id', 'media_id', 'capture_epoch', 'observed_at'))
                                or binding['source_generation'] != current['source_generation']):
                            mapped = []
                            break
                        mapped.append(binding)
                    if not mapped:
                        output['la'] = unavailable('la_frame_unmapped')
                        continue
                    root_times, physical, refs = {}, {}, []
                    for frame in mapped:
                        root_times.update(frame['observation_root_times'])
                        physical.update(frame['sensor_root_bindings'])
                        refs.extend(frame['original_receipt_refs'])
                    facts = receipt['facts']
                    person = current['person_ref']
                    associated = (facts['people_visible'] == 'one' and person is not None
                                  and all(frame['person_ref'] == person for frame in mapped))
                    material = facts['book_state'] if facts['book_state'] in {'open', 'closed'} else (
                        'visible_state_unknown' if facts['held_object'] == 'book' else None)
                    output['la'] = {'status': 'fresh_proposal', 'reason': 'exact_current_capture_receipt',
                        'source_id': LA_SOURCE, 'model_ref': deepcopy(self.model_ref),
                        'artifact_sha256': self.artifact, 'receipt': deepcopy(receipt),
                        'frame_bindings': deepcopy(mapped), 'observed_at': receipt['observed_at'],
                        'available_at': receipt['first_received_at'], 'valid_until': receipt['valid_until'],
                        'sensor_roots': sorted({root for frame in mapped for root in frame['sensor_roots']}),
                        'observation_roots': sorted(root_times), 'observation_root_times': root_times,
                        'sensor_root_bindings': physical, 'original_receipt_refs': refs,
                        'probability': None, 'qualification': 'uncalibrated_model_proposal',
                        'truth_role': 'runtime_model_proposal', 'material_state': material,
                        'person_association': 'qualified' if associated else 'unresolved',
                        'person_ref': deepcopy(person) if associated else None,
                        'activity_proposal': None if facts['activity_hypothesis'] == 'unclear' else facts['activity_hypothesis'],
                        'visible_basis': deepcopy(facts['visible_basis'])}
                    break
        except Exception as error:
            reason = str(error) if str(error) in {'capture_binding_unavailable', 'supplemental_capacity_exhausted'} else 'optional_sources_unavailable'
            output['la'] = unavailable(reason)
        return output

    def clear(self):
        with self.lock:
            self.closed = True
            fact_log, self.fact_log = self.fact_log, None
            self.frames.clear()
            self.receipts.clear()
            self.retired.clear()
            self.expired_frames.clear()
            self.perceiver = None
            self.scope = None
            self.capture_input = None
            self.receipt_resolver = None
        if fact_log is not None:
            fact_log.unsubscribe_receipts(self.observe_la)
        return True

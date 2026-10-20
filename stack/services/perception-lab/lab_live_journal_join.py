"""Original Lab observations admitted through the existing live journal profile.

This finite server-side join needs an explicitly commissioned source/method,
original RGB media timing metadata, and an existing publication grant. It does
not commission those inputs, run models, open a camera or grant person identity.
Positive qualified geometry continues through the existing spatial producer.
"""
from copy import deepcopy
from decimal import Decimal

from lab_live_context_adapter import LabLiveContextAdapter

ORIGINAL_ROOT_LINEAGE = 'persisted-observation-record-1'
BUNDLED_FAMILY = 'existing-api-bundled-rgb-1'


def capture_original(event, frame_metadata, received_at, method_ref, model_ref,
                     *, bundled_originals=None):
    """Freeze the private source body once, preserving the legacy bytes/shape."""
    result = {'schema': 'home-original-lab-observation-1', 'version': 1,
        'event': deepcopy(event), 'frame_metadata': deepcopy(frame_metadata),
        'first_consumer_received_at': received_at, 'method_ref': deepcopy(method_ref),
        'model_ref': deepcopy(model_ref)}
    if bundled_originals is not None:
        result.update(schema='home-original-lab-observation-2', version=2,
            bundled_originals=deepcopy(bundled_originals),
            clock_original=deepcopy(bundled_originals['clock_original']))
    return result


class LabLiveJournalJoin:
    """Admit original evidence as unavailable semantics/geometry, never beams.

    Install resolve_receipt in the graph's fixed resolver before constructing
    its Journal. `publish` uses Journal.append_event and its actual grant. The
    owner-local original_receipt preserves the complete original Lab event.
    Source registration may be null: then only the honest observation fact is
    published. A registered source additionally publishes eight unavailable
    geometry parts, making missing qualification visible to the ordinary worker.
    """

    def __init__(self, session, *, arm, generation, live_config, source_id,
                 revision, method_ref, model_ref, lab_profile, model_manifests,
                 receipt_store_id, receipt_component_id,
                 max_events=24, root_lineage=None, bundle_input_policy_sha256=None):
        from journal import digest, identifier
        from live_ingress_validation import validate_config
        from schema_registry import SchemaRegistry, LIVE_PROFILE
        config = validate_config(live_config, SchemaRegistry((), profile=LIVE_PROFILE))
        self.adapter = LabLiveContextAdapter(session, arm=arm, generation=generation, max_events=max_events)
        self.config, self.scope = config, config['manifest']['identity']
        self.source_id, self.revision = identifier(source_id, 'source_id'), identifier(revision, 'revision')
        self.source = config['manifest']['sources'][source_id]
        if bundle_input_policy_sha256 is not None and (
                not isinstance(bundle_input_policy_sha256, str)
                or len(bundle_input_policy_sha256) != 64
                or any(c not in '0123456789abcdef' for c in bundle_input_policy_sha256)
                or root_lineage != ORIGINAL_ROOT_LINEAGE):
            raise ValueError('fixed_bundled_capture_policy_required')
        self.bundle_input_policy_sha256 = bundle_input_policy_sha256
        if (self.scope['site_id'] != session['site_id'] or self.scope['session_id'] != session['id']
                or method_ref not in self.source['method_refs']
                or method_ref not in self.source['association_method_refs']
                or model_ref not in self.source['model_refs']
                or receipt_store_id not in self.source['receipt_store_ids']
                or receipt_component_id not in self.source['receipt_component_ids']
                or self.source['capture_clock']['original_unit'] != (
                    'unix_s' if bundle_input_policy_sha256 is not None else 'unix_ns')
                or len(self.source['sensor_roots']) != 1):
            raise ValueError('existing_lab_live_source_binding_required')
        self.method = deepcopy(method_ref)
        self.model = deepcopy(model_ref)
        # The existing Lab profile pins a composite artifact, rather than just
        # its detector weights. Rebuild its existing identity from the actual
        # stage manifests, including crop policy and pose preprocessing.
        from staged_specialist import composite_manifest
        from pose_composition import POLICY, MODEL_VERSION
        from rtmpose_worker import RTMPoseWorker
        if (not isinstance(lab_profile, dict) or not isinstance(model_manifests, dict)
                or set(model_manifests) != {'detector', 'pose'}
                or lab_profile.get('profile_id') != 'yolo26s-rtmpose-cpu-v1'
                or lab_profile.get('model_version') != MODEL_VERSION
                or any(not isinstance(lab_profile.get(k), str) or not lab_profile[k]
                       for k in ('artifact_sha256', 'engine_epoch', 'device_id'))):
            raise ValueError('existing_lab_composite_profile_required')
        expected = composite_manifest(model_manifests['detector'], model_manifests['pose'],
            profile_id=lab_profile['profile_id'], engine_epoch=lab_profile['engine_epoch'],
            policy=POLICY, model_version=MODEL_VERSION,
            pose_preprocessing_revision=RTMPoseWorker.preprocessing_revision,
            runtime_revision=lab_profile.get('runtime_revision'))
        if (model_ref['digest'] != expected['sha256']
                or lab_profile['artifact_sha256'] != expected['sha256']
                or any(lab_profile.get(k) != value for k, value in expected.items() if k != 'sha256')
                or 'sha256' in lab_profile and lab_profile['sha256'] != expected['sha256']):
            raise ValueError('lab_composite_model_binding_mismatch')
        self.lab_profile, self.model_manifests = deepcopy(lab_profile), deepcopy(model_manifests)
        self.store_id, self.component_id = receipt_store_id, receipt_component_id
        if root_lineage is not None and root_lineage != ORIGINAL_ROOT_LINEAGE:
            raise ValueError('exact_lab_original_root_lineage_required')
        self.root_lineage = root_lineage
        self.binding_digest = digest(self._binding_material())
        self.prepared, self.records, self.originals, self.heads = {}, {}, {}, {}
        self.original_root_frames = {}
        self.pending = None
        self.journal_authority = None
        self.max_events = max_events

    def _binding_material(self):
        material = [self.config, self.method, self.model, self.lab_profile,
                    self.model_manifests, self.adapter.session,
                    self.adapter.arm, self.adapter.generation]
        # Preserve the historical binding bytes for the default profile. The
        # opt-in successor binds its canonical-root interpretation explicitly.
        if self.root_lineage is not None:
            material.append({'root_lineage': self.root_lineage})
        if self.bundle_input_policy_sha256 is not None:
            material.append({'original_family': BUNDLED_FAMILY,
                             'bundle_input_policy_sha256': self.bundle_input_policy_sha256})
        return material

    def bundled_binding(self):
        if self.bundle_input_policy_sha256 is None:
            raise ValueError('explicit_bundled_capture_family_required')
        return {'scope': deepcopy(self.scope), 'lab_binding_digest': self.binding_digest,
                'bundle_input_policy_sha256': self.bundle_input_policy_sha256}

    def _check_model(self, payload):
        from pose_composition import POLICY
        from rtmpose_worker import RTMPoseWorker
        profile = self.lab_profile
        processing = (payload.get('result') or {}).get('processing')
        if (any(payload.get(field) != profile[key] for field, key in (
                    ('run_id', 'profile_id'), ('model_version', 'model_version'),
                    ('engine_epoch', 'engine_epoch'), ('artifact_sha256', 'artifact_sha256')))
                or not isinstance(processing, dict)
                or processing.get('manifest_sha256') != profile['artifact_sha256']
                or processing.get('model_version') != profile['model_version']
                or processing.get('pipeline') != 'detector-pose-v1'
                or processing.get('pose_preprocessing_revision') != RTMPoseWorker.preprocessing_revision
                or processing.get('crop_policy') != POLICY
                or processing.get('correlated_evidence') is not True
                or not isinstance(payload.get('request_id'), str) or not payload['request_id']
                or processing.get('shared_source_request') != payload['request_id']):
            raise ValueError('original_lab_model_binding_mismatch')
        for stage in ('detector', 'pose'):
            actual = processing.get(stage)
            manifest = self.model_manifests[stage]
            if (not isinstance(actual, dict) or actual.get('artifact_sha256') != manifest['sha256']
                    or actual.get('device_id') != manifest['device_id']):
                raise ValueError('original_lab_stage_binding_mismatch')

    def _check_frame(self, payload, frame, receipt):
        from reasoning import finite
        if (not isinstance(receipt, dict) or receipt.get('schema') != 3
                or frame.get('site_id') != self.adapter.session['site_id']
                or frame.get('camera_id') != self.adapter.session['camera_id']
                or frame.get('sequence') != receipt.get('sequence')
                or type(receipt.get('timing_generation')) is not int
                or frame.get('timing_generation') != receipt['timing_generation']
                or frame.get('frame_id') != f"{receipt['session']}-{receipt['timing_generation']}-{receipt['sequence']}"
                or frame.get('clock_mapping_status') != 'verified'
                or frame.get('clock_mapping_id') != receipt.get('validation_id')
                or frame.get('receiver_clock_validation_id') != receipt.get('host_clock_validation_id')
                or frame.get('source_clock') != receipt.get('source_clock')
                or frame.get('source_clock') != 'depthai_device_monotonic'
                or frame.get('timestamp_semantics') != receipt.get('timestamp_semantics')
                or frame.get('timestamp_semantics') != 'causal_exact_frame_exposure_end'
                or type(receipt.get('frame_device_ns')) is not int or receipt['frame_device_ns'] < 0
                or frame.get('source_timestamp_seconds') != receipt['frame_device_ns']/1e9
                or frame.get('width') != receipt.get('width') or frame.get('width') != 640
                or frame.get('height') != receipt.get('height') or frame.get('height') != 360
                or frame.get('format') != 'jpeg'
                or not finite(receipt.get('uncertainty_seconds')) or receipt['uncertainty_seconds'] < 0
                or not finite(receipt.get('host_clock_uncertainty_seconds')) or receipt['host_clock_uncertainty_seconds'] < 0):
            raise ValueError('original_lab_rgb_metadata_inconsistent')
        total = receipt['uncertainty_seconds'] + receipt['host_clock_uncertainty_seconds']
        observed = float(Decimal(receipt['observed_at_unix_ns'])/Decimal(1_000_000_000))
        if (frame.get('observed_at') != observed or payload.get('observed_at') != observed
                or frame.get('uncertainty_seconds') != total
                or not finite(frame.get('available_at'))
                or not observed <= frame['available_at'] <= payload['available_at']
                or not finite(frame.get('valid_until')) or not observed < frame['valid_until']):
            raise ValueError('original_lab_rgb_metadata_inconsistent')
        return total

    def resolve_receipt(self, store_id, receipt_id):
        if store_id != self.store_id:
            raise KeyError(store_id)
        return deepcopy(self.records[receipt_id])

    def original_receipt(self, store_id, receipt_id):
        if store_id != self.store_id:
            raise KeyError(store_id)
        return deepcopy(self.originals[receipt_id])

    def publish(self, original_event, *, frame_metadata, received_at, journal, grant,
                bundled_originals=None, bundled_media_resolver=None):
        from journal import canonical, digest
        from live_capture import PARTS, _original_digest
        from live_ingress_validation import validate_clock
        # Cached acknowledgments belong to one actual authority, even when a
        # second Journal uses identical public configuration and credentials.
        from journal import Journal
        if not isinstance(journal, Journal):
            raise ValueError('actual_lab_journal_authority_required')
        if self.journal_authority is None:
            self.journal_authority = journal
        elif self.journal_authority is not journal:
            raise ValueError('lab_join_journal_authority_changed')
        if digest(self._binding_material()) != self.binding_digest:
            raise ValueError('lab_join_binding_changed')
        event_id = original_event.get('id') if isinstance(original_event, dict) else None
        # Original camera receipts retain integer nanoseconds beyond JSON's
        # interoperable-number range. The established private-original digest
        # preserves those integers; public clocks carry them as decimal strings.
        bundled = self.bundle_input_policy_sha256 is not None
        if bundled != (bundled_originals is not None) or bundled != callable(bundled_media_resolver):
            raise ValueError('exact_bundled_capture_proof_capability_required')
        fingerprint = _original_digest([original_event, frame_metadata] +
            ([bundled_originals] if bundled else []))
        entry = self.prepared.get(event_id)
        if entry is not None and entry['fingerprint'] != fingerprint:
            raise ValueError('lab_event_identity_conflict')
        if self.pending is not None and self.pending != event_id:
            raise ValueError('lab_publication_pending_exact_retry_required')
        if entry is None:
            if len(self.prepared) >= self.max_events:
                raise ValueError('lab_candidate_event_budget_exhausted')
            payload = original_event.get('payload', {}) if isinstance(original_event, dict) else {}
            receipt = frame_metadata.get('source_timing_receipt') if isinstance(frame_metadata, dict) else None
            if not bundled and (not isinstance(receipt, dict) or frame_metadata.get('stream') != 'rgb'
                    or frame_metadata.get('frame_id') != payload.get('frame_id')
                    or frame_metadata.get('capture_epoch') != payload.get('capture_epoch')
                    or receipt.get('session') != payload.get('capture_epoch')
                    or receipt.get('session') != self.source['capture_epoch']
                    or type(receipt.get('sequence')) is not int or receipt['sequence'] < 0
                    or type(receipt.get('observed_at_unix_ns')) is not int
                    or receipt['observed_at_unix_ns'] < 0):
                raise ValueError('original_lab_rgb_receipt_required')
            self._check_model(payload)
            if bundled:
                from lab_bundled_rgb_stereo import validate_bundled_rgb_originals
                checked = validate_bundled_rgb_originals(bundled_originals,
                    media_refs=bundled_originals['clock_original']['media_refs'],
                    expected_bundle_binding=self.bundled_binding(),
                    capture_clock_policy=self.source['capture_clock'],
                    resolve_media=bundled_media_resolver, event=original_event, frame=frame_metadata)
                clock_original = checked['clock_original']
                if (clock_original != bundled_originals['clock_original']
                        or not clock_original['first_consumer_received_at'] <= received_at
                        or frame_metadata['capture_epoch'] != self.source['capture_epoch']):
                    raise ValueError('original_bundled_capture_availability_changed')
                clock = deepcopy(clock_original['clock'])
                capture_sequence = frame_metadata['sequence']
            else:
                uncertainty = self._check_frame(payload, frame_metadata, receipt)
                clock = {'status': 'commissioned', **{k: self.source['capture_clock'][k] for k in
                    ('mapping_id', 'mapping_digest', 'source_epoch', 'original_unit')},
                    'original_value': str(receipt['observed_at_unix_ns']),
                    'uncertainty_s': uncertainty}
                capture_sequence = receipt['sequence']
            # Existing admission verifies original integer time conversion and
            # the commissioned uncertainty ceiling; no arrival-time substitute.
            validate_clock(clock, self.source['capture_clock'], payload.get('observed_at'))
            candidate = self.adapter.consume(original_event, received_at=received_at)
            if candidate['status'] != 'candidate':
                return {'status': candidate['status'], 'candidate': candidate, 'receipts': []}
            observed, available, expiry = (payload[k] for k in ('observed_at', 'available_at', 'valid_until'))
            if bundled:
                # Media durability precedes this frozen first receipt. Exact
                # retries retain it; neither receipt nor ACK changes exposure.
                available = max(available, received_at)
            if expiry - observed > self.source['max_age_s']:
                raise ValueError('original_lab_expiry_exceeds_source_policy')
            # The incumbent model and RGB receipts have separate leases. A
            # consumer can shorten its publication; it cannot extend either
            # original or rewrite their historical bodies.
            expiry = min(expiry, frame_metadata['valid_until'])
            if received_at >= expiry:
                candidate = {**candidate, 'status': 'expired', 'reason': 'original_rgb_expired'}
                return {'status': 'expired', 'candidate': candidate, 'receipts': []}
            identity = [self.scope, self.source_id, self.adapter.arm, self.adapter.generation, event_id]
            original = capture_original(original_event, frame_metadata, received_at,
                self.method, self.model, bundled_originals=bundled_originals)
            original_id = 'lab-original:' + digest(identity)
            if self.root_lineage is None:
                root_ids = ['lab-frame:' + digest([self.adapter.session, payload['capture_epoch'], payload['frame_id']])]
            else:
                # The existing adapter already validated this original through
                # persisted_evidence.observation_record, exactly as incumbent
                # JEV does. Its event-id fallback is not a new observation or
                # model egress token. Keep raw originals and their clocks intact.
                root_ids = deepcopy(candidate['lineage']['evidence_roots'])
            from journal import identifier
            for root in root_ids:
                identifier(root, 'original_lab_observation_root')
            if self.root_lineage is not None:
                frame_identity = (payload['capture_epoch'], payload['frame_id'], observed)
                if any(root in self.original_root_frames and self.original_root_frames[root] != frame_identity
                       for root in root_ids):
                    raise ValueError('original_lab_root_frame_changed')
                for root in root_ids:
                    self.original_root_frames[root] = frame_identity
            roots = {root: observed for root in root_ids}
            bindings = {root: deepcopy(self.source['sensor_roots']) for root in root_ids}
            ref = {'store_id': self.store_id, 'receipt_id': original_id, 'digest': _original_digest(original),
                'component_id': self.component_id, 'observed_at': observed, 'available_at': available,
                'clock_mapping_id': self.source['capture_clock']['mapping_id']}
            self.originals[original_id] = original
            self.records[original_id] = {'ref': deepcopy(ref), 'capture_epoch': payload['capture_epoch'],
                'capture_seq': capture_sequence, 'observation_root_times': deepcopy(roots),
                'sensor_root_bindings': deepcopy(bindings)}
            association = {'person_status': 'not_applicable', 'subject_status': 'not_applicable',
                'association_id': 'lab-association:' + digest(identity), 'generation': 1,
                'method_ref': deepcopy(self.method), 'candidate_person_refs': [], 'candidate_subject_refs': []}
            common = {**deepcopy(self.scope), 'version': 1, 'manifest_ref': deepcopy(self.config['manifest_ref']),
                'source_id': self.source_id, 'source_generation': self.source['initial_generation'],
                'person_ref': None, 'subject_ref': None, 'association': association,
                'status': 'unobservable', 'observed_at': observed, 'available_at': available, 'valid_until': expiry,
                'sensor_roots': deepcopy(self.source['sensor_roots']), 'observation_roots': root_ids,
                'observation_root_times': roots, 'sensor_root_bindings': bindings,
                'parent_refs': [], 'original_receipt_refs': [ref], 'clock': clock, 'method_ref': deepcopy(self.method)}
            fact = {**deepcopy(common), 'schema': 'home-live-fact-1', 'id': 'lab-fact:' + digest(identity),
                'layer': 'observation', 'kind': 'person_presence', 'value': None, 'assertion_basis': 'inferred',
                'reason': 'missing_applicability', 'model_ref': deepcopy(self.model), 'probability': None, 'conflicting_refs': []}
            publications = [fact]
            if self.source['registration'] is not None:
                geometry = {**deepcopy(common), 'schema': 'home-live-geometry-1', 'id': 'lab-geometry:' + digest(identity),
                    'reason': 'missing_measurement_bound', 'registration': deepcopy(self.source['registration']),
                    'parts': {part: {'status': 'unobservable', 'reason': 'missing_measurement_bound',
                        'support_refs': [deepcopy(ref)], 'observed_at': observed, 'valid_until': expiry} for part in PARTS}}
                publications.append(geometry)
            events = []
            for body in publications:
                key = {'source_id': self.source_id, 'payload_type': body['schema'], 'person_ref': None,
                    'subject_ref': None, 'predicate': 'geometry_bundle' if body['schema'] == 'home-live-geometry-1' else body['kind'],
                    'method_ref': deepcopy(self.method)}
                body['head'] = {'key': key, 'position': {'capture_epoch': payload['capture_epoch'],
                    'capture_seq': capture_sequence, 'revision': 0}, 'supersedes': deepcopy(self.heads.get(canonical(key)))}
                events.append({'schema': 'home-context-event-1', 'schema_version': '1.0', 'id': body['id'],
                    'producer_id': self.source['producer_id'], 'stream_epoch': self.source['producer_epoch'],
                    **deepcopy(self.scope), **{k: body[k] for k in ('observed_at', 'available_at', 'valid_until')},
                    'payload_type': body['schema'], 'payload_version': '1.0', 'revision': self.revision, 'payload': body})
            entry = {'fingerprint': fingerprint, 'events': events, 'receipts': [], 'candidate': candidate}
            self.prepared[event_id] = entry
        self.pending = event_id
        for envelope in entry['events'][len(entry['receipts']):]:
            actual = journal.append_event(deepcopy(envelope), grant)
            entry['receipts'].append(deepcopy(actual))
            self.heads[canonical(envelope['payload']['head']['key'])] = {
                'id': actual['id'], 'cursor': actual['cursor'], 'payload_digest': actual['payload_digest'],
                'producer_id': actual['producer_id'], 'stream_epoch': actual['stream_epoch'], 'component_id': None}
        self.pending = None
        return {'status': 'admitted_unqualified', 'candidate': deepcopy(entry['candidate']),
                'receipts': deepcopy(entry['receipts'])}

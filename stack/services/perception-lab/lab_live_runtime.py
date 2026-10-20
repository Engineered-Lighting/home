"""Attach one current, incumbent LA Lab arm to the existing Den runtime.

The protected server configuration fixes session/profile/source and paths. The
borrowed Store already owns capture and model scheduling. Construction opens no
journal or archive and never starts, stops or replaces those incumbent workers.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import threading
import time

from journal import digest
from lab_live_acquisition import LabLiveAcquisition, utc_now
from lab_live_journal_join import LabLiveJournalJoin
from lab_live_semantic_admission import LabLiveSemanticAdmission
from lab_person_semantic_admission import build_semantic_admission, base_semantic_policy, is_person_policy, TrustedPersonBridge
from lab_desk_capability import TrustedDeskCapability
from lab_live_desk_admission import LabLiveDeskAdmission
from live_detector_app import private_token
from store import Store


PRODUCER_KEYS = {'schema', 'version', 'lab_binding', 'live_config', 'source_id', 'revision',
    'method_ref', 'model_ref', 'receipt_store_id', 'receipt_component_id', 'after_id', 'limits',
    'journal_path', 'journal_grants', 'publication_token_file', 'original_archive'}
BINDING_KEYS = {'session_id', 'arm', 'model_generation', 'media_generation', 'lab_profile', 'model_manifests'}
LIMIT_KEYS = {'max_cycles', 'max_runtime_s', 'page_limit'}
POLL_LIMIT_KEYS = {'max_poll_attempts', 'max_capture_originals', 'max_runtime_s', 'page_limit'}
ORIGINAL_KEYS = {'filename', 'max_records', 'max_bytes', 'min_free_bytes'}


def _producer_shape(producer):
    if not isinstance(producer, dict):
        return False
    version = producer.get('version')
    return type(version) is int and (
        version == 1 and producer.get('schema') == 'home-incumbent-lab-producer-1' and set(producer) == PRODUCER_KEYS
        or version == 2 and producer.get('schema') == 'home-incumbent-lab-producer-2'
        and set(producer) == PRODUCER_KEYS | {'semantic_policy'}
        or version == 3 and producer.get('schema') == 'home-incumbent-lab-producer-3'
        and set(producer) == PRODUCER_KEYS | {'semantic_policy', 'desk_policy'}
        or version == 4 and producer.get('schema') == 'home-incumbent-lab-producer-4'
        and set(producer) == PRODUCER_KEYS | {'semantic_policy', 'desk_policy'}
        or version == 5 and producer.get('schema') == 'home-incumbent-lab-producer-5'
        and set(producer) == PRODUCER_KEYS | {'semantic_policy', 'desk_policy'}
        or version == 6 and producer.get('schema') == 'home-incumbent-lab-producer-6'
        and set(producer) == PRODUCER_KEYS | {'semantic_policy', 'desk_policy'}
        or version == 7 and producer.get('schema') == 'home-incumbent-lab-producer-7'
        and set(producer) == PRODUCER_KEYS | {'semantic_policy', 'desk_policy'}
        or version == 8 and producer.get('schema') == 'home-incumbent-lab-producer-8'
        and set(producer) == PRODUCER_KEYS | {'semantic_policy', 'desk_policy'})


def desk_capability_binding(scope, policy, lab_binding_digest):
    """Commissioned interface identity, independent of an installed capability."""
    return {'scope': deepcopy(scope), 'desk_policy_digest': digest(policy),
            'lab_binding_digest': lab_binding_digest}


def _desk_archive_binding(scope, policy, lab_binding_digest=None):
    return {'scope': deepcopy(scope), 'policy_digest': digest(policy),
            **{key: deepcopy(policy[key]) for key in
               ('source_id', 'method_ref', 'receipt_store_id', 'receipt_component_id')},
            **({'schema': 'home-lab-desk-archive-binding-' + str(policy['version']), 'version': policy['version'],
                'stereo_policy_ref': deepcopy(policy['stereo_policy_ref']),
                'lab_binding_digest': lab_binding_digest} if policy['version'] in (2, 3, 4) else {}),
            **({key: policy[key] for key in ('original_family', 'assessment_policy_sha256')}
               if policy['version'] == 3 else {}),
            **({key: policy[key] for key in ('original_family', 'bundle_input_policy_sha256')}
               if policy['version'] == 4 else {})}


def _assessment_binding(scope, policy, lab_binding_digest):
    return {'scope': deepcopy(scope), 'lab_binding_digest': lab_binding_digest,
            'assessment_policy_sha256': policy['assessment_policy_sha256']}


def _require_desk_capability(capability, expected):
    if (not isinstance(capability, TrustedDeskCapability)
            or capability.binding() != expected):
        raise LabRuntimeRefusal('fixed_trusted_desk_capability_required')


class LabRuntimeRefusal(ValueError):
    pass


def _join_and_configuration(producer, session):
    assessed_subset = isinstance(producer, dict) and producer.get('version') == 8
    poll_budget = isinstance(producer, dict) and producer.get('version') in (7, 8)
    capture_limit = 'max_capture_originals' if poll_budget else 'max_cycles'
    if (not _producer_shape(producer)
            or not isinstance(producer['lab_binding'], dict) or set(producer['lab_binding']) != BINDING_KEYS
            or not isinstance(producer['limits'], dict) or set(producer['limits']) != (POLL_LIMIT_KEYS if poll_budget else LIMIT_KEYS)
            or not isinstance(producer['original_archive'], dict) or set(producer['original_archive']) != ORIGINAL_KEYS
            or not isinstance(producer['journal_grants'], list) or not producer['journal_grants']
            or any(not isinstance(producer[k], str) or not Path(producer[k]).is_absolute()
                   for k in ('journal_path', 'publication_token_file'))):
        raise LabRuntimeRefusal('closed_incumbent_lab_producer_required')
    binding, limits, original = producer['lab_binding'], producer['limits'], producer['original_archive']
    if (not isinstance(binding['session_id'], str) or binding['session_id'] != session.get('id')
            or binding['arm'] != 'b' or type(binding['model_generation']) is not int or binding['model_generation'] < 1
            or type(binding['media_generation']) is not int or binding['media_generation'] < 0
            or binding['media_generation'] != session.get('generation')
            or type(producer['after_id']) is not int or producer['after_id'] < 0
            or type(limits[capture_limit]) is not int or not 1 <= limits[capture_limit] <= 24
            or poll_budget and (type(limits['max_poll_attempts']) is not int or not 1 <= limits['max_poll_attempts'] <= 240)
            or type(limits['page_limit']) is not int or not 1 <= limits['page_limit'] <= 128
            or type(limits['max_runtime_s']) not in (int, float) or not math.isfinite(limits['max_runtime_s'])
            or not 0 < limits['max_runtime_s'] <= 120
            or not isinstance(original['filename'], str) or not Path(original['filename']).is_absolute()
            or Path(original['filename']) == Path(producer['journal_path'])
            or type(original['max_records']) is not int or not limits[capture_limit] <= original['max_records'] <= 96
            or type(original['max_bytes']) is not int or not 1024 <= original['max_bytes'] <= 8*1024**2
            or type(original['min_free_bytes']) is not int or original['min_free_bytes'] < 1):
        raise LabRuntimeRefusal('fixed_la_session_generations_and_bounds_required')
    if producer['version'] in (6, 7, 8) and (
            not isinstance(producer['desk_policy'], dict)
            or producer['desk_policy'].get('original_family') != 'existing-api-bundled-rgb-1'
            or not isinstance(producer['desk_policy'].get('bundle_input_policy_sha256'), str)):
        raise LabRuntimeRefusal('explicit_bundled_source6_policy_required')
    join = LabLiveJournalJoin(session, arm='b', generation=binding['model_generation'],
        live_config=producer['live_config'], source_id=producer['source_id'], revision=producer['revision'],
        method_ref=producer['method_ref'], model_ref=producer['model_ref'], lab_profile=binding['lab_profile'],
        model_manifests=binding['model_manifests'], receipt_store_id=producer['receipt_store_id'],
        receipt_component_id=producer['receipt_component_id'], max_events=limits[capture_limit],
        **({'root_lineage': 'persisted-observation-record-1'} if producer['version'] in (3, 4, 5, 6, 7, 8)
           or is_person_policy(producer.get('semantic_policy')) else {}),
        **({'bundle_input_policy_sha256': producer['desk_policy']['bundle_input_policy_sha256']}
           if producer['version'] in (6, 7, 8) else {}))
    config = {'schema': 'home-incumbent-lab-acquisition-1', 'version': 1,
        'session_ref': deepcopy(join.scope), 'source_id': join.source_id,
        'lab_binding_digest': join.binding_digest, 'media_generation': binding['media_generation'],
        'limits': deepcopy(limits), 'after_id': producer['after_id']}
    if producer['version'] in (2, 3, 4, 5, 6, 7, 8):
        semantic = build_semantic_admission(session, live_config=producer['live_config'],
            revision=producer['revision'], policy=producer['semantic_policy'],
            original_retainer=lambda _: (_ for _ in ()).throw(LabRuntimeRefusal('validation_only_retainer')))
        config.update(schema='home-incumbent-lab-acquisition-2', version=2,
                      semantic_binding_digest=semantic.binding_digest)
    if producer['version'] in (3, 4, 5, 6, 7, 8):
        policy = producer['desk_policy']
        # Validate the commissioned policy without constructing a graph or
        # resolving any purported evidence during configuration preparation.
        LabLiveDeskAdmission(producer['live_config'], policy=policy, revision=producer['revision'],
            resolve_inputs=lambda _: None, original_retainer=lambda _: None, clock=lambda: 0,
            **({'stereo_media_resolver': lambda _: None} if producer['version'] in (4, 5, 6, 7, 8) else {}),
            **({'expected_assessment_binding': _assessment_binding(join.scope, policy, join.binding_digest)}
               if producer['version'] == 5 else {}),
            **({'expected_bundle_binding': join.bundled_binding()}
               if producer['version'] in (6, 7, 8) else {}))
        if (producer['live_config']['version'] != (13 if assessed_subset else 12)
                or policy.get('version') != {3: 1, 4: 2, 5: 3, 6: 4, 7: 4, 8: 4}[producer['version']]
                or semantic.policy.get('version') != (4 if assessed_subset else 3)
                or semantic.policy.get('root_lineage') != 'persisted-observation-record-1'
                or policy['capture_source_id'] != producer['source_id']
                or policy['source_id'] in semantic.source_ids
                or policy['receipt_store_id'] in {producer['receipt_store_id'],
                    *(semantic.policy[k]['receipt_store_id'] for k in ('ha', 'jev'))}
                or producer['live_config']['manifest']['sources'][policy['source_id']]['control_clock']
                    != join.source['control_clock']):
            raise LabRuntimeRefusal('separate_commissioned_desk_source_required')
        config.update(schema='home-incumbent-lab-acquisition-' + str(producer['version']), version=producer['version'],
            desk_binding_digest=digest(desk_capability_binding(join.scope, policy, join.binding_digest)))
    if assessed_subset:
        # The successor's private worker12 authority must be complete before
        # any original archive or Journal path is created.
        from journal import Grant
        from live_ingress import validate_grants
        validate_grants([Grant.from_dict(g, profile='home-live-shadow-1')
                         for g in producer['journal_grants']], join.config)
    return join, config


def lab_graph_configuration(producer, *, session):
    """Exact graph config/digest input before any Journal or archive is opened."""
    return _join_and_configuration(producer, session)[1]


class _CurrentLabOriginals:
    """Preserve the original durable sidecar contract while rechecking admission."""
    def __init__(self, composition, archive):
        self.composition, self.archive = composition, archive

    def __call__(self, original):
        self.composition.require_current()
        retained = self.archive(original)
        if self.composition.producer['version'] in (3, 4, 5, 6, 7, 8) or is_person_policy(self.composition.producer.get('semantic_policy')):
            # Desk originals can grant a new positive publication. Retention
            # may block, so its durable ACK alone cannot preserve a retired
            # incumbent model/session binding across that external operation.
            self.composition.require_current()
        return retained

    def finalize(self):
        result = self.archive.finalize()
        if self.composition.producer['version'] in (4, 5, 6, 7, 8):
            media = self.composition.desk_capability.finalize_media()
            result = {**result, 'stereo_media': media,
                      'status': 'finalized' if result.get('status') == 'finalized'
                                and media.get('status') == 'finalized' else 'incomplete'}
        return result


class _DeskPersonBridgeLedger:
    """Fixed, quota-bound local witness retention before a JEV cutoff.

    The Store uses synchronous FULL transactions. The full witness is also
    carried in the existing semantic original when refinement is published.
    No new file path, external writer or physical capability is introduced.
    """
    def __init__(self, composition):
        self.composition = composition

    def __call__(self, record):
        from live_capture import _original_digest
        c = self.composition
        c.require_current()
        if (not isinstance(record, dict) or set(record) != {'ref', 'original'}
                or record['original'].get('schema') != 'home-lab-person-work-bridge-1'
                or record['original'].get('scope') != c.scope):
            raise LabRuntimeRefusal('fixed_qualified_desk_witness_required')
        fingerprint = _original_digest(record)
        sid = c.scope['session_id']
        with c.store.lock:
            rows = c.store.db.execute("SELECT payload FROM events WHERE session_id=? "
                "AND kind='qualification.person_work_bridge' "
                "AND json_extract(payload,'$.ref.receipt_id')=? ORDER BY id LIMIT 2",
                (sid, record['ref']['receipt_id'])).fetchall()
            if rows:
                if len(rows) != 1 or _original_digest(json.loads(rows[0]['payload'])) != fingerprint:
                    raise LabRuntimeRefusal('qualified_desk_witness_identity_conflict')
            else:
                # Ordinary metadata quota applies; Stop's reserved headroom is
                # never borrowed for a new positive qualification witness.
                c.store.append_event(sid, 'qualification.person_work_bridge', deepcopy(record))
        c.require_current()
        return {'status': 'retained', 'digest': fingerprint}


class LabRuntimeComposition:
    def __init__(self, producer, *, store, preflight, control_clock, desk_capability=None,
                 clock=utc_now, monotonic=time.monotonic, command_current_guard=None,
                 bundled_source=None, person_bridge=None):
        if (not isinstance(store, Store)
                or not _producer_shape(producer)
                or not isinstance(producer.get('lab_binding'), dict) or set(producer['lab_binding']) != BINDING_KEYS
                or not all(callable(port) for port in (preflight, control_clock, clock, monotonic))
                or command_current_guard is not None and not callable(command_current_guard)):
            raise LabRuntimeRefusal('incumbent_store_and_fixed_runtime_capabilities_required')
        self.store, self.producer = store, deepcopy(producer)
        self.pin = digest(self.producer)
        session = store.get_session(producer['lab_binding']['session_id'])
        self.join, self.acquisition_config = _join_and_configuration(self.producer, session)
        self.configuration_digest = digest(self.acquisition_config)
        self.scope = deepcopy(self.join.scope)
        self.person_bridge = person_bridge
        person_policy = self.producer.get('semantic_policy')
        if is_person_policy(person_policy):
            if (person_bridge is not None and (type(person_bridge) is not TrustedPersonBridge
                    or person_bridge.binding() != {'scope': self.scope, 'bridge_policy_digest': digest(person_policy['bridge'])})):
                raise LabRuntimeRefusal('fixed_person_bridge_capability_required')
        elif person_bridge is not None:
            raise LabRuntimeRefusal('person_bridge_requires_explicit_policy')
        self.desk_capability = desk_capability
        self.desk_binding = (desk_capability_binding(self.scope, self.producer['desk_policy'], self.join.binding_digest)
                             if producer['version'] in (3, 4, 5, 6, 7, 8) else None)
        if self.desk_binding is not None:
            _require_desk_capability(desk_capability, self.desk_binding)
            if producer['version'] in (4, 5, 6, 7, 8):
                from lab_stereo_desk_capability import LabStereoDeskCapability
                if not isinstance(desk_capability, LabStereoDeskCapability):
                    raise LabRuntimeRefusal('fixed_stereo_desk_capability_required')
        elif desk_capability is not None:
            raise LabRuntimeRefusal('desk_capability_requires_producer3')
        self.bundled_source = bundled_source
        if producer['version'] in (6, 7, 8):
            from lab_bundled_rgb_stereo import BundledRgbStereoSource
            if (type(bundled_source) is not BundledRgbStereoSource
                    or bundled_source.store is not store
                    or bundled_source.binding() != self.join.bundled_binding()
                    or getattr(desk_capability.anchor_source, 'bundled_rgb_source', None) is not bundled_source):
                raise LabRuntimeRefusal('fixed_current_bundled_source6_required')
        elif bundled_source is not None:
            raise LabRuntimeRefusal('bundled_source_requires_producer6')
        self.preflight, self.control_clock, self.clock, self.monotonic = preflight, control_clock, clock, monotonic
        self.lock, self.cancel = threading.Lock(), threading.Event()
        self.graph = self.originals = None
        self.desk_person_bridge_source = None
        self.used = False
        self._command_current_guard = command_current_guard
        # Lazy controller realization happens after the incumbent Lab starts.
        # Verify its real activation/ready rows; never create replacement ones.
        self._current()

    def _current(self):
        session = self.store.get_session(self.join.adapter.session['id'])
        binding = self.producer['lab_binding']
        if (session.get('state') != 'recording' or session['generation'] != binding['media_generation']
                or any(session.get(k) != value for k, value in self.join.adapter.session.items())):
            raise LabRuntimeRefusal('current_recording_lab_session_required')
        rows = {}
        with self.store.lock:
            for kind in ('experiment.activated', 'workers.ready', 'workers.cleanup'):
                row = self.store.db.execute('SELECT id,payload FROM events WHERE session_id=? AND kind=? ORDER BY id DESC LIMIT 1',
                                            (session['id'], kind)).fetchone()
                rows[kind] = (row['id'], json.loads(row['payload'])) if row else None
        activation, ready, cleanup = (rows[k] for k in ('experiment.activated', 'workers.ready', 'workers.cleanup'))
        if activation is None or ready is None or ready[0] <= activation[0] or cleanup and cleanup[0] >= ready[0]:
            raise LabRuntimeRefusal('current_incumbent_la_worker_receipts_required')
        active, receipt = activation[1], ready[1].get('receipts', {}).get('b', {})
        profile = binding['lab_profile']
        selected = active.get('profiles', {}).get('b', {})
        now = self.clock()
        if (active.get('generation') != binding['model_generation']
                or active.get('site_id') != session['site_id'] or active.get('camera_id') != session['camera_id']
                or any(selected.get(k) != profile[k] for k in ('profile_id', 'artifact_sha256', 'engine_epoch', 'model_version'))
                or receipt.get('state') != 'ready' or receipt.get('profile') != profile
                or receipt.get('session_id') != session['id'] or receipt.get('engine_epoch') != profile['engine_epoch']
                or type(now) not in (int, float) or not math.isfinite(now)
                or type(receipt.get('expires_at')) not in (int, float) or not math.isfinite(receipt['expires_at'])
                or not now < receipt['expires_at']):
            raise LabRuntimeRefusal('current_incumbent_la_profile_binding_required')
        return session

    def require_current(self):
        if (self.cancel.is_set() or digest(self.producer) != self.pin
                or digest(self.acquisition_config) != self.configuration_digest):
            raise LabRuntimeRefusal('incumbent_lab_attachment_cancelled_or_changed')
        if self.desk_binding is not None:
            _require_desk_capability(self.desk_capability, self.desk_binding)
        if self.bundled_source is not None and self.bundled_source.binding() != self.join.bundled_binding():
            raise LabRuntimeRefusal('current_bundled_capture_binding_changed')
        if self.person_bridge is not None and self.person_bridge.binding() != {
                'scope': self.scope, 'bridge_policy_digest': digest(self.producer['semantic_policy']['bridge'])}:
            raise LabRuntimeRefusal('fixed_person_bridge_capability_required')
        if self.preflight() is not True:
            raise LabRuntimeRefusal('incumbent_lab_runtime_preflight_unmet')
        return self._current()

    def command_current(self, graph):
        """Bounded local currentness while the sole Journal lock is held.

        Source/Supervisor preflight still owns external health probes. This
        independently checks the actual incumbent/tap and current Store rows;
        it does not use a cached green health result as source authority.
        """
        if (graph is None or self.graph is not graph or self.cancel.is_set()
                or graph.cancel.is_set() or graph.closed or graph.finished or graph.problem is not None
                or digest(self.producer) != self.pin
                or digest(self.acquisition_config) != self.configuration_digest
                or graph.configuration_digest != self.configuration_digest
                or self.producer['version'] not in (5, 6, 8) or not callable(self._command_current_guard)):
            raise LabRuntimeRefusal('current_fixed_command_graph_required')
        if self._command_current_guard() is not True:
            raise LabRuntimeRefusal('current_incumbent_command_binding_required')
        if self.desk_binding is not None:
            _require_desk_capability(self.desk_capability, self.desk_binding)
        self._current()
        return True

    def create(self):
        if not self.lock.acquire(False):
            raise LabRuntimeRefusal('incumbent_lab_attachment_busy')
        try:
            if self.used:
                raise LabRuntimeRefusal('incumbent_lab_attachment_already_used')
            self.require_current()
            if (is_person_policy(self.producer.get('semantic_policy')) and self.person_bridge is None
                    and (self.producer.get('desk_policy') or {}).get('version') in (2, 3, 4)):
                # Refuse a duplicate owner before opening its archive, Journal
                # or local server. Recheck on registration for concurrent creates.
                with self.store.lock:
                    if self.scope['session_id'] in getattr(self.store, '_qualified_desk_person_bridges', {}):
                        raise LabRuntimeRefusal('qualified_desk_person_bridge_owner_already_exists')
            self.used = True
            # Fixed package resolution is shared with the existing managed mount.
            from live_den_mount import _runtime_module
            _runtime_module()
            from home_den_runtime.lab_original_archive import LabOriginalArchive, semantic_archive_binding
            original = self.producer['original_archive']
            semantic_policy = self.producer.get('semantic_policy')
            desk_policy = self.producer.get('desk_policy')
            self.originals = LabOriginalArchive(original['filename'],
                binding={'session_id': self.join.adapter.session['id'], 'method_ref': self.join.method,
                         'model_ref': self.join.model},
                **{k: original[k] for k in ('max_records', 'max_bytes', 'min_free_bytes')},
                **({'semantic_binding': semantic_archive_binding(self.scope, base_semantic_policy(semantic_policy))}
                   if semantic_policy is not None else {}),
                **({'desk_binding': _desk_archive_binding(self.scope, desk_policy, self.join.binding_digest)}
                   if desk_policy is not None else {}),
                **({'stereo_media_resolver': self.desk_capability.resolve_media}
                   if self.producer['version'] in (4, 5, 6, 7, 8) else {}),
                **({'capture_clock_policy': deepcopy(self.join.source['capture_clock'])}
                    if self.producer['version'] in (6, 7, 8) else {}))
            try:
                self.require_current()
                retainer = _CurrentLabOriginals(self, self.originals)
                if self.producer['version'] in (4, 5, 6, 7, 8):
                    self.desk_capability.attach_retainer(retainer)
                desk = (LabLiveDeskAdmission(self.producer['live_config'], policy=desk_policy,
                    revision=self.producer['revision'], resolve_inputs=self.desk_capability.resolve_inputs,
                    original_retainer=retainer, clock=self.clock,
                    **({'stereo_media_resolver': self.desk_capability.resolve_media}
                        if self.producer['version'] in (4, 5, 6, 7, 8) else {}),
                    **({'expected_assessment_binding': _assessment_binding(self.scope, desk_policy, self.join.binding_digest)}
                       if self.producer['version'] == 5 else {}),
                    **({'expected_bundle_binding': self.join.bundled_binding()}
                        if self.producer['version'] in (6, 7, 8) else {})) if desk_policy is not None else None)
                person_bridge = self.person_bridge
                if (is_person_policy(semantic_policy) and person_bridge is None
                        and desk is not None and desk.policy['version'] in (2, 3, 4)):
                    from lab_desk_person_bridge import DeskPersonBridgeSource
                    self.desk_person_bridge_source = DeskPersonBridgeSource(self.scope, semantic_policy['bridge'],
                        desk_admission=desk, original_retainer=_DeskPersonBridgeLedger(self), clock=self.clock)
                    person_bridge = self.desk_person_bridge_source.capability
                semantic = (build_semantic_admission(self._current(), live_config=self.producer['live_config'],
                    revision=self.producer['revision'], policy=semantic_policy, original_retainer=retainer,
                    person_bridge=person_bridge) if semantic_policy is not None else None)
                self.graph = LabLiveAcquisition(self.join, self.store,
                    journal_path=self.producer['journal_path'], journal_grants=self.producer['journal_grants'],
                    publication_token=private_token(self.producer['publication_token_file']),
                    original_retainer=retainer, control_clock=self.control_clock, semantic=semantic,
                    desk=desk, desk_capability=self.desk_capability, bundled_source=self.bundled_source,
                    desk_person_bridge=self.desk_person_bridge_source,
                    clock=self.clock, monotonic=self.monotonic, after_id=self.producer['after_id'],
                    **self.producer['limits'])
                if self.desk_person_bridge_source is not None:
                    source = self.desk_person_bridge_source
                    source.bind(store=self.store, journal=self.graph.journal, grant=self.graph.principal,
                                semantic_admission=semantic)
                    with self.store.lock:
                        owners = getattr(self.store, '_qualified_desk_person_bridges', None)
                        if owners is None:
                            owners = self.store._qualified_desk_person_bridges = {}
                        if self.scope['session_id'] in owners:
                            raise LabRuntimeRefusal('qualified_desk_person_bridge_owner_already_exists')
                        owners[self.scope['session_id']] = source
                return self.graph
            except BaseException:
                if self.graph is None:
                    self.originals.finalize()
                else:
                    # A post-construction bind/registration refusal happens
                    # before this graph is returned and any worker can start.
                    # Use the actual withdrawal/finalization ports; absence of
                    # a recorder remains explicit incomplete evidence.
                    try:
                        withdrawal = self.graph.withdraw('person_bridge_create_failed')
                        finalization = self.graph.finalize('person_bridge_create_failed', drain_receipt={
                            'schema': 'home-acquisition-drain-1', 'version': 1,
                            'session_ref': deepcopy(self.scope),
                            'configuration_digest': self.graph.configuration_digest,
                            'withdrawal_receipt': deepcopy(withdrawal['barrier_receipt']),
                            'recorder_status': 'incomplete', 'worker_status': 'settled',
                            'terminal_cursor': withdrawal['input_cursor'] or 0})
                        self.failed_creation_cleanup = {'withdrawal': withdrawal,
                            'finalization': finalization, 'worker_started': False}
                    except BaseException as cleanup_error:
                        self.failed_creation_cleanup = {'status': 'unconfirmed',
                            'reason': type(cleanup_error).__name__, 'worker_started': False}
                raise
        finally:
            self.lock.release()

    def stop_preparation(self):
        self.cancel.set()
        busy = self.lock.locked()
        if self.desk_capability is not None:
            receipt = self.desk_capability.quiesce()
            busy = (busy or not isinstance(receipt, dict)
                    or set(receipt) != {'status', 'in_flight'}
                    or receipt.get('status') != 'quiescent' or receipt.get('in_flight') is not False)
        return {'status': 'unconfirmed' if busy else 'quiescent', 'inflight': busy}

    def release(self):
        self.stop_preparation()
        if self.lock.locked() or self.graph is not None and not self.graph.finished:
            return {'status': 'unconfirmed', 'reason': 'incumbent_lab_graph_drain_required'}
        # No ownership of the Store, original Lab runner, camera or models.
        return {'status': 'released'}


def compose_lab_supervisor(runtime, composition, *, supervisor_preflight, command_factory=None):
    """Reuse the existing bounded supervisor/worker/archive, with no new routes."""
    if not isinstance(composition, LabRuntimeComposition) or not callable(supervisor_preflight):
        raise LabRuntimeRefusal('fixed_lab_runtime_composition_required')
    session = runtime.get('session', {}) if isinstance(runtime, dict) else {}
    assessed_subset = composition.producer['version'] == 8
    poll_budget = composition.producer['version'] in (7, 8)
    runtime_version = 3 if assessed_subset else 2 if poll_budget else 1
    if composition.producer['version'] == 7 and command_factory is not None:
        raise LabRuntimeRefusal('poll_budget_command_profile_unavailable')
    if poll_budget:
        limits = composition.producer['limits']
        budget_matches = (type(session.get('max_poll_attempts')) is int
            and session['max_poll_attempts'] <= limits['max_poll_attempts']
            and type(session.get('max_capture_originals')) is int
            and session['max_capture_originals'] == limits['max_capture_originals'])
    else:
        budget_matches = session.get('max_cycles', 25) <= composition.producer['limits']['max_cycles']
    if (session.get('session_ref') != composition.scope
            or session.get('configuration_digest') != composition.configuration_digest
            or session.get('mode') not in {'controlled', 'live_shadow_unqualified'}
            or session.get('mode') == 'controlled' and not composition.store.settings.developer_fixture_mode
            or not budget_matches
            or runtime.get('schema') != 'home-live-runtime-config-' + str(runtime_version)
            or type(runtime.get('version')) is not int or runtime['version'] != runtime_version
            or session.get('schema') != 'home-live-session-config-' + str(runtime_version)
            or type(session.get('version')) is not int or session['version'] != runtime_version
            or session.get('max_runtime_s', 121) > composition.producer['limits']['max_runtime_s']
            or runtime.get('worker_config') is None
            or runtime['worker_config'].get('identity') != composition.scope
            or runtime['worker_config'].get('live_manifest') != composition.producer['live_config']['manifest']
            or runtime['worker_config'].get('manifest_ref') != composition.producer['live_config']['manifest_ref']
            or Path(runtime.get('archive_file', '')) in {Path(composition.producer['journal_path']),
                                                        Path(composition.producer['original_archive']['filename'])}):
        raise LabRuntimeRefusal('exact_lab_supervisor_graph_and_worker_binding_required')
    from live_den_mount import _runtime_module
    compose_supervisor, _ = _runtime_module()
    return compose_supervisor(runtime, graph_factory=composition.create, preflight=supervisor_preflight,
        stop_preparation=composition.stop_preparation, release=composition.release,
        command_factory=command_factory)

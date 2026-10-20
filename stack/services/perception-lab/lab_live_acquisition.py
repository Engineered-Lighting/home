"""Finite, read-only incumbent Lab cursor into one existing live journal.

Trusted server composition supplies the Store, frozen join and durable original
retainer. This graph never starts capture/models or supplies qualified geometry.
Its lifecycle matches SessionSupervisor; the borrowed Lab Store remains open.
"""
from copy import deepcopy
from decimal import Decimal
import math
import json
import os
from pathlib import Path
import threading
import time

from lab_live_journal_join import LabLiveJournalJoin, capture_original
from lab_desk_capability import TrustedDeskCapability
from lab_live_desk_admission import LabLiveDeskAdmission
from store import Store


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def utc_now():
    """Match original/control ns conversion with one epoch-second rounding."""
    return float(Decimal(time.time_ns()) / Decimal(1_000_000_000))


class LabLiveAcquisition:
    def __init__(self, join, store, *, journal_path, journal_grants,
                 publication_token, original_retainer, control_clock,
                 clock=utc_now, monotonic=time.monotonic, after_id=0,
                 max_cycles=24, max_runtime_s=120, page_limit=128, semantic=None,
                 desk=None, desk_capability=None, bundled_source=None,
                 max_poll_attempts=None, max_capture_originals=None, desk_person_bridge=None):
        from journal import Journal, digest
        from server import LocalServer
        poll_budget = max_poll_attempts is not None or max_capture_originals is not None
        if (not isinstance(join, LabLiveJournalJoin) or not isinstance(store, Store)
                or not all(callable(port) for port in (original_retainer, control_clock, clock, monotonic))
                or not isinstance(publication_token, str) or not 1 <= len(publication_token) <= 4096
                or type(after_id) is not int or after_id < 0
                or not poll_budget and (type(max_cycles) is not int or not 1 <= max_cycles <= min(24, join.max_events))
                or not _finite(max_runtime_s) or not 0 < max_runtime_s <= 120
                or type(page_limit) is not int or not 1 <= page_limit <= 128):
            raise ValueError('fixed_finite_lab_graph_required')
        if poll_budget and (max_cycles != 24 or type(max_poll_attempts) is not int
                or not 1 <= max_poll_attempts <= 240 or type(max_capture_originals) is not int
                or not 1 <= max_capture_originals <= min(24, join.max_events)):
            raise ValueError('explicit_finite_lab_poll_and_original_budgets_required')
        assessed_subset = join.config['version'] == 13
        if assessed_subset and (not poll_budget or semantic is None or desk is None):
            raise ValueError('assessed_room_requires_bundled_poll_acquisition8')
        self.join, self.store = join, store
        self.session = store.get_session(join.adapter.session['id'])
        if any(self.session.get(k) != value for k, value in join.adapter.session.items()):
            raise ValueError('incumbent_lab_session_binding_mismatch')
        self.media_generation = self.session['generation']
        self.config = {'schema': 'home-incumbent-lab-acquisition-1', 'version': 1,
            'session_ref': deepcopy(join.scope), 'source_id': join.source_id,
            'lab_binding_digest': join.binding_digest, 'media_generation': self.media_generation,
            'limits': {'max_cycles': max_cycles, 'max_runtime_s': max_runtime_s, 'page_limit': page_limit},
            'after_id': after_id}
        self.semantic = semantic
        if semantic is not None:
            from lab_live_semantic_admission import LabLiveSemanticAdmission
            from lab_person_semantic_admission import LabPersonSemanticAdmission
            expected_semantic = ((4 if assessed_subset else 3) if desk is not None
                                 else 3 if isinstance(semantic,LabPersonSemanticAdmission) else 2)
            if (not isinstance(semantic, LabLiveSemanticAdmission) or semantic.config != join.config
                    or semantic.scope != join.scope or semantic.session != self.session
                    or semantic.policy.get('version') != expected_semantic
                    or join.source_id in {semantic.policy[k]['source_id'] for k in ('ha', 'jev')}
                    or any(join.config['manifest']['sources'][semantic.policy[k]['source_id']]['control_clock']
                           != join.source['control_clock'] for k in ('ha', 'jev'))):
                raise ValueError('fixed_semantic_graph_and_control_clock_required')
            self.config.update(schema='home-incumbent-lab-acquisition-2', version=2,
                               semantic_binding_digest=semantic.binding_digest)
        self.desk, self.desk_capability = desk, desk_capability
        self.desk_person_bridge = desk_person_bridge
        if desk_person_bridge is not None:
            from lab_desk_person_bridge import DeskPersonBridgeSource
            if (type(desk_person_bridge) is not DeskPersonBridgeSource or semantic is None or desk is None
                    or desk_person_bridge.capability is not semantic.person_bridge
                    or desk_person_bridge.desk is not desk or desk_person_bridge.scope != join.scope):
                raise ValueError('same_fixed_qualified_desk_person_bridge_required')
        self.desk_binding = None
        if desk is not None:
            if not isinstance(desk, LabLiveDeskAdmission):
                raise ValueError('fixed_qualified_desk_graph_capability_required')
            self.desk_binding = {'scope': deepcopy(join.scope), 'desk_policy_digest': digest(desk.policy),
                                 'lab_binding_digest': join.binding_digest}
            if (not isinstance(desk, LabLiveDeskAdmission) or not isinstance(desk_capability, TrustedDeskCapability)
                    or desk.config != join.config or desk.scope != join.scope or semantic is None
                    or desk.policy['capture_source_id'] != join.source_id
                    or desk.policy['source_id'] in {join.source_id, *semantic.source_ids}
                    or getattr(join, 'root_lineage', None) != 'persisted-observation-record-1'
                    or semantic.policy.get('root_lineage') != 'persisted-observation-record-1'
                    or desk_capability.binding() != self.desk_binding):
                raise ValueError('fixed_qualified_desk_graph_capability_required')
            version = {1: 3, 2: 4, 3: 5, 4: 6}[desk.policy['version']]
            self.config.update(schema='home-incumbent-lab-acquisition-' + str(version), version=version,
                               desk_binding_digest=digest(self.desk_binding))
        elif desk_capability is not None:
            raise ValueError('desk_capability_requires_desk_source')
        self.bundled_source = bundled_source
        if join.bundle_input_policy_sha256 is not None:
            from lab_bundled_rgb_stereo import BundledRgbStereoSource
            if (type(bundled_source) is not BundledRgbStereoSource
                    or bundled_source.store is not store or desk is None
                    or desk.policy['version'] != 4 or self.config['version'] != 6
                    or bundled_source.binding() != join.bundled_binding()
                    or getattr(desk_capability.anchor_source, 'bundled_rgb_source', None) is not bundled_source):
                raise ValueError('fixed_bundled_source6_capability_required')
        elif bundled_source is not None:
            raise ValueError('bundled_source_requires_explicit_capture_family')
        self.poll_budget = poll_budget
        if poll_budget:
            if (self.config['version'] != 6 or join.bundle_input_policy_sha256 is None
                    or bundled_source is None):
                raise ValueError('poll_budget_requires_validated_bundled_source6')
            version = 8 if assessed_subset else 7
            self.config.update(schema='home-incumbent-lab-acquisition-' + str(version), version=version,
                limits={'max_poll_attempts': max_poll_attempts, 'max_capture_originals': max_capture_originals,
                        'max_runtime_s': max_runtime_s, 'page_limit': page_limit})
        self.configuration_digest = digest(self.config)
        self.clock, self.monotonic, self.control_clock = clock, monotonic, control_clock
        self.original_retainer, self.after_id = original_retainer, after_id
        self.started = monotonic()
        if not _finite(self.started):
            raise ValueError('lab_graph_monotonic_clock_required')
        self.turn, self.cleanup_lock, self.cancel = threading.Lock(), threading.Lock(), threading.Event()
        self.closed, self.finished, self.problem = False, False, None
        self.pending_original, self.barrier_event, self.supervised_withdrawal = None, None, None
        self.pending_capture = None
        self.pending_semantic, self.source_barriers, self.source_barrier_acks = None, None, []
        self.pending_desk = None
        self.cleanup_result = None
        self.shutdown_owner, self.assessment_recorder = 'supervisor', None
        self.counters = dict.fromkeys(('cycles', 'admitted', 'expired', 'ignored', 'refusals'), 0)
        if poll_budget:
            self.counters.update(poll_attempts=0, capture_originals=0)
        if self.desk is not None:
            self.counters.update(desk_admitted=0, desk_unavailable=0)
        self.journal = self.server = None
        if assessed_subset:
            from journal import Grant
            from live_ingress import validate_grants
            validate_grants([Grant.from_dict(g, profile='home-live-shadow-1')
                             for g in journal_grants], join.config)
        path = Path(journal_path)
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(handle)
        try:
            self.journal = Journal(path, grants=journal_grants, profile='home-live-shadow-1',
                live_config=join.config, clock=clock, live_receipt_resolver=self._resolve_original,
                **({'live_wall_clock': clock, 'live_monotonic': monotonic}
                   if join.config['schema'] in {'home-live-ingress-config-7', 'home-live-ingress-config-9',
                                              'home-live-ingress-config-10', 'home-live-ingress-config-11', 'home-live-ingress-config-12',
                                              'home-live-ingress-config-13'} else {}))
            self.principal = self.journal.authenticate(publication_token)
            self.server = LocalServer(('127.0.0.1', 0), self.journal)
            self.address = self.server.server_address
            self.server_thread = threading.Thread(target=self.server.serve_forever,
                kwargs={'poll_interval': .02}, name='home-incumbent-lab-journal', daemon=True)
            self.server_thread.start()
        except BaseException:
            if self.server is not None:
                self.server.server_close()
            if self.journal is not None:
                self.journal.close()
            raise

    def _resolve_original(self, store_id, receipt_id):
        records = []
        for owner in (self.join, self.semantic, self.desk):
            if owner is None:
                continue
            # The surveyed resolver intentionally rejects a foreign store;
            # dispatch its fixed identity before invoking it.
            if owner is self.desk and store_id != self.desk.policy['receipt_store_id']:
                continue
            try:
                records.append(owner.resolve_receipt(store_id, receipt_id))
            except KeyError:
                pass
        if len(records) != 1:
            raise KeyError('one_fixed_original_owner_required')
        return records[0]

    def _capture_descriptor(self, record, result):
        """Pass only an actual current capture and already retained originals."""
        from journal import digest
        receipt = result['receipts'][0]
        keys = ('stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest')
        ref = {key: deepcopy(receipt[key]) for key in keys} | {'component_id': None}
        rows = self.journal.snapshot(self.principal, self.join.scope['site_id'], self.join.scope['room_id'])['items']
        selected = [p for p in rows if all(p.get(key) == ref[key] for key in keys)]
        if len(selected) != 1:
            raise ValueError('admitted_capture_head_unavailable')
        publication = selected[0]
        body = publication['payload']
        original_ref = body['original_receipt_refs'][0]
        original = self.join.original_receipt(original_ref['store_id'], original_ref['receipt_id'])
        if (original['event'] != record['event'] or original['frame_metadata'] != record['frame_metadata']
                or publication['payload_digest'] != digest(body)):
            raise ValueError('admitted_capture_original_mismatch')
        return {'schema': 'home-lab-admitted-capture-1', 'version': 1,
            'scope': deepcopy(self.join.scope), 'lab_binding_digest': self.join.binding_digest,
            'publication': deepcopy(publication), 'publication_ref': ref, 'original': original,
            'original_record': self.join.resolve_receipt(original_ref['store_id'], original_ref['receipt_id']),
            'frame_metadata': deepcopy(record['frame_metadata']),
            'source_position': deepcopy(body['head']['position']), 'source_generation': body['source_generation'],
            'model_generation': self.join.adapter.generation, 'media_generation': self.media_generation}

    def _publish_desk(self, *, settling=False):
        pending = self.pending_desk
        if self.desk_capability.binding() != self.desk_binding:
            raise ValueError('desk_capability_binding_changed')
        if pending['selection'] is None:
            if settling or self.cancel.is_set():
                raise ValueError('desk_selection_cancelled_before_admission')
            selected = self.desk_capability.select(deepcopy(pending['descriptor']))
            if self.cancel.is_set():
                return {'status': 'stopped', 'reason': 'lab_graph_quiesced'}
            if (self.config['version'] in (5, 6, 7, 8) and isinstance(selected, dict)
                    and selected.get('status') == 'pending'):
                if set(selected) != {'status', 'reason'}:
                    raise ValueError('closed_desk_selection_required')
                from journal import identifier
                identifier(selected['reason'], 'desk_pending_reason')
                # The provider fences the actual current Store and original
                # lease. Poll the same descriptor without refreshing its first
                # receipt, fetching another capture, or advancing this cursor.
                return {'status': 'desk_pending', 'reason': selected['reason'],
                        'after_id': self.after_id, 'capture_receipts': deepcopy(pending['capture_receipts']),
                        'operational_geometry': False, 'current': False}
            if (not isinstance(selected, dict) or selected.get('status') not in {'selected', 'unavailable'}
                    or set(selected) != ({'status', 'anchor_ref'} if selected.get('status') == 'selected'
                                        else {'status', 'reason'})):
                raise ValueError('closed_desk_selection_required')
            if selected['status'] == 'unavailable':
                from journal import identifier
                identifier(selected['reason'], 'desk_unavailable_reason')
                pending['selection'] = deepcopy(selected)
            else:
                # The publisher validates the exact full original ref and its
                # independently qualified current capture binding.
                pending['selection'] = deepcopy(selected)
        selected = pending['selection']
        if selected['status'] == 'selected':
            result = self.desk.consume(selected['anchor_ref'], received_at=pending['received_at'],
                journal=self.journal, grant=self.principal)
            if result['status'] not in {'admitted', 'unavailable'}:
                raise ValueError('desk_admission_terminal_result_required')
        else:
            result = {'status': 'unavailable', 'reason': selected['reason'], 'receipt': None}
        self.after_id = pending['event_id']
        self.pending_desk = None
        self.counters['desk_admitted' if result['status'] == 'admitted' else 'desk_unavailable'] += 1
        return {'status': 'admitted_desk' if result['status'] == 'admitted' else 'desk_unavailable',
                'after_id': self.after_id, 'desk': result, 'capture_receipts': deepcopy(pending['capture_receipts']),
                'operational_geometry': result['status'] == 'admitted', 'current': result['status'] == 'admitted'}

    def _semantic_context(self, event):
        """Read bounded original dependencies, including rows before this cursor."""
        if event['kind'] == 'ha.observation':
            return [deepcopy(event)]
        person = event['payload'].get('person_context')
        if person is not None:
            from lab_person_semantic_admission import LabPersonSemanticAdmission
            if not isinstance(self.semantic, LabPersonSemanticAdmission):
                raise ValueError('explicit_person_semantic_graph_required')
            ids = [person.get('visual_event_id'),person.get('semantics_event_id')] if isinstance(person,dict) else []
            if len(ids) != 2 or any(type(i) is not int or i < 1 for i in ids) or len(set(ids)) != 2:
                raise ValueError('bounded_original_person_dependencies_required')
            with self.store.lock:
                rows=self.store.db.execute('SELECT * FROM events WHERE session_id=? AND id IN (?,?)',
                    (self.session['id'],*ids)).fetchall()
                chain=self.store.db.execute("SELECT * FROM events WHERE session_id=? AND id<? AND kind IN ('reasoning.request','reasoning.delivery') "
                    "AND json_extract(payload,'$.request_id')=? ORDER BY id DESC LIMIT 128",
                    (self.session['id'],event['id'],event['payload'].get('request_id'))).fetchall()
            records=[{**dict(row),'payload':json.loads(row['payload'])} for row in [*rows,*chain]]
            records.append(deepcopy(event))
            return sorted(records,key=lambda row:row['id'])
        provenance = event['payload'].get('provenance', {})
        evidence, timeline = provenance.get('evidence_ids', []), provenance.get('timeline_ids', [])
        if not isinstance(evidence, list) or not isinstance(timeline, list):
            raise ValueError('bounded_original_semantic_dependencies_required')
        ids = evidence + timeline
        if (len(ids) > 80
                or any(not isinstance(i, str) or not i.isdecimal() for i in ids)):
            raise ValueError('bounded_original_semantic_dependencies_required')
        with self.store.lock:
            chain = self.store.db.execute("SELECT * FROM events WHERE session_id=? AND id<? "
                "AND kind IN ('reasoning.request','reasoning.delivery') ORDER BY id DESC LIMIT 128",
                (self.session['id'], event['id'])).fetchall()
            records = [{**dict(row), 'payload': json.loads(row['payload'])} for row in chain]
            records = [row for row in records if row['payload'].get('request_id') == provenance.get('request_id')]
            wanted = sorted({int(i) for i in ids})
            if wanted:
                rows = self.store.db.execute('SELECT * FROM events WHERE session_id=? AND id IN ('
                    + ','.join('?' for _ in wanted) + ')', (self.session['id'], *wanted)).fetchall()
                records.extend({**dict(row), 'payload': json.loads(row['payload'])} for row in rows)
        records.append(deepcopy(event))
        by_id = {row['id']: row for row in records}
        if len(by_id) > 128:
            raise ValueError('bounded_original_semantic_dependencies_required')
        return [by_id[key] for key in sorted(by_id)]

    def _publish_semantic(self):
        pending = self.pending_semantic
        from lab_live_semantic_admission import SemanticRefusal
        from live_pc_support import PCSupportRefusal
        terminal = {'selected_work_parent_unavailable', 'original_jev_chain_binding_mismatch',
            'original_jev_clock_binding_mismatch', 'complete_jev_original_evidence_required',
            'jev_original_cutoff_association_unavailable', 'jev_consumed_visual_association_mismatch',
            'jev_future_evidence_not_causal', 'jev_original_visual_clock_required',
            'jev_pc_packet_original_mismatch', 'exact_jev_request_delivery_required',
            'semantic_original_progression_regressed', 'semantic_original_lease_invalid',
            'original_semantic_clock_required', 'original_configured_pc_snapshot_required',
            'jev_additional_source_contract_not_closed', 'person_bridge_retired_before_append',
            'person_request_binding_changed', 'person_output_chain_mismatch', 'person_output_provenance_mismatch',
            'person_output_answers_mismatch', 'person_output_membership_mismatch', 'person_output_original_lease_invalid',
            'person_context_originals_mismatch', 'person_original_payload_mismatch', 'person_original_lease_invalid',
            'invalid_person_judgment', 'exact_person_request_delivery_required'}
        try:
            if pending['event']['kind'] == 'ha.observation':
                result = self.semantic.publish_pc(pending['events'], received_at=pending['received_at'],
                    journal=self.journal, grant=self.principal, work_parent_ref=pending['work_parent_ref'])
            elif pending['event']['kind'] == 'reasoning.person_output':
                result = self.semantic.publish_person_jev(pending['events'], output_id=pending['event']['id'],
                    received_at=pending['received_at'], journal=self.journal, grant=self.principal)
            else:
                result = self.semantic.publish_jev(pending['events'], output_id=pending['event']['id'],
                    received_at=pending['received_at'], journal=self.journal, grant=self.principal)
        except (SemanticRefusal, PCSupportRefusal) as error:
            # Expected terminal evidence failure cannot wedge unrelated later
            # camera/device rows. Never advance over retention/ACK uncertainty.
            if self.semantic.pending is not None or isinstance(error, SemanticRefusal) and str(error) not in terminal:
                raise
            self.counters['refusals'] += 1
            result = {'status': 'refused', 'reason': str(error), 'receipts': [], 'supports_engagement': False}
        self.after_id = pending['event']['id']
        self.pending_semantic = None
        if result['receipts']:
            self.counters['admitted'] += 1
        elif result['status'] == 'expired':
            self.counters['expired'] += 1
        return {**result, 'after_id': self.after_id, 'operational_geometry': False}

    def _frame(self, event):
        payload, frames = event['payload'], []
        ids = payload.get('media_ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 8 or len(set(ids)) != len(ids):
            raise ValueError('original_lab_rgb_media_required')
        for media_id in ids:
            from lab_person_semantic_admission import LabPersonSemanticAdmission
            if isinstance(self.semantic, LabPersonSemanticAdmission):
                # The explicit person successor may borrow this exact session's
                # existing RAM owner. No capture, TTL or durable media is added.
                from apartment_viewing_source import registered_media
                media = registered_media(self.store,self.session['id'],media_id,self.media_generation)
            else:
                media = self.store.get_media(self.session['id'], media_id, generation=self.media_generation)
            frame = media['metadata']
            expected_mime = 'image/png' if self.bundled_source is not None else 'image/jpeg'
            if (media['mime_type'] == expected_mime and frame.get('stream') == 'rgb'
                    and frame.get('frame_id') == payload.get('frame_id')
                    and frame.get('capture_epoch') == payload.get('capture_epoch')):
                frames.append(deepcopy(frame))
        if len(frames) != 1:
            raise ValueError('one_exact_original_lab_rgb_media_required')
        return frames[0]

    def _retain_pending_capture_media(self):
        """Settle only the frozen source7 original; an unknown ACK retains it."""
        pending = self.pending_capture
        media = self.desk_capability.media_archive
        if media.binding_digest != pending['binding_digest']:
            raise ValueError('frozen_bundled_media_binding_changed')
        retained = media.retain_media(deepcopy(pending['media_refs']))
        if retained != {'status': 'retained', 'binding_digest': pending['binding_digest'],
                        'media_refs': pending['media_refs']}:
            raise ValueError('durable_bundled_capture_media_required')
        if self.cancel.is_set():
            return False
        self.pending_original = deepcopy(pending['record'])
        self.pending_capture = None
        return True

    def step(self):
        if not self.turn.acquire(False):
            return {'status': 'blocked', 'reason': 'lab_graph_busy'}
        try:
            if self.closed or self.cancel.is_set():
                return {'status': 'stopped', 'reason': 'lab_graph_quiesced'}
            if self.journal._recorder is None or self.journal._recorder_failed:
                raise ValueError('recorder_ready_before_lab_admission_required')
            limits = self.config['limits']
            elapsed = self.monotonic() - self.started
            if not _finite(elapsed) or elapsed < 0:
                raise ValueError('lab_graph_monotonic_clock_invalid')
            if self.poll_budget:
                if elapsed >= limits['max_runtime_s']:
                    return {'status': 'stopped', 'reason': 'lab_graph_budget_exhausted'}
                if self.counters['poll_attempts'] >= limits['max_poll_attempts']:
                    return {'status': 'stopped', 'reason': 'lab_poll_budget_exhausted'}
                # Only an already reserved original/semantic/desk operation may
                # settle after its capture capacity is spent. Every retry still
                # consumes a poll and is fenced by the same wall/Stop checks.
                if (self.counters['capture_originals'] >= limits['max_capture_originals']
                        and self.pending_capture is None and self.pending_original is None and self.pending_semantic is None
                        and self.pending_desk is None):
                    return {'status': 'stopped', 'reason': 'lab_capture_original_budget_exhausted'}
                self.counters['poll_attempts'] += 1
            elif self.counters['cycles'] >= limits['max_cycles'] or elapsed >= limits['max_runtime_s']:
                return {'status': 'stopped', 'reason': 'lab_graph_budget_exhausted'}
            self.counters['cycles'] += 1
            session = self.store.get_session(self.session['id'])
            if (session['generation'] != self.media_generation
                    or any(session.get(k) != value for k, value in self.join.adapter.session.items())):
                raise ValueError('incumbent_lab_session_binding_changed')
            if self.pending_semantic is not None:
                return self._publish_semantic()
            if self.pending_desk is not None:
                return self._publish_desk()
            record = self.pending_original
            if self.pending_capture is not None:
                if not self._retain_pending_capture_media():
                    return {'status': 'stopped', 'reason': 'lab_graph_quiesced'}
                record = self.pending_original
            if record is None:
                for event in self.store.list_events(self.session['id'], self.after_id, limits['page_limit']):
                    if self.cancel.is_set():
                        return {'status': 'stopped', 'reason': 'lab_graph_quiesced'}
                    payload = event.get('payload', {})
                    if self.semantic is not None:
                        kind = event['kind']
                        relevant = (kind == 'ha.observation'
                            and (payload.get('quality') or {}).get('source_id') == self.semantic.policy['ha_source_id']
                            or kind in {'reasoning.request', 'reasoning.output', 'reasoning.person_output'}
                            and payload.get('arm') == self.semantic.policy['arm']
                            and payload.get('generation') == self.semantic.policy['generation'])
                        if relevant:
                            received_at = self.clock()
                            parent = self.semantic.current_work_parent(journal=self.journal,
                                grant=self.principal, received_at=received_at)
                            if kind == 'reasoning.request':
                                if payload.get('person_context') is not None:
                                    from lab_person_semantic_admission import LabPersonSemanticAdmission
                                    if isinstance(self.semantic, LabPersonSemanticAdmission):
                                        self.semantic.remember_person_request(event,events=self._semantic_context(event),journal=self.journal,
                                            grant=self.principal,received_at=received_at)
                                    # Historical global policy never consumes or attributes the new person request.
                                else:
                                    self.semantic.remember_request(event, journal=self.journal,
                                        grant=self.principal, received_at=received_at, work_parent_ref=parent)
                                self.after_id = event['id']
                                continue
                            if kind == 'reasoning.person_output':
                                from lab_person_semantic_admission import LabPersonSemanticAdmission
                                if not isinstance(self.semantic, LabPersonSemanticAdmission):
                                    self.after_id=event['id'];self.counters['ignored']+=1
                                    continue
                            self.pending_semantic = {'event': deepcopy(event), 'events': self._semantic_context(event),
                                'received_at': received_at, 'work_parent_ref': deepcopy(parent)}
                            return self._publish_semantic()
                    if (event['kind'] != 'perception.output' or payload.get('arm') != self.join.adapter.arm
                            or payload.get('generation') != self.join.adapter.generation):
                        self.after_id = event['id']
                        self.counters['ignored'] += 1
                        continue
                    received_at = self.clock()
                    if not _finite(received_at) or not _finite(payload.get('valid_until')):
                        raise ValueError('original_lab_expiry_required')
                    if received_at >= payload['valid_until']:
                        self.after_id = event['id']
                        self.counters['expired'] += 1
                        continue
                    frame = self._frame(event)
                    if not _finite(frame.get('valid_until')):
                        raise ValueError('original_lab_rgb_expiry_required')
                    if received_at >= min(payload['valid_until'], frame['valid_until']):
                        self.after_id = event['id']
                        self.counters['expired'] += 1
                        continue
                    bundled_originals = None
                    if self.bundled_source is not None:
                        selected = self.bundled_source.capture(event, frame, received_at)
                        if not isinstance(selected, dict) or selected.get('status') not in {
                                'selected', 'pending', 'unavailable'}:
                            raise ValueError('closed_bundled_capture_result_required')
                        if selected['status'] != 'selected':
                            if set(selected) != {'status', 'reason'}:
                                raise ValueError('closed_bundled_capture_result_required')
                            if selected['status'] == 'unavailable':
                                self.after_id = event['id']
                            # Pending originals preserve the exact input cursor.
                            return {'status': selected['status'], 'reason': selected['reason'],
                                    'after_id': self.after_id, 'operational_geometry': False}
                        if set(selected) != {'status', 'rows', 'bundled_originals', 'clock_original'}:
                            raise ValueError('closed_bundled_capture_result_required')
                        from lab_bundled_rgb_stereo import ROLES
                        from home_den_runtime.lab_stereo_media import media_ref
                        rows = selected['rows']
                        if (set(rows) != ROLES - {'mapped_depth'}
                                or selected['clock_original'] != selected['bundled_originals']['clock_original']):
                            raise ValueError('exact_seven_bundled_capture_originals_required')
                        refs = [media_ref(rows[role][0]) for role in sorted(rows)]
                        media = self.desk_capability.media_archive
                        if self.poll_budget:
                            # Freeze the genuine full-read receipt and exact
                            # original BEFORE any binary durability effect.
                            # Neither a lost ACK nor retry reselects/reclocks it.
                            received_at = self.clock()
                            if (not _finite(received_at)
                                    or received_at < selected['clock_original']['first_consumer_received_at']):
                                raise ValueError('actual_bundled_capture_first_receipt_required')
                            record = capture_original(event, frame, received_at, self.join.method,
                                self.join.model, bundled_originals=deepcopy(selected['bundled_originals']))
                            self.pending_capture = {'record': record, 'media_refs': deepcopy(refs),
                                                    'binding_digest': media.binding_digest}
                            self.counters['capture_originals'] += 1
                            if not self._retain_pending_capture_media():
                                return {'status': 'stopped', 'reason': 'lab_graph_quiesced'}
                            record = self.pending_original
                            break
                        retained_media = media.retain_media(refs)
                        if retained_media != {'status': 'retained',
                                'binding_digest': media.binding_digest, 'media_refs': refs}:
                            raise ValueError('durable_bundled_capture_media_required')
                        if self.cancel.is_set():
                            return {'status': 'stopped', 'reason': 'lab_graph_quiesced'}
                        bundled_originals = deepcopy(selected['bundled_originals'])
                        received_at = self.clock()
                        if (not _finite(received_at)
                                or received_at < selected['clock_original']['first_consumer_received_at']):
                            raise ValueError('actual_bundled_capture_first_receipt_required')
                    record = capture_original(event, frame, received_at, self.join.method,
                        self.join.model, bundled_originals=bundled_originals)
                    self.pending_original = record
                    break
            if record is None:
                return {'status': 'idle', 'after_id': self.after_id}
            from live_capture import _original_digest
            if self.cancel.is_set():
                return {'status': 'stopped', 'reason': 'lab_graph_quiesced'}
            retained = self.original_retainer(deepcopy(record))
            if retained != {'status': 'retained', 'digest': _original_digest(record)}:
                raise ValueError('durable_original_lab_receipt_required')
            if self.cancel.is_set():
                return {'status': 'stopped', 'reason': 'lab_graph_quiesced'}
            # fsync latency cannot extend a historical observation's lease.
            if self.join.pending is None and self.clock() >= min(record['event']['payload']['valid_until'],
                                                                record['frame_metadata']['valid_until']):
                self.after_id = record['event']['id']
                self.pending_original = None
                self.counters['expired'] += 1
                return {'status': 'expired', 'after_id': self.after_id}
            result = self.join.publish(record['event'], frame_metadata=record['frame_metadata'],
                received_at=record['first_consumer_received_at'], journal=self.journal, grant=self.principal,
                **({'bundled_originals': record['bundled_originals'],
                    'bundled_media_resolver': self.desk_capability.resolve_media}
                   if self.bundled_source is not None else {}))
            if self.desk is not None and result['status'] == 'admitted_unqualified':
                self.pending_desk = {'descriptor': self._capture_descriptor(record, result),
                    'selection': None, 'event_id': record['event']['id'],
                    'received_at': self.clock(), 'capture_receipts': deepcopy(result['receipts'])}
                self.pending_original = None
                self.counters['admitted'] += 1
                return self._publish_desk()
            self.after_id = record['event']['id']
            self.pending_original = None
            if result['status'] == 'admitted_unqualified':
                self.counters['admitted'] += 1
            return {'status': result['status'], 'after_id': self.after_id,
                    'receipts': result['receipts'], 'operational_geometry': False}
        except Exception as error:
            self.problem = type(error).__name__
            self.counters['refusals'] += 1
            return {'status': 'failed', 'reason': str(error)}
        finally:
            self.turn.release()

    def status(self):
        return {'schema': 'home-acquisition-status-1', 'version': 1,
            'session_ref': deepcopy(self.join.scope), 'source_id': self.join.source_id,
            'configuration_digest': self.configuration_digest, 'counters': dict(self.counters),
            'stage_presence': {'spatial': self.desk is not None, 'interaction': False},
            'closed': self.closed, 'inflight': self.turn.locked(), 'after_id': self.after_id}

    def readiness(self):
        busy = self.turn.locked()
        return {'schema': 'home-acquisition-readiness-1', 'version': 1,
            'session_ref': deepcopy(self.join.scope), 'source_id': self.join.source_id,
            'configuration_digest': self.configuration_digest, 'journal_address': list(self.address),
            'state': ('finalized' if self.finished else 'withdrawn' if self.supervised_withdrawal
                      else 'quiescing' if self.closed else 'busy' if busy else 'idle'),
            'inflight': busy, 'shutdown_owner': 'supervisor'}

    def quiesce(self, reason='operator_stop'):
        from journal import identifier
        identifier(reason, 'quiesce_reason')
        self.closed = True
        self.cancel.set()
        bridge = getattr(self, 'desk_person_bridge', None)
        bridge_busy = False
        if bridge is not None:
            receipt = bridge.quiesce()
            # A pending passive witness grants no authority after quiesce.
            # Only a still-active retention call prevents lifecycle drain.
            bridge_busy = receipt.get('in_flight') is True
            # Stop must not wait behind Store retention/fsync just to remove
            # a registry entry. The quiesced owner is already inert; a later
            # drain/quiesce retries removal once the same lock is available.
            if self.store.lock.acquire(False):
                try:
                    owners = getattr(self.store, '_qualified_desk_person_bridges', {})
                    if owners.get(self.session['id']) is bridge:
                        del owners[self.session['id']]
                finally:
                    self.store.lock.release()
            else:
                bridge_busy = True
        if self.semantic is not None:
            self.semantic.quiesce()
        if self.desk is not None:
            self.desk.quiesce()
        busy = self.turn.locked() or bridge_busy
        if self.desk_capability is not None:
            receipt = self.desk_capability.quiesce()
            busy = (busy or not isinstance(receipt, dict) or set(receipt) != {'status', 'in_flight'}
                    or receipt.get('status') != 'quiescent' or receipt.get('in_flight') is not False)
        return {'status': 'unconfirmed' if busy else 'quiescent', 'reason': reason,
                'inflight': busy, 'buffers_retained': not self.finished}

    def _source_barrier(self, source_id):
        from journal import digest
        from live_ingress_validation import validate_clock
        source = self.join.config['manifest']['sources'][source_id]
        control = self.control_clock()
        asserted, clock = control['observed_at'], control['clock']
        validate_clock(clock, source['control_clock'], asserted)
        available = self.clock()
        if not _finite(available) or asserted > available:
            raise ValueError('commissioned_control_clock_unavailable')
        with self.journal.lock:
            generation = self.journal.db.execute('SELECT generation FROM live_sources WHERE source_id=?',
                                                (source_id,)).fetchone()[0]
            authority = self.journal.revocation_generation(self.join.scope['site_id'], self.join.scope['room_id'])
        body = {'schema': 'home-live-fact-1', 'version': 1,
            'id': 'lab-barrier:' + digest([self.join.scope, source_id, generation, asserted]),
            **deepcopy(self.join.scope), 'manifest_ref': deepcopy(self.join.config['manifest_ref']),
            'source_id': source_id, 'source_generation': generation + 1,
            'status': 'withdrawn', 'reason': 'operator_withdrawal', 'observed_at': asserted,
            'available_at': available, 'valid_until': available + 1,
            'control': {'kind': 'source_barrier', 'asserted_at': asserted, 'clock': deepcopy(clock),
                'expected_source_generation': generation, 'next_source_generation': generation + 1,
                'expected_authority_generation': authority, 'retired_refs': []}}
        return {'schema': 'home-context-event-1', 'schema_version': '1.0', 'id': body['id'],
            'producer_id': source['producer_id'], 'stream_epoch': source['producer_epoch'],
            **deepcopy(self.join.scope), **{k: body[k] for k in ('observed_at', 'available_at', 'valid_until')},
            'payload_type': body['schema'], 'payload_version': '1.0', 'revision': self.join.revision, 'payload': body}

    def withdraw(self, reason='operator_stop'):
        if self.quiesce(reason)['inflight'] or not self.cleanup_lock.acquire(False):
            return {'status': 'unconfirmed', 'withdrawal': 'not_recorded', 'barrier_receipt': None,
                    'input_cursor': None, 'reason': 'lab_graph_busy'}
        try:
            if self.supervised_withdrawal and self.supervised_withdrawal['withdrawal'] == 'recorded':
                return deepcopy(self.supervised_withdrawal)
            if self.pending_desk is not None:
                if self.pending_desk['selection'] is not None and self.pending_desk['selection']['status'] == 'selected':
                    # The publisher's quiesced path can recover a durable ACK;
                    # it independently prohibits any FIRST append after Stop.
                    self._publish_desk(settling=True)
                elif self.desk.pending is None:
                    # Selection never reached the publisher, so no desk effect
                    # exists to settle. The retained capture remains causal.
                    self.pending_desk = None
            if (self.join.pending is not None or self.semantic is not None and self.semantic.pending is not None
                    or self.desk is not None and self.desk.pending is not None):
                raise ValueError('lab_publication_ack_unconfirmed')
            if self.journal._recorder is None or self.journal._recorder_failed:
                raise ValueError('recorder_ready_before_withdrawal_required')
            sources = [self.join.source_id]
            if self.desk is not None:
                sources.extend(self.desk.source_ids)
            if self.semantic is not None:
                sources.extend(self.semantic.policy[k]['source_id'] for k in ('ha', 'jev'))
            if self.source_barriers is None:
                self.source_barriers = []
            for index, source_id in enumerate(sources):
                if index < len(self.source_barrier_acks):
                    continue
                if index == len(self.source_barriers):
                    # Each accepted barrier advances room authority. Freeze the
                    # next event only after the preceding ACK, and retain that
                    # exact event across a lost ACK instead of rereading its CAS.
                    self.source_barriers.append(self._source_barrier(source_id))
                    self.barrier_event = self.source_barriers[0]
                self.source_barrier_acks.append(self.journal.append_event(deepcopy(self.source_barriers[index]), self.principal))
            ack = self.source_barrier_acks[-1]
            self.supervised_withdrawal = {'status': 'withdrawn', 'withdrawal': 'recorded',
                'barrier_receipt': deepcopy(ack), 'input_cursor': ack['cursor'], 'reason': reason}
        except Exception as error:
            self.problem = type(error).__name__
            self.supervised_withdrawal = {'status': 'unconfirmed', 'withdrawal': 'not_recorded',
                'barrier_receipt': None, 'input_cursor': None, 'reason': str(error)}
        finally:
            self.cleanup_lock.release()
        return deepcopy(self.supervised_withdrawal)

    def finalize(self, reason='operator_stop', *, drain_receipt=None):
        from journal import identifier
        identifier(reason, 'finalize_reason')
        if self.finished:
            return deepcopy(self.cleanup_result)
        withdrawal = self.supervised_withdrawal
        keys = {'schema', 'version', 'session_ref', 'configuration_digest', 'withdrawal_receipt',
                'recorder_status', 'worker_status', 'terminal_cursor'}
        if (not isinstance(drain_receipt, dict) or set(drain_receipt) != keys or withdrawal is None
                or drain_receipt['schema'] != 'home-acquisition-drain-1' or type(drain_receipt['version']) is not int
                or drain_receipt['version'] != 1 or drain_receipt['session_ref'] != self.join.scope
                or drain_receipt['configuration_digest'] != self.configuration_digest
                or drain_receipt['withdrawal_receipt'] != withdrawal['barrier_receipt']
                or drain_receipt['recorder_status'] not in {'drained', 'incomplete'}
                or drain_receipt['worker_status'] not in {'settled', 'unconfirmed'}
                or type(drain_receipt['terminal_cursor']) is not int or drain_receipt['terminal_cursor'] < 0
                or withdrawal['input_cursor'] is not None and drain_receipt['terminal_cursor'] < withdrawal['input_cursor']):
            return {'status': 'refused', 'reason': 'bound_terminal_drain_required', 'buffers_cleared': False}
        if self.quiesce(reason)['inflight'] or not self.cleanup_lock.acquire(False):
            return {'status': 'unconfirmed', 'reason': 'lab_graph_busy', 'buffers_cleared': False}
        try:
            original_status = {'status': 'unconfirmed'}
            try:
                finalize_originals = getattr(self.original_retainer, 'finalize', None)
                if callable(finalize_originals):
                    original_status = finalize_originals()
            except Exception:
                original_status = {'status': 'incomplete'}
            confirmed = (self.problem is None and withdrawal['withdrawal'] == 'recorded'
                and (not self.poll_budget or self.pending_capture is None and self.pending_original is None
                     and self.pending_semantic is None and self.pending_desk is None)
                and drain_receipt['recorder_status'] == 'drained' and drain_receipt['worker_status'] == 'settled'
                and isinstance(original_status, dict) and original_status.get('status') == 'finalized')
            self.server.shutdown()
            self.server.server_close()
            self.server_thread.join(1)
            self.journal.close()
            self.pending_original = None
            self.pending_capture = None
            self.pending_semantic = None
            self.pending_desk = None
            for values in (self.join.prepared, self.join.records, self.join.originals,
                           self.join.heads, self.join.adapter.seen):
                values.clear()
            if self.semantic is not None:
                for name in ('prepared', 'records', 'originals', 'heads', 'identities', 'progress', 'requests', 'person_requests'):
                    values = getattr(self.semantic, name, None)
                    if isinstance(values, dict):
                        values.clear()
            if self.desk is not None:
                for name in ('prepared', 'originals', 'records', 'heads'):
                    getattr(self.desk, name).clear()
            self.finished = True
            buffers_cleared = (True if self.desk is None or self.desk.policy['version'] == 1 else
                               original_status.get('stereo_media', {}).get('buffers_cleared') is True)
            self.cleanup_result = {'status': 'stopped' if confirmed else 'unconfirmed', 'reason': reason,
                'withdrawal': withdrawal['withdrawal'], 'buffers_cleared': buffers_cleared,
                'originals': deepcopy(original_status)}
            return deepcopy(self.cleanup_result)
        finally:
            self.cleanup_lock.release()

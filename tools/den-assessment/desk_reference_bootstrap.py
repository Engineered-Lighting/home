"""Candidate-free private independent references; never geometry qualification.

Only owner measurements and server entry clocks are accepted. The fixed Guide
owns the durable recorder and all HTTP/UI concerns; this module has no prediction,
collection, model, camera, Journal or operational export capability.
"""
from copy import deepcopy
import threading

from desk_method_assessment import (PARTS, REQUIREMENTS, AUTHORITY, MAX_ACTION_BYTES,
    MAX_RECORD_BYTES, MAX_ACTIONS, MAX_REPORT_BYTES, GUIDE_CLOSE_RESERVE_BYTES,
    _math, encoded, digest, exact, identity, number, need, DeskAssessmentRefusal)


class IndependentReferenceCapture:
    def __init__(self, references, *, clock, monotonic, record):
        need(all(callable(port) for port in (clock, monotonic, record)), 'fixed_reference_capture_ports_required')
        guide, _ = _math()
        self.references = guide.reference_frame(deepcopy(references))
        self.references_ref = dict(id='independent-room-references-'+digest(self.references), digest=digest(self.references))
        self.clock, self.monotonic, self.record = clock, monotonic, record
        self.sequence = self.recorded_bytes = 0
        self.failed = False
        self.frozen = self.active = self.last_stamp = self.cached_results = None
        self.windows = []
        self.lock = threading.RLock()

    def _ready(self):
        need(not self.failed, 'reference_capture_recording_incomplete')
        need(digest(self.references) == self.references_ref['digest'], 'frozen_room_references_changed')

    def _stamp(self):
        try:
            utc, mono = self.clock(), self.monotonic()
            number(utc)
            need(type(mono) is int and 0 <= mono < 2**63, 'actual_reference_monotonic_ns_required')
            stamp = dict(utc_s=utc, monotonic_ns=str(mono))
            if self.last_stamp is not None:
                need(utc >= self.last_stamp['utc_s'] and mono >= int(self.last_stamp['monotonic_ns']),
                     'reference_entry_clock_regressed')
            return stamp
        except BaseException:
            self.failed = True
            raise

    def _action(self, kind, body, *, seq=None):
        return dict(schema='home-desk-reference-bootstrap-action-1', version=1,
            seq=self.sequence+1 if seq is None else seq, kind=kind,
            reference_frame_ref=deepcopy(self.references_ref), body=deepcopy(body))

    def _retain(self, kind, body):
        action = self._action(kind, body)
        raw = encoded(action)
        need(len(raw) <= MAX_ACTION_BYTES and self.sequence+2 <= MAX_ACTIONS
             and self.recorded_bytes+len(raw)+1+GUIDE_CLOSE_RESERVE_BYTES <= MAX_RECORD_BYTES,
             'reference_capture_action_capacity')
        fingerprint = digest(action)
        try:
            need(self.record(deepcopy(action)) == dict(status='retained', sha256=fingerprint),
                 'durable_reference_capture_ACK_required')
        except BaseException:
            self.failed = True
            raise DeskAssessmentRefusal('reference_capture_recording_incomplete') from None
        self.sequence += 1
        self.recorded_bytes += len(raw)+1
        self.cached_results = None
        return dict(id='reference-bootstrap-action-'+str(self.sequence), digest=fingerprint)

    def freeze(self, protocol, requirements, *, selected_anchor_label):
        with self.lock:
            self._ready()
            exact(protocol, ('id','reference_method','supported_configuration_ids','max_windows'), 'closed_reference_protocol_required')
            identity(protocol['id'])
            ids = protocol['supported_configuration_ids']
            need(protocol['reference_method'] in ('tape','laser') and type(ids) is list
                 and 1 <= len(ids) <= 32, 'bounded_independent_configurations_required')
            for value in ids: identity(value)
            need(len(set(ids)) == len(ids), 'bounded_independent_configurations_required')
            need(type(protocol['max_windows']) is int and 1 <= protocol['max_windows'] <= 32, 'reference_window_bound')
            exact(requirements, REQUIREMENTS, 'closed_independent_requirements_required')
            for key, value in requirements.items():
                if value is not None: number(value, 0, 1 if 'fraction' in key else 1e6)
            need(type(selected_anchor_label) is str and 0 < len(selected_anchor_label) <= 200,
                 'independently_named_reference_point_required')
            if self.frozen is not None:
                need(protocol == self.frozen['protocol']['body'] and requirements == self.frozen['requirements']['body']
                     and selected_anchor_label == self.frozen['selected_anchor_label'], 'independent_freeze_identity_changed')
                return deepcopy(self.frozen['receipt'])
            stamp = self._stamp()
            body = dict(references=deepcopy(self.references), protocol=dict(body=deepcopy(protocol),sha256=digest(protocol)),
                requirements=dict(body=deepcopy(requirements),sha256=digest(requirements)),
                selected_anchor_label=selected_anchor_label, frozen_at=stamp,
                prediction_visibility='unavailable_in_reference_capture',
                truth_role='independent_owner_measurement_claim_not_qualification')
            ref = self._retain('freeze', body)
            receipt = dict(status='frozen',freeze_ref=ref,reference_frame_ref=deepcopy(self.references_ref),
                protocol_ref=dict(id=protocol['id'],digest=digest(protocol)),
                requirements_ref=dict(id='held-requirements-'+digest(requirements),digest=digest(requirements)))
            self.frozen = dict(**body, receipt=deepcopy(receipt))
            self.last_stamp = stamp
            return receipt

    def _parts(self, values):
        exact(values, PARTS, 'six_explicit_independent_reference_parts_required')
        guide, _ = _math()
        result = {}
        for name in PARTS:
            value = values[name]
            exact(value, ('status','outer_corners','thickness_m'), 'raw_reference_part_required')
            status = value['status']
            need(status in ('available','unavailable','unknown'), 'explicit_reference_part_status_required')
            shape = None
            if status == 'available':
                number(value['thickness_m'], 0, .25)
                need(name == 'work' or value['thickness_m'] == 0, 'part_measurement_thickness_not_applicable')
                shape = guide.outer_box(value['outer_corners'], self.references,
                    thickness_m=value['thickness_m'], require_volume=name != 'work')
            else:
                need(value['outer_corners'] is None and value['thickness_m'] is None, 'missing_reference_has_no_measurements')
            result[name] = dict(**deepcopy(value), shape=shape)
        return result

    def _document(self, windows):
        return dict(schema='home-desk-independent-references-1',version=1,
            reference_frame_ref=deepcopy(self.references_ref),freeze_ref=deepcopy(self.frozen['receipt']['freeze_ref']),
            references=deepcopy(self.references),protocol=deepcopy(self.frozen['protocol']),
            requirements=deepcopy(self.frozen['requirements']),selected_anchor_label=self.frozen['selected_anchor_label'],
            reference_method_ref=dict(id=self.frozen['protocol']['body']['id'],digest=self.frozen['protocol']['sha256']),
            truth_role='independent_owner_measurement_claim_not_qualification',
            windows=deepcopy(windows),candidate_ref=None,collection_binding=None,
            clock=dict(role='server_entry_not_sensor_exposure',qualification=None,error_bound_s=None),
            qualified=False,operational_geometry=None,authority=deepcopy(AUTHORITY))

    def _reserve_end_results(self, windows, *, next_action_bytes, extra_actions):
        # Reserve a complete future End/results plus the fixed Guide close.
        latest = dict(utc_s=10**12,monotonic_ns=str(2**63-1))
        reference = dict(id='reference-bootstrap-action-'+str(MAX_ACTIONS),digest='f'*64)
        future = deepcopy(windows)
        end_bound = 0
        for window in future:
            if window['ended'] is None:
                window['ended'] = latest
                window.setdefault('start_receipt',dict(status='started',window_id=window['id'],
                    started_at=window['started']['utc_s'],reference_ref=reference))
                window['end_receipt'] = dict(status='ended',window_id=window['id'],
                    started_at=window['started']['utc_s'],ended_at=latest['utc_s'],duration_s=300,reference_ref=reference)
                body = dict(window_id=window['id'],started=window['started'],ended=latest,
                    duration_s=300,source_clock_qualification=None)
                end_bound = len(encoded(self._action('end',body,seq=MAX_ACTIONS)))+257
        result_bound = len(encoded(self._action('results',dict(reference_document=self._document(future),summary=None),seq=MAX_ACTIONS)))
        result_bound += MAX_REPORT_BYTES+len(future)*256
        need(result_bound <= MAX_ACTION_BYTES and end_bound <= MAX_ACTION_BYTES
             and self.sequence+extra_actions+1 <= MAX_ACTIONS
             and self.recorded_bytes+next_action_bytes+end_bound+result_bound+1+GUIDE_CLOSE_RESERVE_BYTES <= MAX_RECORD_BYTES,
             'reserved_reference_end_results_capacity')

    def start(self, window_id, configuration_id, parts, selected_anchor, reference_uncertainty_m, activity):
        with self.lock:
            self._ready()
            need(self.frozen is not None, 'freeze_independent_protocol_first')
            identity(window_id);identity(configuration_id)
            need(configuration_id in self.frozen['protocol']['body']['supported_configuration_ids'], 'configuration_outside_frozen_protocol')
            number(reference_uncertainty_m, 0, 2)
            need(activity in ('focused','not_engaged','unknown'), 'independent_activity_annotation_required')
            measured = self._parts(parts)
            exact(selected_anchor, ('status','original_measurement'), 'raw_selected_anchor_reference_required')
            need(selected_anchor['status'] in ('available','unavailable','unknown'), 'selected_anchor_reference_status_required')
            point = axes = None
            if selected_anchor['status'] == 'available':
                guide, _ = _math()
                point, axes = guide.room_point(selected_anchor['original_measurement'], self.references)
            else:
                need(selected_anchor['original_measurement'] is None, 'missing_anchor_has_no_measurement')
            request = dict(id=window_id,configuration_id=configuration_id,reference_parts=measured,
                selected_anchor=dict(**deepcopy(selected_anchor),point_m=point,error_axes_m=axes),
                reference_uncertainty_m=reference_uncertainty_m,activity=activity)
            old = next((window for window in self.windows if window['id'] == window_id), None)
            if old is not None:
                need(old['request'] == request, 'held_window_identity_changed')
                return deepcopy(old['start_receipt'])
            need(self.active is None, 'held_reference_intervals_cannot_overlap')
            need(len(self.windows) < self.frozen['protocol']['body']['max_windows'], 'reference_window_budget_exhausted')
            stamp = self._stamp()
            window = dict(id=window_id,request=request,started=stamp,ended=None)
            self._reserve_end_results(self.windows+[window],next_action_bytes=len(encoded(self._action('start',dict(**request,started=stamp))))+1,
                extra_actions=3)
            ref = self._retain('start',dict(**request,started=stamp))
            receipt = dict(status='started',window_id=window_id,started_at=stamp['utc_s'],reference_ref=ref)
            window['start_receipt'] = deepcopy(receipt)
            self.windows.append(window)
            self.active,self.last_stamp = window_id,stamp
            return receipt

    def end(self, window_id):
        with self.lock:
            self._ready();identity(window_id)
            window = next((item for item in self.windows if item['id'] == window_id),None)
            need(window is not None, 'held_reference_window_unavailable')
            if window['ended'] is not None: return deepcopy(window['end_receipt'])
            need(self.active == window_id, 'exact_active_reference_window_required')
            stamp = self._stamp()
            elapsed = (int(stamp['monotonic_ns'])-int(window['started']['monotonic_ns']))/1e9
            need(0 < elapsed <= 300, 'bounded_positive_held_interval_required')
            ref = self._retain('end',dict(window_id=window_id,started=deepcopy(window['started']),ended=stamp,
                duration_s=elapsed,source_clock_qualification=None))
            receipt = dict(status='ended',window_id=window_id,started_at=window['started']['utc_s'],
                ended_at=stamp['utc_s'],duration_s=elapsed,reference_ref=ref)
            window.update(ended=stamp,end_receipt=deepcopy(receipt))
            self.active,self.last_stamp = None,stamp
            return receipt

    def read_results(self):
        with self.lock:
            self._ready()
            need(self.frozen is not None, 'freeze_independent_protocol_first')
            if self.cached_results is not None: return deepcopy(self.cached_results)
            counts = {name:dict(available=0,unavailable=0,unknown=0) for name in PARTS}
            anchors = dict(available=0,unavailable=0,unknown=0)
            ended = [window for window in self.windows if window['ended'] is not None]
            for window in self.windows:
                for name,value in window['request']['reference_parts'].items():counts[name][value['status']] += 1
                anchors[window['request']['selected_anchor']['status']] += 1
            unmeasured = sorted(set(self.frozen['protocol']['body']['supported_configuration_ids'])-
                {window['request']['configuration_id'] for window in ended})
            summary = dict(status='references_recorded' if self.windows and self.active is None and not unmeasured else 'incomplete',
                recorded_windows=len(self.windows),ended_windows=len(ended),open_windows=len(self.windows)-len(ended),
                missingness=counts,selected_anchor_missingness=anchors,unmeasured_configuration_ids=unmeasured,
                candidate_available=False,predictions_supplied=False,qualified=False,operational_geometry=None,
                operational_qualification=dict(decision='block',missing=['full_candidate_and_independent_method_assessment',
                    'qualified_reference_sensor_alignment','applicable_original_predictions_and_service_evidence']),
                authority=deepcopy(AUTHORITY))
            document = self._document(self.windows)
            body = dict(reference_document=document,summary=summary)
            size = len(encoded(self._action('results',body)))+1
            if self.active is not None:self._reserve_end_results(self.windows,next_action_bytes=size,extra_actions=3)
            need(len(encoded(summary)) <= MAX_REPORT_BYTES, 'bounded_reference_summary_required')
            ref = self._retain('results',body)
            self.cached_results = dict(**summary,reference_ref=ref,reference_document_ref=dict(
                id='independent-references-'+digest(document),digest=digest(document)))
            return deepcopy(self.cached_results)

"""Bounded, opt-in nonrecording acquisition using incumbent camera/model owners.

This is a production composition, not a new camera or model scheduler. The
caller supplies already admitted resident workers and local authority readers.
Construction starts only an isolated loopback journal; step() is explicit.
No model, URL, source, executable or authority is selected by observed data.
"""
from copy import deepcopy
import http.client
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
for directory in (ROOT / 'tools/shared-home', ROOT / 'stack/services/home-context'):
    if str(directory) not in sys.path:
        sys.path.append(str(directory))

from controller_load import _rss_bytes
from journal import Journal, PREFIX, canonical, digest, finite, identifier
from server import LocalServer
from live_capture import LiveCaptureAdapter
from live_acquisition_owner import create_controller_capture_owner, CaptureOwnerRefusal
from live_acquisition_models import LocalModelStages
from live_publication import PublicationRefusal
from occupancy_reader import OccupancyReader
import occupancy_gate

CONFIG_KEYS = {'schema', 'version', 'session_ref', 'source_id', 'revision', 'local_grant_id',
               'model_refs', 'limits', 'person_threshold', 'max_people', 'occupancy_entity', 'camera_derived_entities'}
GRANT_KEYS = {'schema', 'version', 'id', 'session_ref', 'source_id', 'camera_id', 'configuration_digest',
              'model_refs', 'operations', 'not_before', 'valid_until', 'generation'}


class AcquisitionRefusal(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _configuration(config, live_config, policy, capture_method, stages, spatial_policy, interaction_policy, interaction_sources, assessment_recorder, lifecycle_policy=None):
    lifecycle = isinstance(config, dict) and config.get('schema') == 'home-production-acquisition-5'
    assessment = lifecycle or isinstance(config, dict) and config.get('schema') == 'home-production-acquisition-4'
    interaction = isinstance(config, dict) and (config.get('schema') == 'home-production-acquisition-3'
                  or assessment and config.get('interaction_method_ref') is not None)
    spatial = assessment or interaction or isinstance(config, dict) and config.get('schema') == 'home-production-acquisition-2'
    keys = (CONFIG_KEYS | ({'spatial_method_ref'} if spatial else set())
            | ({'interaction_method_ref'} if interaction or assessment else set()) | ({'assessment_ref'} if assessment else set())
            | ({'lifecycle_ref'} if lifecycle else set()))
    if not isinstance(config, dict) or set(config) != keys:
        raise ValueError('exact production acquisition configuration required')
    if ((config['schema'], config['version']) not in {('home-production-acquisition-1', 1),
            ('home-production-acquisition-2', 2), ('home-production-acquisition-3', 3), ('home-production-acquisition-4', 4),
            ('home-production-acquisition-5', 5)}
            or type(config['version']) is not int or config['session_ref'] != live_config['manifest']['identity']
            or config['source_id'] not in live_config['manifest']['sources']
            or policy['schema'] != 'home-live-capture-policy-2' or policy['version'] != 2
            or set(config['model_refs']) != {'detector', 'pose'}):
        raise ValueError('bound session, sources, models and per-observation clock policy required')
    if spatial:
        method = config['spatial_method_ref']
        if (not isinstance(spatial_policy, dict) or not isinstance(method, dict) or set(method) != {'id', 'digest'}
                or method['digest'] != digest(spatial_policy) or 'spatial' in stages
                or spatial_policy.get('model_refs') != config['model_refs']):
            raise ValueError('fixed pinned spatial producer required')
        identifier(method['id'], 'spatial_method_id')
    elif spatial_policy is not None:
        raise ValueError('spatial publication requires production acquisition version two')
    if interaction:
        method = config['interaction_method_ref']
        if (not isinstance(interaction_policy, dict) or not isinstance(method, dict) or set(method) != {'id', 'digest'}
                or method['digest'] != digest(interaction_policy) or 'interaction' in stages
                or interaction_policy.get('model_refs') != config['model_refs']
                or interaction_policy.get('capture_method_ref') != capture_method
                or interaction_policy.get('spatial_method_ref') != config['spatial_method_ref']):
            raise ValueError('fixed pinned interaction producer required')
        identifier(method['id'], 'interaction_method_id')
    elif interaction_policy is not None or interaction_sources is not None:
        raise ValueError('interaction publication requires production acquisition version three')
    if assessment:
        from live_assessment import AssessmentRecorder
        if not isinstance(assessment_recorder, AssessmentRecorder) or config['assessment_ref'] != assessment_recorder.policy_ref:
            raise ValueError('fixed pinned assessment recorder required')
    elif assessment_recorder is not None:
        raise ValueError('assessment requires production acquisition version four')
    if lifecycle:
        from live_person_lifecycle import validate_policy
        if ((live_config.get('schema'), live_config.get('version')) not in {
                ('home-live-ingress-config-6', 6), ('home-live-ingress-config-7', 7), ('home-live-ingress-config-9', 9), ('home-live-ingress-config-10', 10), ('home-live-ingress-config-11', 11), ('home-live-ingress-config-12', 12), ('home-live-ingress-config-13', 13)}
                or not isinstance(lifecycle_policy, dict)
                or config['lifecycle_ref'] != {'id': lifecycle_policy.get('id'), 'digest': digest(lifecycle_policy)}
                or lifecycle_policy.get('capture_method_ref') != capture_method
                or lifecycle_policy.get('spatial_method_ref') != config['spatial_method_ref']
                or lifecycle_policy.get('model_refs') != config['model_refs']):
            raise ValueError('explicit config6/config7 and pinned fixed lifecycle producer required')
        validate_policy(lifecycle_policy, live_config['manifest']['sources'][config['source_id']],
                        live_config['manifest']['worker']['lifecycle_policy'])
        # Config6 closure must be qualified before fixed resident preparation,
        # rather than discovering a missing shared schema after native load.
        from live_ingress_validation import validate_config
        from schema_registry import SchemaRegistry, LIVE_PROFILE
        validate_config(live_config, SchemaRegistry((), profile=LIVE_PROFILE))
    elif lifecycle_policy is not None:
        raise ValueError('lifecycle requires explicit production acquisition version five')
    for key in ('source_id', 'revision', 'local_grant_id'):
        identifier(config[key], key)
    limits = config['limits']
    if (not isinstance(limits, dict) or set(limits) != {'interval_s', 'max_cycles', 'max_runtime_s', 'stage_timeout_s', 'memory_limit_mb'}
            or not finite(limits['interval_s']) or not .5 <= limits['interval_s'] <= 120
            or type(limits['max_cycles']) is not int or not 1 <= limits['max_cycles'] <= 24
            or not finite(limits['max_runtime_s']) or not 0 < limits['max_runtime_s'] <= 120
            or not finite(limits['stage_timeout_s']) or not 0 < limits['stage_timeout_s'] <= 3
            or type(limits['memory_limit_mb']) is not int or not 1 <= limits['memory_limit_mb'] <= 512
            or not isinstance(config['camera_derived_entities'], list)
            or not config['camera_derived_entities'] or len(config['camera_derived_entities']) > 8
            or len(set(config['camera_derived_entities'])) != len(config['camera_derived_entities'])
            or not isinstance(stages, dict) or set(stages) - {'spatial', 'interaction'}
            or not all(callable(value) for value in stages.values())):
        raise ValueError('explicit bounded acquisition limits and fixed stage capabilities required')
    if len(canonical(config).encode()) > 65536:
        raise ValueError('bounded configuration required')


class ProductionAcquisition:
    def __init__(self, config, *, live_config, capture_policy, capture_method, journal_path, journal_grants,
                 publication_token, detector, pose, local_authority, household_enabled, occupancy,
                 clock=time.time, monotonic=time.monotonic, camera_dependencies=None, control_clock=None,
                 stages=None, rss=None, spatial_policy=None, interaction_policy=None, interaction_sources=None,
                 assessment_recorder=None, shutdown_owner='acquisition', lifecycle_policy=None):
        stages = stages or {}
        _configuration(config, live_config, capture_policy, capture_method, stages, spatial_policy, interaction_policy, interaction_sources, assessment_recorder, lifecycle_policy)
        if shutdown_owner not in {'acquisition', 'supervisor'}:
            raise ValueError('fixed shutdown owner required')
        self.shutdown_owner = shutdown_owner
        self.assessment_recorder = assessment_recorder
        self.interaction_sources = interaction_sources
        if interaction_sources is not None:
            from live_interaction_sources import ExistingReceiptSources
            if not isinstance(interaction_sources, ExistingReceiptSources):
                raise ValueError('fixed existing interaction receipt sources required')
        if (not all(callable(port) for port in (clock, monotonic, local_authority, household_enabled, control_clock))
                or not isinstance(publication_token, str) or not 1 <= len(publication_token) <= 4096
                or not isinstance(occupancy, OccupancyReader)
                or occupancy_gate.reader_entity(config['camera_derived_entities'], occupancy.entity,
                                                  explicit=config['occupancy_entity']) != occupancy.entity):
            raise ValueError('local grants, commissioned control clock and bound incumbent occupancy reader required')
        dependencies = dict(camera_dependencies or {})
        if set(dependencies) - {'turns', 'runtime_factory', 'pair_transport'}:
            raise ValueError('only fixed camera I/O capabilities may be supplied')
        self.config, self.configuration_digest = deepcopy(config), digest(config)
        self.clock, self.monotonic = clock, monotonic
        self.local_authority, self.household_enabled, self.occupancy = local_authority, household_enabled, occupancy
        self.rss = rss or _rss_bytes
        if not callable(self.rss):
            raise ValueError('resident memory sampler required')
        self.stages, self.limits = dict(stages), deepcopy(config['limits'])
        self.started, self.last_turn = monotonic(), None
        if not finite(self.started):
            raise ValueError('monotonic clock unavailable')
        self.turn = threading.Lock()
        self.cleanup_lock = threading.Lock()
        self.cancel = threading.Event()
        self.closed, self.finished, self.revoked = False, False, False
        self.cleanup_result = None
        self.supervised_withdrawal = None
        self.generation, self.current_capture = None, None
        self.problem, self.close_reason = None, 'development_complete'
        self.seen = set()
        self.extra_task = None
        self.counters = dict.fromkeys(('cycles', 'captures', 'detector_runs', 'pose_runs', 'refusals', 'source_gaps'), 0)
        self.models = LocalModelStages(detector, pose, detector_ref=config['model_refs']['detector'],
                                       pose_ref=config['model_refs']['pose'], person_threshold=config['person_threshold'],
                                       max_people=config['max_people'])
        if detector.model is None or pose.model is None:
            raise ValueError('resident model admission and loading must precede composition')
        self.owner = create_controller_capture_owner(clock=clock, monotonic=monotonic,
                       clock_authority_ref=capture_policy['clock_binding']['authority_ref'], **dependencies)
        self.capture = LiveCaptureAdapter(live_config, source_id=config['source_id'], revision=config['revision'],
                       policy=capture_policy, method_ref=capture_method, clock=clock, publish=self._publish,
                       control_clock=control_clock, max_captures=self.limits['max_cycles'],
                       clock_authority=self.owner.authorize_clock_receipt,
                       authority_generation=self._room_authority_generation)
        self.spatial = self.interaction = self.lifecycle = None
        if config['schema'] == 'home-production-acquisition-5':
            from live_person_lifecycle import LivePersonLifecycle
            self.lifecycle = LivePersonLifecycle(lifecycle_policy,
                worker_policy=live_config['manifest']['worker']['lifecycle_policy'],
                source=live_config['manifest']['sources'][config['source_id']], session_ref=config['session_ref'],
                source_id=config['source_id'], configuration_digest=self.configuration_digest,
                revision=config['revision'], clock=clock, publish=self._publish, max_captures=self.limits['max_cycles'])
        if config['schema'] in {'home-production-acquisition-2', 'home-production-acquisition-3', 'home-production-acquisition-4', 'home-production-acquisition-5'}:
            if spatial_policy['capture_method_ref'] != capture_method:
                raise ValueError('spatial input must pin the actual qualified capture method')
            from live_spatial import LiveSpatialProducer
            self.spatial = LiveSpatialProducer(live_config, source_id=config['source_id'], revision=config['revision'],
                           policy=spatial_policy, method_ref=config['spatial_method_ref'],
                           capture_input=self.capture.measurement_input, clock=clock, publish=self._publish,
                           barrier=self.capture.source_gap, max_captures=self.limits['max_cycles'], monotonic=monotonic,
                           assessment_recorder=assessment_recorder, lifecycle=self.lifecycle)
        if assessment_recorder is not None:
            assessment_recorder.bind(config['session_ref'], config['source_id'], self.configuration_digest, clock,
                                     interaction_policy, optional_la_configured=interaction_sources is not None)
        if config['schema'] == 'home-production-acquisition-3' or config['schema'] in {'home-production-acquisition-4', 'home-production-acquisition-5'} and config['interaction_method_ref'] is not None:
            from live_interaction import LiveInteractionProducer
            if interaction_sources is not None:
                interaction_sources.bind_capture(self.capture.measurement_input, self.capture.resolve_receipt)
            self.interaction = LiveInteractionProducer(live_config, source_id=config['source_id'], revision=config['revision'],
                           policy=interaction_policy, method_ref=config['interaction_method_ref'],
                           capture_input=self.capture.measurement_input, spatial_original=self.spatial.original_receipt,
                           clock=clock, publish=self._publish, barrier=self.capture.source_gap,
                           max_captures=self.limits['max_cycles'], monotonic=monotonic,
                           optional_sources=interaction_sources, assessment_recorder=assessment_recorder, lifecycle=self.lifecycle)
        self.publication_token = publication_token
        # Exclusive fresh file reservation: never reopen, migrate or infer a head.
        path = Path(journal_path)
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(handle)
        self.journal = self.server = None
        try:
            self.journal = Journal(path, grants=journal_grants, profile='home-live-shadow-1', live_config=live_config,
                                   clock=clock, live_receipt_resolver=self._resolve_receipt,
                                   **({'live_wall_clock': clock, 'live_monotonic': monotonic}
                                      if live_config['schema'] in {'home-live-ingress-config-7','home-live-ingress-config-9','home-live-ingress-config-10','home-live-ingress-config-11','home-live-ingress-config-12','home-live-ingress-config-13'} else {}))
            self.server = LocalServer(('127.0.0.1', 0), self.journal)
            self.address = self.server.server_address
            self.server_thread = threading.Thread(target=self.server.serve_forever,
                                                   kwargs={'poll_interval': .02}, name='home-producer-journal', daemon=True)
            self.server_thread.start()
        except BaseException:
            if self.server is not None:
                self.server.server_close()
            if self.journal is not None:
                self.journal.close()
            self.owner.close()
            raise

    def _resolve_receipt(self, store_id, receipt_id):
        matches = []
        for owner in (self.capture, self.spatial, self.interaction, self.lifecycle):
            if owner is None:
                continue
            try:
                matches.append(owner.resolve_receipt(store_id, receipt_id))
            except KeyError:
                continue
        if not matches:
            raise KeyError((store_id, receipt_id))
        if len(matches) != 1:
            raise ValueError('original receipt identity collision')
        return matches[0]

    def _room_authority_generation(self):
        # This is the sole factory journal, never a caller-selected endpoint.
        with self.journal.lock:
            return self.journal.revocation_generation(self.config['session_ref']['site_id'],
                                                       self.config['session_ref']['room_id'])

    def _authority(self):
        try:
            grant, now = self.local_authority(), self.clock()
            operations = ['capture', 'detector', 'pose'] + sorted(self.stages)
            if self.spatial is not None:
                operations.append('spatial')
            if self.interaction is not None:
                operations.append('interaction')
            if self.assessment_recorder is not None:
                operations.append('assessment')
            if self.lifecycle is not None:
                operations.append('lifecycle')
            allowed = (self.household_enabled() is True and isinstance(grant, dict) and set(grant) == GRANT_KEYS
                       and grant['schema'] == 'home-local-acquisition-grant-1' and type(grant['version']) is int
                       and grant['version'] == 1 and grant['id'] == self.config['local_grant_id']
                       and grant['session_ref'] == self.config['session_ref'] and grant['source_id'] == self.config['source_id']
                       and grant['camera_id'] == self.capture.policy['camera_id']
                       and grant['configuration_digest'] == self.configuration_digest
                       and grant['model_refs'] == self.config['model_refs']
                       and isinstance(grant['operations'], list) and len(grant['operations']) == len(operations)
                       and set(grant['operations']) == set(operations)
                       and finite(now) and finite(grant['not_before']) and finite(grant['valid_until'])
                       and grant['not_before'] <= now < grant['valid_until']
                       and type(grant['generation']) is int and grant['generation'] >= 1)
            if allowed and self.generation is None:
                self.generation = grant['generation']
            allowed = allowed and grant['generation'] == self.generation
        except Exception:
            allowed = False
        if not allowed:
            self.revoked = True
            self.problem = 'local_acquisition_revoked'
            self.cancel.set()
        return allowed

    def _authorized(self):
        if self.closed or self.cancel.is_set() or not self._authority():
            return False
        try:
            if self.owner.controller_scope is not None:
                self.owner.controller_scope.require_turns()
            mono = self.monotonic()
            memory = self.rss()
            if not finite(mono) or mono < self.started or mono - self.started >= self.limits['max_runtime_s']:
                self.problem = 'runtime_limit'
                return False
            if not finite(memory) or memory <= 0 or memory > self.limits['memory_limit_mb'] * 1024**2:
                self.problem = 'memory_limit'
                return False
            skip, _ = occupancy_gate.check(self.occupancy)
            if skip:
                self.problem = skip
                return False
            capture = self.current_capture
            if capture is not None and not capture['available_at'] <= self.clock() < capture['effective_valid_until']:
                self.problem = 'capture_expired'
                return False
            return True
        except Exception:
            self.problem = 'local_source_unavailable'
            return False

    def _publish(self, event):
        # Control authority is deliberately separate: it must retire support even
        # after capture/model rights or the household switch were withdrawn.
        if event['payload'].get('status') != 'withdrawn' and not self._authorized():
            raise AcquisitionRefusal(self.problem or 'local_acquisition_revoked')
        connection = http.client.HTTPConnection(*self.address, timeout=3)
        try:
            body = canonical(event).encode()
            if len(body) > self.journal.limits.max_body_bytes:
                raise PublicationRefusal('publication_too_large', http_status=413)
            connection.request('POST', PREFIX + '/events', body=body,
                               headers={'Authorization': 'Bearer ' + self.publication_token, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            raw = response.read(self.journal.limits.max_response_bytes + 1)
            if len(raw) > self.journal.limits.max_response_bytes:
                raise AcquisitionRefusal('publication_unconfirmed')
            result = json.loads(raw)
            if response.status != 200:
                if ((400 <= response.status < 500 or response.status == 507) and isinstance(result, dict)
                        and result.get('schema') == 'home-context-error-1'
                        and isinstance(result.get('error'), dict)
                        and isinstance(result['error'].get('code'), str)
                        and 0 < len(result['error']['code']) <= 128):
                    raise PublicationRefusal(result['error']['code'], http_status=response.status)
                raise AcquisitionRefusal('publication_unconfirmed')
            if (self.assessment_recorder is not None and event['payload'].get('status') == 'withdrawn'
                    and result.get('status') == 'accepted' and result.get('id') == event['id']
                    and result.get('payload_digest') == digest(event['payload'])):
                if not self.assessment_recorder.control(event, result):
                    raise AcquisitionRefusal('assessment_control_recording_unavailable')
            return result
        finally:
            connection.close()

    def _cycle(self, status, reason, **parts):
        if status in {'blocked', 'failed'}:
            self.counters['refusals'] += 1
        result = {'schema': 'home-acquisition-cycle-1', 'version': 1, 'status': status, 'reason': reason,
                'capture': 'unavailable', 'detector': 'not_run', 'pose': 'not_run',
                'spatial': 'unavailable' if self.spatial is not None or 'spatial' in self.stages else 'missing',
                'interaction': 'unavailable' if self.interaction is not None or 'interaction' in self.stages else 'missing',
                **parts, 'operational_geometry': False, 'focused_output': False}
        if self.assessment_recorder is not None and not self.assessment_recorder.cycle(result, self.counters, self.clock()):
            result.update(status='failed', reason='assessment_recording_unavailable')
        return result

    def _gap(self):
        self.counters['source_gaps'] += 1
        if self.spatial is not None:
            self.spatial.reset('source_unavailable')
        if self.interaction is not None:
            self.interaction.reset('source_unavailable')
        self.capture.source_gap()

    def _deadline(self):
        deadline = min(self.monotonic() + self.limits['stage_timeout_s'], self.started + self.limits['max_runtime_s'])
        if self.current_capture is not None:
            deadline = min(deadline, self.monotonic() + self.current_capture['effective_valid_until'] - self.clock())
        return deadline

    def _later_stage(self, callback, context):
        """Bound wait and retain the occupied slot until a trusted adapter returns."""
        task = {'done': threading.Event(), 'discard': False}
        self.extra_task = task
        deadline = self._deadline()
        wall_deadline = time.monotonic() + max(0, deadline - self.monotonic())
        def authorized():
            return (not task['discard'] and self.monotonic() < deadline and time.monotonic() < wall_deadline
                    and self._authorized())
        def run():
            try:
                result = callback(deepcopy(context), authorized=authorized, deadline=deadline, cancel=self.cancel)
                if not task['discard']:
                    task['result'] = result
            except BaseException as error:
                task['error'] = error.reason if isinstance(error, CaptureOwnerRefusal) else 'adapter_unavailable'
            finally:
                if task['discard']:
                    task.pop('result', None)
                task['done'].set()
        threading.Thread(target=run, name='home-producer-adapter', daemon=True).start()
        while not task['done'].wait(.02):
            if not self._authorized() or self.monotonic() >= deadline or time.monotonic() >= wall_deadline:
                task['discard'] = True
                task.pop('result', None)
                raise AcquisitionRefusal(self.problem or 'adapter_deadline')
        late = self.monotonic() >= deadline or time.monotonic() >= wall_deadline
        if not self._authorized() or late or task.get('error'):
            reason = self.problem or task.get('error') or ('adapter_deadline' if late else 'adapter_unavailable')
            task['discard'] = True
            task.pop('result', None)
            self.extra_task = None
            raise AcquisitionRefusal(reason)
        # Generic callbacks prepare local experimental context. Only the fixed
        # version-two spatial producer receives a publication capability.
        result = task.pop('result', None)
        self.extra_task = None
        return result

    def step(self):
        if not self.turn.acquire(False):
            return self._cycle('blocked', 'acquisition_busy')
        parts = {}
        try:
            self.problem = None
            if self.closed:
                return self._cycle('stopped', 'acquisition_closed')
            if not self._authorized():
                if not self.revoked:
                    self._gap()
                return self._cycle('blocked', self.problem or 'local_source_unavailable')
            if self.counters['cycles'] >= self.limits['max_cycles']:
                self.closed = True
                return self._cycle('stopped', 'cycle_limit')
            now = self.monotonic()
            if self.last_turn is not None and now - self.last_turn < self.limits['interval_s']:
                return self._cycle('blocked', 'rate_limited')
            if self.models.busy() or self.extra_task is not None and not self.extra_task['done'].is_set():
                return self._cycle('blocked', 'local_model_busy')
            self.last_turn = now
            self.counters['cycles'] += 1
            capture_started_at = self.clock() if self.assessment_recorder is not None else None
            pair = self._later_stage(lambda _, *, authorized, deadline, cancel:
                       self.owner.acquire(cancel=cancel, deadline=deadline, authorized=authorized), {})
            if not self._authorized():
                raise AcquisitionRefusal(self.problem or 'local_source_unavailable')
            qualified = self.capture.offer(pair['jpeg'], pair['header'], pair['bundle'], pair['calibration'], received_at=pair['received_at'])
            if qualified['status'] != 'qualified':
                raise AcquisitionRefusal(self.problem or qualified['reason'])
            self.current_capture = qualified['capture']
            parts['capture'] = 'qualified'
            if self.interaction_sources is not None:
                # A nonblocking offer into the admitted existing LA ring.
                # Failure is optional semantic absence, never local gating.
                self.interaction_sources.offer_capture(pair['jpeg'], pair['header'], self.current_capture['id'])
            if not self._authorized():
                raise AcquisitionRefusal(self.problem or 'capture_expired')
            frame_id = self.current_capture['id']
            if frame_id in self.seen:
                return self._cycle('blocked', 'duplicate_capture', **parts)
            self.seen.add(frame_id)
            self.counters['captures'] += 1
            request_prefix = frame_id + ':local'
            self.counters['detector_runs'] += 1
            detector_started_at = self.clock() if self.assessment_recorder is not None else None
            detection = self.models.detect(pair['jpeg'], frame_id=frame_id, request_id=request_prefix + ':detector',
                              authorized=self._authorized, deadline=self._deadline(), cancel=self.cancel, clock=self.monotonic)
            detector_at = self.clock()
            parts['detector'] = 'completed'
            if not self._authorized():
                raise AcquisitionRefusal(self.problem or 'capture_expired')
            self.counters['pose_runs'] += 1
            pose_started_at = self.clock() if self.assessment_recorder is not None else None
            pose = self.models.pose(detection, frame_id=frame_id, request_id=request_prefix + ':pose',
                            authorized=self._authorized, deadline=self._deadline(), cancel=self.cancel, clock=self.monotonic)
            pose_at = self.clock()
            parts['pose'] = 'completed'
            if not self._authorized():
                raise AcquisitionRefusal(self.problem or 'capture_expired')
            payload = qualified['publication']['payload']
            context = {'schema': 'home-acquisition-local-context-1', 'version': 1,
                       'session_ref': deepcopy(self.config['session_ref']), 'source_id': self.config['source_id'],
                       'configuration_digest': self.configuration_digest, 'capture': deepcopy(self.current_capture),
                       'capture_payload': deepcopy(payload), 'publication_receipt': deepcopy(qualified['receipt']),
                       'original_receipts': [self.capture.original_receipt(ref['store_id'], ref['receipt_id']) for ref in payload['original_receipt_refs']],
                       'detector': detection.metadata(), 'pose': pose,
                       'detector_available_at': detector_at, 'pose_available_at': pose_at,
                       'availability_clock': 'utc_epoch_seconds', 'model_availability_clock': 'monotonic_seconds',
                       'person_association': 'unresolved', 'spatial': None, 'interaction': None}
            if self.assessment_recorder is not None:
                context['model_refs'] = deepcopy(self.config['model_refs'])
                context['spatial_method_ref'] = deepcopy(self.config['spatial_method_ref'])
                context['pipeline_times'] = {'capture_started_at': capture_started_at,
                    'capture_received_at': pair['received_at'], 'capture_available_at': payload['available_at'],
                    'capture_committed_at': qualified['receipt']['committed_at'],
                    'detector_started_at': detector_started_at, 'pose_started_at': pose_started_at}
            if self.spatial is not None:
                context['spatial'] = self._later_stage(self.spatial.offer, context)
                result = context['spatial']
                if not isinstance(result, dict) or result.get('status') not in {'supplied', 'unobservable'}:
                    raise AcquisitionRefusal(result.get('reason', 'spatial_unavailable') if isinstance(result, dict)
                                             else 'spatial_unavailable')
                parts['spatial'] = 'completed'
            if self.interaction is not None and context['spatial']['status'] == 'supplied':
                context['interaction'] = self._later_stage(self.interaction.offer, context)
                result = context['interaction']
                if not isinstance(result, dict) or result.get('status') not in {'supplied', 'unobservable'}:
                    raise AcquisitionRefusal(result.get('reason', 'interaction_unavailable') if isinstance(result, dict)
                                             else 'interaction_unavailable')
                parts['interaction'] = 'completed'
            for name in ('spatial', 'interaction'):
                if name in self.stages:
                    context[name] = self._later_stage(self.stages[name], context)
                    parts[name] = 'completed'
            if not self._authorized():
                raise AcquisitionRefusal(self.problem or 'capture_expired')
            return self._cycle('observed', 'capture_and_models_completed', **parts)
        except Exception as error:
            reason = self.problem or getattr(error, 'reason', 'acquisition_unavailable')
            if not self.revoked:
                self._gap()
            return self._cycle('failed', reason, **parts)
        finally:
            self.current_capture = None
            self.owner.clear()
            self.models.clear()
            self.turn.release()
            if self.shutdown_owner == 'supervisor':
                pass  # The supervisor must drain the still-open journal/originals.
            elif self.revoked:
                self.close(reason='grant_revoked')
            elif self.closed:
                self.close(reason=self.close_reason)

    def status(self):
        return {'schema': 'home-acquisition-status-1', 'version': 1,
                'session_ref': deepcopy(self.config['session_ref']), 'source_id': self.config['source_id'],
                'configuration_digest': self.configuration_digest, 'counters': dict(self.counters),
                'stage_presence': {'spatial': self.spatial is not None or 'spatial' in self.stages,
                                   'interaction': self.interaction is not None or 'interaction' in self.stages},
                'closed': self.closed, 'inflight': self.turn.locked() or self.models.busy()
                    or self.extra_task is not None and not self.extra_task['done'].is_set()}

    def readiness(self):
        """Trusted composition status, not camera/accuracy or host qualification."""
        inflight = self.status()['inflight']
        state = ('finalized' if self.finished else 'withdrawn' if self.supervised_withdrawal is not None
                 else 'quiescing' if self.closed or self.cancel.is_set() else 'busy' if inflight else 'idle')
        return {'schema': 'home-acquisition-readiness-1', 'version': 1,
                'session_ref': deepcopy(self.config['session_ref']), 'source_id': self.config['source_id'],
                'configuration_digest': self.configuration_digest, 'journal_address': list(self.address),
                'state': state, 'inflight': inflight, 'shutdown_owner': self.shutdown_owner}

    def quiesce(self, reason='operator_stop'):
        identifier(reason, 'quiesce_reason')
        self.closed, self.close_reason = True, reason
        self.cancel.set()
        if self.extra_task is not None:
            self.extra_task['discard'] = True
            self.extra_task.pop('result', None)
        self.owner.close()
        busy = self.status()['inflight']
        return {'status': 'unconfirmed' if busy else 'quiescent', 'reason': reason,
                'inflight': busy, 'buffers_retained': not self.finished}

    def withdraw(self, reason='operator_stop'):
        quiet = self.quiesce(reason)
        if quiet['inflight'] or not self.cleanup_lock.acquire(False):
            return {'status': 'unconfirmed', 'withdrawal': 'not_recorded',
                    'barrier_receipt': None, 'input_cursor': None, 'reason': 'acquisition_busy'}
        try:
            if self.supervised_withdrawal is not None and self.supervised_withdrawal['withdrawal'] == 'recorded':
                return deepcopy(self.supervised_withdrawal)
            result = self.capture.withdraw('grant_revoked' if self.revoked else 'operator_withdrawal', retain_originals=True)
            receipt = result.get('receipt')
            self.supervised_withdrawal = {'status': 'withdrawn' if result['withdrawal'] == 'recorded' else 'unconfirmed',
                'withdrawal': result['withdrawal'], 'barrier_receipt': deepcopy(receipt),
                'input_cursor': receipt.get('cursor') if receipt is not None else None, 'reason': reason}
            return deepcopy(self.supervised_withdrawal)
        finally:
            self.cleanup_lock.release()

    def finalize(self, reason='operator_stop', *, drain_receipt=None):
        """Close owned resources only after an exact trusted terminal drain.

        An incomplete/unconfirmed disposition may release idle resources but
        cannot become a successful session. No publication occurs after drain.
        """
        identifier(reason, 'finalize_reason')
        if self.finished:
            return dict(self.cleanup_result)
        keys = {'schema', 'version', 'session_ref', 'configuration_digest', 'withdrawal_receipt',
                'recorder_status', 'worker_status', 'terminal_cursor'}
        withdrawal = self.supervised_withdrawal
        if (not isinstance(drain_receipt, dict) or set(drain_receipt) != keys or withdrawal is None
                or drain_receipt['schema'] != 'home-acquisition-drain-1' or type(drain_receipt['version']) is not int
                or drain_receipt['version'] != 1 or drain_receipt['session_ref'] != self.config['session_ref']
                or drain_receipt['configuration_digest'] != self.configuration_digest
                or drain_receipt['withdrawal_receipt'] != withdrawal['barrier_receipt']
                or drain_receipt['recorder_status'] not in {'drained', 'incomplete'}
                or drain_receipt['worker_status'] not in {'settled', 'unconfirmed'}
                or type(drain_receipt['terminal_cursor']) is not int or drain_receipt['terminal_cursor'] < 0
                or withdrawal['input_cursor'] is not None and drain_receipt['terminal_cursor'] < withdrawal['input_cursor']):
            return {'status': 'refused', 'reason': 'bound_terminal_drain_required',
                    'withdrawal': withdrawal['withdrawal'] if withdrawal else 'not_recorded', 'buffers_cleared': False}
        if self.quiesce(reason)['inflight'] or not self.cleanup_lock.acquire(False):
            return {'status': 'unconfirmed', 'reason': 'acquisition_busy',
                    'withdrawal': withdrawal['withdrawal'], 'buffers_cleared': False}
        try:
            if not self.models.clear() or not self.capture.finalize():
                return {'status': 'unconfirmed', 'reason': 'acquisition_busy',
                        'withdrawal': withdrawal['withdrawal'], 'buffers_cleared': False}
            if self.spatial is not None and not self.spatial.clear() or self.interaction is not None and not self.interaction.clear():
                return {'status': 'unconfirmed', 'reason': 'producer_cleanup_busy',
                        'withdrawal': withdrawal['withdrawal'], 'buffers_cleared': False}
            if self.lifecycle is not None and not self.lifecycle.clear():
                return {'status': 'unconfirmed', 'reason': 'producer_cleanup_busy',
                        'withdrawal': withdrawal['withdrawal'], 'buffers_cleared': False}
            self.extra_task = None
            self.seen.clear()
            self.server.shutdown()
            self.server.server_close()
            self.server_thread.join(1)
            self.journal.close()
            self.finished = True
            confirmed = withdrawal['withdrawal'] == 'recorded' and drain_receipt['recorder_status'] == 'drained' and drain_receipt['worker_status'] == 'settled'
            self.cleanup_result = {'status': 'stopped' if confirmed else 'unconfirmed', 'reason': reason,
                                   'withdrawal': withdrawal['withdrawal'], 'buffers_cleared': True}
            return dict(self.cleanup_result)
        finally:
            self.cleanup_lock.release()

    def close(self, reason='development_complete'):
        identifier(reason, 'close_reason')
        if self.shutdown_owner == 'supervisor':
            self.quiesce(reason)
            if self.finished:
                return dict(self.cleanup_result)
            return {'status': 'unconfirmed', 'reason': 'supervisor_drain_required',
                    'withdrawal': self.supervised_withdrawal['withdrawal'] if self.supervised_withdrawal else 'not_recorded',
                    'buffers_cleared': False}
        self.closed, self.close_reason = True, reason
        self.cancel.set()
        if not self.cleanup_lock.acquire(False):
            return {'status': 'unconfirmed', 'reason': reason, 'withdrawal': 'not_recorded', 'buffers_cleared': False}
        try:
            return self._finish_close(reason)
        finally:
            self.cleanup_lock.release()

    def _finish_close(self, reason):
        if self.finished:
            return {**self.cleanup_result, 'reason': reason}
        if self.extra_task is not None:
            self.extra_task['discard'] = True
            self.extra_task.pop('result', None)
        self.owner.close()
        models_clear = self.models.clear()
        withdrawal = self.capture.withdraw('grant_revoked' if self.revoked else 'operator_withdrawal')
        busy = self.turn.locked() or self.extra_task is not None and not self.extra_task['done'].is_set()
        if busy or not models_clear:
            return {'status': 'unconfirmed', 'reason': reason, 'withdrawal': withdrawal['withdrawal'],
                    'buffers_cleared': not busy and models_clear and withdrawal['buffers_cleared']}
        interaction_clear = self.interaction is None or self.interaction.clear() is True
        spatial_clear = self.spatial is None or self.spatial.clear() is True
        lifecycle_clear = self.lifecycle is None or self.lifecycle.clear() is True
        if not spatial_clear or not interaction_clear or not lifecycle_clear:
            return {'status': 'unconfirmed', 'reason': reason, 'withdrawal': withdrawal['withdrawal'],
                    'buffers_cleared': False}
        self.extra_task = None
        self.seen.clear()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(1)
        self.journal.close()
        self.finished = True
        self.cleanup_result = {'status': 'stopped' if withdrawal['withdrawal'] == 'recorded' else 'unconfirmed',
                               'reason': reason, 'withdrawal': withdrawal['withdrawal'], 'buffers_cleared': True}
        return dict(self.cleanup_result)


def create_production_acquisition(config, **ports):
    """Compose actual owners; external SDK/camera I/O may be replaced in tests."""
    return ProductionAcquisition(config, **ports)

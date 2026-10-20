"""Explicit production-family Home command factory, bounded and simulated.

The installed supervisor selects this factory as a trusted capability. JSON
alone cannot construct it: it needs the actual Lab composition and the private
human bearer already installed in that graph's Journal. Historical factories
and readers retain their original selection and types.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR
import json
import math
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

from journal import Journal, digest
from lab_live_runtime import LabRuntimeComposition
from room_committed_source import CommittedRoomSource, RoomCommitBinding, RoomCommitUnavailable
from room_command_session import CanonicalCommandSession
from room_production_canonical_owner import ProductionCanonicalOwner
from room_production_canonical_provider import FAMILY
from room_production_spotlight_sample import TrustedCommittedSpotlightSample

SCHEMA = 'home-den-production-canonical-command-declaration-1'


def need(value, reason):
    if not value:
        raise ValueError(reason)


def validate_production_command_declaration(value, *, source_bundle_digest):
    need(type(value) is dict and set(value) == {
        'schema','version','selection','source_bundle_digest','recording','limits'},
        'closed_production_command_declaration_required')
    need(value['schema'] == SCHEMA and type(value['version']) is int and value['version'] == 1
        and value['selection'] == FAMILY and value['source_bundle_digest'] == source_bundle_digest
        and type(source_bundle_digest) is str and len(source_bundle_digest) == 64
        and all(c in '0123456789abcdef' for c in source_bundle_digest),
        'explicit_production_command_family_and_source_required')
    recording, limits = value['recording'], value['limits']
    need(type(recording) is dict and set(recording) == {'native_file','birth_file','ledger_directory',
        'approval_directory','max_records','max_bytes','min_free_bytes'}, 'closed_production_recording_required')
    for key in ('native_file','birth_file','ledger_directory','approval_directory'):
        need(type(recording[key]) is str and Path(recording[key]).is_absolute()
            and not any(p.is_symlink() for p in (Path(recording[key]), *Path(recording[key]).parents)),
            'fixed_absolute_production_recording_path_required')
    need(type(recording['max_records']) is int and 8 <= recording['max_records'] <= 8192
        and type(recording['max_bytes']) is int and 4096 <= recording['max_bytes'] <= 33554432
        and type(recording['min_free_bytes']) is int and recording['min_free_bytes'] >= 0,
        'bounded_production_recording_required')
    need(type(limits) is dict and set(limits) == {
        'max_runtime_s','max_commits','interval_s','maximum_requests'}, 'closed_production_limits_required')
    need(type(limits['max_runtime_s']) in (int,float) and math.isfinite(limits['max_runtime_s'])
        and 0 < limits['max_runtime_s'] <= 120
        and type(limits['max_commits']) is int and 1 <= limits['max_commits'] <= 256
        and type(limits['maximum_requests']) is int and 1 <= limits['maximum_requests'] <= 256
        and type(limits['interval_s']) in (int,float) and math.isfinite(limits['interval_s'])
        and .02 <= limits['interval_s'] <= 1, 'bounded_production_command_limits_required')
    return deepcopy(value)


class ProductionCommandFactory:
    """Single-use installed factory; constructor opens no recording or process."""
    selection = FAMILY

    def __init__(self, declaration, *, composition, owner_bearer):
        need(type(composition) is LabRuntimeComposition and composition.producer['version'] == 8,
            'actual_production_source8_composition_required')
        self.declaration = validate_production_command_declaration(declaration,
            source_bundle_digest=declaration.get('source_bundle_digest') if type(declaration) is dict else None)
        need(type(owner_bearer) is str and 1 <= len(owner_bearer) <= 4096,
            'private_existing_human_bearer_required')
        need(self.declaration['limits']['max_runtime_s'] <= composition.producer['limits']['max_runtime_s'],
            'production_command_budget_exceeds_source')
        self.composition, self._owner_bearer = composition, owner_bearer
        self._lock, self._used = threading.Lock(), False

    def __call__(self, graph, worker_recorder):
        from live_den_mount import _runtime_module
        _runtime_module()
        from home_den_runtime.archive import Recorder
        need(type(worker_recorder) is Recorder and type(graph.journal) is Journal
            and graph is self.composition.graph and graph.join is self.composition.join
            and self.composition.command_current(graph) is True,
            'actual_current_production_graph_and_parent_recorder_required')
        need(worker_recorder.manifest['source_bundle_digest'] == self.declaration['source_bundle_digest'],
            'production_declaration_source_bundle_changed')
        with self._lock:
            need(not self._used, 'production_command_factory_single_use')
            self._used = True
        grant = graph.journal.authenticate(self._owner_bearer)
        worker = graph.journal.dynamic.worker
        binding = RoomCommitBinding(deepcopy(self.composition.scope), worker.policy['worker_id'],
            worker.policy['producer_id'], worker.policy['config_digest'],
            graph.journal.dynamic.config['manifest_ref']['digest'], worker.worker_version,
            tuple(f['fixture_id'] for f in worker.policy['room_policy']['fixtures']))
        return ProductionCommandSession(composition=self.composition, graph=graph,
            binding=binding, worker_recorder=worker_recorder,grant=grant,declaration=self.declaration)


class ProductionCommandSession:
    """One command consumer; original pairing remains the incumbent algorithm."""
    selection = FAMILY

    def __init__(self, *, composition, graph, binding, worker_recorder, grant, declaration):
        self.composition, self.graph = composition, graph
        self.owner = self.source = None
        self.journal, self.binding, self.worker_recorder = graph.journal, binding, worker_recorder
        self.grant, self.declaration = grant, deepcopy(declaration)
        self.lock, self._sink_lock = threading.RLock(), threading.Lock()
        self.cancel, self.done, self._wake = threading.Event(), threading.Event(), threading.Event()
        self.thread = None
        self.pending = self.accepted = None
        self.seen_prepared, self.seen_results, self.seen_resets, self.abandoned = {}, {}, {}, {}
        self.max_commits = declaration['limits']['max_commits']
        self.accepted_commits = self.selected_commits = 0
        self.last_result = self.last_commit_ref = self.terminal = self.error = None
        self.inflight, self.writers = False, 0
        self.bridges = {}
        self._activation_attempted = False
        self._selected = set()
        self._sink_bytes = self._sink_records = 0
        self.command_file = Path(declaration['recording']['birth_file']).with_suffix('.commands.jsonl')
        need(self.command_file not in {Path(declaration['recording']['native_file']),
            Path(declaration['recording']['birth_file']),Path(worker_recorder.path)},
            'distinct_production_command_originals_required')
        paths = [self.command_file,Path(declaration['recording']['native_file']),
            Path(declaration['recording']['birth_file']),Path(worker_recorder.path)]
        need(len(set(paths)) == 4 and all(path.parent.is_dir() for path in paths),
            'distinct_existing_production_recording_parents_required')
        directories = [Path(declaration['recording'][key]) for key in ('ledger_directory','approval_directory')]
        need(len(set(directories)) == 2 and all(path.is_dir() and not any(path.iterdir()) for path in directories)
            and all(path not in directories and all(directory not in path.parents for directory in directories)
                for path in paths), 'fresh_separate_production_approval_and_ledger_directories_required')
        # Retain capacity for command and native originals plus the bounded birth.
        reserve = 2*declaration['recording']['max_bytes'] + 2*1024**2
        need(all(shutil.disk_usage(path.parent).free >= reserve + declaration['recording']['min_free_bytes']
            for path in paths), 'production_original_storage_headroom_required')
        self._sink = self.command_file.open('xb')

    def _retain(self, kind, data):
        from journal import canonical
        raw = (canonical({'kind':kind,'data':data}) + '\n').encode('utf-8')
        with self._sink_lock:
            need(self._sink_records < self.declaration['recording']['max_records']
                and self._sink_bytes + len(raw) <= self.declaration['recording']['max_bytes'],
                'production_command_original_capacity_exhausted')
            self._sink.write(raw); self._sink.flush(); os.fsync(self._sink.fileno())
            self._sink_bytes += len(raw); self._sink_records += 1

    _pair = CanonicalCommandSession._pair
    _reset = CanonicalCommandSession._reset

    def record(self, body, *, record_id=None):
        # Keep retaining the worker's real withdrawal after command quiescence.
        with self.lock: self.writers += 1
        try:
            acknowledgement = self.worker_recorder.record(body, record_id=record_id)
            CanonicalCommandSession._ack(acknowledgement)
            if body.get('kind') in ('worker_prepared','worker_commit_result','worker_restore','worker_cold_start'):
                with self.lock: self._pair(body, acknowledgement)
                self._wake.set()
            return acknowledgement
        except BaseException:
            self.error = 'production_worker_original_pairing_failed'; self.cancel.set(); self._wake.set()
            raise
        finally:
            with self.lock: self.writers -= 1

    def start(self):
        with self.lock:
            need(self.thread is None and not self.cancel.is_set() and self.journal._recorder == self.record,
                'single_production_consumer_with_attached_original_recording_required')
            # The real supervisor attaches this exact recorder before start.
            # Source qualification is created before READY and the first input;
            # no accepted commit is fabricated for that preparation.
            self.source = CommittedRoomSource(self.journal,binding=self.binding,
                current=lambda:self.composition.command_current(self.graph) is True)
            recording = self.declaration['recording']
            self.owner = ProductionCanonicalOwner(self.source,composition=self.composition,graph=self.graph,
                worker_recorder=self.worker_recorder,
                recording={key:deepcopy(recording[key]) for key in
                    ('native_file','birth_file','max_records','max_bytes','min_free_bytes')},
                limits={'max_runtime_s':self.declaration['limits']['max_runtime_s']})
            self.owner.prepare()
            self._retain('selected_family', {'selection':FAMILY,'declaration':self.declaration,
                'source_birth':self.owner.qualification.public(),'physical_permission':False})
            self.thread = threading.Thread(target=self._run, name='home-production-command-owner', daemon=True)
            self.thread.start()

    def readiness(self):
        return {'status':'incomplete' if self.error else 'ready' if self.thread is not None
            and not self.cancel.is_set() else 'idle','selection':FAMILY,
            'native_ready':bool(self.owner and self.owner.started),'physical_permission':False}

    def _activate(self, translation):
        from room_production_canonical_manual import ProductionManualBridge
        need(not self._activation_attempted, 'production_native_activation_is_single_use')
        self._activation_attempted = True
        self.owner.require_current()
        TrustedCommittedSpotlightSample(self.source).require_current(translation)
        recording, limits = self.declaration['recording'], self.declaration['limits']
        sample_cap = int((Decimal(str(translation.valid_until))*1000000).to_integral_value(rounding=ROUND_FLOOR))
        for index in range(4):
            original = self.owner.manual_approval_template(index,
                ledger_path=Path(recording['ledger_directory'])/('fixture-' + str(index) + '.sqlite'),
                maximum_requests=limits['maximum_requests'], modes=('manual-spotlight',))
            original.update(principal_id=self.grant.principal_id,can_travel=True)
            original['caps'] = {name:str(min(int(value),sample_cap)) for name,value in original['caps'].items()}
            self.owner.authorize_manual(index, grant=self.grant, original=original,
                original_file=Path(recording['approval_directory'])/('fixture-' + str(index) + '.json'))
        self.owner.start_native_boot()
        for index in range(4):
            bridge = ProductionManualBridge(self.owner,fixture_index=index,
                authorization=self.owner.manual_inputs(index))
            self.bridges[index] = bridge
            bridge.start()

    def _consume(self, accepted):
        installed = self.source.install_commit(accepted['commit'])
        need(installed['receipt']['status'] == 'accepted', 'actual_production_accepted_commit_required')
        snapshot = self.source.current()
        results = []
        for index, fixture_id in enumerate(self.binding.fixture_ids):
            request_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                self.owner.birth['birth_id'] + ':' + accepted['commit']['id'] + ':' + fixture_id))
            try:
                translation = TrustedCommittedSpotlightSample(self.source).translate(fixture_id,request_id=request_id)
            except RoomCommitUnavailable as error:
                results.append({'fixture_id':fixture_id,'status':'unavailable','reason':str(error),'dispatched':False})
                continue
            self._retain('committed_sample_original', {'fixture_id':fixture_id,
                'room_commit_ref':translation.room_commit_ref,'publication_utf8':translation.publication_bytes.decode('utf-8'),
                'publication_sha256':translation.publication_sha256,'sample_pointer':translation.sample_pointer,
                'sample_sha256':translation.sample_sha256,'valid_until':translation.valid_until,
                'translation':translation.translation})
            if translation.translation['production_request'] is None:
                result = {'status':'unmet','translation':deepcopy(translation.translation),'dispatched':False}
            else:
                if not self.owner.started:
                    self._activate(translation)
                result = self.bridges[index].translate_and_dispatch(fixture_id,request_id=request_id)
            results.append({'fixture_id':fixture_id,**result})
        result = {'selection':FAMILY,'room_commit_ref':snapshot['room_commit_ref'],'fixtures':results,
            'native_ready':self.owner.started,'physical_permission':False,'physical_truth':False}
        self._retain('committed_sample_result', result)
        return result

    def _run(self):
        try:
            while not self.cancel.is_set():
                self._wake.wait(self.declaration['limits']['interval_s']); self._wake.clear()
                if self.cancel.is_set():
                    break
                try:
                    self.owner.clock.sample()  # Original clock epoch and finite source deadline.
                except Exception:
                    # Supervisor Stop retires acquisition after this consumer's
                    # sticky cancel. An idle clock read racing that retirement
                    # is not an in-flight native failure. Operation exceptions
                    # below remain incomplete even when Stop is concurrent.
                    if self.cancel.is_set():
                        break
                    raise
                with self.lock:
                    accepted = deepcopy(self.accepted)
                if accepted is None or accepted['commit']['id'] in self._selected:
                    continue
                self.inflight = True
                try:
                    result = self._consume(accepted)
                    with self.lock:
                        self._selected.add(accepted['commit']['id']); self.selected_commits += 1
                        self.last_result = result; self.last_commit_ref = deepcopy(result['room_commit_ref'])
                finally:
                    self.inflight = False
        except BaseException as error:
            self.error = type(error).__name__ + ':' + str(error)
            self.cancel.set()
        finally:
            self.done.set()

    def status(self):
        with self.lock:
            return {'status':'incomplete' if self.error else 'complete' if self.terminal else
                'stopping' if self.cancel.is_set() else 'running' if self.thread is not None else 'prepared',
                'selection':FAMILY,'error':self.error,'accepted_commits':self.accepted_commits,
                'selected_commits':self.selected_commits,'last_commit_ref':deepcopy(self.last_commit_ref),
                'last_result':deepcopy(self.last_result),'pending':self.pending is not None or self.inflight,
                'native_ready':bool(self.owner and self.owner.started),'physical_permission':False,'physical_truth':False}

    def quiesce(self):
        self.cancel.set(); self._wake.set()
        if self.owner is not None:
            self.owner.stop_output_admission()
        return {'status':'quiescing','physical_permission':False}

    def drain(self, timeout_s=10):
        need(type(timeout_s) in (int,float) and 0 <= timeout_s <= 10, 'bounded_production_drain_required')
        self.quiesce()
        if self.thread is not None and not self.done.wait(timeout_s):
            return {'status':'incomplete','pending':True,'safe_to_close_journal':False,'physical_permission':False}
        with self.lock:
            if self.terminal is not None:
                return deepcopy(self.terminal)
        stops, errors = [], []
        for index, bridge in self.bridges.items():
            try:
                stop = bridge.protective_stop()
                stops.append({'fixture_index':index,'receipt':stop})
                if stop.get('status') != 'protective_stop_acknowledged':
                    errors.append('protective_stop_unconfirmed:' + str(index))
                bridge.close()
            except BaseException as error:
                errors.append(type(error).__name__ + ':' + str(error))
        native = self.owner.close() if self.owner is not None else {
            'status':'complete','cleanup_unconfirmed':False,'native_processes_started':0}
        with self.lock:
            pending = self.inflight or self.writers != 0 or self.pending is not None
        safe = native.get('cleanup_unconfirmed') is False and not errors and not pending
        self.terminal = {'status':'complete' if safe and self.error is None else 'incomplete',
            'pending':bool(pending),'safe_to_close_journal':safe,'protective_stops':stops,'errors':errors,
            'native':native,'error':self.error,'physical_permission':False,'physical_truth':False}
        self._retain('command_terminal',self.terminal)
        self._sink.close()
        return deepcopy(self.terminal)


def compose_production_home_runtime(runtime, composition, *, declaration, owner_bearer, supervisor_preflight):
    """Supported explicit owner/runtime selection; never enabled by room JSON."""
    from lab_live_runtime import compose_lab_supervisor
    factory = ProductionCommandFactory(declaration,composition=composition,owner_bearer=owner_bearer)
    return compose_lab_supervisor(runtime,composition,supervisor_preflight=supervisor_preflight,
        command_factory=factory)

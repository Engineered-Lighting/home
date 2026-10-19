"""One session-owned canonical software command consumer; no physical route.

Trusted composition supplies the actual four-member plant, fixed callbacks and
recorders. Recorder callbacks retain originals only; one owned thread selects
accepted commits, performs native operations and settles cleanup.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import threading
import time

if __package__:
    from .archive import Recorder, TASK_ORIGINAL_KIND
else:
    from archive import Recorder, TASK_ORIGINAL_KIND
from journal import Journal, digest
from fixture_canonical_archive import CanonicalFixtureRecorder, canonical_recorders, KINDS
from fixture_execution_validation import require
from room_canonical_adapter import load_canonical_modules
from room_canonical_plant import RoomCanonicalPlant
from room_canonical_plant_recording import RoomCanonicalRecorder
from room_committed_source import CommittedRoomSource, RoomCommitBinding, RoomCommitUnavailable
from room_fixture_command_join import CanonicalRoomCommandJoin
from room_command_projection import retained_task_profile


class CanonicalCommandSession:
    def __init__(self, *, journal, binding, worker_recorder, canonical_recorder, plant,
                 bind_join, advance_native, current, interval_s=.02, max_commits=256, task_profile=None):
        retained_task_profile(task_profile)
        require(type(journal) is Journal and type(binding) is RoomCommitBinding
            and type(worker_recorder) is Recorder and type(canonical_recorder) is CanonicalFixtureRecorder
            and type(plant) is RoomCanonicalPlant and type(plant.recorder) is RoomCanonicalRecorder,
            'actual fixed session Journal, binding, recorders and plant required')
        require(all(callable(v) for v in (bind_join, advance_native, current))
            and type(interval_s) in (int, float) and math.isfinite(interval_s) and .01 <= interval_s <= 1
            and type(max_commits) is int and 1 <= max_commits <= 256, 'bounded installed command capabilities required')
        require(journal._recorder is None and not journal._recorder_failed
            and worker_recorder.status()['status'] == canonical_recorder.status()['status'] == 'ready'
            and plant.recorder.ready is True and not plant._started and not plant._closing
            and plant._terminal is None and plant._receipt is None, 'fresh recording before native/source admission required')
        modules = load_canonical_modules()
        header = plant.recorder.header
        archive_binding = canonical_recorder.manifest['extension']['binding']
        require(archive_binding['parent_manifest_sha256'] == digest(worker_recorder.manifest)
            and archive_binding['provider_pins'] == modules.provider_pins
            and header['producer']['manifest_sha256'] == modules.provider_pins['manifest_sha256']
            and header['producer']['source_sha256'] == modules.provider_pins['source_sha256']
            and header['producer']['config_sha256'] == modules.provider_pins['configuration_sha256']
            and header['producer']['executable_sha256'] == modules.provider_pins['executable_sha256']
            and tuple(plant.fixture_ids) == binding.fixture_ids
            and tuple(r['fixture_id'] for r in archive_binding['authorities']) == binding.fixture_ids,
            'same source-pinned worker/native/canonical inventory required')
        root = Path(__file__).resolve().parents[2]
        for relative, expected in archive_binding['source_pins'].items():
            filename = root / relative
            require(filename.is_file() and not filename.is_symlink()
                and hashlib.sha256(filename.read_bytes()).hexdigest() == expected,
                'fixed canonical session source pin differs')
        expected_room = dict(configuration_digest=binding.configuration_digest, manifest_digest=binding.manifest_digest,
            worker_version=binding.worker_version, room_output_schema='home-live-room-output-1')
        for member, authority in zip(header['fixtures'], archive_binding['authorities']):
            require(authority['home_scope'] == binding.scope and authority['room_binding'] == expected_room
                and authority['device_id'] == member['device_id']
                and authority['context']['boot_generation'] == member['boot'], 'fixed canonical authority member differs')
        self.journal, self.binding = journal, deepcopy(binding)
        self.worker_recorder, self.canonical_recorder, self.plant = worker_recorder, canonical_recorder, plant
        self.bind_join, self.advance_native, self.current = bind_join, advance_native, current
        self.task_profile = deepcopy(task_profile)
        self.interval, self.max_commits = interval_s, max_commits
        self.lock = threading.RLock()
        self.cancel, self.drain_requested, self.done = threading.Event(), threading.Event(), threading.Event()
        self.thread = self.source = self.join = None
        self.state, self.error = 'prepared', None
        self.native_started = self.native_ready = self.inflight = False
        self.pending = self.accepted = None
        self.seen_prepared, self.seen_results = {}, {}
        self.seen_resets, self.abandoned = {}, {}
        self.accepted_commits = self.selected_commits = self.selection = 0
        self.last_commit_ref = self.last_result = None
        self.terminal = self.frozen_terminal = None
        self.writers = 0

    def _poison(self, reason):
        with self.lock:
            if self.error is None: self.error = reason
            self.cancel.set()

    @staticmethod
    def _ack(value):
        require(type(value) is dict and set(value) == {'schema', 'version', 'seq', 'digest'}
            and value['schema'] == 'home-live-recorded-1' and value['version'] == 1
            and type(value['seq']) is int and value['seq'] >= 0 and type(value['digest']) is str
            and len(value['digest']) == 64 and all(c in '0123456789abcdef' for c in value['digest']),
            'original durable recorder ACK required')

    def record(self, body, *, record_id=None):
        # Never hold this lock while entering a recorder, Journal or native port.
        with self.lock: self.writers += 1
        try:
            if body.get('kind') == TASK_ORIGINAL_KIND:
                # This new data-only original belongs to the parent causal
                # stream. Keep the historical canonical router unchanged.
                manifest = self.worker_recorder.manifest
                require(retained_task_profile(self.task_profile) and self.worker_recorder.task_originals
                        and manifest.get('schema') == 'home-live-session-manifest-2'
                        and manifest.get('version') == 2
                        and manifest.get('runtime_profile') == 'home-live-runtime-config-3',
                        'assessed_task_original_session_profile_required')
                acknowledgement = self.worker_recorder.record(body, record_id=record_id)
            else:
                route = canonical_recorders(lambda value: self.worker_recorder.record(value, record_id=record_id),
                                             self.canonical_recorder)
                if body.get('kind') in KINDS: require(record_id is None, 'canonical transaction identity only')
                acknowledgement = route(body)
            self._ack(acknowledgement)
            if body['kind'] in ('worker_prepared', 'worker_commit_result',
                                'worker_restore', 'worker_cold_start'):
                with self.lock: self._pair(body, acknowledgement)
            return acknowledgement
        except BaseException:
            self._poison('command_recording_or_pairing_incomplete')
            raise
        finally:
            with self.lock: self.writers -= 1

    def _pair(self, body, acknowledgement):
        data, kind = body['data'], body['kind']
        if kind in ('worker_restore', 'worker_cold_start'):
            self._reset(body, acknowledgement)
            return
        original_digest = digest(body)
        if kind == 'worker_prepared':
            commit = data['commit']; identity = commit['id']
            require(type(identity) is str and 0 < len(identity) <= 160
                and commit['worker_id'] == self.binding.worker_id and commit['producer_id'] == self.binding.producer_id
                and commit['site_id'] == self.binding.scope['site_id'] and commit['room_id'] == self.binding.scope['room_id']
                and commit['checkpoint']['scope'] == self.binding.scope
                and commit['checkpoint']['config_digest'] == self.binding.configuration_digest
                and commit['checkpoint']['manifest_ref']['digest'] == self.binding.manifest_digest
                and data['prepared']['commit_id'] == identity
                and data['prepared']['checkpoint'] == commit['checkpoint']
                and data['prepared']['publications'] == commit['publications'], 'fixed original prepared commit required')
            if identity in self.seen_prepared:
                require(self.seen_prepared[identity] == original_digest, 'changed prepared original retry')
                return
            require(self.pending is None and len(self.seen_prepared) < self.max_commits,
                    'one pending bounded original commit required')
            self.seen_prepared[identity] = original_digest
            self.pending = dict(commit=deepcopy(commit), digest=digest(commit), prepared_ack=deepcopy(acknowledgement))
        else:
            receipt = data['receipt']; identity = receipt['id']
            if identity in self.seen_results:
                require(self.seen_results[identity] == original_digest, 'changed accepted original retry')
                return
            pending = self.pending
            require(pending is not None and pending['commit']['id'] == identity and receipt['status'] == 'accepted'
                and receipt['commit_digest'] == pending['digest']
                and data['checkpoint'] == pending['commit']['checkpoint']
                and receipt['input_cursor'] == pending['commit']['input_cursor'], 'matching accepted original commit required')
            self.seen_results[identity] = original_digest
            self.accepted_commits += 1
            self.accepted = {**pending, 'accepted_ack': deepcopy(acknowledgement), 'number': self.accepted_commits}
            self.pending = None

    def _reset(self, body, acknowledgement):
        data, kind = body['data'], body['kind']
        expected = {'checkpoint', 't', 'allow_expired'} if kind == 'worker_restore' else {'checkpoint', 'reset_transport'}
        require(type(data) is dict and set(data) == expected, 'original worker reset required')
        checkpoint = data['checkpoint']
        if kind == 'worker_restore':
            require(type(checkpoint) is dict
                and type(data['t']) in (int, float) and math.isfinite(data['t']) and data['t'] >= 0
                and type(data['allow_expired']) is bool, 'original worker restore required')
        else:
            require((checkpoint is None or type(checkpoint) is dict)
                and type(data['reset_transport']) is bool, 'original worker cold start required')
        if checkpoint is not None:
            require(checkpoint.get('worker_id') == self.binding.worker_id
                and checkpoint.get('scope') == self.binding.scope
                and checkpoint.get('config_digest') == self.binding.configuration_digest
                and type(checkpoint.get('manifest_ref')) is dict
                and checkpoint['manifest_ref'].get('digest') == self.binding.manifest_digest,
                'fixed original worker reset checkpoint required')
        identity = acknowledgement['seq']
        signature = (acknowledgement['digest'], digest(body))
        if identity in self.seen_resets:
            require(self.seen_resets[identity] == signature, 'changed worker reset original retry')
            return
        require(len(self.seen_resets) < self.worker_recorder.max_records,
                'bounded original worker resets required')
        self.seen_resets[identity] = signature
        pending = self.pending
        if pending is None or identity <= pending['prepared_ack']['seq']:
            return
        # This records command-selection abandonment, not proof of Journal rejection.
        commit_id = pending['commit']['id']
        self.abandoned[commit_id] = dict(commit_id=commit_id, commit_digest=pending['digest'],
            prepared_ack=deepcopy(pending['prepared_ack']), reset_kind=kind,
            reset_original_digest=signature[1], reset_ack=deepcopy(acknowledgement),
            command_selection='abandoned')
        self.pending = None

    def readiness(self):
        with self.lock:
            if self.frozen_terminal is not None:
                return dict(status='incomplete', state=self.frozen_terminal['status'],
                    missing=['waiting_accepted_commit'] if self.join is None else [],
                    error=self.frozen_terminal['error'])
            status = 'incomplete' if self.error else 'ready' if self.native_ready and not self.cancel.is_set() else 'starting'
            return dict(status=status, state=self.state,
                missing=['waiting_accepted_commit'] if self.join is None else [], error=self.error)

    def status(self):
        with self.lock:
            terminal = self.frozen_terminal
            state = terminal['status'] if terminal is not None else self.state
            return dict(status=state if terminal is not None else 'incomplete' if self.error else state, state=state,
                missing=['waiting_accepted_commit'] if self.join is None else [],
                error=terminal['error'] if terminal is not None else self.error,
                accepted_commits=self.accepted_commits, selected_commits=self.selected_commits,
                last_commit_ref=deepcopy(self.last_commit_ref), last_result=deepcopy(self.last_result),
                pending=terminal['pending'] if terminal is not None else bool(self.pending is not None or self.inflight), physical_permission=False,
                combined_replay='unproven')

    def start(self):
        with self.lock:
            require(self.thread is None and not self.cancel.is_set(), 'command owner is single-use')
            require(self.journal._recorder == self.record, 'sole Journal must attach fixed command recording first')
            self.state = 'starting'
            self.thread = threading.Thread(target=self._run, name='den-canonical-command-owner', daemon=True)
            self.thread.start()
        return self.readiness()

    def _call(self, callback, *args):
        with self.lock: self.inflight = True
        try: return callback(*args)
        finally:
            with self.lock: self.inflight = False

    @staticmethod
    def _supported(snapshot, *, task_profile=None):
        if retained_task_profile(task_profile):
            output, now = snapshot['decision']['room_output'], snapshot['checked_at']
            task = output['task']
            if (task.get('fixture_id') is not None and task['allocation'].get('status') == 'allocated'
                    and task['execution'].get('status') == 'virtual' and task['display_lease'].get('status') == 'current'
                    and now < min(task['allocation']['valid_until'], task['execution']['valid_until'],
                                  task['display_lease']['valid_until'])):
                members = [f for f in output['fixtures'] if f['fixture_id'] == task['fixture_id']]
                if len(members) == 1:
                    row = members[0]['domains'][7]
                    if row['status'] in ('modeled', 'off') and row['sample'] is not None and now < row['valid_until']:
                        return True
            # Nullable inventory does not require invented support or zeros on
            # the other members merely to consume a genuinely supported row.
            return any(d['status'] in ('modeled', 'off') and d['sample'] is not None
                       and d['sample']['brightness_fraction'] > 0 and now < d['valid_until']
                       for f in output['fixtures'] for d in f['domains'][:7])
        # A real positive committed ambient sample must precede lazy binding.
        # Other fixtures may be explicitly supported off, never unavailable zero.
        positive = False
        for fixture in snapshot['decision']['room_output']['fixtures']:
            eligible = [d for d in fixture['domains'][:7] if d['status'] in ('modeled', 'off')
                and d['sample'] is not None and snapshot['checked_at'] < d['valid_until']]
            if not eligible: return False
            positive |= any(d['sample']['brightness_fraction'] > 0 for d in eligible)
        return positive

    def _bind(self):
        candidate = self._call(self.bind_join, self.source)
        require(type(candidate) is CanonicalRoomCommandJoin and candidate.source is self.source
            and candidate.task_profile == self.task_profile
            and tuple(m.fixture_id for m in candidate.members) == self.binding.fixture_ids,
            'actual same-Journal canonical join required')
        authorities = self.canonical_recorder.manifest['extension']['binding']['authorities']
        for member, row in zip(candidate.members, authorities):
            require(member.adapter.plant is self.plant and member.dispatcher.journal is self.journal
                and member.dispatcher.authority.namespace == row['namespace']
                and member.dispatcher.authority.policy_digest == row['policy_sha256']
                and member.dispatcher.policy_digest == row['dispatch_policy_sha256']
                and member.adapter.binding.binding_sha256 == row['native_binding_sha256'],
                'fixed recorded authority/dispatcher/native binding differs')
        self.join = candidate

    def _select(self):
        with self.lock:
            selected = deepcopy(self.accepted)
            if selected is None or selected['number'] == self.selection: return
        try:
            snapshot = self.source.install_commit(selected['commit'])
        except RoomCommitUnavailable:
            # A newer accepted checkpoint may supersede a queued original.
            # Its original remains recorded; wait for its exact matching pair.
            return
        if self.cancel.is_set(): return
        if self.join is None:
            if not self._supported(snapshot, task_profile=self.task_profile): return
            self._bind()
        if self.cancel.is_set(): return
        result = self._call(self.join.dispatch_current)
        with self.lock:
            self.selection = selected['number']; self.selected_commits += 1
            self.last_commit_ref = deepcopy(snapshot['room_commit_ref'])
            self.last_result = deepcopy(result); self.state = 'running'

    def _run(self):
        try:
            if not self.cancel.is_set():
                self.source = CommittedRoomSource(self.journal, binding=self.binding,
                    current=lambda: not self.cancel.is_set() and self.current() is True)
                self._call(self.plant.start)
                self.native_started = True
                with self.lock: self.native_ready, self.state = True, 'waiting_accepted_commit'
            while not self.drain_requested.is_set():
                if not self.cancel.is_set():
                    self._select()
                    if not self.cancel.is_set():
                        progress = self._call(self.advance_native, self.plant)
                        require(type(progress) is dict and progress.get('status') in ('advanced', 'waiting'),
                                'fixed bounded native progress receipt required')
                self.drain_requested.wait(self.interval)
        except BaseException:
            self._poison('command_operation_incomplete')
            self.drain_requested.wait()
        finally:
            self._cleanup()
            self.done.set()

    def quiesce(self):
        self.cancel.set()
        with self.lock:
            return dict(status='quiescing', pending=bool(self.inflight or self.writers), physical_permission=False)

    def _cleanup(self):
        safe, complete, native, canonical = False, False, None, None
        try:
            if self.join is not None:
                self._call(self.join.quiesce)
                joined = self._call(self.join.drain)
                safe = joined.get('safe_to_close_journal') is True
                complete = safe and all(row['receipt'].get('status') == 'drained' for row in joined['fixtures'])
            else:
                safe = True; complete = self.native_started
            with self.lock: safe = safe and self.writers == 0
            if safe:
                native = self._call(self.plant.close)
                safe = type(native) is dict and native.get('cleanup_unconfirmed') is False
                complete = complete and safe and native.get('status') == 'complete' and self.error is None and self.pending is None
                if safe:
                    canonical = self.canonical_recorder.drain(dict(status='complete' if complete else 'incomplete',
                        reason=self.error or 'command_session_stopped', physical_permission=False))
                    complete = complete and canonical.get('status') == 'drained'
            receipt = dict(status='drained' if complete else 'incomplete' if safe else 'unconfirmed',
                safe_to_close_journal=safe, pending=not safe or self.pending is not None, native_receipt=native,
                canonical_recording=canonical, error=self.error, combined_replay='unproven', physical_permission=False)
        except BaseException:
            receipt = dict(status='unconfirmed', safe_to_close_journal=False, pending=True,
                native_receipt=native, canonical_recording=canonical, error=self.error or 'command_cleanup_unconfirmed',
                combined_replay='unproven', physical_permission=False)
        with self.lock:
            self.terminal = receipt; self.state = receipt['status']

    def drain(self, timeout_s):
        require(type(timeout_s) in (int, float) and math.isfinite(timeout_s) and 0 <= timeout_s <= 10,
                'bounded command drain required')
        self.quiesce(); self.drain_requested.set()
        with self.lock:
            if self.frozen_terminal is not None: return deepcopy(self.frozen_terminal)
            if self.thread is None:
                # Cancellation before Start still owns the opened archives.
                self.thread = threading.Thread(target=self._run, name='den-canonical-command-owner', daemon=True)
                self.thread.start()
        settled = self.done.wait(timeout_s)
        with self.lock:
            if self.frozen_terminal is None:
                self.frozen_terminal = deepcopy(self.terminal) if settled else dict(status='unconfirmed',
                    safe_to_close_journal=False, pending=True, native_receipt=None, canonical_recording=None,
                    error='command_drain_deadline', combined_replay='unproven', physical_permission=False)
            return deepcopy(self.frozen_terminal)

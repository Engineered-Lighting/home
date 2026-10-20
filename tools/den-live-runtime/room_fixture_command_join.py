"""Accepted Home samples -> pinned canonical ambient commands, software only.

No scheduler, bootstrap, coordinate mapper, emitter model or physical transport
is introduced here. The installed source, same-Journal issuer/outbox and fixed
canonical adapter retain those responsibilities. Spotlight semantics refuse
until their separately versioned mapping exists.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
import threading

from fixture_canonical_authority import CanonicalAuthorityState, CanonicalNativeBootstrapOriginal
from fixture_canonical_dispatch import CanonicalFixtureDispatcher
from fixture_execution_validation import CAP_FIELDS, MAX_U64, FixtureAuthorityDenied, require
from journal import digest, finite
from room_canonical_adapter import (CanonicalRoomObservation, RoomCanonicalAdapter,
    SimulatedClockObservation, SimulatedClockSuccessor, load_canonical_modules)
from room_command_projection import RoomCommandProjector, MIXER_SOURCE_SHA256
from room_command_projection import retained_task_profile
from room_committed_source import CommittedRoomSource
from room_delivery import DOMAINS, room_policy_view
from fixture_joint_execution import JointFixtureCompiler, RETAINED_REQUEST_SCHEMA, RETAINED_REQUEST_FIELDS

TASK_REFUSAL = 'spotlight_semantic_CCT_mapping_unavailable'
AMBIENT = 'autonomous-ambient'
TASK = 'autonomous-task'


@dataclass(frozen=True)
class RoomRetainedTaskAssessment:
    """A retained fictional original, never a qualification or callback.

    The full request includes independently authored range/path checks and its
    original clocks. A future operational assessor must derive these from the
    actual cut; this port does not manufacture them from commanded angles.
    """
    request: dict
    path_witness: dict | None = None
    assessor_ref: dict | None = None

    def __post_init__(self):
        require(type(self.request) is dict and set(self.request) == RETAINED_REQUEST_FIELDS
                and self.request.get('schema') == RETAINED_REQUEST_SCHEMA
                and type(self.request.get('version')) is int and self.request['version'] == 2,
                'retained_angle_request2_original_required')
        original = deepcopy(self.request)
        object.__setattr__(self, 'request', original)
        object.__setattr__(self, 'sha256', digest(original))
        require((self.path_witness is None) == (self.assessor_ref is None), 'complete_retained_path_original_required')
        object.__setattr__(self, 'path_witness', deepcopy(self.path_witness))
        object.__setattr__(self, 'assessor_ref', deepcopy(self.assessor_ref))
        object.__setattr__(self, 'evidence_sha256', digest(dict(path_witness=self.path_witness, assessor_ref=self.assessor_ref)))
        if self.path_witness is not None:
            from archive import validate_task_assessment_original
            validate_task_assessment_original(self.record_data(fixture_id=original['scope']['fixture_id'],
                profile_sha256=original['assessment']['profile_sha256'], room_commit_ref=original['retained_aim_ref']['room_commit_ref']))

    def original(self):
        require(digest(self.request) == self.sha256 and self.evidence_sha256 == digest(dict(
            path_witness=self.path_witness, assessor_ref=self.assessor_ref)), 'retained_task_assessment_original_changed')
        return deepcopy(self.request)

    def record_data(self, *, fixture_id, profile_sha256, room_commit_ref):
        data = dict(fixture_id=fixture_id, assessment=self.original(), assessment_sha256=self.sha256,
                    profile_sha256=profile_sha256, room_commit_ref=deepcopy(room_commit_ref))
        if self.path_witness is not None:
            data.update(schema='home-room-task-assessment-original-2', version=2,
                path_witness=deepcopy(self.path_witness), assessor_ref=deepcopy(self.assessor_ref))
        return data


def canonical_dispatch_outcome(request_id, dispatch, *, exact_retry=False):
    """Preserve an original outbox result, including an uncertain native effect."""
    require(type(dispatch) is dict and dispatch.get('request_id') == request_id,
            'exact original dispatch request required')
    record = dispatch.get('record') or {}
    state, disposition = dispatch.get('state'), record.get('adapter_disposition')
    if state == 'terminal' and disposition == 'ADMITTED':
        status = 'dispatched'
    elif state in ('refused', 'abandoned') or state == 'terminal' and disposition in ('REFUSED', 'REJECTED'):
        status = 'refused'
    else:
        # Reserved/prepared/attempting, a lost retention ACK, or any unknown
        # effect is pending/unconfirmed. No repeat invocation follows from it.
        status = 'unconfirmed'
    return dict(status=status, request_id=request_id, dispatch=deepcopy(dispatch), exact_retry=exact_retry)


def _us(seconds):
    require(finite(seconds) and seconds >= 0, 'finite original semantic time required')
    value = int((Decimal(str(seconds)) * 1000000).to_integral_value(rounding=ROUND_FLOOR))
    require(0 <= value <= MAX_U64, 'original time outside canonical clock')
    return value


@dataclass(frozen=True)
class RoomCommandGeneration:
    """Installed output capability, independent of camera source epochs."""
    command_source_generation: int
    authority_not_after_home_us: int


class RoomCommandStateResolver:
    def __init__(self, source, projector, fixture_id, *, modules, native_binding,
                 initial_observation, clock_observer, generation, task_compiler=None, task_assessment=None,
                 retained_task_assessor=None):
        require(type(source) is CommittedRoomSource and type(projector) is RoomCommandProjector
                and projector.source is source, 'one actual committed room source required')
        require(modules is load_canonical_modules() and type(native_binding) is modules.zha.RuntimeBinding,
                'fixed pinned canonical modules/binding required')
        require(fixture_id in source.binding.fixture_ids and callable(clock_observer)
                and type(generation) is RoomCommandGeneration
                and type(generation.command_source_generation) is int
                and 0 < generation.command_source_generation <= MAX_U64
                and type(generation.authority_not_after_home_us) is int
                and 0 < generation.authority_not_after_home_us <= MAX_U64,
                'fixed member, clock and installed command generation required')
        self.source, self.projector, self.fixture_id = source, projector, fixture_id
        self.modules, self.binding, self.generation = modules, native_binding, generation
        self.clock_observer = clock_observer
        self._clock_original = self._last_clock = None
        self._observation = None
        self.task_compiler, self.task_assessment = None, None
        self.task_assessor = retained_task_assessor
        if retained_task_assessor is not None:
            from room_retained_task_assessor import TrustedRetainedTaskAssessor
            require(type(retained_task_assessor) is TrustedRetainedTaskAssessor,
                    'fixed_retained_task_assessor_capability_required')
            retained_task_assessor.require_current()
        self.task_previous, self.task_prepared, self.task_start_deadlines = None, {}, {}
        self._task_assessment_lock = threading.RLock()
        self._task_assessment_pending = None
        if task_compiler is not None:
            require(retained_task_profile(projector.task_profile)
                and type(task_compiler) is JointFixtureCompiler and task_compiler.binding == native_binding
                and task_compiler.p['fixture_id'] == fixture_id
                and task_compiler.provider_pins == modules.provider_pins
                and task_compiler.decode_status is modules.zha.decode_snapshot_v3
                and task_compiler.decode_capabilities is modules.zha.decode_capabilities_v2,
                'fixed_same_member_retained_joint_compiler_required')
            self.task_compiler = task_compiler
        if task_assessment is not None:
            self.install_task_assessment(task_assessment)
        self.replace_observation(initial_observation)

    def install_task_assessment(self, original):
        require(self._task_assessment_lock.acquire(False), 'retained_task_assessment_pending')
        try:
            require(self._task_assessment_pending is None, 'retained_task_assessment_pending')
            require(retained_task_profile(self.projector.task_profile) and self.task_compiler is not None
                    and type(original) is RoomRetainedTaskAssessment,
                    'fixed_retained_task_assessment_original_required')
            original.original()
            self.task_assessment = original
        finally:
            self._task_assessment_lock.release()

    def _pin_task_assessment(self, request_id):
        with self._task_assessment_lock:
            require(self._task_assessment_pending is None, 'retained_task_assessment_pending')
            require(type(self.task_assessment) is RoomRetainedTaskAssessment, 'task_assessment_unavailable')
            self.task_assessment.original()
            pending = dict(request_id=request_id, original=self.task_assessment,
                           sha256=self.task_assessment.sha256, reservation_attempted=False)
            self._task_assessment_pending = pending
            return pending

    def _finish_task_assessment(self, pending):
        with self._task_assessment_lock:
            require(self._task_assessment_pending is pending, 'retained_task_assessment_pin_changed')
            self._task_assessment_pending = None

    def _task_assessment_original(self):
        with self._task_assessment_lock:
            original = self.task_assessment
            require(type(original) is RoomRetainedTaskAssessment, 'task_assessment_unavailable')
            pending = self._task_assessment_pending
            require(pending is None or pending['original'] is original and pending['sha256'] == original.sha256,
                    'retained_task_assessment_pin_changed')
            return original.original()

    def _fixture(self, policy):
        view = room_policy_view(policy, allow_assessed_subset=True) if retained_task_profile(self.projector.task_profile) else policy
        return next(f for f in view['fixtures'] if f['fixture_id'] == self.fixture_id)

    def _clock(self):
        c = self.clock_observer()
        require(type(c) is SimulatedClockObservation and all(type(getattr(c, k)) is int
                and 0 <= getattr(c, k) <= MAX_U64 for k in c.__dataclass_fields__),
                'closed original simulated clock required')
        require(c.mapping_revision > 0 and c.observed_home_us <= c.now_home_us
                and c.now_home_us + c.uncertainty_us < c.qualified_until_home_us
                and c.device_now_lower_us <= c.now_device_us <= c.device_now_upper_us
                and c.device_now_upper_us - c.device_now_lower_us <= 2 * c.uncertainty_us,
                'exclusive original simulated clock bound required')
        original = (c.observed_home_us, c.qualified_until_home_us, c.mapping_revision, c.uncertainty_us)
        require(self._clock_original is None or self._clock_original == original,
                'clock renewal requires explicit successor')
        require(self._last_clock is None or c.now_home_us >= self._last_clock.now_home_us
                and c.now_device_us >= self._last_clock.now_device_us, 'command clock rollback')
        self._clock_original, self._last_clock = original, c
        return c

    def replace_observation(self, observation):
        """Only the sole adapter owner refreshes originals, before a new plan."""
        with self.source.journal.lock:
            require(type(observation) is CanonicalRoomObservation
                    and type(observation.current_ref) is self.modules.outputs.CurrentStateReference,
                    'actual canonical original observation required')
            ref, c = observation.current_ref, self._clock()
            require(observation.clock_mapping_revision == c.mapping_revision
                    and ref.binding_sha256 == self.binding.binding_sha256
                    and ref.context_digest == self.binding.context_digest.hex()
                    and ref.evidence_source == 'simulated', 'native original binding/mapping differs')
            # These decoders verify all original context/pin/hash fields again.
            self.modules.zha.decode_snapshot_v3(observation.raw_status_v3, binding=self.binding,
                captured_device_us=ref.captured_device_us)
            self.modules.zha.decode_capabilities_v2(observation.raw_capabilities_v2,
                self.modules.zha.decode_context(self.binding.context)['pins'])
            require(self._observation is None or ref.captured_device_us >= self._observation.current_ref.captured_device_us,
                    'native observation capture rollback')
            self._observation = deepcopy(observation)

    def accept_simulated_clock_successor(self, adapter, receipt):
        """Accept only this actual adapter's retained, horizon-preserving change.

        An earlier STATUS is discarded even though its bytes remain archived.
        The owner must obtain a fresh original before the next grant/dispatch.
        """
        with self.source.journal.lock:
            require(type(adapter) is RoomCanonicalAdapter and adapter.binding == self.binding
                and adapter.dispatcher.journal is self.source.journal
                and type(receipt) is SimulatedClockSuccessor
                and adapter.last_clock_successor is receipt
                and receipt.native_binding_sha256 == self.binding.binding_sha256
                and self._clock_original is not None
                and receipt.previous_mapping_revision == self._clock_original[2]
                and receipt.mapping_revision == receipt.previous_mapping_revision + 1
                and receipt.qualified_until_home_us == self._clock_original[1]
                and receipt.uncertainty_us == self._clock_original[3]
                and receipt.grant_caps_renewed is False,
                'actual retained same-member clock successor required')
            current = adapter.clock_observation()
            require(current.mapping_revision == receipt.mapping_revision
                and current.observed_home_us == receipt.observed_home_us
                and current.qualified_until_home_us == receipt.qualified_until_home_us
                and current.uncertainty_us == receipt.uncertainty_us
                and (self._last_clock is None or current.now_home_us >= self._last_clock.now_home_us
                     and current.now_device_us >= self._last_clock.now_device_us),
                'current same-horizon successor clock required')
            self._clock_original = (current.observed_home_us, current.qualified_until_home_us,
                current.mapping_revision, current.uncertainty_us)
            self._observation = None
            self._clock()

    def _selection(self):
        """Independent derivation from raw accepted samples, not projector output."""
        self.projector._installed()
        snapshot = self.source.current()
        policy = self.projector.policy
        require(digest(policy) == self.projector.policy_sha256, 'installed room policy changed')
        fixture = self._fixture(policy)
        raw = next(f for f in snapshot['decision']['room_output']['fixtures'] if f['fixture_id'] == self.fixture_id)
        require(len(raw['domains']) == 8 and tuple(d['domain'] for d in raw['domains']) == (*DOMAINS, 'spotlight')
                and all(raw[k] == fixture[k] for k in ('fixture_id', 'mount_id', 'profile_ref')),
                'exact eight-domain member required')
        now = snapshot['checked_at']
        changes, proofs, horizons = [], [], []
        for zone, row in enumerate(raw['domains'][:7]):
            if fixture.get('assessment_available') is False or fixture['ambient'][zone].get('assessment_available') is False:
                require(row['status'] == 'unavailable' and row['sample'] is None,
                        'unassessed_ambient_cannot_supply_command')
                continue
            until = min(row['valid_until'], policy['valid_until'], policy['exposure']['valid_until'],
                        fixture['valid_until'], fixture['ambient'][zone]['valid_until'])
            if row['status'] not in ('modeled', 'off') or row['sample'] is None or not now < until:
                continue
            sample = row['sample']
            require(set(sample) == {'brightness_fraction', 'cct_kelvin'}
                    and finite(sample['brightness_fraction']) and 0 <= sample['brightness_fraction'] <= 1
                    and finite(sample['cct_kelvin']) and 1500 <= sample['cct_kelvin'] <= 6500,
                    'closed bounded accepted ambient sample required')
            require(row['status'] != 'off' or sample['brightness_fraction'] == 0, 'off is not positive ambient')
            fraction, kelvin = Decimal(str(sample['brightness_fraction'])), Decimal(str(sample['cct_kelvin']))
            level = int((fraction * 254).to_integral_value(rounding=ROUND_HALF_UP))
            mired = int((Decimal(1000000) / kelvin).to_integral_value(rounding=ROUND_HALF_UP))
            # Unsupported representation is unavailable, never clamped.
            if not 154 <= mired <= 556:
                continue
            changes.append(dict(zone=zone, level=level, mired=mired))
            proofs.append(dict(zone=zone, evidence_refs=deepcopy(row['evidence_refs']),
                assessment_refs=deepcopy(row['assessment_refs']), valid_until=row['valid_until']))
            horizons.append(_us(until))
        require(bool(changes), 'no supported current committed ambient samples')
        return snapshot, fixture, changes, proofs, min(horizons)

    def bootstrap_original(self):
        """Read this exact current room/native owner without selecting a command."""
        with self.source.journal.lock:
            self.projector._installed()
            snapshot = self.source.current()
            c, observation = self._clock(), self._observation
            require(observation is not None and observation.clock_mapping_revision == c.mapping_revision
                    and abs(c.now_home_us - _us(snapshot['checked_at'])) < 250000,
                    'current bootstrap native/room clock original required')
            ref = replace(observation.current_ref, device_now_lower_us=c.device_now_lower_us,
                          device_now_upper_us=c.device_now_upper_us)
            return CanonicalNativeBootstrapOriginal(deepcopy(snapshot['home_scope']),
                deepcopy(snapshot['room_commit_ref']),
                str(snapshot['commit']['checkpoint']['authority_generation']),
                c.now_home_us, c.qualified_until_home_us, c.uncertainty_us, ref,
                observation.raw_status_v3, observation.raw_capabilities_v2)

    def _resolve(self):
        snapshot, fixture, changes, proofs, support = self._selection()
        c, observation = self._clock(), self._observation
        require(observation is not None, 'fresh native original required after clock successor')
        require(observation.clock_mapping_revision == c.mapping_revision, 'original clock mapping changed')
        require(abs(c.now_home_us - _us(snapshot['checked_at'])) < 250000,
                'Home semantic/native clock views disagree')
        ref = replace(observation.current_ref, device_now_lower_us=c.device_now_lower_us,
                      device_now_upper_us=c.device_now_upper_us)
        require(ref.captured_device_us <= c.device_now_lower_us
                and c.device_now_upper_us - ref.captured_device_us < ref.maximum_status_age_us,
                'cached original native status expired')
        authority_cap = min(self.generation.authority_not_after_home_us,
            _us(self.projector.policy['valid_until']), _us(fixture['valid_until']),
            _us(self.projector.policy['exposure']['valid_until']))
        caps = dict(zip(CAP_FIELDS, map(str, (support, support, authority_cap, c.qualified_until_home_us))))
        require(c.now_home_us + c.uncertainty_us < min(map(int, caps.values())), 'committed support expired')
        cut = snapshot['decision']['input_cut']
        state = CanonicalAuthorityState(True, deepcopy(snapshot['home_scope']), deepcopy(snapshot['room_commit_ref']),
            str(self.generation.command_source_generation), str(snapshot['commit']['checkpoint']['authority_generation']),
            cut['cut_digest'], digest(dict(input_cut=cut, selected_domain_proofs=proofs,
                registration=self.projector.policy['registration'])),
            digest(dict(room_policy_sha256=self.projector.policy_sha256, fixture_id=self.fixture_id,
                native_binding_sha256=self.binding.binding_sha256, home_scope=snapshot['home_scope'],
                authority_generation=snapshot['commit']['checkpoint']['authority_generation'],
                command_generation=self.generation.command_source_generation, authority_cap=authority_cap)),
            str(_us(snapshot['sample_at'])), caps, ref, observation.raw_status_v3, observation.raw_capabilities_v2)
        return state, changes, c

    def resolve(self, mode):
        require(mode == AMBIENT or mode == TASK and retained_task_profile(self.projector.task_profile), TASK_REFUSAL)
        with self.source.journal.lock:
            return self._resolve_task()[0] if mode == TASK else self._resolve()[0]

    def assessment_basis(self):
        self.projector._installed()
        require(retained_task_profile(self.projector.task_profile), TASK_REFUSAL)
        snapshot = self.source.current()
        projected = self.projector.project()
        require(snapshot['room_commit_ref'] == projected['room_commit_ref'], 'task_commit_changed_during_resolution')
        selected = next(f for f in projected['fixtures'] if f['fixture_id'] == self.fixture_id)
        task, fixture = selected['task'], self._fixture(self.projector.policy)
        require(task['status'] == 'retained_aim_candidate' and task['retained_motion'] is not None
                and fixture['task_policy'] is not None, 'current_assessed_retained_task_required')
        require(self.task_compiler is not None, 'task_assessment_unavailable')
        retained = task['retained_motion']
        basis = retained['fixture_basis']; geometry = self.task_compiler.p['geometry']
        require(geometry['optical_pivot_m'] == basis['origin_m']
                and geometry['fixture_to_room_basis'] == basis['local_to_room'],
                'retained_task_source_or_fixture_basis_changed')
        c, observation = self._clock(), self._observation
        require(observation is not None and observation.clock_mapping_revision == c.mapping_revision
                and abs(c.now_home_us - _us(snapshot['checked_at'])) < 250000,
                'current_task_native_original_required')
        ref = replace(observation.current_ref, device_now_lower_us=c.device_now_lower_us,
                      device_now_upper_us=c.device_now_upper_us)
        require(ref.captured_device_us <= c.device_now_lower_us
                and c.device_now_upper_us - ref.captured_device_us < ref.maximum_status_age_us,
                'cached_original_native_status_expired')
        support = _us(retained['support_until'])
        authority_cap = min(self.generation.authority_not_after_home_us, _us(fixture['valid_until']),
                            _us(self.projector.policy['valid_until']), _us(self.projector.policy['exposure']['valid_until']))
        caps = dict(zip(CAP_FIELDS, map(str, (support, support, authority_cap, c.qualified_until_home_us))))
        require(c.now_home_us + c.uncertainty_us < min(map(int, caps.values())), 'committed_task_support_expired')
        cut = snapshot['decision']['input_cut']
        state = CanonicalAuthorityState(True, deepcopy(snapshot['home_scope']), deepcopy(snapshot['room_commit_ref']),
            str(self.generation.command_source_generation), str(snapshot['commit']['checkpoint']['authority_generation']),
            cut['cut_digest'], digest(dict(input_cut=cut, geometry_refs=retained['geometry_refs'],
                protection_refs=retained['protection_refs'], execution_assessment_refs=retained['execution_assessment_refs'])),
            digest(dict(room_policy_sha256=self.projector.policy_sha256, fixture_id=self.fixture_id,
                native_binding_sha256=self.binding.binding_sha256, home_scope=snapshot['home_scope'],
                authority_generation=snapshot['commit']['checkpoint']['authority_generation'],
                command_generation=self.generation.command_source_generation, authority_cap=authority_cap)),
            str(_us(snapshot['sample_at'])), caps, ref, observation.raw_status_v3, observation.raw_capabilities_v2)
        scene = snapshot['decision']['scene']
        correction = None
        if scene.get('correction_refs'):
            corrections = snapshot['commit']['checkpoint']['state']['scene_and_corrections']['state']['corrections']
            exact_corrections = [row['intent_ref'] for row in corrections
                if row['scene_id'] == scene['scene_id'] and row['intent_ref']['id'] in scene['correction_refs']]
            require(len(exact_corrections) == 1 and len(scene['correction_refs']) == 1,
                    'exact_retained_task_correction_required')
            correction = dict(intent_id=exact_corrections[0]['id'], body_sha256=exact_corrections[0]['payload_digest'])
        require(scene['status'] == 'selected', 'retained_task_scene_or_correction_changed')
        return state, deepcopy(retained), c, snapshot, correction

    def _resolve_task(self):
        state, retained, c, snapshot, correction = self.assessment_basis()
        require(type(self.task_assessment) is RoomRetainedTaskAssessment, 'task_assessment_unavailable')
        original = self._task_assessment_original()
        require(original['retained_aim_ref'] == retained['retained_aim_ref']
                and original['fixture_angles_deg'] == [retained['original']['to_pan_deg'], retained['original']['to_tilt_deg']],
                'retained_task_source_or_fixture_basis_changed')
        require(original['original_caps'] == {k: int(v) for k, v in state.caps.items()}
                and original['scope'] == dict(home_id=state.home_scope['site_id'],
                    room_id=state.home_scope['room_id'], fixture_id=self.fixture_id)
                and original['lineage']['source_cut_sha256'] == state.source_cut
                and original['lineage']['protection_sha256'] == state.protection
                and original['lineage']['authority_sha256'] == state.authority,
                'retained_task_original_caps_or_lineage_changed')
        scene = snapshot['decision']['scene']
        require(original['lineage']['scene_id'] == scene['scene_id']
                and original['lineage']['episode_id'] == scene['episode_id']
                and original['lineage']['example_id'] == scene['example_ref']['id']
                and original['lineage']['correction_ref'] == correction,
                'retained_task_scene_or_correction_changed')
        return state, original, c

    def _compose_task(self, state, request, c):
        status = self.modules.zha.decode_snapshot_v3(state.raw_status_v3, binding=self.binding,
            captured_device_us=state.current_ref.captured_device_us)
        semantic = status['current_semantic_target']
        lighting = status['lighting_observation']
        require(semantic['spotlight_frame'] == [0, 0, 0] and status['requested'][21:] == [0, 0, 0]
                and status['applied'][21:] == [0, 0, 0] and status['lighting_verified'] is True
                and lighting['present'] is True and lighting['verified'] is True and lighting['applied'][21:] == [0, 0, 0]
                and 0 <= c.device_now_upper_us - int(lighting['sampled_device_us'])
                    < min(state.current_ref.maximum_status_age_us, self.task_compiler.p['bounds']['max_observation_age_us']),
                'aim_only_requires_current_original_dark_spotlight')
        require(request['kind'] in ('MOVE', 'CONTINUE'), 'aim_only_task_cannot_change_emitters')
        draft = self.task_compiler.compile_retained_stationary_joint(request, now_home_us=c.now_home_us,
            current_ref=state.current_ref, raw_status_v3=state.raw_status_v3,
            raw_capabilities_v2=state.raw_capabilities_v2, previous=self.task_previous)
        # Use the actual canonical composer for unchanged full24 projection and
        # power validation. No ambient fields are selected in the final plan.
        unchanged = self.modules.outputs.compose_ambient_levels(binding=self.binding, status_v3=status,
            raw_capabilities404=state.raw_capabilities_v2,
            expected_capability_sha256=state.current_ref.capabilities_sha256,
            mixer_source_sha256=MIXER_SOURCE_SHA256, current_ref=state.current_ref,
            changes=[dict(zone=0, level=semantic['ambient_level'][0], mired=semantic['ambient_mired'][0])],
            transition_us=0, evidence_source='simulated')['semantic_plan']
        if request['kind'] == 'MOVE':
            bounds = self.task_compiler.p['bounds']
            start_deadline = self.task_start_deadlines.get(str(request['revision_id']))
            if start_deadline is None:
                start_deadline = c.device_now_upper_us + sum(bounds[k] for k in
                    ('darkness_decay_us', 'driver_commit_us', 'launch_skew_us'))
        else:
            start_deadline = int(semantic['start_deadline_device_us'])
        unchanged.update(kind=request['kind'], domain_mask=128, field_mask=[0] * 7 + [4],
            motion_id=str(draft['motion_id']), target_centideg=deepcopy(draft['target_centideg']),
            speed_deg_s=deepcopy(draft['speed_deg_s']), motion_timeout_us=str(draft['motion_timeout_us']),
            start_deadline_device_us=str(start_deadline), preset_id='0')
        return unchanged, draft

    def prepare_task_plan(self, reservation, *, max_support_us, clock_error_us):
        with self.source.journal.lock:
            state, request, c = self._resolve_task()
            require(request['revision_id'] == int(reservation['revision_id'])
                    and request['predecessor_id'] == state.current_ref.revision_id
                    and c.uncertainty_us == clock_error_us
                    and request['support_expiry_home_us'] - request['issued_home_us'] <= max_support_us,
                    'exact_reserved_task_original_required')
            semantic, draft = self._compose_task(state, request, c)
            plan = self.modules.plan.example_plan(); plan.update(semantic)
            plan.update(manual=False, manual_duration_minutes=0, revision_id=reservation['revision_id'],
                grant_id=reservation['grant_id'], grant_body_sha256='0' * 64,
                context_digest=self.binding.context_digest.hex(), source_cut=state.source_cut,
                protection=state.protection, authority=state.authority,
                issued_home_us=str(request['issued_home_us']), support_expiry_home_us=str(request['support_expiry_home_us']),
                clock_mapping_revision=c.mapping_revision, **state.caps)
            self.task_prepared[reservation['revision_id']] = deepcopy(draft)
            self.task_start_deadlines[reservation['revision_id']] = int(semantic['start_deadline_device_us'])
            return plan

    def _compose(self, state, changes):
        status = self.modules.zha.decode_snapshot_v3(state.raw_status_v3, binding=self.binding,
            captured_device_us=state.current_ref.captured_device_us)
        return self.modules.outputs.compose_ambient_levels(binding=self.binding, status_v3=status,
            raw_capabilities404=state.raw_capabilities_v2,
            expected_capability_sha256=state.current_ref.capabilities_sha256,
            mixer_source_sha256=MIXER_SOURCE_SHA256, current_ref=state.current_ref,
            changes=changes, transition_us=0, evidence_source='simulated')['semantic_plan']

    def prepare_plan(self, reservation, *, max_support_us, clock_error_us):
        with self.source.journal.lock:
            state, changes, c = self._resolve()
            require(c.uncertainty_us == clock_error_us, 'authority/observer clock error differs')
            semantic = self._compose(state, changes)
            plan = self.modules.plan.example_plan()
            plan.update(semantic)
            plan.update(manual=False, manual_duration_minutes=0,
                revision_id=reservation['revision_id'], grant_id=reservation['grant_id'], grant_body_sha256='0' * 64,
                context_digest=self.binding.context_digest.hex(), source_cut=state.source_cut,
                protection=state.protection, authority=state.authority,
                issued_home_us=str(c.now_home_us),
                support_expiry_home_us=str(min(c.now_home_us + max_support_us, *map(int, state.caps.values()))),
                clock_mapping_revision=c.mapping_revision, **state.caps)
            return plan

    def validate(self, plan, state):
        """Recompute real selections, all24 power and native CAS independently."""
        with self.source.journal.lock:
            if plan.get('domain_mask') == 128 and retained_task_profile(self.projector.task_profile):
                current, request, c = self._resolve_task()
                comparable = replace(current, current_ref=replace(current.current_ref,
                    device_now_lower_us=state.current_ref.device_now_lower_us,
                    device_now_upper_us=state.current_ref.device_now_upper_us))
                require(comparable == state, 'current_accepted_task_native_authority_changed')
                expected, draft = self._compose_task(current, request, c)
                prepared = deepcopy(self.task_prepared.get(plan['revision_id']))
                recomputed = deepcopy(draft)
                for value in (prepared, recomputed):
                    if value is not None:
                        value.pop('draft_sha256', None)
                        value['source_reference'].pop('device_now_lower_us', None)
                        value['source_reference'].pop('device_now_upper_us', None)
                require(prepared == recomputed
                        and all(plan.get(k) == v for k, v in expected.items())
                        and plan['revision_id'] == str(request['revision_id'])
                        and plan.get('manual') is False and plan.get('manual_duration_minutes') == 0
                        and plan.get('context_digest') == self.binding.context_digest.hex()
                        and plan.get('clock_mapping_revision') == c.mapping_revision
                        and all(plan.get(k) == getattr(current, k) for k in ('source_cut', 'protection', 'authority'))
                        and all(plan.get(k) == current.caps[k] for k in CAP_FIELDS)
                        and plan['issued_home_us'] == str(request['issued_home_us'])
                        and plan['support_expiry_home_us'] == str(request['support_expiry_home_us']),
                        'canonical_retained_task_fields_differ')
                return True
            current, changes, c = self._resolve()
            # Advancing clock bounds do not replace the original observation.
            comparable = replace(current, current_ref=replace(current.current_ref,
                device_now_lower_us=state.current_ref.device_now_lower_us,
                device_now_upper_us=state.current_ref.device_now_upper_us))
            require(comparable == state, 'current accepted room/native authority changed')
            expected = self._compose(current, changes)
            require(all(plan.get(k) == v for k, v in expected.items()), 'canonical ambient fields differ from accepted samples')
            require(plan.get('manual') is False and plan.get('manual_duration_minutes') == 0
                    and plan.get('context_digest') == self.binding.context_digest.hex()
                    and plan.get('clock_mapping_revision') == c.mapping_revision
                    and all(plan.get(k) == getattr(current, k) for k in ('source_cut', 'protection', 'authority'))
                    and all(plan.get(k) == current.caps[k] for k in CAP_FIELDS),
                    'canonical causal/clock/support mapping differs')
            return True


@dataclass(frozen=True)
class RoomCommandMember:
    fixture_id: str
    resolver: RoomCommandStateResolver
    dispatcher: CanonicalFixtureDispatcher
    adapter: RoomCanonicalAdapter


class CanonicalRoomCommandJoin:
    def __init__(self, source, projector, *, members, task_profile=None):
        mode = retained_task_profile(task_profile)
        require(type(source) is CommittedRoomSource and type(projector) is RoomCommandProjector
                and projector.source is source and type(members) is tuple and len(members) == 4,
                'one fixed four-member command join required')
        require(projector.task_profile == task_profile, 'same_fixed_room_command_profile_required')
        require(tuple(m.fixture_id for m in members) == source.binding.fixture_ids, 'four exact ordered member identities required')
        for member in members:
            require(type(member) is RoomCommandMember and type(member.resolver) is RoomCommandStateResolver
                    and type(member.dispatcher) is CanonicalFixtureDispatcher and type(member.adapter) is RoomCanonicalAdapter,
                    'actual installed command capabilities required')
            r, d, a = member.resolver, member.dispatcher, member.adapter
            require(r.source is source and r.projector is projector and r.fixture_id == member.fixture_id
                    and d.journal is source.journal and d.authority.journal is source.journal
                    and d.authority.current_resolver == r.resolve and d.authority.command_validator == r.validate
                    and d.authority.binding == r.binding == a.binding and a.dispatcher is d
                    and d.port is a.port and d.codec is r.modules.plan
                    and d.authority.policy.fixture_id == member.fixture_id
                    and d.authority.policy.provider_pins == r.modules.provider_pins,
                    'same source/Journal/authority/adapter/member binding required')
            require(d.authority.policy.clock_error_us == r._clock().uncertainty_us,
                    'authority clock error must bind original observer')
        self.source, self.projector, self.members = source, projector, members
        self.task_profile = deepcopy(task_profile)
        self._lock, self._quiesced = threading.Lock(), threading.Event()

    def _ambient(self, member, projection, fixture):
        if not fixture['ambient']['changes']:
            return dict(status='unavailable', reason='no_supported_current_committed_ambient_samples')
        request_id = 'room-ambient-' + digest(dict(room_commit_ref=projection['room_commit_ref'], fixture_id=member.fixture_id))
        d, r = member.dispatcher, member.resolver
        # An exact repeat reads the original durable outcome before new native
        # STATUS or plan preparation. An attempted/unknown effect is never sent
        # again, and a reserved-only crash requires explicit recovery.
        try:
            old = d.status(request_id)
        except FixtureAuthorityDenied as exc:
            if str(exc) != 'unknown exact canonical reservation': raise
        else:
            return canonical_dispatch_outcome(request_id, old, exact_retry=True)
        require(not self._quiesced.is_set(), 'room command join quiesced')
        r.replace_observation(member.adapter.observe())
        # Verify independent raw derivation agrees with the read-only projector
        # before reserving a durable identity; do not reserve unavailable work.
        with self.source.journal.lock:
            snapshot, _, changes, _, _ = r._selection()
            require(snapshot['room_commit_ref'] == projection['room_commit_ref']
                    and changes == fixture['ambient']['changes'], 'room command projection changed before reservation')
            r._compose(r.resolve(AMBIENT), changes)
        reservation = d.reserve(request_id, digest(dict(room_commit_ref=projection['room_commit_ref'],
            fixture_id=member.fixture_id, mode=AMBIENT, changes=changes)), AMBIENT)
        plan = r.prepare_plan(reservation, max_support_us=d.authority.policy.max_support_us,
                              clock_error_us=d.authority.policy.clock_error_us)
        admitted = d.authority.admit_ambient(request_id, reservation, plan)
        d.prepare(request_id, admitted)
        # The dispatcher owns a thread which needs the same Journal lock for
        # live issuer lookup. Never hold that lock across external submission.
        dispatched = d.dispatch(request_id)
        return canonical_dispatch_outcome(request_id, dispatched)

    def dispatch_current(self):
        with self._lock:
            require(not self._quiesced.is_set(), 'room command join quiesced')
            projection = self.projector.project()
            rows = []
            for member, fixture in zip(self.members, projection['fixtures']):
                try:
                    ambient = self._ambient(member, projection, fixture)
                except ValueError as exc:
                    ambient = dict(status='refused', reason=str(exc))
                task = dict(status='refused', reason=TASK_REFUSAL, native_motion_equivalence=False)
                if retained_task_profile(self.task_profile):
                    try: task = self._task(member, projection, fixture)
                    except ValueError as exc:
                        task = dict(status='refused', reason=str(exc), aim_only=True,
                            native_motion_equivalence=False, light_request=deepcopy(fixture['task'].get('light_request')))
                rows.append(dict(fixture_id=member.fixture_id, room_commit_ref=deepcopy(projection['room_commit_ref']),
                    ambient=ambient, task=task))
            mode = retained_task_profile(self.task_profile)
            return dict(schema='home-room-canonical-command-result-2' if mode else 'home-room-canonical-command-result-1',
                version=2 if mode else 1,
                room_commit_ref=deepcopy(projection['room_commit_ref']), fixtures=rows,
                physical_permission=False, native_achieved_truth=False)

    def _task(self, member, projection, fixture):
        trace = fixture['task']
        if trace['retained_motion'] is None:
            return dict(status='unavailable', reason=trace['reason'], aim_only=True,
                        native_motion_equivalence=False)
        d, r = member.dispatcher, member.resolver
        request_id = 'room-task-' + digest(dict(room_commit_ref=projection['room_commit_ref'], fixture_id=member.fixture_id))
        try: old = d.status(request_id)
        except FixtureAuthorityDenied as exc:
            if str(exc) != 'unknown exact canonical reservation': raise
        else:
            outcome = canonical_dispatch_outcome(request_id, old, exact_retry=True)
            with r._task_assessment_lock:
                pending = r._task_assessment_pending
                if pending is not None and pending['request_id'] == request_id and outcome['status'] != 'unconfirmed':
                    r._finish_task_assessment(pending)
            return dict(**outcome, aim_only=True,
                light_request=deepcopy(trace['light_request']), native_motion_equivalence=False)
        if r.task_assessor is not None:
            r.replace_observation(member.adapter.observe())
            r.task_assessor.install(member)
        pending = r._pin_task_assessment(request_id)
        try:
            result = self._task_pinned(member, projection, fixture, request_id, pending)
        except BaseException:
            if not pending['reservation_attempted']:
                r._finish_task_assessment(pending)
            raise
        if result['status'] != 'unconfirmed':
            r._finish_task_assessment(pending)
        return result

    def _task_pinned(self, member, projection, fixture, request_id, pending):
        d, r, trace = member.dispatcher, member.resolver, fixture['task']
        require(not self._quiesced.is_set(), 'room command join quiesced')
        r.replace_observation(member.adapter.observe())
        with self.source.journal.lock:
            state, request, _ = r._resolve_task()
            require(state.room_commit_ref == projection['room_commit_ref'], 'task_projection_changed_before_reservation')
            body = dict(kind='room_task_assessment_original', data=pending['original'].record_data(
                fixture_id=member.fixture_id, profile_sha256=digest(r.task_compiler.profile),
                room_commit_ref=state.room_commit_ref))
            ack = self.source.journal._recorder(body, record_id='room-task-assessment:' + digest(body['data']))
            require(type(ack) is dict and set(ack) == {'schema', 'version', 'seq', 'digest'}
                    and ack['schema'] == 'home-live-recorded-1' and type(ack['version']) is int and ack['version'] == 1
                    and type(ack['seq']) is int and ack['seq'] >= 0 and type(ack['digest']) is str
                    and len(ack['digest']) == 64 and all(c in '0123456789abcdef' for c in ack['digest']),
                    'durable_retained_task_assessment_ack_required')
            # Exact original and native path validation precedes allocation.
            r._compose_task(*r._resolve_task())
        pending['reservation_attempted'] = True
        reservation = d.reserve(request_id, digest(dict(room_commit_ref=projection['room_commit_ref'],
            fixture_id=member.fixture_id, mode=TASK, assessment_sha256=pending['sha256'])), TASK)
        plan = r.prepare_task_plan(reservation, max_support_us=d.authority.policy.max_support_us,
                                   clock_error_us=d.authority.policy.clock_error_us)
        admitted = d.authority.admit_task_joint(request_id, reservation, plan)
        d.prepare(request_id, admitted)
        dispatched = d.dispatch(request_id)
        result = canonical_dispatch_outcome(request_id, dispatched)
        if result['status'] == 'dispatched':
            r.task_previous = deepcopy(r.task_prepared[reservation['revision_id']])
        return dict(**result, aim_only=True, light_request=deepcopy(trace['light_request']), native_motion_equivalence=False)

    def quiesce(self):
        self._quiesced.set()
        return dict(fixtures=[dict(fixture_id=m.fixture_id, receipt=m.dispatcher.quiesce()) for m in self.members],
                    physical_stop_confirmed=False)

    def drain(self):
        self._quiesced.set()
        rows = [dict(fixture_id=m.fixture_id, receipt=m.dispatcher.drain()) for m in self.members]
        return dict(fixtures=rows, safe_to_close_journal=all(r['receipt']['safe_to_close_journal'] for r in rows),
                    physical_stop_confirmed=False)

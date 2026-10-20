"""Fixed source5/6 and explicit assessed source8 command composition.

This installed factory derives Home bindings from the actual graph. The six
native profile pins and feedback are explicitly fictional; they qualify no
real fixture, optics, person, geometry, temperature or motor state.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import struct
import threading

from room_command_projection import RETAINED_TASK_PROFILE1, RETAINED_TASK_PROFILE_REF


# Fixed command/accepted-room/source5-record consumer inventory. The complete
# installed baseline and subsequent overlays remain separately sealed. Each
# DIGESTS file below authenticates the schema bytes checked by the unchanged
# registry/JS contract; this is not an enumeration of optional model branches.
SOURCE_INVENTORY = (
    'tools/den-live-runtime/room_command_factory.py',
    'tools/den-live-runtime/room_command_session.py',
    'tools/den-live-runtime/room_canonical_client.py',
    'tools/den-live-runtime/room_canonical_provider.py',
    'tools/den-live-runtime/room_canonical_adapter.py',
    'tools/den-live-runtime/room_canonical_plant.py',
    'tools/den-live-runtime/room_canonical_plant_recording.py',
    'tools/den-live-runtime/room_canonical_plant_replay.py',
    'tools/den-live-runtime/room_fake_plant.py',
    'tools/den-live-runtime/room_fake_plant_recording.py',
    'tools/den-live-runtime/room_fake_plant_replay.py',
    'tools/den-live-runtime/room_committed_source.py',
    'tools/den-live-runtime/room_command_projection.py',
    'tools/den-live-runtime/room_fixture_command_join.py',
    'tools/den-live-runtime/fixture_canonical_dispatch.py',
    'tools/den-live-runtime/fixture_canonical_archive.py',
    'tools/den-live-runtime/archive.py',
    'tools/den-live-runtime/supervisor.py',
    'stack/services/home-context/fixture_canonical_authority.py',
    'stack/services/home-context/fixture_execution_validation.py',
    'stack/services/home-context/fixture_joint_execution.py',
    'stack/services/home-context/journal.py',
    'stack/services/home-context/room_delivery.py',
    'stack/services/perception-lab/lab_live_runtime.py',
    'stack/services/perception-lab/lab_live_acquisition.py',
    'stack/services/perception-lab/lab_stereo_mount_source.py',
    'stack/services/perception-lab/live_den_mount.py',
    'stack/services/perception-lab/live_den_bootstrap.py',
    'stack/services/home-context/fixture_execution.py',
    'stack/services/home-context/fixture_profile.py',
    'stack/services/home-context/server.py',
    'stack/services/home-context/live_ingress.py',
    'stack/services/home-context/live_ingress_validation.py',
    'stack/services/home-context/live_service_clock.py',
    'stack/services/home-context/schema_registry.py',
    'stack/services/home-context/live_worker.py',
    'stack/services/home-context/live_scene.py',
    'stack/services/home-context/live_delivery.py',
    'stack/services/home-context/live_lifecycle.py',
    'stack/services/home-context/room_worker.py',
    'stack/services/home-context/pc_support_worker.py',
    'stack/services/home-context/live_pc_support.py',
    'stack/services/home-context/room_clarification.py',
    'stack/services/home-context/room_manual.py',
    'stack/services/home-context/manual_control_authority.py',
    'stack/services/home-context/validation.py',
    'stack/services/home-context/producer_adapters.py',
    'stack/services/perception-lab/managed_detector_app.py',
    'stack/services/perception-lab/live_detector_app.py',
    'stack/services/perception-lab/live_den_preflight.py',
    'stack/services/perception-lab/live_context_edge.py',
    'stack/services/perception-lab/lab_live_journal_join.py',
    'stack/services/perception-lab/lab_live_context_adapter.py',
    'stack/services/perception-lab/lab_live_semantic_admission.py',
    'stack/services/perception-lab/lab_live_desk_admission.py',
    'stack/services/perception-lab/lab_live_desk_anchor.py',
    'stack/services/perception-lab/lab_desk_capability.py',
    'stack/services/perception-lab/lab_stereo_desk_capability.py',
    'stack/services/perception-lab/lab_existing_api_stereo.py',
    'stack/services/perception-lab/service_runtime.py',
    'stack/services/perception-lab/assessment_existing_api.py',
    'stack/services/perception-lab/store.py',
    'stack/services/perception-lab/ownership.py',
    'stack/services/perception-lab/depth_clock.py',
    'stack/services/perception-lab/live_capture.py',
    'stack/services/perception-lab/persisted_evidence.py',
    'stack/services/perception-lab/reasoning.py',
    'stack/services/perception-lab/geometry.py',
    'stack/services/perception-lab/capture_timing.py',
    'stack/services/perception-lab/bundle_ingest.py',
    'stack/services/perception-lab/registered_depth.py',
    'stack/services/perception-lab/depth_association.py',
    'stack/services/perception-lab/head3d.py',
    'stack/services/perception-lab/room_facts.py',
    'stack/services/perception-lab/room_words.py',
    'stack/services/perception-lab/paired_preview_client.py',
    'stack/services/perception-lab/preview_client.py',
    'stack/services/perception-lab/recorder.py',
    'stack/services/perception-lab/frame_ring.py',
    'stack/services/perception-lab/spatial_validation.py',
    'stack/services/perception-lab/jev_evidence.py',
    'stack/services/perception-lab/jev_semantics.py',
    'stack/services/perception-lab/ambient_context.py',
    'stack/services/perception-lab/qwen_scene.py',
    'stack/services/perception-lab/vjepa_scene.py',
    'stack/services/perception-lab/chrony_preview_client.py',
    'stack/services/perception-lab/preview_ingest.py',
    'stack/services/perception-lab/live_publication.py',
    'stack/services/perception-lab/live_acquisition_owner.py',
    'tools/den-live-runtime/lab_original_archive.py',
    'tools/den-live-runtime/lab_stereo_media.py',
    'tools/den-live-runtime/lab_stereo_session_package.py',
    'tools/den-live-runtime/lab_session_package.py',
    'tools/den-assessment/desk_enclosure.py',
    'tools/home-live-context-worker.cjs',
    'tools/home-live-context-contract.cjs',
    'tools/home-context-shadow-worker.cjs',
    'tools/den-live-runtime/worker-runner.cjs',
    'tools/den-live-runtime/recorder-client.cjs',
    'tools/den-live-runtime/replay.cjs',
    'tools/den-live-runtime/manual-control-replay.cjs',
    'app/src/home-context-service-contract.js',
    'app/src/home-lighting-target-contract.js',
    'app/src/home-engagement-reducer.js',
    'app/src/home-live-engagement-profile.js',
    'app/src/home-live-person-lifecycle.js',
    'app/src/home-live-pc-support.js',
    'app/src/home-live-scene-state.js',
    'app/src/home-live-task-delivery.js',
    'app/src/home-live-room-composition.js',
    'app/src/home-live-room-allocation.js',
    'app/src/home-live-clarification-state.js',
    'app/src/home-live-room-control-state.js',
    'contracts/home-live-context/schemas/DIGESTS.json',
    'contracts/home-live-context/worker-schemas/DIGESTS.json',
    'contracts/home-live-context/scene-schemas/DIGESTS.json',
    'contracts/home-live-context/delivery-schemas/DIGESTS.json',
    'contracts/home-live-context/stable-delivery-schemas/DIGESTS.json',
    'contracts/home-live-context/lifecycle-schemas/DIGESTS.json',
    'contracts/home-live-context/conservative-delivery-schemas/DIGESTS.json',
    'contracts/home-live-context/room-schemas/DIGESTS.json',
    'contracts/home-live-context/room-clarification-schemas/DIGESTS.json',
    'contracts/home-live-context/room-manual-schemas/DIGESTS.json',
    'contracts/home-live-context/pc-support-schemas/DIGESTS.json',
)


# Declaration1 inventory and profile remain byte-identical.
BUNDLED_SOURCE_INVENTORY = SOURCE_INVENTORY + (
    'stack/services/perception-lab/bundle_rgb_input.py',
    'stack/services/perception-lab/lab_bundled_rgb_stereo.py',
)

# Explicit assessed-room consumer. Historical declarations keep their exact
# inventories; this manifest authenticates each additional schema leaf.
ASSESSED_SOURCE_INVENTORY = BUNDLED_SOURCE_INVENTORY + (
    'contracts/home-live-context/room-subset-schemas/DIGESTS.json',
    'stack/services/home-context/room_subset_worker.py',
)

# Explicit opt-in successor; declarations1/2/3 retain their exact inventories.
TRUSTED_TASK_SOURCE_INVENTORY = tuple(dict.fromkeys(ASSESSED_SOURCE_INVENTORY + (
    'tools/den-live-runtime/room_retained_task_assessor.py',
    'stack/services/perception-lab/lab_desk_person_bridge.py',
    'stack/services/perception-lab/lab_person_semantic_admission.py',
    'stack/services/perception-lab/qwen_person_scene.py',
    'stack/services/perception-lab/qwen_person_live.py',
    'stack/services/perception-lab/jev_person_wire.py',
    'stack/services/perception-lab/jev_person_source.py',
    'stack/services/perception-lab/jev_person_provider.py',
    'stack/services/perception-lab/jev_person_policy.py',
    'stack/services/perception-lab/live_reasoning.py',
    'stack/services/perception-lab/managed_jev.py',
)))

def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def _hash(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


SIMULATION_PROFILE = dict(schema='home-canonical-simulation-profile-1', version=1,
    evidence_source='simulated', physical_permission=False, physical_truth=False,
    native_profile='fictional-c12-v1', principal_id='3',
    wire_pins={name: bytes([i + 1]).hex() * 32 for i, name in enumerate(
        ('fixture', 'firmware', 'protocol', 'profile', 'calibration', 'contract'))},
    stationary_joint_positions_centideg=[0, 0], stationary_speed_deg_s=0,
    temperature_millic=25000, temperature_interval_us=1000000,
    native_tick_interval_us=100000,
    clock_refresh_interval_us=2000000, maximum_status_age_us=250000,
    max_support_us=3000000, max_lookup_age_us=250000,
    dispatch_timeout_ms=3000, max_dispatch_operations=4096,
    maximum_can_drain_steps=16)
SIMULATION_PROFILE_REF = dict(id='home-canonical-simulation-profile-1', digest=_hash(SIMULATION_PROFILE))
FACTORY_REF = dict(id='home-room-command-factory-1', digest=_hash(dict(
    schema='home-room-command-factory-1', version=1, source_inventory=list(SOURCE_INVENTORY),
    simulation_profile_ref=SIMULATION_PROFILE_REF, input_profile='producer5-config12-stage11-room-output1')))
BUNDLED_FACTORY_REF = dict(id='home-room-command-factory-2', digest=_hash(dict(
    schema='home-room-command-factory-2', version=2, source_inventory=list(BUNDLED_SOURCE_INVENTORY),
    simulation_profile_ref=SIMULATION_PROFILE_REF, input_profile='producer6-config12-stage11-room-output1',
    original_family='existing-api-bundled-rgb-1')))
ASSESSED_FACTORY_REF = dict(id='home-room-command-factory-3', digest=_hash(dict(
    schema='home-room-command-factory-3', version=3, source_inventory=list(ASSESSED_SOURCE_INVENTORY),
    simulation_profile_ref=SIMULATION_PROFILE_REF, input_profile='producer8-config13-stage12-room-output1',
    original_family='existing-api-bundled-rgb-1', task_profile_ref=RETAINED_TASK_PROFILE_REF)))
TRUSTED_TASK_FACTORY_REF = dict(id='home-room-command-factory-4', digest=_hash(dict(
    schema='home-room-command-factory-4', version=4, source_inventory=list(TRUSTED_TASK_SOURCE_INVENTORY),
    simulation_profile_ref=SIMULATION_PROFILE_REF, input_profile='producer8-config13-stage12-room-output1',
    original_family='existing-api-bundled-rgb-1', task_profile_ref=RETAINED_TASK_PROFILE_REF,
    assessor_schema='home-retained-task-assessor-capability-1')))
DECLARATION_KEYS = {'schema', 'version', 'factory_ref', 'provider_pins', 'source_pins',
                    'source_bundle_digest', 'recording', 'limits', 'simulation_profile_ref'}


def _need(condition, reason):
    if not condition:
        raise ValueError(reason)


def _sha(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def validate_command_declaration(value, *, source_bundle_digest, expected_provider_pins=None):
    """Pure data validation; this does not install a client or qualify inputs."""
    from room_canonical_provider import WINDOWS_PROVIDER, LINUX_ARM64_PROVIDER
    trusted_task = type(value) is dict and value.get('schema') == 'home-den-canonical-command-declaration-4'
    assessed = trusted_task or type(value) is dict and value.get('schema') == 'home-den-canonical-command-declaration-3'
    bundled = assessed or type(value) is dict and value.get('schema') == 'home-den-canonical-command-declaration-2'
    inventory = TRUSTED_TASK_SOURCE_INVENTORY if trusted_task else ASSESSED_SOURCE_INVENTORY if assessed else BUNDLED_SOURCE_INVENTORY if bundled else SOURCE_INVENTORY
    expected_factory = TRUSTED_TASK_FACTORY_REF if trusted_task else ASSESSED_FACTORY_REF if assessed else BUNDLED_FACTORY_REF if bundled else FACTORY_REF
    version = 4 if trusted_task else 3 if assessed else 2 if bundled else 1
    _need(type(value) is dict and set(value) == DECLARATION_KEYS | ({'original_family', 'bundle_input_policy_sha256'} if bundled else set()) | ({'task_profile_ref'} if assessed else set()) | ({'task_assessor_ref'} if trusted_task else set())
          and value['schema'] == 'home-den-canonical-command-declaration-' + str(version)
          and type(value['version']) is int and value['version'] == version,
          'closed_canonical_command_declaration_required')
    if trusted_task:
        ref = value['task_assessor_ref']
        _need(type(ref) is dict and set(ref) == {'id', 'digest'}
              and ref['id'] == 'home-retained-task-assessor-capability-1' and _sha(ref['digest']),
              'fixed_retained_task_assessor_ref_required')
    if bundled:
        _need(value['original_family'] == 'existing-api-bundled-rgb-1' and _sha(value['bundle_input_policy_sha256']),
              'fixed_bundled_command_policy_required')
    if assessed:
        from journal import digest
        _need(value['task_profile_ref'] == RETAINED_TASK_PROFILE_REF
              and digest(RETAINED_TASK_PROFILE1) == RETAINED_TASK_PROFILE_REF['digest'],
              'fixed_retained_task_profile_required')
    _need(value['factory_ref'] == expected_factory and value['simulation_profile_ref'] == SIMULATION_PROFILE_REF,
          'fixed_canonical_command_factory_profile_required')
    _need(value['provider_pins'] in (WINDOWS_PROVIDER.provider_pins, LINUX_ARM64_PROVIDER.provider_pins)
          and (expected_provider_pins is None or value['provider_pins'] == expected_provider_pins),
          'fixed_selected_canonical_provider_required')
    _need(_sha(source_bundle_digest) and value['source_bundle_digest'] == source_bundle_digest,
          'same_command_source_bundle_required')
    pins = value['source_pins']
    _need(type(pins) is dict and set(pins) == set(inventory) and all(_sha(p) for p in pins.values()),
          'complete_fixed_command_source_inventory_required')
    r, limits = value['recording'], value['limits']
    _need(type(r) is dict and set(r) == {'native_file', 'canonical_file', 'provenance_file', 'max_records', 'max_bytes', 'min_free_bytes'}
          and all(type(r[k]) is str and Path(r[k]).is_absolute() for k in ('native_file', 'canonical_file', 'provenance_file'))
          and len({r[k] for k in ('native_file', 'canonical_file', 'provenance_file')}) == 3
          and type(r['max_records']) is int and 4 <= r['max_records'] <= 8192
          and type(r['max_bytes']) is int and 4096 <= r['max_bytes'] <= 32 * 1024**2
          and type(r['min_free_bytes']) is int and r['min_free_bytes'] >= 1,
          'bounded_private_command_recordings_required')
    _need(type(limits) is dict and set(limits) == {'max_runtime_s', 'interval_s', 'max_commits'}
          and type(limits['max_runtime_s']) in (int, float) and math.isfinite(limits['max_runtime_s']) and 0 < limits['max_runtime_s'] <= 120
          and type(limits['interval_s']) in (int, float) and math.isfinite(limits['interval_s']) and .01 <= limits['interval_s'] <= 1
          and type(limits['max_commits']) is int and 1 <= limits['max_commits'] <= 256,
          'bounded_canonical_command_lifecycle_required')
    _encoded(value)
    return deepcopy(value)


def _config12_policy1(config):
    """Current command declarations have one exact ingress/room meaning."""
    if (type(config) is not dict or config.get('schema') != 'home-live-ingress-config-12'
            or type(config.get('version')) is not int or config['version'] != 12):
        return False
    manifest = config.get('manifest')
    if (type(manifest) is not dict or manifest.get('schema') != 'home-live-ingress-manifest-12'
            or type(manifest.get('version')) is not int or manifest['version'] != 12):
        return False
    worker = manifest.get('worker')
    if type(worker) is not dict:
        return False
    policy = worker.get('room_policy')
    return (type(policy) is dict and policy.get('schema') == 'home-live-room-policy-1'
        and type(policy.get('version')) is int and policy['version'] == 1)


def _config13_policy2(config):
    """Declaration3 has one fixed assessed-room ingress/worker meaning."""
    if (type(config) is not dict or config.get('schema') != 'home-live-ingress-config-13'
            or type(config.get('version')) is not int or config['version'] != 13):
        return False
    manifest = config.get('manifest')
    if (type(manifest) is not dict or manifest.get('schema') != 'home-live-ingress-manifest-13'
            or type(manifest.get('version')) is not int or manifest['version'] != 13):
        return False
    worker = manifest.get('worker')
    if type(worker) is not dict or worker.get('room_subset_mode') is not True:
        return False
    policy = worker.get('room_policy')
    return (type(policy) is dict and policy.get('schema') == 'home-live-room-policy-2'
        and type(policy.get('version')) is int and policy['version'] == 2)


class RoomCommandFactory:
    """One installed factory; constructor performs no native or recording I/O."""
    def __init__(self, declaration, *, composition, retained_task_assessor=None):
        from lab_live_runtime import LabRuntimeComposition
        _need(type(composition) is LabRuntimeComposition and composition.producer.get('version') in (5, 6, 8),
              'actual_source5_or_source6_command_composition_required')
        self.composition = composition
        self.declaration = validate_command_declaration(declaration,
            source_bundle_digest=declaration.get('source_bundle_digest') if type(declaration) is dict else None)
        _need((composition.producer['version'], self.declaration['version']) in ((5, 1), (6, 2), (8, 3), (8, 4)),
              'exact_command_source_profile_required')
        self.assessed = self.declaration['version'] in (3, 4)
        self.task_assessor = retained_task_assessor
        if self.declaration['version'] == 4:
            from journal import digest
            from room_retained_task_assessor import TrustedRetainedTaskAssessor
            _need(type(retained_task_assessor) is TrustedRetainedTaskAssessor,
                  'fixed_retained_task_assessor_capability_required')
            retained_task_assessor.require_current()
            _need(self.declaration['task_assessor_ref'] == dict(
                id='home-retained-task-assessor-capability-1', digest=digest(retained_task_assessor.descriptor())),
                'retained_task_assessor_declaration_differs')
        else:
            _need(retained_task_assessor is None, 'trusted_task_declaration4_required')
        _need((_config13_policy2 if self.assessed else _config12_policy1)(composition.producer.get('live_config')),
              'config13_policy2_command_profile_required' if self.assessed else 'config12_policy1_command_profile_required')
        if composition.producer['version'] in (6, 8):
            _need(self.declaration['original_family'] == composition.producer['desk_policy']['original_family']
                  and self.declaration['bundle_input_policy_sha256'] == composition.producer['desk_policy']['bundle_input_policy_sha256'],
                  'same_bundled_command_policy_required')
        _need(self.declaration['limits']['max_runtime_s'] <= composition.producer['limits']['max_runtime_s'],
              'command_budget_exceeds_source_budget')
        self.lock, self.used = threading.Lock(), False

    def _graph(self, graph):
        c = self.composition
        if self.declaration['version'] == 4:
            from journal import digest
            self.task_assessor.require_current()
            _need(self.declaration['task_assessor_ref'] == dict(
                id='home-retained-task-assessor-capability-1', digest=digest(self.task_assessor.descriptor())),
                'retained_task_assessor_declaration_differs')
        _need(c.graph is graph and graph.join is c.join and graph.configuration_digest == c.configuration_digest
              and (c.producer['version'], self.declaration['version']) in ((5, 1), (6, 2), (8, 3), (8, 4))
              and graph.config.get('version') == c.producer['version'] and graph.join.scope == c.scope
              and callable(getattr(c, 'command_current', None)) and c.command_current(graph) is True,
              'same_current_source5_or_source6_command_graph_required')
        if c.producer['version'] in (6, 8):
            _need(graph.bundled_source is c.bundled_source
                  and self.declaration['original_family'] == c.producer['desk_policy']['original_family']
                  and self.declaration['bundle_input_policy_sha256'] == c.join.bundled_binding()['bundle_input_policy_sha256'],
                  'same_bundled_command_graph_required')
        worker = graph.journal.dynamic.worker
        room_policy = worker.policy.get('room_policy')
        profile = _config13_policy2 if self.assessed else _config12_policy1
        _need(profile(c.producer.get('live_config')) and profile(graph.journal.dynamic.config)
              and worker.worker_version == ('home-live-context-worker-12.0.0' if self.assessed else 'home-live-context-worker-11.0.0')
              and type(room_policy) is dict and room_policy.get('schema') == ('home-live-room-policy-2' if self.assessed else 'home-live-room-policy-1')
              and type(room_policy.get('version')) is int and room_policy['version'] == (2 if self.assessed else 1)
              and (not self.assessed or worker.policy.get('room_subset_mode') is True
                  and graph.journal.dynamic.config == c.producer['live_config']
                  and room_policy == c.producer['live_config']['manifest']['worker']['room_policy']),
              'config13_stage12_command_graph_required' if self.assessed else 'config12_stage11_command_graph_required')
        _need(graph.journal.dynamic.identity == c.scope and worker.policy.get('room_policy') is not None,
              'actual_room_worker_command_binding_required')
        return worker

    def _provenance(self, graph):
        """Full private Home pin preimages, separately retained before effects."""
        worker = self._graph(graph)
        p, policy = self.composition.producer, worker.policy
        basis = dict(schema='home-canonical-command-home-provenance-1', version=1,
            scope=deepcopy(self.composition.scope), acquisition_config=deepcopy(graph.config),
            lab_binding=deepcopy(p['lab_binding']), model_ref=deepcopy(p['model_ref']),
            semantic_policy=deepcopy(p['semantic_policy']), desk_policy=deepcopy(p['desk_policy']),
            live_manifest=deepcopy(p['live_config']['manifest']),
            room_policy=deepcopy(policy['room_policy']), worker_version=worker.worker_version,
            configuration_digest=policy['config_digest'])
        provenance = dict(schema='home-room-command-provenance-1', version=1,
            declaration=deepcopy(self.declaration), home_provenance=basis,
            home_hashes=dict(model=_hash(dict(lab_binding=basis['lab_binding'], model_ref=basis['model_ref'])),
                home_source=_hash({k: basis[k] for k in ('scope', 'acquisition_config', 'semantic_policy', 'desk_policy')}),
                home_schema=_hash(dict(manifest=basis['live_manifest'], worker_version=basis['worker_version']))),
            simulation_profile=deepcopy(SIMULATION_PROFILE))
        if self.task_assessor is not None:
            self.task_assessor.require_current()
            provenance['retained_task_assessor'] = self.task_assessor.provenance()
        return provenance

    def __call__(self, graph, worker_recorder):
        if not self.lock.acquire(False):
            raise ValueError('canonical_command_factory_busy')
        try:
            _need(not self.used, 'canonical_command_factory_single_use')
            worker = self._graph(graph)
            if __package__:
                from .archive import Recorder
            else:
                from archive import Recorder
            _need(type(worker_recorder) is Recorder, 'actual_ready_worker_recorder_required')
            from room_canonical_adapter import load_canonical_modules
            from room_canonical_provider import verified_provider_export
            from room_canonical_client import CanonicalClientFactory
            modules = load_canonical_modules()
            self.declaration = validate_command_declaration(self.declaration,
                source_bundle_digest=worker_recorder.manifest['source_bundle_digest'],
                expected_provider_pins=modules.provider_pins)
            _need(worker_recorder.status()['status'] == 'ready'
                  and worker_recorder.manifest['session_ref'] == self.composition.scope
                  and worker_recorder.manifest['configuration_digest'] == graph.configuration_digest,
                  'same_ready_worker_recording_required')
            root = Path(__file__).resolve().parents[2]
            for relative, expected in self.declaration['source_pins'].items():
                path = root / relative
                _need(path.is_file() and not path.is_symlink()
                      and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                      'fixed_command_source_changed')
            profile, manifest, _ = verified_provider_export()
            for relative in ('sketches/c12_runtime_core/runtime_types.h', 'tools/runtime/c12_canonical_capture_bridge.cpp'):
                _need(hashlib.sha256((profile.build / 'source' / relative).read_bytes()).hexdigest()
                      == manifest['source']['files'][relative], 'fixed_simulated_native_profile_changed')
            r = self.declaration['recording']
            paths = tuple(Path(r[k]) for k in ('native_file', 'canonical_file', 'provenance_file'))
            protected = {Path(worker_recorder.path), Path(self.composition.producer['journal_path']),
                         Path(self.composition.producer['original_archive']['filename'])}
            _need(len(set(paths)) == 3 and not set(paths) & protected and all(p.parent.is_dir() and not p.exists()
                  and not any(parent.is_symlink() for parent in p.parents) for p in paths),
                  'fresh_disjoint_command_recordings_required')
            self.used = True
            return self._create(graph, worker_recorder, worker, modules, profile, manifest, CanonicalClientFactory())
        finally:
            self.lock.release()

    def _create(self, graph, worker_recorder, worker, modules, profile, manifest, client):
        if __package__:
            from .room_command_session import CanonicalCommandSession
            from .archive import _safe
        else:
            from room_command_session import CanonicalCommandSession
            from archive import _safe
        from fixture_canonical_authority import CanonicalFixtureAuthority, CanonicalFixtureAuthorityPolicy, CanonicalIssuerPolicy
        from fixture_canonical_dispatch import CanonicalFixtureDispatcher
        from fixture_canonical_archive import CanonicalFixtureRecorder
        from fixture_execution_validation import record_bytes
        from room_canonical_adapter import CanonicalRoomObservation, RoomCanonicalAdapter, SimulatedClockObservation
        from room_canonical_plant import RoomCanonicalPlant
        from room_canonical_plant_recording import RoomCanonicalRecorder
        from room_command_projection import CanonicalProjectionProvider, RoomCommandProjector, MIXER_SOURCE_SHA256
        from room_committed_source import RoomCommitBinding
        from room_fixture_command_join import CanonicalRoomCommandJoin, RoomCommandGeneration, RoomCommandMember, RoomCommandStateResolver
        p, r, limits = self.composition.producer, self.declaration['recording'], self.declaration['limits']
        room = worker.policy['room_policy']
        binding = RoomCommitBinding(deepcopy(self.composition.scope), worker.policy['worker_id'],
            worker.policy['producer_id'], worker.policy['config_digest'],
            graph.journal.dynamic.config['manifest_ref']['digest'], worker.worker_version,
            tuple(f['fixture_id'] for f in room['fixtures']))
        clock = _SimulatedClock(self.composition, graph, limits['max_runtime_s'])
        provenance = self._provenance(graph)
        provenance.update(client_binding=client.binding(), clock_original=deepcopy(clock.original))
        _safe(provenance)
        raw = _encoded(provenance) + b'\n'
        _need(len(raw) <= 2 * 1024**2, 'bounded_command_provenance_required')
        _need(shutil.disk_usage(Path(r['provenance_file']).parent).free >= r['min_free_bytes'] + len(raw),
              'command_provenance_storage_headroom')
        fd = os.open(r['provenance_file'], os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
        try:
            offset = 0
            while offset < len(raw):
                n = os.write(fd, raw[offset:]); _need(n > 0, 'command_provenance_short_write'); offset += n
            os.fsync(fd)
        finally:
            os.close(fd)
        if os.name == 'posix':
            directory = os.open(Path(r['provenance_file']).parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
        _need(self.composition.command_current(graph) is True, 'command_source_changed_after_retention')
        provenance_sha = hashlib.sha256(raw).hexdigest()
        config = json.loads((profile.build / 'source' / 'contracts/c12/canonical-capture-config-v1.json').read_bytes())
        if self.task_assessor is not None:
            from fixture_joint_execution import validate_joint_profile
            self.task_assessor.require_current()
            _need(set(self.task_assessor.provenance()['profiles']) <= set(binding.fixture_ids),
                  'retained_task_profile_outside_room_inventory')
            for fixed_profile in self.task_assessor.provenance()['profiles'].values():
                validate_joint_profile(fixed_profile, canonical_config=config, provider_pins=modules.provider_pins)
        room_binding = dict(configuration_digest=binding.configuration_digest, manifest_digest=binding.manifest_digest,
                            worker_version=binding.worker_version, room_output_schema='home-live-room-output-1')
        bindings, policies, rows = [], [], []
        for index, fixture in enumerate(room['fixtures']):
            native = config['fixtures'][index]
            identity = dict(scope=binding.scope, fixture_id=fixture['fixture_id'], index=index,
                            simulated_device_id=native['device_id'], provenance_sha256=provenance_sha)
            context = dict(pins=deepcopy(SIMULATION_PROFILE['wire_pins']),
                source_cut=provenance_sha, protection=_hash(dict(registration=room['registration'], fixture=fixture)),
                authority=_hash(dict(factory_ref=self.declaration['factory_ref'], identity=identity)),
                boot_generation=native['boot'], session_id=str(_numeric(identity, 'session')),
                principal_id=SIMULATION_PROFILE['principal_id'], ownership_generation=str(_numeric(identity, 'owner')))
            context_raw = modules.plan.encode_context(context)
            device = bytes.fromhex(native['device_id'])
            ieee = ':'.join(native['device_id'][n:n + 2] for n in range(0, 16, 2))
            native_binding = modules.zha.RuntimeBinding('ff:ff:ff:ff:ff:ff:ff:fe', ieee,
                _hash(identity)[:32], device, bytes.fromhex(native['key_hex']), int(native['boot']),
                int(context['session_id']), int(context['ownership_generation']), context_raw,
                _hash(dict(home_binding=room_binding, identity=identity)), _hash(client.binding()))
            pins = {**context['pins'], **provenance['home_hashes']}
            issuers = [CanonicalIssuerPolicy(mode, 'den-' + role + '-' + _hash(identity)[:24], '1', secrets.token_hex(32))
                       for mode, role in (('autonomous-ambient', 'ambient'), ('autonomous-task', 'task'))]
            policy = CanonicalFixtureAuthorityPolicy(fixture['fixture_id'], native['device_id'], context, pins,
                context['principal_id'], issuers[0], issuers[1], deepcopy(binding.scope), deepcopy(room_binding),
                deepcopy(modules.provider_pins), clock_error_us=clock.uncertainty)
            authority_sha = _authority_digest(policy, native_binding, modules)
            namespace = _hash(dict(fixture_id=fixture['fixture_id'], device_id=native['device_id']))
            dispatch_policy = dict(schema='home-fixture-canonical-dispatch-policy-1', version=1,
                authority_policy_sha256=authority_sha, namespace=namespace,
                max_operations=SIMULATION_PROFILE['max_dispatch_operations'],
                call_timeout_ms=SIMULATION_PROFILE['dispatch_timeout_ms'])
            rows.append(dict(namespace=namespace, policy_sha256=authority_sha, fixture_id=fixture['fixture_id'],
                device_id=native['device_id'], principal_id=policy.principal_id,
                native_binding_sha256=native_binding.binding_sha256, context=deepcopy(context),
                context_hex=context_raw.hex(), pins=deepcopy(pins), home_scope=deepcopy(binding.scope),
                room_binding=deepcopy(room_binding), issuers=[dict(mode=i.mode, issuer_id=i.issuer_id,
                    issuer_epoch=i.issuer_epoch) for i in issuers], dispatch_policy=dispatch_policy,
                dispatch_policy_sha256=hashlib.sha256(record_bytes(dispatch_policy)).hexdigest()))
            bindings.append(native_binding); policies.append(policy)
        header = dict(session_id=binding.scope['session_id'], producer=dict(abi=manifest['abi'],
            build_id=manifest['build_id'], manifest_sha256=profile.manifest_sha256,
            source_sha256=manifest['source_digest'], config_sha256=manifest['config_sha256'],
            executable_sha256=manifest['executable']['sha256']), fixtures=[dict(fixture_index=i,
                fixture_id=fid, device_id=config['fixtures'][i]['device_id'], boot=config['fixtures'][i]['boot'],
                launch_id=secrets.token_hex(16)) for i, fid in enumerate(binding.fixture_ids)],
            evidence_source='simulated', physical_permission=False, physical_truth=False)
        native_recorder = sidecar = plant = None
        try:
            native_recorder = RoomCanonicalRecorder(r['native_file'], header=header,
                max_records=r['max_records'], max_bytes=r['max_bytes'])
            archive_version = 2 if self.declaration['version'] == 4 else 1
            archive_binding = dict(schema='home-fixture-canonical-archive-binding-' + str(archive_version), version=archive_version,
                parent_manifest_sha256=_hash(worker_recorder.manifest),
                source_pins=deepcopy(self.declaration['source_pins']), provider_pins=deepcopy(modules.provider_pins), authorities=rows)
            sidecar = CanonicalFixtureRecorder(r['canonical_file'], binding=archive_binding,
                max_records=r['max_records'], max_bytes=r['max_bytes'], min_free_bytes=r['min_free_bytes'])
            plant = RoomCanonicalPlant(str(profile.build / 'manifest.json'), profile.manifest_sha256,
                native_recorder, fixture_ids=binding.fixture_ids, process_factory=client)
            members, states = [], []

            def bind_join(source):
                provider = CanonicalProjectionProvider(**modules.provider_pins,
                    mixer_source_sha256=MIXER_SOURCE_SHA256, ambient_mix=modules.outputs.ambient_mix)
                projector = RoomCommandProjector(source, room_policy=room, provider=provider,
                    **({'task_profile': deepcopy(RETAINED_TASK_PROFILE1)} if self.assessed else {}))
                for index, fid in enumerate(binding.fixture_ids):
                    state = _NativeMember(plant, index, bindings[index], modules, clock, config)
                    states.append(state); state.bootstrap(); state.feedback(force=True)
                    cap, _ = state.document(modules.wire.Operation.CAPABILITIES)
                    status, captured = state.document(modules.wire.Operation.STATUS)
                    decoded = modules.zha.decode_snapshot_v3(status, binding=bindings[index], captured_device_us=captured)
                    observed = clock.sample()
                    reference = modules.outputs.CurrentStateReference(bindings[index].binding_sha256,
                        hashlib.sha256(status).hexdigest(), hashlib.sha256(cap).hexdigest(),
                        bindings[index].boot_generation, bindings[index].session_id, bindings[index].ownership_generation,
                        bindings[index].context_digest.hex(), int(decoded['state_generation']), int(decoded['revision_id']),
                        tuple(d['generation'] for d in decoded['domains']), captured,
                        observed.now_device_us, observed.now_device_us, SIMULATION_PROFILE['maximum_status_age_us'], 'simulated')
                    installed = {}
                    def clock_observer(holder=installed):
                        return holder['adapter'].clock_observation() if 'adapter' in holder else clock.sample()
                    task_compiler = (self.task_assessor.compiler_for(fid, modules=modules,
                        binding=bindings[index], canonical_config=config) if self.task_assessor is not None else None)
                    resolver = RoomCommandStateResolver(source, projector, fid, modules=modules,
                        native_binding=bindings[index], initial_observation=CanonicalRoomObservation(reference, status, cap, 1),
                        clock_observer=clock_observer, generation=RoomCommandGeneration(1, clock.until),
                        **(dict(task_compiler=task_compiler, retained_task_assessor=self.task_assessor)
                           if task_compiler is not None else {}))
                    authority = CanonicalFixtureAuthority(graph.journal, policy=policies[index], codec=modules.plan,
                        binding=bindings[index], decode_status=modules.zha.decode_snapshot_v3,
                        decode_capabilities=modules.zha.decode_capabilities_v2, current_resolver=resolver.resolve,
                        reservation_reader=lambda g, rev, h=installed: h['dispatcher'].row(g, rev),
                        command_validator=resolver.validate, bootstrap_resolver=resolver.bootstrap_original,
                        clock=lambda: clock.sample().now_home_us, create=True)
                    _need(authority.policy_digest == rows[index]['policy_sha256'], 'prepared_canonical_policy_differs')
                    dispatcher = CanonicalFixtureDispatcher(graph.journal, authority=authority, codec=modules.plan,
                        clock_home_us=lambda: clock.sample().now_home_us, create=True,
                        call_timeout_ms=SIMULATION_PROFILE['dispatch_timeout_ms'])
                    installed['dispatcher'] = dispatcher
                    cursor = modules.adapter.SessionCursor(bindings[index].boot_generation, bindings[index].session_id,
                        bindings[index].context_digest.hex(), state.sequence, 0, 0, 0)
                    adapter = RoomCanonicalAdapter(plant, index=index, dispatcher=dispatcher, binding=bindings[index],
                        session_cursor=cursor, clock_observer=clock.sample,
                        submit_credential=secrets.token_hex(32), observe_credential=secrets.token_hex(32))
                    installed['adapter'] = adapter
                    resolver.replace_observation(adapter.observe())
                    members.append(RoomCommandMember(fid, resolver, dispatcher, adapter))
                # Earlier members can age while the remaining three bootstrap.
                # This is an explicit retained successor, never a new horizon.
                for member in members:
                    current = member.adapter.clock_observation()
                    if current.now_home_us - current.observed_home_us >= SIMULATION_PROFILE['clock_refresh_interval_us']:
                        receipt = member.adapter.refresh_simulated_clock()
                        member.resolver.accept_simulated_clock_successor(member.adapter, receipt)
                        member.resolver.replace_observation(member.adapter.observe())
                return CanonicalRoomCommandJoin(source, projector, members=tuple(members),
                    **({'task_profile': deepcopy(RETAINED_TASK_PROFILE1)} if self.assessed else {}))

            def advance_native(selected):
                _need(selected is plant, 'same_owned_native_room_required')
                changed = False
                for member, state in zip(members, states):
                    current = member.adapter.clock_observation()
                    if current.now_home_us - current.observed_home_us >= SIMULATION_PROFILE['clock_refresh_interval_us']:
                        receipt = member.adapter.refresh_simulated_clock()
                        member.resolver.accept_simulated_clock_successor(member.adapter, receipt)
                        member.resolver.replace_observation(member.adapter.observe())
                        changed = True
                    changed = state.feedback() or changed
                return dict(status='advanced' if changed else 'waiting')

            return CanonicalCommandSession(journal=graph.journal, binding=binding, worker_recorder=worker_recorder,
                canonical_recorder=sidecar, plant=plant, bind_join=bind_join, advance_native=advance_native,
                current=lambda: self.composition.command_current(graph) is True,
                interval_s=limits['interval_s'], max_commits=limits['max_commits'],
                **({'task_profile': deepcopy(RETAINED_TASK_PROFILE1)} if self.assessed else {}))
        except BaseException:
            if plant is not None: plant.close()
            elif native_recorder is not None: native_recorder.abort('command_factory_failed_before_start', finalize=True)
            if sidecar is not None: sidecar.drain(dict(status='incomplete', reason='command_factory_failed_before_start'))
            raise


def _numeric(value, role):
    return int(_hash(dict(role=role, value=value))[:16], 16) or 1


def _authority_digest(policy, binding, modules):
    from fixture_execution_validation import record_bytes
    marker = dict(schema='home-fixture-canonical-authority-policy-1', version=1,
        fixture_id=policy.fixture_id, device_id=policy.device_id,
        context_digest=hashlib.sha256(modules.plan.encode_context(policy.context)).hexdigest(),
        binding_sha256=binding.binding_sha256, pins=policy.pins, principal_id=policy.principal_id,
        home_scope=policy.home_scope, room_binding=policy.room_binding, provider_pins=policy.provider_pins,
        issuers=[dict(mode=i.mode, issuer_id=i.issuer_id, issuer_epoch=i.issuer_epoch,
            lookup_credential_sha256=hashlib.sha256(i.lookup_credential.encode()).hexdigest())
            for i in (policy.ambient_issuer, policy.task_issuer)], max_support_us=policy.max_support_us,
        max_lookup_age_us=policy.max_lookup_age_us, clock_error_us=policy.clock_error_us, capacity=policy.capacity)
    return hashlib.sha256(record_bytes(marker)).hexdigest()


class _SimulatedClock:
    def __init__(self, composition, graph, duration):
        self.composition, self.graph = composition, graph
        first = self._input()
        self.original = deepcopy(first['clock'])
        self.home = self._home(first)
        self.uncertainty = int((Decimal(str(first['clock']['uncertainty_s'])) * 1000000).to_integral_value(rounding=ROUND_CEILING))
        _need(0 <= self.uncertainty <= 50000, 'canonical_simulated_clock_uncertainty_exceeded')
        self.until = self.home + int(Decimal(str(duration)) * 1000000)
        self.last = self.home

    @staticmethod
    def _home(value):
        _need(type(value) is dict and set(value) == {'observed_at', 'clock'}
              and type(value['clock']) is dict and value['clock'].get('status') == 'commissioned'
              and value['clock'].get('original_unit') == 'unix_ns',
              'original_commissioned_home_clock_required')
        ns = value['clock']['original_value']
        _need(type(ns) is str and ns.isdigit()
              and value['observed_at'] == float(Decimal(ns) / Decimal(1000000000)), 'original_home_clock_value_differs')
        return int(ns) // 1000

    def _input(self):
        _need(self.composition.command_current(self.graph) is True, 'command_clock_source_not_current')
        return self.composition.control_clock()

    def sample(self):
        from room_canonical_adapter import SimulatedClockObservation
        value = self._input(); now = self._home(value)
        _need({k: v for k, v in value['clock'].items() if k != 'original_value'}
              == {k: v for k, v in self.original.items() if k != 'original_value'}, 'command_clock_original_changed')
        _need(self.last <= now and now + self.uncertainty < self.until, 'command_clock_rollback_or_expired')
        self.last = now
        device = 1000000 + now - self.home
        return SimulatedClockObservation(now, device, self.home, self.uncertainty, self.until, 1, device, device)


class _NativeMember:
    """Bounded explicit simulated feedback over the actual retained ABI."""
    def __init__(self, plant, index, binding, modules, clock, config):
        self.plant, self.index, self.binding, self.modules, self.clock = plant, index, binding, modules, clock
        self.config, self.sequence, self.acknowledged = config, 0, set()
        self.last_temperature = self.last_tick = None

    def ask(self, operation, **fields):
        return self.plant.step(self.index, operation, **fields)

    def exchange(self, operation, body, *, bootstrap=False):
        w, b = self.modules.wire, self.binding
        self.sequence += 1
        c = self.clock.sample()
        context = w.BOOTSTRAP_CONTEXT if bootstrap else b.context_digest
        packet = w.seal_packet(b.key, b.device_id, b.boot_generation, context, w.Direction.REQUEST,
            operation, b.session_id, self.sequence, body)
        response = self.ask('exchange', at_device_us=str(c.now_device_us), ingress_device_us=str(c.now_device_us),
            packet_hex=packet.hex())
        _need(response['ok'] is True and response['packet_hex'] == packet.hex(), 'retained_bootstrap_exchange_required')
        opened = w.open_packet(b.key, b.device_id, b.boot_generation, context, w.Direction.RESPONSE,
            bytes.fromhex(response['reply_hex']))
        _need(opened.operation is operation and opened.session == b.session_id and opened.sequence == self.sequence,
              'bootstrap_reply_identity_differs')
        return w.Code(opened.body[0]), opened.body, c

    def bootstrap(self):
        w, b = self.modules.wire, self.binding
        for body in w.session_open_bodies(b.context, b.ownership_generation):
            code, _, _ = self.exchange(w.Operation.SESSION_OPEN, body, bootstrap=True)
            _need(code is w.Code.OK, 'simulated_session_open_refused')
        sent = self.clock.sample().now_home_us
        code, body, c = self.exchange(w.Operation.TIME_EXCHANGE, w.clock_query_body(sent))
        _need(code is w.Code.OK and len(body) == 26, 'simulated_clock_query_refused')
        echoed, ingress, built = struct.unpack('<3Q', body[2:])
        _need(echoed == sent and ingress == built == c.now_device_us, 'simulated_clock_query_original_differs')
        code, _, _ = self.exchange(w.Operation.TIME_EXCHANGE,
            w.clock_install_body(1, built, c.now_home_us, self.clock.uncertainty, 0))
        _need(code is w.Code.ADMITTED, 'simulated_clock_install_refused')
        self.ask('set_effect', at_device_us=str(self.clock.sample().now_device_us), driver='can', effect='submitted')
        self.ask('set_effect', at_device_us=str(self.clock.sample().now_device_us), driver='lighting', effect='submitted')

    def document(self, operation):
        w = self.modules.wire
        identity = offset = 0
        captured = total = None
        output = bytearray()
        for _ in range(80):
            code, body, _ = self.exchange(operation, w.status_body(identity, offset, 15))
            _need(code is w.Code.OK and len(body) >= 18, 'original_bootstrap_document_unavailable')
            got, stamp, size, returned, count = struct.unpack('<IQHHB', body[1:18])
            _need(count > 0 and len(body) == count + 18 and returned == offset, 'bootstrap_document_page_differs')
            if identity == 0: identity, captured, total = got, stamp, size
            _need((got, stamp, size) == (identity, captured, total)
                  and total in (404, 998), 'bootstrap_document_identity_differs')
            output.extend(body[18:]); offset += count
            if offset == total: return bytes(output), captured
        raise ValueError('bounded_bootstrap_document_incomplete')

    def feedback(self, *, force=False):
        c = self.clock.sample(); now = c.now_device_us
        if not force and self.last_tick is not None and now - self.last_tick < SIMULATION_PROFILE['native_tick_interval_us']:
            return False
        if force or self.last_temperature is None or now - self.last_temperature >= SIMULATION_PROFILE['temperature_interval_us']:
            self.ask('observe', at_device_us=str(now), observation=dict(kind='temperature',
                millic=SIMULATION_PROFILE['temperature_millic'], sampled_us=str(now), valid=True,
                config_valid=True, conversion_ready=True))
            self.last_temperature = now
        response = self.ask('tick', at_device_us=str(now)); self.last_tick = now
        for _ in range(SIMULATION_PROFILE['maximum_can_drain_steps']):
            pending = [op for op in response['canonical_ops'] if op['kind'] == 'can_port_transmit_once'
                and op['fields']['returned'] and op['operation_id'] not in self.acknowledged]
            if not pending: return True
            _need(len(pending) == 1, 'one_simulated_can_transaction_required')
            op = pending[0]; self.acknowledged.add(op['operation_id'])
            axis = op['fields']['axis']; motor = self.config['motors'][axis]
            opcode = bytes.fromhex(op['fields']['data_hex'])[0]
            _need(opcode in (0x92, 0x9c), 'unsupported_simulated_feedback_operation')
            data = (bytes((0x92, 0, 0, 0)) + struct.pack('<i', motor['motor_zero_centideg'])
                    if opcode == 0x92 else bytes((0x9c, 0, 0, 0, 0, 0, 0, 0)))
            response = self.ask('can_port', at_device_us=str(self.clock.sample().now_device_us), completed=True,
                failed=False, running=True, tx_count=0, frames=[dict(can_id=motor['response_id'], dlc=8,
                    extended=False, remote=False, data_hex=data.hex())])
        raise ValueError('bounded_simulated_can_drain_exceeded')

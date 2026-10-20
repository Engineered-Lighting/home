"""Selected declaration4 factory acceptance under the existing native guard.

Collection is inert. The root coordinator selects this ONE case in its pinned
Windows Job guard, with four canonical children maximum. It executes the actual
factory __call__, source8/config13/worker12, recorder READY, device bootstrap,
unavailable ambient, missing task assessment refusal and close. Camera/model/commissioning/currentness inputs are
authored; missing independent path evidence must refuse TASK. This case does
not establish movement, settlement, semantic spotlight mixing or physical use.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
TASK = ROOT / 'artifacts/den-e2e-desk-command-20261018T235057Z-fbe624b5'


def _load_upstream():
    path = ROOT / 'tools/fixtures/den-qualified-desk-source-runtime.py'
    spec = importlib.util.spec_from_file_location('_factory4_qualified_desk_source', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def _fixed_joint_profile(owner, worker, modules, canonical_config):
    """Authored software profile, fixed before native/session admission."""
    from fixture_joint_execution import ABI, COORDINATES, PROFILE_SCHEMA
    from test_fixture_profile import fictional_profile
    from room_command_factory import SIMULATION_PROFILE
    row = worker['room_policy']['fixtures'][0]
    fixture = row['assessment']['task']['delivery_policy']['fixture']
    profile = fictional_profile()
    profile['fixture_id'] = row['fixture_id']
    profile['pins'].update(SIMULATION_PROFILE['wire_pins'])
    until = int(Decimal(str(owner.f.now)) * 1000000) + 20000000
    for original in profile['evidence'].values():
        original['valid_until_home_us'] = until
    profile['geometry'].update(optical_pivot_m=deepcopy(fixture['origin_m']),
        fixture_to_room_basis=deepcopy(fixture['local_to_room']))
    for axis, motor, limit in zip(profile['axes'], canonical_config['motors'], (100, 50)):
        axis.update(zero_centideg=motor['motor_zero_centideg'], direction=motor['direction'],
            units_per_fixture_degree=dict(numerator=100 * motor['joint_per_motor_den'],
                denominator=motor['joint_per_motor_num']), minimum_centideg=-40000,
            maximum_centideg=40000, max_speed_deg_s=limit)
    profile['bounds']['max_quantization_error_rad'] = .0004
    return dict(schema=PROFILE_SCHEMA, version=1, motor_profile=profile,
        canonical=dict(abi=ABI, coordinate_contract=COORDINATES, **modules.provider_pins),
        spotlight=deepcopy(canonical_config['spotlight']))


class TrustedTaskFactoryNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # This only verifies/imports immutable provider originals. The caller
        # must run this selector inside the existing resource/launch guard.
        from test_room_canonical_plant import CanonicalNativeTests
        CanonicalNativeTests.setUpClass()

    def test_declaration4_actual_call_ready_bootstrap_and_close(self):
        from archive import Recorder
        from journal import digest
        from room_canonical_adapter import load_canonical_modules
        from room_canonical_provider import verified_provider_export
        from room_command_factory import (RoomCommandFactory, TRUSTED_TASK_FACTORY_REF,
            TRUSTED_TASK_SOURCE_INVENTORY, SIMULATION_PROFILE_REF, RETAINED_TASK_PROFILE_REF)
        from room_retained_task_assessor import TrustedRetainedTaskAssessor

        evidence = Path(os.environ['DEN_QUALIFIED_DESK_EVIDENCE_DIR'])
        self.assertTrue(evidence.is_absolute() and evidence.is_dir()
            and evidence.resolve().is_relative_to(TASK.resolve()))
        self.assertFalse(any(path.is_symlink() for path in (evidence, *evidence.parents)))
        self.assertFalse(any(evidence.iterdir()), 'fresh coordinator-selected evidence directory required')
        holder, lookup_queries = {}, []
        owner = None
        def on_graph(source, graph, worker):
            holder['source_owner'] = source  # Also preserve a partial constructor.
            self.assertEqual(int(graph.journal._meta('cursor')), 0)
            self.assertIsNone(graph.journal._recorder)
            self.assertEqual(source.composition.producer['version'], 8)
            self.assertEqual(graph.journal.dynamic.config['version'], 13)
            self.assertEqual(graph.journal.dynamic.worker.worker_version, 'home-live-context-worker-12.0.0')
            # Trusted authored server capability; the actual command_current
            # still validates Store/current graph/config/source/desk bindings.
            source.composition._command_current_guard = lambda: not source.closed
            modules = load_canonical_modules()
            export, _, _ = verified_provider_export()
            config = json.loads((export.build / 'source/contracts/c12/canonical-capture-config-v1.json').read_bytes())
            profile = _fixed_joint_profile(source, worker, modules, config)
            def original_lookup(query):
                lookup_queries.append(deepcopy(query))
                return None  # Independent witness is absent, never manufactured.
            assessor = TrustedRetainedTaskAssessor(profiles={profile['motor_profile']['fixture_id']: profile},
                receipt_store_id='authored-factory4-path-store', receipt_component_id='retained-path-assessor',
                method_ref=dict(id='authored-independent-path-method', digest='a' * 64),
                qualification_ref=dict(id='authored-software-only-path-qualification', digest='b' * 64),
                resolve_original=original_lookup, speed_fixture_deg_s=[20, 20], motion_timeout_us=2000000)
            pins = {relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                    for relative in TRUSTED_TASK_SOURCE_INVENTORY}
            bundle = digest(pins)
            public_worker = deepcopy(worker)
            for key in ('token', 'token_file', 'service_url'):
                public_worker.pop(key, None)
            manifest = dict(schema='home-live-session-manifest-2', version=2,
                runtime_profile='home-live-runtime-config-3', session_ref=deepcopy(source.source.scope),
                configuration_digest=graph.configuration_digest, source_bundle_digest=bundle,
                worker_config=public_worker, worker_description=None)
            recorder = Recorder(evidence / 'factory4-worker-originals.jsonl', manifest,
                max_records=8192, max_bytes=32 * 1024**2, min_free_bytes=1)
            holder.update(recorder=recorder)
            declaration = dict(schema='home-den-canonical-command-declaration-4', version=4,
                factory_ref=deepcopy(TRUSTED_TASK_FACTORY_REF), simulation_profile_ref=deepcopy(SIMULATION_PROFILE_REF),
                provider_pins=deepcopy(modules.provider_pins), source_bundle_digest=bundle, source_pins=pins,
                original_family=source.composition.producer['desk_policy']['original_family'],
                bundle_input_policy_sha256=source.composition.producer['desk_policy']['bundle_input_policy_sha256'],
                task_profile_ref=deepcopy(RETAINED_TASK_PROFILE_REF),
                task_assessor_ref=dict(id='home-retained-task-assessor-capability-1', digest=digest(assessor.descriptor())),
                recording=dict(native_file=str(evidence / 'factory4-native-originals.jsonl'),
                    canonical_file=str(evidence / 'factory4-authority-originals.jsonl'),
                    provenance_file=str(evidence / 'factory4-provenance.json'),
                    max_records=8192, max_bytes=32 * 1024**2, min_free_bytes=1),
                limits=dict(max_runtime_s=20, interval_s=.02, max_commits=128))
            factory = RoomCommandFactory(declaration, composition=source.composition, retained_task_assessor=assessor)
            commands = factory(graph, recorder)  # Actual new production branch.
            holder.update(factory=factory, commands=commands, declaration=declaration)
            self.assertTrue(factory.used)
            self.assertIs(commands.journal, graph.journal)
            self.assertEqual(recorder.status()['status'], 'ready')
            self.assertEqual(commands.canonical_recorder.status()['status'], 'ready')
            self.assertTrue(commands.plant.recorder.ready)
            self.assertFalse(commands.plant._started)
            self.assertEqual(commands.plant._members, [None] * 4)
            self.assertEqual(recorder.status()['records'], 1)
            provenance = json.loads(Path(declaration['recording']['provenance_file']).read_bytes())
            self.assertEqual(provenance['retained_task_assessor'], assessor.provenance())
            self.assertEqual(provenance['declaration'], declaration)
            self.assertEqual(provenance['client_binding']['launch_policy']['native_launches'], 4)
            holder['before_source_ready'] = dict(journal_cursor=0, worker=recorder.status(),
                canonical=commands.canonical_recorder.status(), native_recorder_ready=commands.plant.recorder.ready,
                native_started=False, provenance_sha256=hashlib.sha256(
                    Path(declaration['recording']['provenance_file']).read_bytes()).hexdigest())
            graph.journal.attach_recorder(commands.record)
            # RecorderRPC needs only its normal write port/path. Keep the actual
            # Recorder untouched so commands.record cannot recurse into itself.
            return SimpleNamespace(path=recorder.path, record=commands.record, status=recorder.status)
        cleanup, errors = {}, []
        try:
            upstream = _load_upstream()
            owner = upstream.Fixture(self, on_graph=on_graph)
            positive = owner.run_positive()
            _write_json(evidence / 'factory4-source-positive.json', positive)
            commands = holder['commands']
            self.assertIs(commands.journal, owner.graph.journal)
            self.assertGreaterEqual(commands.accepted_commits, 6)
            self.assertIsNone(commands.thread)
            self.assertFalse(commands.plant._started)
            commands.start()
            deadline = time.monotonic() + 20
            state = commands.status()
            while time.monotonic() < deadline:
                state = commands.status()
                if state['error'] or state['last_result'] is not None:
                    break
                commands.done.wait(.02)
            self.assertIsNone(state['error'], state)
            self.assertIsNotNone(state['last_result'], state)
            self.assertEqual(commands.readiness()['status'], 'ready')
            self.assertIsNotNone(commands.join)
            self.assertEqual(len(commands.join.members), 4)
            self.assertEqual(commands.plant._canonical_factory._launches, 4)
            self.assertIs(commands.join.members[0].resolver.task_assessor, holder['factory'].task_assessor)
            self.assertIsNotNone(commands.join.members[0].resolver.task_compiler)
            result = state['last_result']
            self.assertEqual(result['room_commit_ref']['commit_id'], positive['commit']['id'])
            self.assertEqual(result['fixtures'][0]['ambient']['status'], 'unavailable', result)
            self.assertEqual(result['fixtures'][0]['task']['status'], 'refused', result)
            self.assertIn('retained_path_original_required', result['fixtures'][0]['task']['reason'])
            self.assertEqual(result['fixtures'][0]['task']['light_request']['status'], 'unmet')
            self.assertEqual(len(lookup_queries), 1)
            for member in commands.join.members:
                current = member.resolver._observation
                self.assertEqual(len(current.raw_status_v3), 998)
                self.assertEqual(len(current.raw_capabilities_v2), 404)
                self.assertIs(member.dispatcher.journal, owner.graph.journal)
            originals = [json.loads(line)['record'] for line in holder['recorder'].path.read_bytes().splitlines()]
            self.assertFalse(any(row['kind'] == 'room_task_assessment_original' for row in originals))
            _write_json(evidence / 'factory4-positive.json', dict(schema='den-factory4-ready-bootstrap-acceptance-1',
                version=1, status='passed', before_source=holder['before_source_ready'], command=state,
                actual_factory_call=True, actual_source8_config13_worker12=True,
                independent_witness_missing=True, ambient_effect=False, task_effect=False, native_launches=4,
                native_settlement=False, semantic_spotlight_mapping=False, physical_permission=False,
                physical_truth=False, full_combined_replay=False))
        finally:
            # Command settlement is the original fence for Journal lifetime.
            # An uncertain drain must keep source/recorders alive until the
            # outer Job terminates the failed gate, never fabricate safe Stop.
            selected_owner = owner or holder.get('source_owner')
            safe = 'commands' not in holder
            if 'commands' in holder:
                try:
                    cleanup['commands'] = holder['commands'].drain(10)
                    safe = (cleanup['commands'].get('safe_to_close_journal') is True
                            and cleanup['commands'].get('pending') is False)
                except BaseException as error:
                    errors.append(dict(stage='commands', type=type(error).__name__, reason=str(error)[:2000]))
            if safe:
                try:
                    cleanup['source'] = selected_owner.close() if selected_owner is not None else None
                    safe = selected_owner is None or selected_owner.graph.finished is True
                except BaseException as error:
                    safe = False
                    errors.append(dict(stage='source', type=type(error).__name__, reason=str(error)[:2000]))
            if safe:
                try:
                    cleanup['worker_archive'] = holder['recorder'].drain(dict(status='complete',
                        reason='bounded_factory4_acceptance_finished', physical_permission=False)) if 'recorder' in holder else None
                except BaseException as error:
                    errors.append(dict(stage='worker_archive', type=type(error).__name__, reason=str(error)[:2000]))
            elif selected_owner is not None:
                from test_room_command_session import RoomCommandSessionTests
                RoomCommandSessionTests._defer_unsafe_fixture_cleanup(self, selected_owner,
                    holder.get('commands'), holder)
                cleanup['deferred'] = deepcopy(holder['deferred_cleanup'])
            _write_json(evidence / 'factory4-cleanup.json', dict(receipts=cleanup, errors=errors,
                physical_permission=False, native_settlement=False, full_combined_replay=False))
            self.assertFalse(errors, errors)
            self.assertTrue(safe, cleanup)
            if 'commands' in holder:
                receipt = cleanup['commands']
                self.assertTrue(receipt['safe_to_close_journal'], receipt)
                self.assertFalse(receipt['pending'], receipt)
                if holder['commands'].native_started:
                    self.assertEqual(receipt['status'], 'drained', receipt)
                    self.assertEqual(len(receipt['native_receipt']['members']), 4)
                    self.assertFalse(receipt['native_receipt']['cleanup_unconfirmed'])
                    self.assertTrue(all(row['close_acknowledged'] and row['recording_status']['complete']
                        for row in receipt['native_receipt']['members']))


if __name__ == '__main__':
    unittest.main()

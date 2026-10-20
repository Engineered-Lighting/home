"""Source4 -> sole recorded supervisor -> canonical software command lifecycle.

Only external observations, independent fictional commissioning and simulated
device feedback are authored. No accepted commit/decision/checkpoint is seeded.
The optional presence producer in this test is NOT installed source4 capability.
Its ordinary PID1 and the surveyed method's PID2 are two UNASSOCIATED authored
method identities on the SAME camera, not measured two-person truth or proof
of cross-method identity association/independent corroboration.
This is one tracer; the coordinator owns its bounded execution.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import http.client
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / 'stack/services/perception-lab', ROOT / 'stack/services/home-context',
             ROOT / 'tools/den-live-runtime'):
    if str(path) not in sys.path: sys.path.append(str(path))

from live_den_mount import _runtime_module
from journal import PREFIX, JournalError, canonical, digest
from live_capture import LiveCaptureAdapter
from live_acquisition_models import LocalModelStages
from live_spatial import LiveSpatialProducer
from live_interaction import LiveInteractionProducer
import live_capture_fixtures as capture_fixture
import lab_live_acquisition
import test_lab_stereo_worker_runtime as source4
import test_room_canonical_plant as native_fixture
from controlled_external_io import build_base_configuration, SOURCE as PRESENCE_SOURCE
from pc_support_configuration import upgrade_configuration
from fixture_canonical_authority import CanonicalFixtureAuthority, CanonicalFixtureAuthorityPolicy, CanonicalIssuerPolicy
from fixture_canonical_dispatch import CanonicalFixtureDispatcher
from fixture_canonical_archive import CanonicalFixtureRecorder
from fixture_execution_validation import PIN_NAMES, record_bytes
from room_canonical_adapter import CanonicalRoomObservation, RoomCanonicalAdapter, SimulatedClockObservation, load_canonical_modules
from room_canonical_plant import RoomCanonicalPlant
from room_canonical_plant_recording import RoomCanonicalRecorder
from room_command_projection import CanonicalProjectionProvider, RoomCommandProjector, MIXER_SOURCE_SHA256
from room_committed_source import RoomCommitBinding
from room_fixture_command_join import CanonicalRoomCommandJoin, RoomCommandGeneration, RoomCommandMember, RoomCommandStateResolver

_runtime_module()
from home_den_runtime.archive import Recorder
from home_den_runtime.supervisor import NodeWorkerDriver, SessionSupervisor
# Intentional first RED at the actual public producer seam, no substitute owner.
from home_den_runtime.room_command_session import CanonicalCommandSession

# One selected test per guarded process. An unsafe terminal receipt preserves
# its actual owners until the outer Job ends; no cancellation is claimed here.
_DEFERRED_AUTHORED_OWNERS = []


class RoomCommandSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native_fixture.CanonicalNativeTests.setUpClass()
        cls.native = native_fixture.CanonicalNativeTests

    def test_source4_supervisor_owns_accepted_commit_commands_and_stop(self):
        owner = source4.LabStereoWorkerRuntimeTests()
        self.addCleanup(owner.doCleanups)
        original_bundle = source4.authored_stereo_worker_bundle
        generated = source4.stereo_fixture.generated
        original_generated = generated.GeneratedStereoFixture
        def disjoint_authored_identity(*args, **kwargs):
            fixture = original_generated(*args, **kwargs)
            # The constructor has retained only raw camera/model/depth. Set
            # the fictional assessed-method identity BEFORE its upstream
            # qualification, independent assessment and binding are assembled.
            # This does not alias the ordinary Spatial producer's PID1 track.
            fixture.person_ref = {**fixture.person_ref, 'pid': 2}
            return fixture
        def bundle(source):
            value = original_bundle(source)
            self.presence = self._presence_commissioning(source, value)
            return value
        with patch.object(source4, 'authored_stereo_worker_bundle', bundle), \
                patch.object(generated, 'GeneratedStereoFixture', disjoint_authored_identity):
            owner.setUp()
        self.assertEqual(owner.f.person_ref['pid'], 2)
        # Deliberately do NOT call owner.create(): the supervisor must perform
        # the first Journal recorder attachment before its first worker/source.
        holder, ready = {'worker_commit_errors': []}, threading.Event()
        self.holder = holder
        self.owner = owner
        archive_path = owner.path / 'command-supervisor-session.jsonl'
        manifest = dict(schema='home-live-session-manifest-1', version=1,
            session_ref=deepcopy(owner.source.scope),
            configuration_digest=owner.composition.configuration_digest,
            source_bundle_digest='b' * 64, worker_config=deepcopy(owner.worker),
            worker_description=None)

        def graph_factory():
            original_resolver = lab_live_acquisition.LabLiveAcquisition._resolve_original
            def resolve(graph, store_id, receipt_id):
                # Closed new test-only stores delegate to ACTUAL original
                # owners; incumbent Lab lookup and every admission check stay.
                for producer in self.presence_owners:
                    if store_id == producer.policy['receipt_store_id']:
                        return producer.resolve_receipt(store_id, receipt_id)
                return original_resolver(graph, store_id, receipt_id)
            with patch.object(lab_live_acquisition.LabLiveAcquisition, '_resolve_original', resolve):
                graph = owner.composition.create()
            owner.graph = owner.source.graph = graph
            self.assertIsNone(graph.journal._recorder)
            holder['graph'] = graph
            original_worker_commit = graph.journal.dynamic.worker.commit
            def diagnosed_worker_commit(commit, grant):
                try: return original_worker_commit(commit, grant)
                except JournalError as error:
                    if len(holder['worker_commit_errors']) < 8:
                        holder['worker_commit_errors'].append(dict(commit_id=commit.get('id'),
                            code=error.code, status=error.status, message=error.message[:512]))
                    raise  # The real independent validator remains unchanged.
            graph.journal.dynamic.worker.commit = diagnosed_worker_commit
            step, quiesce, withdraw, finalize = graph.step, graph.quiesce, graph.withdraw, graph.finalize
            cycle, source_turn = [0], threading.Event()
            def produce_step():
                holder['source_stage'] = 'before_original_pair'
                if graph.cancel.is_set(): return dict(status='stopped', reason='authored_source_cancelled')
                cycle[0] += 1
                offset = cycle[0] + 1
                owner.f.now = source4.live.NOW + offset + .35
                owner.f.append_pair(sequence=7 + offset, observed_at=source4.live.NOW + offset)
                if graph.cancel.is_set(): return dict(status='stopped', reason='authored_source_cancelled')
                holder['source_stage'] = 'desk_admission'
                admitted = step()
                if graph.cancel.is_set(): return dict(status='stopped', reason='authored_source_cancelled')
                self.assertEqual(admitted['status'], 'admitted_desk', admitted)
                ha = source4.authored_pc_fixture(seq=offset, snapshot_at=source4.live.NOW + offset + .1,
                    input_at=source4.live.NOW + offset - .1)['original']['ha_event']
                owner.f.store.append_event(owner.f.session['id'], 'ha.observation', ha['payload'])
                holder['source_stage'] = 'pc_admission'
                pc = step()
                if graph.cancel.is_set(): return dict(status='stopped', reason='authored_source_cancelled')
                self.assertEqual(pc['status'], 'admitted_attributed')
                holder['source_stage'] = 'qualified_presence'
                self._publish_presence(owner)
                if graph.cancel.is_set(): return dict(status='stopped', reason='authored_source_cancelled')
                owner.f.now = source4.live.NOW + offset + .55
                holder['source_stage'] = 'worker_cycle'
                owner.driver.cycle(owner.f.now)
                holder['source_stage'] = 'accepted_ambient_sample'
                if not self._accepted_positive_ambient(archive_path):
                    return admitted  # Preserve the real dark fade-in sample.
                # Hold this authored original cut until its actual command
                # result, avoiding a fabricated command for a superseded cut.
                # Retained authored run 75816b501d0a44519d95e0ec8eeaa469
                # took 37.234s for native I/O. This finite software settlement
                # wait stays within 90s Supervisor/100s assertion/180s Job;
                # no source lease, clock, per-call bound or real-time claim.
                deadline = time.monotonic() + 60
                holder['source_stage'] = 'accepted_command_settlement'
                while time.monotonic() < deadline and not supervisor.cancel.is_set():
                    result = holder['commands'].status().get('last_result')
                    if result is not None and result['room_commit_ref']['commit_id'] == self._latest_accepted(archive_path):
                        return admitted
                    time.sleep(.02)
                if supervisor.cancel.is_set():
                    return dict(status='stopped', reason='authored_source_cancelled')
                self.fail('bounded original accepted room command did not settle: ' + canonical(holder['commands'].status()))
            def observed_step():
                source_turn.set()
                try: return produce_step()
                except Exception as error:
                    # Authored fixture only. The real supervisor deliberately
                    # exposes a safe reason, so preserve this bounded original
                    # exception before its ordinary cleanup removes Temp.
                    holder['source_error'] = dict(type=type(error).__name__,
                        message=str(error)[:4096], traceback=traceback.format_exc()[-16384:],
                        stage=holder.get('source_stage'), cycle=cycle[0],
                        publish_error=holder.get('publish_error'))
                    raise
                finally: source_turn.clear()
            def observed_quiesce(reason='operator_stop'):
                receipt = quiesce(reason)
                # The test-only public presence producer is part of this same
                # source turn and must finish before source barriers can drain.
                if source_turn.is_set():
                    receipt.update(status='unconfirmed', inflight=True)
                return receipt
            def observed_withdraw(reason='operator_stop'):
                retired = self.presence_owners[0].withdraw(retain_originals=True)
                self.assertEqual(retired['withdrawal'], 'recorded', retired)
                return withdraw(reason)
            def observed_finalize(reason='operator_stop', *, drain_receipt=None):
                for producer in reversed(self.presence_owners):
                    clear = getattr(producer, 'finalize', None) or producer.clear
                    self.assertTrue(clear())
                self.assertTrue(self.presence_models.clear())
                return finalize(reason, drain_receipt=drain_receipt)
            graph.step, graph.quiesce, graph.withdraw, graph.finalize = observed_step, observed_quiesce, observed_withdraw, observed_finalize
            return graph

        def recorder_factory(graph):
            value = Recorder(archive_path, manifest, max_records=4096,
                max_bytes=64 * 1024**2, min_free_bytes=1)
            holder['worker_recorder'] = value
            return value

        def worker_factory(graph, recorder_port):
            value = NodeWorkerDriver(node_runtime=shutil.which('node'),
                config={**deepcopy(owner.worker), 'token': owner.worker_token,
                    'service_url': 'http://127.0.0.1:' + str(graph.address[1])},
                recorder=recorder_port, controlled_time=owner.f.now)
            owner.driver = value
            ordinary_drain = value.drain
            def drained(*, deadline_ms, through_cursor=0):
                # Controlled clock only: advance the SAME actual worker after
                # durable source withdrawal, rather than fabricate its final cut.
                owner.f.now += 1
                value.cycle(owner.f.now)
                return ordinary_drain(deadline_ms=deadline_ms, through_cursor=through_cursor)
            value.drain = drained
            return value

        def command_factory(graph, worker_recorder):
            self.assertIs(worker_recorder, holder['worker_recorder'])
            binding = RoomCommitBinding(deepcopy(owner.source.scope), owner.worker['worker_id'],
                owner.worker['producer_id'], graph.journal.dynamic.worker.policy['config_digest'],
                graph.journal.dynamic.config['manifest_ref']['digest'],
                graph.journal.dynamic.worker.worker_version,
                tuple(f['fixture_id'] for f in owner.worker['room_policy']['fixtures']))
            commands = self._canonical_commands(owner, graph, binding, worker_recorder)
            self.assertIs(type(commands), CanonicalCommandSession)
            holder['commands'] = commands
            ready.set()
            return commands

        limits = dict(schema='home-live-session-config-1', version=1,
            session_ref=deepcopy(owner.source.scope), configuration_digest=owner.composition.configuration_digest,
            mode='controlled', max_runtime_s=90, max_cycles=4, interval_s=1, drain_timeout_s=10)
        self._create_presence_owners(owner)
        supervisor = SessionSupervisor(limits, graph_factory=graph_factory,
            recorder_factory=recorder_factory, worker_factory=worker_factory, preflight=lambda _: {'status': 'ready'},
            stop_preparation=owner.composition.stop_preparation, release=owner.composition.release,
            command_factory=command_factory)
        self.addCleanup(self._stop_and_wait, supervisor, 'tracer_cleanup')
        try:
            supervisor.start()
            self.assertTrue(ready.wait(15), supervisor.status())
            self._advance_and_assert_terminal(owner, supervisor, holder, archive_path)
        finally:
            # Keep ordinary bounded Stop, then copy fixed authored artifacts
            # before any registered owner/TemporaryDirectory cleanup runs.
            try:
                receipt = self._stop_and_wait(supervisor, 'authored_fixture_evidence')
                holder['final_stop_receipt'] = receipt
                safe = (receipt.get('status') == 'stopped'
                    and (receipt.get('command_drain') or {}).get('safe_to_close_journal') is True)
            except BaseException as error:
                safe = False
                holder['final_stop_error'] = dict(type=type(error).__name__, message=str(error)[:512])
                raise
            finally:
                if not safe:
                    self._defer_unsafe_fixture_cleanup(owner, supervisor, holder)
                self._retain_fixture_evidence(owner, supervisor, holder, archive_path)

    @staticmethod
    def _latest_accepted(path):
        rows = [json.loads(line)['record'] for line in path.read_bytes().splitlines()]
        return next(row['data']['receipt']['id'] for row in reversed(rows) if row['kind'] == 'worker_commit_result')

    @staticmethod
    def _accepted_positive_ambient(path):
        rows = [json.loads(line)['record'] for line in path.read_bytes().splitlines()]
        accepted = next(row['data']['receipt']['id'] for row in reversed(rows) if row['kind'] == 'worker_commit_result')
        prepared = next(row['data']['prepared'] for row in reversed(rows)
            if row['kind'] == 'worker_prepared' and row['data']['commit']['id'] == accepted)
        return any(domain['sample'] is not None and domain['sample']['brightness_fraction'] > 0
            for fixture in prepared['decision']['room_output']['fixtures'] for domain in fixture['domains'][:7])

    def _presence_commissioning(self, source, value):
        """One SAME generated camera pair, extra fictional method, no new owner.

        The real source4 path does not presently publish qualified presence.
        This test explicitly commissions the incumbent spatial/interaction
        producers for that SAME camera, not independent corroboration. No
        camera transport is called by this fixture augmentation.
        """
        f = source.f
        authored = build_base_configuration(session_ref=source.scope, node_runtime=shutil.which('node'),
            wall_clock=lambda: f.now, monotonic=lambda: f.now - source4.live.NOW)
        _, cp, _, _, _ = capture_fixture.setup()
        cp.update(camera_id=f.session['camera_id'], projection=deepcopy(f.projection),
            mount_signature=deepcopy(f.projection['mount_signature']), registration=deepcopy(f.registration))
        cm = dict(id='authored-runtime-presence-capture', digest=digest(cp))
        sp = deepcopy(authored['spatial_policy']); sp['capture_method_ref'] = deepcopy(cm)
        sm = dict(id='authored-runtime-presence-spatial', digest=digest(sp))
        ip = deepcopy(authored['interaction_policy'])
        ip.update(capture_method_ref=deepcopy(cm), spatial_method_ref=deepcopy(sm))
        im = dict(id='authored-runtime-presence-interaction', digest=digest(ip))
        config, worker = value['live_config'], value['worker_config']
        camera = config['manifest']['sources']['synthetic-source']
        source_definition = deepcopy(authored['configuration']['manifest']['sources'][PRESENCE_SOURCE])
        source_definition.update(producer_id=camera['producer_id'], producer_epoch=camera['producer_epoch'],
            capture_epoch=camera['capture_epoch'], capture_clock=deepcopy(camera['capture_clock']),
            control_clock=deepcopy(camera['control_clock']), registration=deepcopy(camera['registration']),
            sensor_roots=deepcopy(camera['sensor_roots']), method_refs=[deepcopy(cm), deepcopy(sm), deepcopy(im)],
            association_method_refs=[deepcopy(cm), deepcopy(sm)],
            receipt_store_ids=[cp['receipt_store_id'], sp['receipt_store_id'], ip['receipt_store_id']],
            receipt_component_ids=['rgb', 'depth', 'spatial', 'interaction'])
        for name, rule in source_definition['part_policies'].items():
            if not name.endswith('_forward'):
                rule.update(bound_at_observation_m=.2,
                    components_m=dict(capture_association=.02, depth=.04, registration=.02, estimator=.12))
        config['manifest']['sources'][PRESENCE_SOURCE] = source_definition
        # Current scene authority resolves background from its approved catalog
        # (live_scene.validate_scene_policy), never a top-level policy shortcut.
        worker['scene_policy']['catalog']['background']['methods'].append(deepcopy(im))
        config['manifest']['worker']['scene_policy'] = deepcopy(worker['scene_policy'])
        config.update(schema='home-live-ingress-config-9', version=9)
        config['manifest'].update(schema='home-live-ingress-manifest-9', version=9)
        config['manifest'].pop('pc_support_interface_digest')
        config['manifest']['worker'].pop('pc_support_policy')
        config['manifest']['worker'].pop('pc_support_schema_pins')
        config, worker = upgrade_configuration(config, worker, worker['pc_support_policy'])
        value.update(live_config=config, worker_config=worker)
        value['semantic_policy']['pc_support_policy_digest'] = digest(worker['pc_support_policy'])
        return dict(configuration=config, capture_policy=cp, capture_method=cm,
            spatial_policy=sp, spatial_method=sm, interaction_policy=ip, interaction_method=im,
            detector=authored['detector'], pose=authored['pose'], external=authored['external_io'])

    def _create_presence_owners(self, owner):
        p = self.presence
        original_path = owner.path / 'authored-presence-originals.jsonl'
        stream = original_path.open('xb'); self.addCleanup(stream.close)
        originals = set()
        phase = ['before_callback']
        def publish_original(event):
            phase[0] = 'current_before_original_retention'
            graph = self.holder['graph']
            if event['payload']['status'] != 'withdrawn':
                self.assertFalse(graph.cancel.is_set(), 'no_first_positive_presence_append_after_Stop')
            for ref in event['payload'].get('original_receipt_refs', []):
                if ref['digest'] in originals: continue
                phase[0] = 'original_owner_lookup'
                producer = next(item for item in self.presence_owners if item.policy['receipt_store_id'] == ref['store_id'])
                original = producer.original_receipt(ref['store_id'], ref['receipt_id'])
                phase[0] = 'private_original_encoding'
                # Same private JSON dialect as live_capture._original_digest:
                # native 64-bit ns are original metadata, not wire JSON.
                raw = (json.JSONEncoder(sort_keys=True, separators=(',', ':'), allow_nan=False).encode(
                    dict(ref=ref, original=original)) + '\n').encode()
                self.assertLess(len(raw), 8 * 1024**2)
                phase[0] = 'original_write_fsync'
                self.assertEqual(stream.write(raw), len(raw)); stream.flush(); __import__('os').fsync(stream.fileno())
                originals.add(ref['digest'])
            phase[0] = 'public_http_append'
            connection = http.client.HTTPConnection('127.0.0.1', graph.address[1], timeout=4)
            try:
                connection.request('POST', PREFIX + '/events', canonical(event),
                    {'Authorization': 'Bearer ' + source4.live.TOKEN, 'Content-Type': 'application/json'})
                reply = connection.getresponse(); result = json.loads(reply.read())
                phase[0] = 'public_http_ack'
                self.assertEqual(reply.status, 200, result)
                return result
            finally: connection.close()
        def publish(event):
            try: return publish_original(event)
            except Exception as error:
                # Capture the authored port exception before the production
                # capture adapter correctly turns an uncertain ACK into refusal.
                self.holder['publish_error'] = dict(stage=phase[0], type=type(error).__name__,
                    message=str(error)[:4096], traceback=traceback.format_exc()[-16384:])
                raise
        capture = LiveCaptureAdapter(p['configuration'], source_id=PRESENCE_SOURCE, revision='synthetic-r1',
            policy=p['capture_policy'], method_ref=p['capture_method'], clock=lambda: owner.f.now, publish=publish,
            control_clock=owner.source.control_clock,
            authority_generation=lambda: self.holder['graph'].journal.revocation_generation(
                owner.source.scope['site_id'], owner.source.scope['room_id']))
        spatial = LiveSpatialProducer(p['configuration'], source_id=PRESENCE_SOURCE, revision='synthetic-r1',
            policy=p['spatial_policy'], method_ref=p['spatial_method'], capture_input=capture.measurement_input,
            clock=lambda: owner.f.now, monotonic=lambda: owner.f.now - source4.live.NOW,
            publish=publish, barrier=lambda reason: capture.source_gap(reason))
        interaction = LiveInteractionProducer(p['configuration'], source_id=PRESENCE_SOURCE, revision='synthetic-r1',
            policy=p['interaction_policy'], method_ref=p['interaction_method'], capture_input=capture.measurement_input,
            spatial_original=spatial.original_receipt, clock=lambda: owner.f.now,
            monotonic=lambda: owner.f.now - source4.live.NOW, publish=publish, barrier=lambda reason: capture.source_gap(reason))
        self.presence_owners = (capture, spatial, interaction)
        self.presence_models = LocalModelStages(p['detector'], p['pose'], detector_ref=p['spatial_policy']['model_refs']['detector'],
            pose_ref=p['spatial_policy']['model_refs']['pose'], person_threshold=.5)
        p['external'].set_scenario('presence')

    def _publish_presence(self, owner):
        capture, spatial, interaction = self.presence_owners
        pair = owner.f.pair  # Exact SAME original JPEG/header/NPZ/calibration; no camera fetch.
        captured = capture.offer(pair['jpeg'], pair['header'], pair['bundle'], pair['calibration'], received_at=pair['received_at'])
        self.assertEqual(captured['status'], 'qualified', captured)
        frame = captured['capture']['id']
        graph = self.holder['graph']
        options = dict(authorized=lambda: not graph.cancel.is_set(), deadline=owner.f.now - source4.live.NOW + 2,
                       cancel=graph.cancel, clock=lambda: owner.f.now - source4.live.NOW)
        detected = self.presence_models.detect(pair['jpeg'], frame_id=frame, request_id=frame + ':detector', **options)
        posed = self.presence_models.pose(detected, frame_id=frame, request_id=frame + ':pose', **options)
        context = dict(schema='home-acquisition-local-context-1', version=1, session_ref=deepcopy(owner.source.scope),
            source_id=PRESENCE_SOURCE, configuration_digest=digest(dict(capture=self.presence['capture_policy'],
                spatial=self.presence['spatial_policy'], interaction=self.presence['interaction_policy'])),
            capture=deepcopy(captured['capture']), capture_payload=deepcopy(captured['publication']['payload']),
            publication_receipt=deepcopy(captured['receipt']),
            original_receipts=[capture.original_receipt(ref['store_id'], ref['receipt_id'])
                for ref in captured['publication']['payload']['original_receipt_refs']],
            detector=detected.metadata(), pose=posed, detector_available_at=owner.f.now, pose_available_at=owner.f.now,
            availability_clock='utc_epoch_seconds', model_availability_clock='monotonic_seconds',
            person_association='unresolved', spatial=None, interaction=None)
        stage_options = {key: options[key] for key in ('authorized', 'deadline', 'cancel')}
        context['spatial'] = spatial.offer(context, **stage_options)
        self.assertEqual(context['spatial']['status'], 'supplied', context['spatial'])
        result = interaction.offer(context, **stage_options)
        self.assertEqual(result['status'], 'supplied', result)
        presence = next(event['payload'] for event in result['publications'] if event['payload']['kind'] == 'person_presence')
        self.assertEqual((presence['status'], presence['value']), ('supplied', 'present'))
        self.assertEqual(presence['person_ref']['pid'], 1)
        self.assertEqual(presence['sensor_roots'], ['camera:den'])

    def _canonical_commands(self, owner, graph, binding, worker_recorder):
        modules, native = load_canonical_modules(), self.native
        initial_home = int(Decimal(str(owner.f.now)) * 1000000)
        header = native_fixture.header(native.manifest)
        for row, fixture_id in zip(header['fixtures'], binding.fixture_ids): row['fixture_id'] = fixture_id
        header['session_id'] = owner.f.session['id']
        native_recorder = RoomCanonicalRecorder(owner.path / 'command-native-originals.jsonl', header=header)
        plant = RoomCanonicalPlant(str(native.manifest_path), native_fixture.PIN, native_recorder,
            fixture_ids=binding.fixture_ids, process_factory=native.provider)
        contexts, bindings, policies, rows = [], [], [], []
        room_binding = {key: getattr(binding, key) for key in ('configuration_digest', 'manifest_digest', 'worker_version')}
        room_binding['room_output_schema'] = 'home-live-room-output-1'
        for index, fixture_id in enumerate(binding.fixture_ids):
            context = modules.plan.example_context()
            context.update(boot_generation=str(index + 1), session_id=str(index + 2), ownership_generation=str(index + 4))
            raw_context = modules.plan.encode_context(context)
            native_binding = modules.zha.RuntimeBinding('00:11:22:33:44:55:66:77',
                '00:11:22:33:44:55:66:' + format(128 + index, '02x'), format(index + 1, '02x') * 16,
                bytes(n + index * 16 for n in range(16)), bytes(n + index for n in range(32)),
                index + 1, index + 2, index + 4, raw_context, 'aa' * 32, 'bb' * 32)
            pins = {**context['pins'], **{key: hashlib.sha256(('authored-' + key).encode()).hexdigest()
                for key in PIN_NAMES if key not in context['pins']}}
            policy = CanonicalFixtureAuthorityPolicy(fixture_id, native_binding.device_id.hex(), deepcopy(context), pins,
                context['principal_id'], CanonicalIssuerPolicy('autonomous-ambient', 'ambient-' + str(index), '1', 'lookup-ambient-' + str(index)),
                CanonicalIssuerPolicy('autonomous-task', 'task-' + str(index), '1', 'lookup-task-' + str(index)),
                deepcopy(binding.scope), deepcopy(room_binding), deepcopy(modules.provider_pins))
            authority_sha = self._authority_policy_sha(policy, native_binding, modules)
            namespace = hashlib.sha256(record_bytes(dict(fixture_id=fixture_id, device_id=policy.device_id))).hexdigest()
            dispatch_policy = dict(schema='home-fixture-canonical-dispatch-policy-1', version=1,
                authority_policy_sha256=authority_sha, namespace=namespace, max_operations=4096, call_timeout_ms=3000)
            rows.append(dict(namespace=namespace, policy_sha256=authority_sha, fixture_id=fixture_id,
                device_id=policy.device_id, principal_id=policy.principal_id, native_binding_sha256=native_binding.binding_sha256,
                context=deepcopy(context), context_hex=raw_context.hex(), pins=deepcopy(pins), home_scope=deepcopy(binding.scope),
                room_binding=deepcopy(room_binding), issuers=[dict(mode=i.mode, issuer_id=i.issuer_id, issuer_epoch=i.issuer_epoch)
                    for i in (policy.ambient_issuer, policy.task_issuer)], dispatch_policy=dispatch_policy,
                dispatch_policy_sha256=hashlib.sha256(record_bytes(dispatch_policy)).hexdigest()))
            contexts.append(context); bindings.append(native_binding); policies.append(policy)
        sidecar_binding = dict(schema='home-fixture-canonical-archive-binding-1', version=1,
            parent_manifest_sha256=digest(worker_recorder.manifest),
            source_pins={str(Path(__file__).relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
            provider_pins=deepcopy(modules.provider_pins), authorities=rows)
        sidecar = CanonicalFixtureRecorder(owner.path / 'command-authority-originals.jsonl', binding=sidecar_binding)
        plants, members, last_ticks = [], [], {}
        fixture = self
        class JoinedPlant(native.scenarios.Plant):
            def __init__(self, index):
                self.index = index; self.now = 1000000 + int(Decimal(str(owner.f.now)) * 1000000) - initial_home
                self.sequence = 0
                self.effects, self.events, self.operations, self.responses = [], [], [], []
                self.key, self.device = bindings[index].key, bindings[index].device_id
                self.context = deepcopy(contexts[index]); self.context_raw = modules.plan.encode_context(self.context)
                self.context_sha = hashlib.sha256(self.context_raw).digest()
                self.description = self.ask('describe')
            def ask(self, op, **fields): return self.collect(plant.step(self.index, op, **fields))
            def port(self, *, completed=False, failed=False, running=True, tx_count=0, frames=(), at=None):
                if at is not None: self.now = at
                return self.ask('can_port', at_device_us=str(self.now), completed=completed, failed=failed,
                    running=running, tx_count=tx_count, frames=list(frames))
            def bootstrap(self):
                import struct
                for body in native.scenarios.wire.session_open_bodies(self.context_raw, self.index + 4):
                    code, _, _ = self.exchange(native.scenarios.wire.Operation.SESSION_OPEN, body, bootstrap=True)
                    fixture.assertEqual(code, native.scenarios.wire.Code.OK)
                home_us = int(Decimal(str(owner.f.now)) * 1000000)
                code, raw, _ = self.exchange(native.scenarios.wire.Operation.TIME_EXCHANGE,
                    native.scenarios.wire.clock_query_body(home_us))
                fixture.assertEqual(code, native.scenarios.wire.Code.OK)
                echoed, ingress, built = struct.unpack('<3Q', raw[2:])
                fixture.assertEqual((echoed, ingress, built), (home_us, self.now, self.now))
                self.mapping_home, self.mapping_device = home_us, built
                code, _, _ = self.exchange(native.scenarios.wire.Operation.TIME_EXCHANGE,
                    native.scenarios.wire.clock_install_body(1, built, home_us, 0, 0))
                fixture.assertEqual(code, native.scenarios.wire.Code.ADMITTED)
            def clock(self):
                return SimulatedClockObservation(self.mapping_home + self.now - self.mapping_device, self.now,
                    self.mapping_home, 0, self.mapping_home + 60000000, 1, self.now, self.now)
        def bind_join(source):
            provider = CanonicalProjectionProvider(**modules.provider_pins,
                mixer_source_sha256=MIXER_SOURCE_SHA256, ambient_mix=modules.outputs.ambient_mix)
            projector = RoomCommandProjector(source, room_policy=owner.worker['room_policy'], provider=provider)
            # The session owns this call, exactly once, after a real supported
            # commit. No native bootstrap or I/O occurs in recorder callbacks.
            for index, fixture_id in enumerate(binding.fixture_ids):
                p = JoinedPlant(index); plants.append(p)
                p.bootstrap(); p.healthy(); p.effect('lighting'); p.effect('can')
                cap, _ = p.document(native.scenarios.wire.Operation.CAPABILITIES)
                raw, captured = p.document(native.scenarios.wire.Operation.STATUS)
                decoded = modules.zha.decode_snapshot_v3(raw, binding=bindings[index], captured_device_us=captured)
                ref = modules.outputs.CurrentStateReference(bindings[index].binding_sha256,
                    hashlib.sha256(raw).hexdigest(), hashlib.sha256(cap).hexdigest(), index + 1, index + 2, index + 4,
                    p.context_sha.hex(), int(decoded['state_generation']), int(decoded['revision_id']),
                    tuple(d['generation'] for d in decoded['domains']), captured, p.now, p.now, 250000, 'simulated')
                resolver = RoomCommandStateResolver(source, projector, fixture_id, modules=modules, native_binding=bindings[index],
                    initial_observation=CanonicalRoomObservation(ref, raw, cap, 1), clock_observer=p.clock,
                    generation=RoomCommandGeneration(1, initial_home + 60000000))
                selected = {}
                authority = CanonicalFixtureAuthority(graph.journal, policy=policies[index], codec=modules.plan,
                    binding=bindings[index], decode_status=modules.zha.decode_snapshot_v3,
                    decode_capabilities=modules.zha.decode_capabilities_v2, current_resolver=resolver.resolve,
                    reservation_reader=lambda g, r, h=selected: h['dispatcher'].row(g, r), command_validator=resolver.validate,
                    bootstrap_resolver=resolver.bootstrap_original,
                    clock=lambda p=p: p.clock().now_home_us, create=True)
                self.assertEqual(authority.policy_digest, rows[index]['policy_sha256'])
                dispatcher = CanonicalFixtureDispatcher(graph.journal, authority=authority, codec=modules.plan,
                    clock_home_us=lambda p=p: p.clock().now_home_us, create=True, call_timeout_ms=3000)
                selected['dispatcher'] = dispatcher
                cursor = modules.adapter.SessionCursor(index + 1, index + 2, p.context_sha.hex(), p.sequence, 0, 0, 0)
                adapter = RoomCanonicalAdapter(plant, index=index, dispatcher=dispatcher, binding=bindings[index],
                    session_cursor=cursor, clock_observer=p.clock, submit_credential='submit-' + str(index),
                    observe_credential='observe-' + str(index))
                resolver.replace_observation(adapter.observe())
                members.append(RoomCommandMember(fixture_id, resolver, dispatcher, adapter))
            return CanonicalRoomCommandJoin(source, projector, members=tuple(members))
        def advance_native(room):
            self.assertIs(room, plant)
            changed = False
            for p in plants:
                mapped = p.mapping_device + int(Decimal(str(owner.f.now)) * 1000000) - p.mapping_home
                # Pinned scenario.exchange consumes one device microsecond per
                # packet. Preserve elapsed protocol ticks without renewing the
                # original mapping, qualification deadline or source leases.
                p.now = max(p.now, mapped)
                key = (p.now, p.sequence)
                if last_ticks.get(p.index) == key: continue
                p.tick(); last_ticks[p.index] = key; changed = True
            return dict(status='advanced' if changed else 'waiting')
        self.holder.update(native=plant, native_recorder=native_recorder, sidecar=sidecar,
            sidecar_binding=sidecar_binding, members=members, plants=plants)
        return CanonicalCommandSession(journal=graph.journal, binding=binding, worker_recorder=worker_recorder,
            canonical_recorder=sidecar, plant=plant, bind_join=bind_join, advance_native=advance_native,
            current=lambda: not graph.cancel.is_set() and owner.composition.require_current() is not None,
            interval_s=.02, max_commits=256)

    @staticmethod
    def _authority_policy_sha(policy, binding, modules):
        # Same inert declaration as the real issuer. The real constructed
        # issuer must later match this pre-effect recorder binding exactly.
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

    def _advance_and_assert_terminal(self, owner, supervisor, holder, archive_path):
        deadline, positive = time.monotonic() + 100, False
        while time.monotonic() < deadline:
            status = holder['commands'].status()
            result = status.get('last_result')
            if result is not None and all(row['ambient']['status'] == 'dispatched' for row in result['fixtures']):
                plans = [row['ambient']['dispatch']['record']['admitted']['plan'] for row in result['fixtures']]
                if all(any(plan['frame'][:21]) for plan in plans):
                    positive = True
                    break
            if supervisor.status()['status'] in ('stopped', 'incomplete', 'refused'): break
            time.sleep(.02)
        self.assertTrue(positive, dict(commands=holder['commands'].status(),
            supervisor=supervisor.status(), source_error=holder.get('source_error'),
            source_stage=holder.get('source_stage'), worker_commit_errors=holder['worker_commit_errors']))
        stopped = self._stop_and_wait(supervisor, 'authored_command_tracer_complete')
        self.assertEqual(stopped['status'], 'stopped', stopped)
        command = holder['commands'].drain(10)
        self.assertEqual(command['status'], 'drained', command)
        self.assertTrue(command['safe_to_close_journal'])
        self.assertEqual(command['native_receipt']['status'], 'complete')
        self.assertEqual(command['canonical_recording']['status'], 'drained')
        self.assertEqual(command['combined_replay'], 'unproven')
        rows = [json.loads(line)['record'] for line in archive_path.read_bytes().splitlines()]
        self.assertEqual(rows[0]['kind'], 'session_manifest')
        self.assertNotIn('test_capture_boundary', [row['kind'] for row in rows])
        accepted = {row['data']['receipt']['id'] for row in rows if row['kind'] == 'worker_commit_result'}
        self.assertGreaterEqual(len(accepted), 3)
        self.assertTrue(any(row['kind'] == 'journal_prepare' for row in rows))
        self.assertEqual(rows[-1]['kind'], 'session_end')
        self.assertEqual(rows[-1]['data']['worker_status'], 'settled')
        for fixture in result['fixtures']:
            self.assertIn(fixture['room_commit_ref']['commit_id'], accepted)
            self.assertEqual(fixture['task']['reason'], 'spotlight_semantic_CCT_mapping_unavailable')
            self.assertFalse(result['physical_permission'])
            self.assertFalse(result['native_achieved_truth'])
        self.assertEqual(len(owner.graph.source_barrier_acks), 4)
        self.assertTrue(owner.graph.finished)
        self.assertFalse(holder['native_recorder'].ready)

    @staticmethod
    def _stop_and_wait(supervisor, reason):
        """Await a concurrent actual Stop; never upgrade its original result."""
        receipt = supervisor.stop(reason)
        deadline = time.monotonic() + 15
        while receipt.get('status') in ('starting', 'running', 'stopping') and time.monotonic() < deadline:
            time.sleep(.02)
            receipt = supervisor.status()
        return receipt

    def _defer_unsafe_fixture_cleanup(self, owner, supervisor, holder):
        # Only this authored fixture's cleanup is deferred. An outer Job kill
        # is still a failed/incomplete gate and cannot become a native ACK.
        if _DEFERRED_AUTHORED_OWNERS:
            raise ValueError('one_guarded_authored_fixture_owner_only')
        directory = owner.f.directory
        self.assertIs(type(directory), tempfile.TemporaryDirectory)
        directory._finalizer.detach()
        self._cleanups.clear()
        holder['deferred_cleanup'] = dict(status='deferred_unsafe',
            reason='actual_Stop_not_confirmed_safe_and_stopped',
            original_directory=str(directory.name), outer_Job_cleanup_required=True)
        _DEFERRED_AUTHORED_OWNERS.append(dict(test=self, owner=owner,
            supervisor=supervisor, holder=holder))

    def _retain_fixture_evidence(self, owner, supervisor, holder, archive_path):
        """Bounded diagnostics, not a complete replay or household receipt.

        Only these five authored JSONL originals are eligible; no recursive
        Temp copy, SQLite database, bearer token, media or private config.
        A failed/partial copy is retained and never labeled complete.
        """
        parent = ROOT / 'artifacts/den-command-session-lifecycle-20261017/fixture'
        parent.mkdir(parents=True, exist_ok=True)
        directory = parent / ('run-' + uuid.uuid4().hex)
        directory.mkdir()
        maximum_files, maximum_bytes = 6, 128 * 1024**2
        originals = {
            'worker-originals.jsonl': archive_path,
            'canonical-authority-originals.jsonl': owner.path / 'command-authority-originals.jsonl',
            'canonical-native-originals.jsonl': owner.path / 'command-native-originals.jsonl',
            'lab-originals.jsonl': Path(owner.producer['original_archive']['filename']),
            'authored-presence-originals.jsonl': owner.path / 'authored-presence-originals.jsonl',
        }
        copied, missing, failures, total = {}, [], [], 0
        for name, source in originals.items():
            if not source.exists():
                missing.append(name)
                continue
            try:
                if source.is_symlink() or source.parent.resolve() != owner.path.resolve():
                    raise ValueError('fixed_authored_archive_path_required')
                size = source.stat().st_size
                if len(copied) >= maximum_files - 1 or size > 64 * 1024**2 or total + size > maximum_bytes - 131072:
                    raise ValueError('authored_diagnostic_copy_bound')
                fingerprint, count = hashlib.sha256(), 0
                with source.open('rb') as original, (directory / name).open('xb') as retained:
                    while True:
                        chunk = original.read(65536)
                        if not chunk: break
                        count += len(chunk)
                        if count > size: raise ValueError('authored_original_grew_during_copy')
                        total += len(chunk)  # Includes a failed partial copy.
                        self.assertEqual(retained.write(chunk), len(chunk))
                        fingerprint.update(chunk)
                    if count != size: raise ValueError('authored_original_changed_during_copy')
                    retained.flush(); os.fsync(retained.fileno())
                copied[name] = dict(sha256=fingerprint.hexdigest(), size=count)
            except Exception as error:
                failures.append(dict(file=name, type=type(error).__name__, message=str(error)[:512]))
        report = dict(schema='home-authored-command-session-diagnostics-1', version=1,
            status='retained' if not failures and not holder.get('deferred_cleanup') else 'incomplete',
            source_test_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            supervisor=supervisor.status(), source_error=holder.get('source_error'),
            source_stage=holder.get('source_stage'), worker_commit_errors=holder['worker_commit_errors'],
            final_stop_receipt=holder.get('final_stop_receipt'),
            final_stop_error=holder.get('final_stop_error'), deferred_cleanup=holder.get('deferred_cleanup'),
            files=copied, missing=missing,
            failures=failures, bytes=total, maximum_files=maximum_files, maximum_bytes=maximum_bytes,
            classification='authored_fixture_diagnostics_only',
            combined_replay_proven=False, household_qualification=False, physical_permission=False)
        raw = (canonical(report) + '\n').encode()
        if len(raw) > 131072: raise ValueError('authored_diagnostic_report_bound')
        with (directory / 'diagnostics.json').open('xb') as stream:
            self.assertEqual(stream.write(raw), len(raw)); stream.flush(); os.fsync(stream.fileno())
        print('AUTHORED_COMMAND_SESSION_DIAGNOSTICS=' + str(directory / 'diagnostics.json'), flush=True)


if __name__ == '__main__': unittest.main()

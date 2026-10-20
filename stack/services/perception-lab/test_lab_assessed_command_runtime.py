"""Producer8 source-only proof with actual bundled factory and config13 Journal.

Camera/model/qualification inputs are authored. The positive case attaches the
real bounded Recorder but starts no worker or canonical command owner.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest

from controlled_external_io import assemble_journal_grants
from journal import digest
import lab_live_runtime as runtime
from lab_live_semantic_admission import LabLiveSemanticAdmission, SemanticRefusal
from paired_preview_client import encoded, sha
from room_subset_configuration import upgrade_configuration
from schema_registry import schema_digest
import test_lab_stereo_mount as mounted
import test_room_subset_worker as subset_fixture


def assessed_bundled_fixture(case):
    """One actual config4 Store/runtime, upgraded through the existing builder.

    This deliberately shares the unchanged source7 fixture recipe; it does not
    alter its methods, clocks, cadence, originals or qualifications. Consumers
    may reuse the returned inputs/worker/config through their actual new ports.
    """
    f = mounted.PollBudgetStereoMountTests()
    f.setUp()
    case.addCleanup(f.doCleanups)
    f.predecessor_producer = deepcopy(f.source.producer)
    f.predecessor_worker = deepcopy(f.inputs['worker_config'])
    producer = deepcopy(f.source.producer)
    room = deepcopy(f.inputs['worker_config']['room_policy'])
    # The source7 fixture deliberately has zero task slots. Use its existing
    # complete, independently authored policy4 for the one assessed slot, just
    # as authored_pc_base does; never wrap a null task as an assessment.
    task = deepcopy(f.inputs['worker_config']['delivery_policy'])
    task['fixture'].update(id=room['fixtures'][0]['fixture_id'],
                           fixture_pose_ref=deepcopy(room['fixtures'][0]['pose_ref']))
    room['fixtures'][0]['task_policy'] = task
    policy = subset_fixture.subset_policy(room)
    configuration, worker = upgrade_configuration(producer['live_config'], f.inputs['worker_config'],
        policy, allow_assessed_subset=True)
    producer.update(schema='home-incumbent-lab-producer-8', version=8, live_config=configuration)
    producer['semantic_policy'].update(schema='home-lab-semantic-admission-policy-4', version=4)
    producer['journal_grants'] = assemble_journal_grants(configuration, worker,
        publication_token=mounted.TOKEN, worker_token='authored-stereo-worker-token',
        human_token='authored-stereo-owner')
    for grant in producer['journal_grants']:
        grant['revisions'] = list(dict.fromkeys([*grant['revisions'], 'synthetic-r1', worker['revision']]))
    f.source.producer, f.source.config = producer, configuration
    join, _ = runtime._join_and_configuration(producer, f.source.f.session)
    f.source.f.lab_binding_digest = join.binding_digest
    f.inputs = mounted.package_fixture.LabStereoSessionPackageTests.inputs.__func__(
        SimpleNamespace(source=f.source))
    f.inputs['worker_config'] = worker
    f.inputs['runtime']['node_runtime'] = shutil.which('node')
    f.inputs['runtime'].pop('max_cycles')
    f.inputs['runtime'].update(max_poll_attempts=40, max_capture_originals=4)
    f.inputs.update(schema='home-den-lab-session-inputs-7', version=7)
    original = f.inputs['stereo']
    original.update(schema='home-den-stereo-package-inputs-3', version=3)
    f.qualified = {key: deepcopy(original[key]) for key in
        ('stereo_policy', 'depth_clock_policy', 'authority_binding', 'association_originals')}
    f.authority = mounted.AuthoredQualificationAuthority(f.source, f.qualified['authority_binding'])
    f.config.update(schema='home-lab-stereo-source-config-5', version=5,
        commissioning_digest=sha(encoded(f.source.commissioning)), qualification_digest=sha(encoded(f.qualified)),
        qualification_authority_ref=dict(id=f.qualified['authority_binding']['association_method_ref']['id'],
            digest=sha(encoded(f.qualified['authority_binding']))))
    paths = f.inputs['provisioning']
    f.records = {paths['commissioning_file']: f.source.commissioning,
                 paths['qualification_file']: f.qualified}
    return f


class AssessedBundledRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One fixed fixture for this finite class: eleven preparation Node
        # calls plus the positive Recorder description remain below32 total.
        cls.owner = SimpleNamespace(addCleanup=cls.addClassCleanup)
        cls.fixture = assessed_bundled_fixture(cls.owner)

    def test_actual_source8_config13_retains_before_admission_and_stops_without_worker_claim(self):
        f, source = self.fixture, self.fixture.source
        composition = f.compose()
        composition.monotonic = lambda: source.f.now - mounted.NOW
        source.composition = composition
        limits = f.inputs['runtime']
        session = dict(schema='home-live-session-config-3', version=3,
            session_ref=deepcopy(composition.scope), configuration_digest=composition.configuration_digest,
            mode='live_shadow_unqualified', **{key: limits[key] for key in
                ('max_runtime_s', 'max_poll_attempts', 'max_capture_originals', 'interval_s', 'drain_timeout_s')})
        config = dict(schema='home-live-runtime-config-3', version=3, session=session,
            worker_config=deepcopy(f.inputs['worker_config']), **{key: limits[key] for key in
                ('node_runtime', 'archive_file', 'archive_limits', 'source_bundle_digest')})
        supervisor = runtime.compose_lab_supervisor(config, composition,
            supervisor_preflight=lambda _: {'status': 'ready'}, command_factory=None)
        graph = supervisor.graph_factory()
        source.graph = graph
        recorder = supervisor.recorder_factory(graph)
        graph.journal.attach_recorder(recorder.record)
        supervisor.graph, supervisor.recorder = graph, recorder
        self.addCleanup(supervisor.stop)
        self.assertIs(graph.store, source.f.store)
        self.assertIs(composition.bundled_source.runtime, source.f.runtime)
        self.assertEqual(graph.config['version'], 8)
        self.assertEqual(graph.semantic.policy['version'], 4)
        self.assertEqual(graph.journal.dynamic.config['version'], 13)
        self.assertEqual(graph.journal.dynamic.worker.worker_version, 'home-live-context-worker-12.0.0')
        self.assertEqual(graph.desk.policy['version'], 4)
        self.assertIsNone(supervisor.command_owner)
        self.assertEqual(recorder.status()['status'], 'ready')
        result = graph.step()
        self.assertEqual(result['status'], 'admitted_desk', result)
        self.assertEqual(graph.status()['counters']['capture_originals'], 1)
        self.assertEqual(graph.status()['counters']['poll_attempts'], 1)
        self.assertEqual(len(composition.desk_capability.media_archive.media_refs()), 8)
        original = next(iter(graph.join.originals.values()))
        self.assertEqual((original['schema'], original['version']), ('home-original-lab-observation-2', 2))
        self.assertEqual(original['clock_original'], original['bundled_originals']['clock_original'])
        retained = [json.loads(raw) for _, raw in composition.originals.records.values()]
        self.assertIn(original, retained)
        # Resolve only a genuinely retained, currently admitted original.
        ref = next(iter(graph.join.records.values()))['ref']
        self.assertEqual(graph._resolve_original(ref['store_id'], ref['receipt_id']),
                         graph.join.resolve_receipt(ref['store_id'], ref['receipt_id']))
        with self.assertRaises(KeyError): graph._resolve_original('foreign-store', ref['receipt_id'])
        self.assertIsNone(supervisor.worker)
        self.assertIsNone(supervisor.thread)
        stopped = supervisor.stop(reason='authored_source8_source_only_stop')
        self.assertEqual(stopped['status'], 'stopped', stopped)
        self.assertEqual(stopped['recording']['status'], 'drained')
        self.assertFalse(stopped['acceptance_eligible'])
        self.assertFalse(stopped['physical_authority'])
        rows = [json.loads(line)['record'] for line in Path(limits['archive_file']).read_bytes().splitlines()]
        self.assertFalse([row for row in rows if row['kind'].startswith('worker_')])
        self.assertEqual(rows[-1]['kind'], 'session_end')

    def test_crossed_producer_and_semantic_profiles_refuse_before_any_owned_files(self):
        f = self.fixture
        for name, edit in (
            ('old_producer', lambda p: p.update(schema='home-incumbent-lab-producer-7', version=7)),
            ('old_semantics', lambda p: p['semantic_policy'].update(schema='home-lab-semantic-admission-policy-3', version=3)),
            ('old_config', lambda p: p.update(live_config=deepcopy(f.predecessor_producer['live_config']))),
        ):
            p = deepcopy(f.source.producer); edit(p)
            p['journal_path'] = str(f.source.path/(name+'.sqlite'))
            p['original_archive']['filename'] = str(f.source.path/(name+'.jsonl'))
            with self.subTest(name=name), self.assertRaises(ValueError):
                runtime.lab_graph_configuration(p, session=f.source.f.session)
            self.assertFalse(Path(p['journal_path']).exists())
            self.assertFalse(Path(p['original_archive']['filename']).exists())
        # The predecessor remains the exact source7/config12 acquisition dialect.
        config = runtime.lab_graph_configuration(f.predecessor_producer, session=f.source.f.session)
        self.assertEqual(config['version'], 7)

    def test_predecessor_private_grant_refuses_before_archive_or_journal_creation(self):
        f = self.fixture
        p = deepcopy(f.source.producer)
        g = next(row for row in p['journal_grants'] if 'worker_commit' in row['operations'])
        old = 'home-live-worker-checkpoint-11'
        g['payload_types'].append(old)
        g['schema_pins'][old] = schema_digest(old, profile='home-live-shadow-1')
        p['journal_path'] = str(f.source.path/'predecessor-grant.sqlite')
        p['original_archive']['filename'] = str(f.source.path/'predecessor-grant.jsonl')
        with self.assertRaisesRegex(ValueError, 'predecessor'):
            runtime.lab_graph_configuration(p, session=f.source.f.session)
        self.assertFalse(Path(p['journal_path']).exists())
        self.assertFalse(Path(p['original_archive']['filename']).exists())

    def test_semantic4_uses_same_real_visual_original_and_rejects_changed_frame(self):
        f = self.fixture
        semantic = LabLiveSemanticAdmission(f.source.f.session, live_config=f.source.producer['live_config'],
            policy=f.source.producer['semantic_policy'], revision='synthetic-r1', original_retainer=lambda _: None)
        prior = f.predecessor_producer
        historical = LabLiveSemanticAdmission(f.source.f.session, live_config=prior['live_config'],
            policy=prior['semantic_policy'], revision='synthetic-r1', original_retainer=lambda _: None)
        original = f.source.f.store.list_events(f.source.f.session['id'])
        row = next(row for row in original if row['id'] == f.source.f.row['id'])
        self.assertEqual(semantic._roots(row), historical._roots(row))
        changed = deepcopy(row); changed['payload']['result']['frame_id'] = 'foreign-frame'
        with self.assertRaisesRegex(SemanticRefusal, 'visual_frame'):
            semantic._roots(changed)


if __name__ == '__main__': unittest.main()

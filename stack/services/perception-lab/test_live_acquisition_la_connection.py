"""Real factory/ring/perceiver/pump/receipt join, with fake SDK/LA transport."""
from copy import deepcopy
import unittest

from frame_ring import FrameRing
from la_live_perceiver import LaLivePerceiver, check_config
import test_live_interaction_optional as optional_tests
from test_qwen_live import PROFILE, FakeTransport, completed
from qwen_transport import CompletionUnproven
import test_live_interaction_sources as source_tests


class AcquisitionLaConnectionTests(unittest.TestCase):
    setUp = optional_tests.OptionalReceiptIntegrationTests.setUp
    control_clock = optional_tests.OptionalReceiptIntegrationTests.control_clock
    camera_pair = optional_tests.OptionalReceiptIntegrationTests.camera_pair
    snapshot = optional_tests.OptionalReceiptIntegrationTests.snapshot
    advance = optional_tests.OptionalReceiptIntegrationTests.advance
    construct = optional_tests.OptionalReceiptIntegrationTests.construct
    facts = optional_tests.OptionalReceiptIntegrationTests.facts
    original = optional_tests.OptionalReceiptIntegrationTests.original

    def test_managed_composition_binds_only_the_incumbent_exact_model_capability(self):
        from types import SimpleNamespace
        from live_supervised_composition import ManagedProducerComposition, PreparationUnavailable
        self.prepare(completed(source_tests.FACTS))
        owner=SimpleNamespace(la_perceiver=self.perceiver,provision={'interaction_policy':deepcopy(self.interaction_policy),
            'la_binding':{'schema':'home-live-existing-la-binding-1','version':1,'model_ref':deepcopy(self.la_model),
                          'artifact_sha256':self.la_artifact}},clock=lambda:self.now)
        sources=ManagedProducerComposition._existing_la_sources(owner)
        self.addCleanup(sources.clear)
        self.assertIs(sources.perceiver,self.perceiver)
        self.assertIs(sources.fact_log,self.perceiver.fact_log)
        self.assertNotEqual(sources.model_ref['digest'],sources.artifact)
        self.assertEqual(owner.optional_la_status,'bound_existing_perceiver')
        owner.provision['interaction_policy']['semantic_model_refs']=[]
        self.assertIsNone(ManagedProducerComposition._existing_la_sources(owner))
        self.assertEqual(owner.optional_la_status,'unavailable_model_pin')
        owner.la_perceiver=object()
        with self.assertRaises(PreparationUnavailable):
            ManagedProducerComposition._existing_la_sources(owner)

    def prepare(self, *outcomes):
        optional_tests.OptionalReceiptIntegrationTests.prepare(self)
        self.changed_pixels = True
        # Independently varied external bytes are required by the incumbent
        # ring's image-deduplication rule, not synthesized by the adapter.
        base_camera = self.camera['pair_transport']
        def changing_camera(token, *, timeout):
            import base64, hashlib, io, json
            from PIL import Image
            from live_capture_fixtures import decoded_header
            pair = json.loads(base_camera(token, timeout=timeout))
            output = io.BytesIO()
            Image.new('RGB', (640, 360), (self.sequence * 10, 100, 60)).save(output, format='JPEG', quality=85)
            jpeg = output.getvalue()
            receipt = decoded_header(pair['header'])
            receipt['jpeg_sha256'] = hashlib.sha256(jpeg).hexdigest()
            pair.update(jpeg_b64=base64.b64encode(jpeg).decode(),
                        header=base64.urlsafe_b64encode(json.dumps(receipt).encode()).decode())
            return json.dumps(pair).encode()
        self.camera['pair_transport'] = changing_camera
        self.transport = FakeTransport(*outcomes)
        settings = check_config(dict(enabled=True, transport_file='/authored/transport.json',
            artifact_sha256=self.la_artifact, runtime_revision='authored-runtime',
            fact_log_directory='/authored/log', interval_seconds=.5, frames=2, span_seconds=1))
        self.ring = FrameRing(clock=lambda: self.now)
        self.perceiver = LaLivePerceiver(transport=self.transport, profile=deepcopy(PROFILE),
            reader=self.occupancy, fact_log=self.log, granted=lambda: self.la_granted,
            fetch=lambda: self.fail('no independent camera turn authorized'), settings=settings,
            site_id=self.config['session_ref']['site_id'], camera_id=self.policy['camera_id'],
            clock=lambda: self.now, monotonic=lambda: self.mono, local_time=lambda _: (12, 0),
            ring=self.ring, turns=self.turns)
        self.perceiver.state = 'running'
        # Identity-only probe remains the incumbent transport's operation.
        self.perceiver.pump.tick()
        self.la_identity = dict(engine_epoch='la-epoch', artifact_sha256=self.la_artifact,
                                session_id=self.perceiver.pump.session['id'])
        self.sources.bind_perceiver(self.perceiver)
        def finish():
            self.perceiver.pump.stop(0)
            self.perceiver.ring.clear()
        self.addCleanup(finish)

    def test_exact_acquisition_frames_reach_actual_pump_and_refine_current_capture(self):
        self.prepare(completed(source_tests.FACTS))
        graph = self.construct(interaction_sources=self.sources)
        self.assertEqual(graph.step()['interaction'], 'completed')
        self.assertEqual(len(self.ring.frames), 1)
        self.advance()
        sdk = self.pose_worker.model
        tick = []
        def finish_during_pose(image, bboxes):
            result = sdk(image, bboxes)
            try:
                tick.append(self.perceiver.pump.tick())
            except Exception as error:
                tick.append(repr(error))
                raise
            return result
        self.pose_worker.model = finish_during_pose
        cycle = graph.step()
        self.assertEqual(cycle['interaction'], 'completed', (cycle, tick))
        self.assertEqual(len(self.transport.envelopes), 1, tick)
        self.assertEqual(self.facts()['activity_refinement']['value'], 'reading', self.facts())
        proposal = self.original(self.facts()['object_interaction'])['supplemental']['la']
        self.assertEqual(proposal['status'], 'fresh_proposal')
        self.assertEqual(proposal['person_association'], 'qualified')
        self.assertEqual(proposal['frame_bindings'][-1]['capture_id'], graph.capture.last_ref['id'])
        self.assertEqual(proposal['observed_at'], self.facts()['activity_refinement']['observed_at'])
        self.assertEqual(proposal['sensor_roots'], ['camera:synthetic-oak'])

    def test_missing_or_late_existing_la_completion_does_not_gate_local_stream(self):
        self.prepare(CompletionUnproven('authored completion delayed'))
        graph = self.construct(interaction_sources=self.sources)
        self.assertEqual(graph.step()['interaction'], 'completed')
        self.advance()
        sdk = self.pose_worker.model
        tick = []
        def pending_during_pose(image, bboxes):
            result = sdk(image, bboxes)
            try:
                tick.append(self.perceiver.pump.tick())
            except Exception as error:
                tick.append(repr(error))
                raise
            return result
        self.pose_worker.model = pending_during_pose
        cycle = graph.step()
        self.assertEqual(cycle['interaction'], 'completed', (cycle, tick))
        self.assertIsNotNone(self.perceiver.pump.outstanding)
        self.assertTrue(self.facts()['object_interaction']['value'])
        self.assertIsNone(self.facts()['activity_refinement']['value'])


if __name__ == '__main__':
    unittest.main()

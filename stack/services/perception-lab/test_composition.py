from pathlib import Path
import tempfile
import unittest
import hashlib
import time
from unittest.mock import patch
from composition import compose_runtime
from config import Settings
from recorder import StreamSource
from store import Store
from artifacts import ArtifactRegistry, ArtifactPreparation
from controller import Controller


class CompositionFactoryTests(unittest.TestCase):
    def test_encoded_only_start_does_not_create_auxiliary_camera_readers(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(Settings(Path(root),min_free_bytes=0,cameras=({'id':'den'},)))
            source=StreamSource('oak','den','rtsp://127.0.0.1:8554/fixture',enabled=True,admitted=True,
                codec='avc1.640028',codec_validation_id='fixture')
            runtime=compose_runtime(store,sources=[source],recording_only=True,auxiliary_capture=False)
            try:
                controller=Controller(store,runtime)
                with patch.object(runtime.recorders['den'],'start',return_value={'state':'recording'}),patch('preview_client.PreviewPump',side_effect=AssertionError('unexpected camera pump')):
                    session=controller.start({'camera_id':'den','experiment_id':'detection','name':'Encoded only','request_id':'encoded'})
                self.assertEqual(session['state'],'recording')
                for group in (runtime.preview_sources,runtime.bundle_sources,runtime.preview_clients,
                              runtime.bundle_clients,runtime.preview_pumps,runtime.bundle_pumps,runtime.runners):
                    self.assertEqual(group,{})
                with patch.object(runtime.recorders['den'],'stop',return_value={'state':'stopped'}):
                    self.assertEqual(controller.stop(session['id'])['state'],'stopped')
            finally:runtime.close();store.close()

    def test_recording_only_commissioning_records_without_model_bindings(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(Settings(Path(root),min_free_bytes=0,cameras=({"id":"den"},)))
            source=StreamSource("oak","den","rtsp://127.0.0.1:8554/fixture",enabled=True,admitted=True,
                codec="avc1.640028",codec_validation_id="fixture")
            runtime=None
            def forbidden(*args,**kwargs):raise AssertionError('unexpected model or network operation')
            try:
                runtime=compose_runtime(store,sources=[source],camera_credential=forbidden,
                    receiver_clock={"validation_id":"fixture","uncertainty_seconds":.1},recording_only=True)
                controller=Controller(store,runtime)
                capabilities=controller.capabilities()
                self.assertTrue(capabilities['recording_allowed'])
                self.assertEqual(capabilities['gates'][0]['reason'],'recording_only_commissioning')
                # Exercise controller lifecycle using a recorder double. Disable
                # fetches at the pump boundary; no camera or subprocess is used.
                from preview_client import PreviewPump
                with patch.object(runtime.recorders['den'],'start',return_value={'state':'recording'}),patch.object(PreviewPump,'_run',return_value=None):
                    session=controller.start({'camera_id':'den','experiment_id':capabilities['experiments'][0]['id'],
                        'name':'Capture commissioning','request_id':'capture-only'})
                self.assertEqual(session['state'],'recording')
                self.assertEqual(session['reason'],'recording_only_commissioning')
                self.assertEqual(runtime.runners,{})
                self.assertIsNone(runtime.reasoning_thread)
                self.assertEqual(runtime.live(session)['reason'],'recording_only_commissioning')
                media=store.add_media(session['id'],b'synthetic recording','video/mp4',{'fixture':True})
                self.assertEqual(runtime.run_job({'kind':'analysis'})['state'],'blocked')
                self.assertEqual(runtime.run_job({'kind':'preparation'})['state'],'blocked')
                with patch.object(runtime.recorders['den'],'stop',return_value={'state':'stopped'}):
                    self.assertEqual(controller.stop(session['id'])['state'],'stopped')
                self.assertEqual(store.list_media(session['id'])['items'][0]['id'],media['id'])
                self.assertTrue(runtime.close()['drained'])
                for binding in ('resolve_profiles','specialist_executor','jev_provider','qwen_transport','local_workers'):
                    with self.subTest(binding=binding),self.assertRaisesRegex(ValueError,'cannot bind'):
                        compose_runtime(store,sources=[source],camera_credential=forbidden,
                            receiver_clock={},recording_only=True,**{binding:forbidden})
            finally:
                if runtime:runtime.close()
                store.close()

    def test_composed_preparation_reaches_registry_without_capture_or_inference(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(Settings(Path(root),min_free_bytes=0,cameras=({"id":"den"},)))
            cache=Path(root)/"cache";cache.mkdir()
            content=b"not a model";(cache/"fixture.bin").write_bytes(content)
            registry=ArtifactRegistry(store)
            registry.register({"id":"fixture","checkpoint":"fixture","version":"1","license":"fixture",
                "runtime":"none","precision":"none","device_id":"test-device","sha256":hashlib.sha256(content).hexdigest()})
            prep=ArtifactPreparation(registry,cache,{"fixture":"fixture.bin"},device_id="test-device",quota_bytes=1024)
            source=StreamSource("oak","den","rtsp://127.0.0.1:8554/fixture",enabled=True,admitted=True,
                codec="avc1.640028",codec_validation_id="fixture")
            def forbidden(*a,**k):self.fail("preparation contacted camera or inference")
            runtime=None
            try:
                with patch("http.client.HTTPConnection",forbidden),patch("subprocess.Popen",forbidden):
                    runtime=compose_runtime(store,sources=[source],camera_credential=forbidden,
                        receiver_clock={"validation_id":"fixture","uncertainty_seconds":.1},
                        resolve_profiles=forbidden,specialist_executor=forbidden,artifact_preparation=prep)
                    self.assertEqual(registry.get("fixture")["on_disk"],"unverified")
                    controller=Controller(store,runtime)
                    job=controller.job({"kind":"preparation","request_id":"prepare-fixture",
                        "parameters":{"artifact_ids":["fixture"],"keep_ready":False}})
                    deadline=time.monotonic()+2
                    while store.get_job(job["id"])["state"] in {"requested","queued","running"} and time.monotonic()<deadline:
                        time.sleep(.005)
                    final=store.get_job(job["id"])
                    self.assertEqual(final["state"],"completed")
                    self.assertEqual(final["result"]["phase"],"stored_only")
                    self.assertFalse(registry.get("fixture")["loaded"])
                    self.assertFalse(registry.get("fixture")["runtime_validated"])
                    self.assertEqual(runtime.runners,{})
                    self.assertTrue(runtime.close()["drained"])
                    self.assertEqual(runtime.run_job(job)["reason"],"runtime_shutting_down")
            finally:
                if runtime:runtime.close()
                store.close()

    def test_construction_is_inert_and_uses_single_ledger(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(Settings(Path(root),min_free_bytes=0,cameras=({"id":"den"},)))
            source=StreamSource("oak","den","rtsp://127.0.0.1:8554/fixture",enabled=True,admitted=True,
                codec="avc1.640028",codec_validation_id="fixture")
            def forbidden(*a,**k): self.fail("construction contacted a service")
            try:
                with patch("http.client.HTTPConnection",forbidden),patch("subprocess.Popen",forbidden):
                    runtime=compose_runtime(store,sources=[source],camera_credential=forbidden,
                        receiver_clock={"validation_id":"fixture","uncertainty_seconds":.1},
                        resolve_profiles=forbidden,specialist_executor=forbidden)
                self.assertIs(runtime.store,store)
                self.assertIs(runtime.recorders["den"].store,store)
                self.assertIs(runtime.preview_sources["den"].store,store)
                self.assertIs(runtime.bundle_sources["den"].store,store)
                self.assertEqual(runtime.preview_pumps,{})
                self.assertIsNone(runtime.reasoning_thread)
                self.assertTrue(runtime.close()["drained"])
                with self.assertRaises(ValueError):
                    compose_runtime(store,sources=[source,source],camera_credential=forbidden,
                        receiver_clock={},resolve_profiles=forbidden,specialist_executor=forbidden)
            finally:
                store.close()

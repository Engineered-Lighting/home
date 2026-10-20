import base64
import hashlib
import io
import json
import unittest
import test_service_runtime as fixtures
from preview_ingest import PreviewIngestor
from analysis_inputs import RegisteredAnalysisInputs


class PreviewTests(unittest.TestCase):
    setUp=fixtures.CompositionTests.setUp
    tearDown=fixtures.CompositionTests.tearDown
    runtime=fixtures.CompositionTests.runtime
    receipt=fixtures.CompositionTests.receipt
    settle=fixtures.CompositionTests.settle

    def source(self):
        self.now=101
        self.store.clock=lambda:self.now
        return PreviewIngestor(self.store,site_id="victoria",camera_id="den",
            clock_validation_id="fixture-receiver",clock_uncertainty_s=.05)

    def sample(self,**changes):
        from PIL import Image
        data=io.BytesIO()
        Image.new("RGB",(640,360),(100,20,5)).save(data,format="JPEG")
        meta={"schema":2,"session":"camera1","sequence":4,"timing_generation":1,
            "width":640,"height":360,"source_clock":"depthai_host_monotonic",
            "source_timestamp_seconds":12,"observed_at_unix_ns":100500000000,
            "uncertainty_seconds":.1,"exposure_duration_seconds":.02,"validation_id":"fixture-camera",
            "timestamp_semantics":"commissioned_source_frame_timestamp_mapping",**changes}
        return data.getvalue(),base64.urlsafe_b64encode(json.dumps(meta).encode()).decode().rstrip("=")

    def test_preview_registers_once_and_duplicate_does_not_refresh_time(self):
        source=self.source()
        data,header=self.sample()
        first=source.ingest(self.session["id"],data,header)
        self.now=102
        second=source.ingest(self.session["id"],data,header)
        self.assertEqual(first,second)
        self.assertEqual(first["session_time_s"],.5)
        self.assertEqual(first["available_at"],101)
        self.assertEqual(first["valid_until"],103.35)
        frames,_=RegisteredAnalysisInputs(self.store)({"session_id":self.session["id"]})
        self.assertEqual(frames[0]["media_id"],first["media_ids"][0])

    def test_bad_clock_and_source_dimensions_are_rejected(self):
        source=self.source()
        for change in ({"schema":1},{"width":1920},{"observed_at_unix_ns":102000000000},
                {"observed_at_unix_ns":99000000000},{"uncertainty_seconds":1},
                {"exposure_duration_seconds":.5}):
            data,header=self.sample(**change)
            with self.assertRaises(ValueError):
                source.ingest(self.session["id"],data,header)

    def causal_sample(self,**changes):
        data,_=self.sample()
        meta=dict(schema=3,session="camera1",sequence=4,timing_generation=1,
            width=640,height=360,quality=85,observed_monotonic_seconds=5.1,
            observed_at_unix_ns=100500000000,uncertainty_seconds=.1+.01+.000001+.2,
            validation_id="causal:"+"a"*32,exposure_duration_seconds=.01,
            source_clock="depthai_device_monotonic",monotonic_clock_scope="python_monotonic_on_camera_host",
            timestamp_semantics="causal_exact_frame_exposure_end",challenge_nonce="a"*32,
            challenge_device_ns=100000,frame_device_ns=200000,
            host_send_monotonic_ns=5000000000,host_receive_monotonic_ns=5200000000,
            timestamp_quantization_ns=1000,preview_sha256="b"*64,
            jpeg_sha256=hashlib.sha256(data).hexdigest(),host_boot_id="12345678-1234-1234-1234-123456789abc",
            host_clock_validation_id="host-clock",host_clock_uncertainty_seconds=.2)
        meta.update(changes)
        return data,base64.urlsafe_b64encode(json.dumps(meta).encode()).decode()

    def test_causal_preview_retains_original_provenance(self):
        result=self.source().ingest(self.session["id"],*self.causal_sample())
        self.assertEqual(result["source_timing_receipt"]["schema"],3)
        self.assertEqual(result["timestamp_semantics"],"causal_exact_frame_exposure_end")
        self.assertAlmostEqual(result["uncertainty_seconds"],.310001)
        self.assertEqual(result["receiver_clock_validation_id"],"host-clock")
        self.assertEqual(result["source_timestamp_seconds"],.0002)

    def test_causal_substitution_and_invalid_brackets_rejected(self):
        source=self.source()
        for change in ({"jpeg_sha256":"0"*64},{"frame_device_ns":100000},
                       {"host_receive_monotonic_ns":4999999999},{"uncertainty_seconds":.1}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                source.ingest(self.session["id"],*self.causal_sample(**change))

    def test_source_to_archive_and_both_specialists_without_new_camera_owner(self):
        source=self.source()
        runtime,_=self.runtime(lambda *_:{"state":"ready","profiles":self.profiles})
        runtime.preview_sources={"den":source}
        runtime.start_session(self.session)
        try:
            result=runtime.ingest_preview(self.session["id"],*self.sample())
            self.assertEqual(result["inference"]["state"],"offered")
            def outputs():
                return [e for e in self.store.list_events(self.session["id"]) if e["kind"]=="perception.output"]
            self.settle(lambda:len(outputs())==2)
            self.assertEqual({e["payload"]["arm"] for e in outputs()},{"a","b"})
            self.assertTrue(all(e["payload"]["media_ids"]==result["media_ids"] for e in outputs()))
        finally:
            self.assertTrue(runtime.close(timeout=2)["drained"])

    def test_original_nanosecond_clock_survives_preview_and_actual_lab_output(self):
        # Generated real-resolution UTC receipt: converting the integer through
        # binary float before scaling rounds this to 1792173344.0 instead of
        # the journal's correctly rounded 1792173344.0000002.
        from pathlib import Path
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'home-context'))
        from live_ingress_validation import validate_clock

        source = self.source()
        stamp = 1792173344000000120
        observed = 1792173344.0000002
        self.store.transition(self.session['id'], 'stopped')
        self.now = observed - 1
        self.session, _ = self.store.create_session('den', 'detection',
            'Generated original clock', 'nanosecond-preview')
        self.store.transition(self.session['id'], 'starting')
        self.store.transition(self.session['id'], 'recording')
        self.session = self.store.get_session(self.session['id'])
        self.now = observed + .5
        runtime, _ = self.runtime(lambda *_: {"state": "ready", "profiles": self.profiles})
        runtime.preview_sources = {"den": source}
        # Only model I/O is authored. The real preview registration, paired
        # runner and stored perception.output envelopes carry the original time.
        runtime.executor = lambda request: self.receipt(request, result={
            'schema_version': 1, 'coordinate_space': 'source_pixels',
            'frame_id': request['frame']['frame_id'], 'objects': [], 'boxes': [],
            'pose_instances': []})
        runtime.start_session(self.session)
        try:
            admitted = runtime.ingest_preview(self.session['id'],
                *self.causal_sample(observed_at_unix_ns=stamp))
            media = self.store.get_media(self.session['id'], admitted['media_ids'][0])
            captured = {**media['metadata'], 'session_id': self.session['id'],
                'media_ids': admitted['media_ids']}
            def outputs():
                return [row for row in self.store.list_events(self.session['id'])
                        if row['kind'] == 'perception.output']
            self.settle(lambda: len(outputs()) == 2)
            original = captured['source_timing_receipt']
            self.assertEqual(original['observed_at_unix_ns'], stamp)
            policy = {'mapping_id': original['validation_id'], 'mapping_digest': 'a'*64,
                'source_epoch': original['session'], 'original_unit': 'unix_ns',
                'epoch_origin_s': 0, 'max_uncertainty_s': 1}
            clock = {'status': 'commissioned',
                **{key: policy[key] for key in ('mapping_id', 'mapping_digest', 'source_epoch', 'original_unit')},
                'original_value': str(stamp), 'uncertainty_s': captured['uncertainty_seconds']}
            for row in outputs():
                payload = row['payload']
                self.assertEqual(payload['frame_id'], captured['frame_id'])
                self.assertEqual(payload['media_ids'], captured['media_ids'])
                validate_clock(clock, policy, payload['observed_at'])
                self.assertEqual(payload['observed_at'], observed)
            self.assertAlmostEqual(captured['uncertainty_seconds'], .310001)
            self.assertEqual(captured['valid_until'], observed + 3 - captured['uncertainty_seconds'])
            self.now += .5
            retry = source.ingest(self.session['id'],
                *self.causal_sample(observed_at_unix_ns=stamp))
            self.assertEqual(retry, captured)
        finally:
            self.assertTrue(runtime.close(timeout=2)['drained'])


if __name__=='__main__':
    unittest.main()

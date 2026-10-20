import io
import json
import unittest
import test_paired_runtime as fixtures
from bundle_ingest import BundleIngestor
from analysis_inputs import RegisteredAnalysisInputs
import test_service_runtime as runtime_fixtures


class BundleTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown
    runtime=runtime_fixtures.CompositionTests.runtime
    receipt=runtime_fixtures.CompositionTests.receipt
    settle=fixtures.PairedRuntimeTests.settle

    def sample(self,**changes):
        import numpy as np
        meta={"schema":1,"session":"capture1","depth_alignment":"rectified_left","depth_units":"millimetres",
            "invalid_depth":0,"captured_unix_ns":100000000000,"captured_unix_ns_semantics":"bundle_packaging_time_not_sensor_exposure",
            "frames":{name:{"sequence":2,"device_timestamp_seconds":1,"host_monotonic_timestamp_seconds":10,
                "transformation_valid":False} for name in ("rgb","depth","left","right")},**changes}
        arrays={"depth_mm":np.full((400,640),1234,dtype=np.uint16),"left":np.zeros((400,640),dtype=np.uint8),
            "right":np.zeros((400,640),dtype=np.uint8),"rgb_nv12":np.zeros((540,640),dtype=np.uint8),
            "metadata_json":np.frombuffer(json.dumps(meta).encode(),dtype=np.uint8)}
        output=io.BytesIO(); np.savez_compressed(output,**arrays)
        calibration={"session":"capture1","depth_alignment":"rectified_left","depth_units":"millimetres","eeprom":{}}
        return output.getvalue(),calibration

    def ingestor(self):
        return BundleIngestor(self.store,site_id="victoria",camera_id="den")

    def test_lossless_archive_dedup_and_honest_timing(self):
        data,cal=self.sample()
        item=self.ingestor().ingest(self.session["id"],data,cal)
        self.assertEqual(self.store.media_path(self.session["id"],item["id"]).read_bytes(),data)
        self.assertEqual(self.ingestor().ingest(self.session["id"],data,cal)["id"],item["id"])
        self.assertEqual(item["metadata"]["clock_mapping_status"],"unverified")
        self.assertFalse(item["metadata"]["continuous_capture"])
        self.assertNotIn("observed_at",item["metadata"])
        with self.assertRaisesRegex(ValueError,"verified clock"):
            RegisteredAnalysisInputs(self.store)({"session_id":self.session["id"]})

    def test_calibration_content_id_survives_camera_session_restart(self):
        from bundle_ingest import calibration_content_id
        data,cal=self.sample()
        first=self.ingestor().ingest(self.session["id"],data,cal)
        data,cal=self.sample(session="capture2")
        second=self.ingestor().ingest(self.session["id"],data,{**cal,"session":"capture2"})
        # The session digest stays provenance; the content binding is unchanged.
        self.assertNotEqual(first["metadata"]["calibration_id"],second["metadata"]["calibration_id"])
        self.assertEqual(first["metadata"]["calibration_content_id"],second["metadata"]["calibration_content_id"])
        self.assertEqual(first["metadata"]["calibration_content_id"],calibration_content_id({**cal,"session":"any"}))
        self.assertNotEqual(calibration_content_id(cal),calibration_content_id({**cal,"eeprom":{"changed":1}}))
        views=[m for m in self.store.list_media(self.session["id"])["items"] if m["metadata"].get("source_media_id")==second["id"]]
        self.assertEqual({v["metadata"]["calibration_content_id"] for v in views},{second["metadata"]["calibration_content_id"]})

    def test_calibration_epoch_and_false_rgb_alignment_are_rejected(self):
        data,cal=self.sample()
        with self.assertRaises(ValueError):
            self.ingestor().ingest(self.session["id"],data,{**cal,"session":"another"})
        data,cal=self.sample(depth_alignment="rgb")
        with self.assertRaises(ValueError):
            self.ingestor().ingest(self.session["id"],data,cal)

    def test_browser_views_preserve_pixels_receipt_time_and_source_link(self):
        import numpy as np
        from PIL import Image
        data,cal=self.sample()
        self.store.clock=lambda:102
        item=self.ingestor().ingest(self.session["id"],data,cal)
        depth=self.store.list_media(self.session["id"],kind="depth",at_ms=2000,radius_ms=0)["items"]
        self.assertEqual(len(depth),1)
        meta=depth[0]["metadata"]
        self.assertEqual(meta["source_media_id"],item["id"])
        self.assertEqual(meta["timeline_basis"],"controller_receipt")
        self.assertEqual(meta["clock_mapping_status"],"unverified")
        self.assertNotIn("observed_at",meta)
        raw=self.store.media_path(self.session["id"],depth[0]["id"]).read_bytes()
        self.assertEqual(len(raw),640*400*2)
        self.assertTrue(np.all(np.frombuffer(raw,dtype="<u2")==1234))
        images=self.store.list_media(self.session["id"],kind="image",at_ms=2000,radius_ms=0)["items"]
        self.assertEqual({m["metadata"]["modality"] for m in images},{"left","right"})
        for m in images:
            with Image.open(self.store.media_path(self.session["id"],m["id"])) as image:
                self.assertEqual(image.size,(640,400))
                self.assertEqual(image.mode,"L")
                self.assertTrue(np.all(np.asarray(image)==0))
        self.store.clock=lambda:103
        self.ingestor().ingest(self.session["id"],data,cal)
        repeated=self.store.list_media(self.session["id"],kind="depth")["items"]
        self.assertEqual([m["id"] for m in repeated],[depth[0]["id"]])
        self.assertEqual(repeated[0]["metadata"]["session_time_ms"],2000)

    def test_partial_view_failure_keeps_archive_and_retry_repairs_missing_views(self):
        data,cal=self.sample()
        original=self.store.add_media
        def fail_right(sid,data,mime,meta,source_key=None):
            if meta.get("modality")=="right": raise OSError("fixture write failure")
            return original(sid,data,mime,meta,source_key)
        self.store.add_media=fail_right
        try:
            with self.assertRaises(OSError):
                self.ingestor().ingest(self.session["id"],data,cal)
        finally:
            self.store.add_media=original
        self.ingestor().ingest(self.session["id"],data,cal)
        media=self.store.list_media(self.session["id"])["items"]
        self.assertEqual(len([m for m in media if m["mime_type"]=="application/x-npz"]),1)
        self.assertEqual(len([m for m in media if m["metadata"].get("source_media_id")]),3)

    def test_numerical_archive_polling_without_optional_inference(self):
        runtime,_=self.runtime(lambda *_:{"state":"blocked"})
        runtime.bundle_sources={"den":self.ingestor()}
        runtime.bundle_clients={"den":self.sample}
        runtime.start_session(self.session)
        try:
            def archived():
                return any(m["mime_type"]=="application/x-npz" for m in self.store.list_media(self.session["id"])["items"])
            self.settle(archived)
            self.assertEqual(runtime.runners,{})
        finally:
            self.assertTrue(runtime.close(timeout=2)["drained"])
        self.assertFalse(runtime.bundle_pumps[self.session["id"]].thread.is_alive())


if __name__=='__main__':
    unittest.main()

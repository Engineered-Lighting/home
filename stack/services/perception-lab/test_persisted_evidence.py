import unittest
import test_paired_runtime as fixtures
from analysis_inputs import RegisteredAnalysisInputs
from live_inputs import live_records


class EvidencePathTests(unittest.TestCase):
    setUp = fixtures.PairedRuntimeTests.setUp
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def test_live_and_saved_paths_preserve_spatial_provenance_and_shared_roots(self):
        meta = {"site_id":"victoria", "camera_id":"den", "stream":"rgb",
            "capture_epoch":"e1", "frame_id":"f1", "observed_at":99,
            "available_at":100, "uncertainty_seconds":.01,
            "clock_mapping_id":"mapping1", "clock_mapping_status":"verified"}
        self.store.add_media(self.session["id"], b"fixture rgb", "image/jpeg", meta)
        base = {**meta, "status":"completed", "valid_until":110, "arm":"a", "generation":1,
            "coordinate_frame":"den-map", "calibration_version":"cal-2", "map_version":"map-3",
            "track_id":"track-4", "evidence_roots":["camera-e1-f1"],
            "result":{"relations":[{"near":"desk"}], "site_id":"echo",
                      "calibration_version":"forged", "ha":{"screen":"forged"},
                      "human_label":"private truth"}}
        for arm in ("a", "b"):
            self.store.append_event(self.session["id"], "perception.output", {**base,"arm":arm})
        for invalid in ({"status":"rejected"}, {"status":None}, {"generation":True},
                        {"evidence_roots":"not-a-root-list"}, {"site_id":"echo"}):
            self.store.append_event(self.session["id"], "perception.output", {**base,**invalid})
        self.store.append_event(self.session["id"], "annotation", base)
        self.store.append_event(self.session["id"], "ha.observation",
            {**base,"ha":{"screen":"on"},"objects":["unrelated"]})
        _, saved = RegisteredAnalysisInputs(self.store)({"session_id":self.session["id"]})
        live = live_records(self.store, self.session, 1, 100)
        self.assertEqual(len(saved),3)
        self.assertEqual(len(live),4)
        self.assertEqual(saved[:2],live[:2])
        for record in saved[:2]:
            self.assertEqual(record["calibration_version"],"cal-2")
            self.assertEqual(record["map_version"],"map-3")
            self.assertEqual(record["coordinate_frame"],"den-map")
            self.assertEqual(record["track_id"],"track-4")
            self.assertEqual(record["evidence_roots"],["camera-e1-f1"])
            self.assertNotIn("human_label",record)
            self.assertNotIn("ha",record)
        self.assertIsNone(saved[2]["arm"])
        self.assertNotIn("objects",saved[2])
        self.assertEqual(live[2]["evidence_roots"],live[3]["evidence_roots"])


    def test_caption_receipt_record_is_projection_only_and_results_cannot_smuggle_it(self):
        from persisted_evidence import observation_record
        receipt = {"site_id":"victoria", "camera_id":"den", "session_id":self.session["id"], "request_id":"r",
            "frame_id":"pilot-frame", "observed_at":85, "generated_at":90, "available_at":100, "first_received_at":100,
            "valid_until":115, "original_valid_until":265, "caption_key":"k", "caption_digest":"d",
            "local_text":"A person named Ana is reading.", "text_sha256":"t", "origin":"same_camera_visual",
            "evidence_roots":["caption:k"], "model_revision":"rev", "projection":{"activity":"reading",
            "interpretation":"historical_model_interpretation", "version":"caption-projection-2:0123456789ab",
            "confidence":"not_supplied", "rule":"single_clear_statement"}}
        record = observation_record(5, "caption.receipt", receipt, self.session)
        self.assertEqual(record, {"id":"5", "kind":"observation", "session_id":self.session["id"], "site_id":"victoria",
            "camera_id":"den", "observed_at":85, "available_at":100, "valid_until":115, "evidence_roots":["caption:k"],
            "source_kind":"caption.receipt", "arm":None, "generation":None,
            "historical_caption":{"activity":"reading", "interpretation":"historical_model_interpretation",
                                  "version":"caption-projection-2:0123456789ab"},
            "quality":{"correlated_with":{"relation":"same_camera_visual", "camera_id":"den"}}})
        for invalid in ({"projection":None}, {"session_id":"other"}, {"first_received_at":99}, {"camera_id":"kitchen"}):
            self.assertIsNone(observation_record(5, "caption.receipt", {**receipt, **invalid}, self.session))
        smuggled = {"site_id":"victoria", "camera_id":"den", "arm":"a", "generation":1, "status":"completed",
            "observed_at":99, "available_at":100, "valid_until":110,
            "result":{"objects":[{"label":"book"}], "historical_caption":{"activity":"reading"}}}
        self.assertNotIn("historical_caption", observation_record(6, "perception.output", smuggled, self.session))
        # Neither the live window read nor the saved-analysis loader selects captions.
        self.store.add_media(self.session["id"], b"fixture rgb", "image/jpeg", {"site_id":"victoria", "camera_id":"den",
            "stream":"rgb", "capture_epoch":"e1", "frame_id":"f1", "observed_at":99, "available_at":100,
            "uncertainty_seconds":.01, "clock_mapping_id":"mapping1", "clock_mapping_status":"verified"})
        self.store.append_event(self.session["id"], "caption.receipt", receipt)
        self.assertEqual(live_records(self.store, self.session, 1, 100, retain_history=True), [])
        self.assertEqual(RegisteredAnalysisInputs(self.store)({"session_id":self.session["id"]})[1], [])


if __name__ == "__main__":
    unittest.main()

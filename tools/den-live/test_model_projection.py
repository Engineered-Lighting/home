import unittest
import dataclasses

import model_projection as projection


class StageTest(unittest.TestCase):
    def sample(self):
        return dict(id="vjepa", role="motion_intent", state="idle", configured=True, provisioned=True,
                    loaded=True, device="cuda:0", model_id="vjepa-lm", model_revision="revision",
                    profile_digest="a" * 64, reason="ready", queued=0, running=0,
                    observed_mono=100, valid_until_mono=103)

    def test_republication_does_not_renew_model_observation(self):
        first = projection.stage(self.sample(), 101)
        self.assertTrue(first["loaded"])
        old = projection.stage(self.sample(), 104)
        self.assertEqual(old["state"], "unavailable")
        self.assertIsNone(old["loaded"])
        self.assertIsNone(old["running"])
        self.assertEqual(old["observed_mono"], 100)
        self.assertEqual(old["valid_until_mono"], 103)

    def test_missing_or_nonfinite_observation_cannot_publish_an_expiry(self):
        for observed in (None, float("nan"), "100"):
            value = self.sample(); value["observed_mono"] = observed
            out = projection.stage(value, 101)
            self.assertIsNone(out["observed_mono"])
            self.assertIsNone(out["valid_until_mono"])
            self.assertTrue(out["configured"])
            self.assertIsNone(out["loaded"])

    def test_fresh_observed_lease_stays_exact(self):
        out = projection.stage(self.sample(), 101)
        self.assertEqual(out["state"], "idle")
        self.assertTrue(out["loaded"])
        self.assertEqual((out["observed_mono"], out["valid_until_mono"]), (100, 103))

    def test_configured_does_not_imply_loaded(self):
        value = self.sample(); value.update(state="not_admitted", provisioned=None, loaded=None)
        self.assertIsNone(projection.stage(value, 101)["loaded"])

    def test_paths_text_and_unknown_worker_state_do_not_leak(self):
        value = self.sample(); value.update(reason="secret/path?token=x", state="arbitrary-worker-state",
                                           model_id="https://secret", queued=True)
        out = projection.stage(value, 101)
        self.assertEqual(out["state"], "unavailable")
        self.assertIsNone(out["reason"])
        self.assertIsNone(out["model_id"])
        self.assertIsNone(out["queued"])

    def test_current_receipts_are_independent_lossless_and_do_not_mutate_markers(self):
        from test_forecast_spatial import fixture
        _, snapshot, track = fixture()
        marker = dict(track_id=track.track_id, xy_m=[1, 2], state="confirmed")
        event = dict(tracks=[marker])
        output = projection.project(event, snapshot, 100.4)
        self.assertEqual(output["frame"], dataclasses.asdict(snapshot.frame))
        self.assertEqual(output["service_run_id"], snapshot.service_run_id)
        projected = output["tracks"][0]
        self.assertEqual(projected["motion"], dataclasses.asdict(track.motion))
        self.assertEqual(projected["current_obs_ids"], list(track.current_obs_ids))
        self.assertEqual(projected["motion_forecast"]["state"], dataclasses.asdict(track.motion))
        self.assertNotIn("motion", marker)
        self.assertNotIn("frame", event)


if __name__ == "__main__":
    unittest.main()

import unittest

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


if __name__ == "__main__":
    unittest.main()

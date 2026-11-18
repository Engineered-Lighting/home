"""One Den clip through the worker's own contract and back as a bound IntentHint: CPU only, no model or socket.

Den's bridge builds a real-shaped clip (640x360 frames from the registered den-oak@ transform at about 2.3 frames
a second, so some slots hold a frame between 0.4 and 0.5 s); den_models.contracts validates it exactly as the worker
does and reads its frame blobs; the worker's own make_hint answers; intent.from_wire must bind that hint to the
current person. With PYTHONPATH naming a den-models tree, that tree's contract is the one under test.
"""
from pathlib import Path
import sys
import unittest

try:  # A worker tree supplied on PYTHONPATH is the contract under test; otherwise this checkout's own.
    import den_models  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "den-models"))
try:
    from den_models import contracts as worker
except ImportError:  # No worker tree at all: nothing to check against.
    worker = None


@unittest.skipIf(worker is None, "den_models (the worker contract) is unavailable")
class RoundTrip(unittest.TestCase):
    def setUp(self):
        import test_enrichment_link as fixtures
        self.fixtures = fixtures
        self.case = fixtures.BridgeTests("test_bridge_clip_obeys_real_worker_contract")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_real_shaped_half_second_hold_clip_binds_as_an_intent_hint(self):
        import intent
        fixtures, case = self.fixtures, self.case
        bridge = case.live()
        stamps = [500 + k / 2.3 for k in range(12)]
        source = case.feed(stamps)
        self.assertEqual(len(case.client.submitted), 1)
        request = case.client.submitted[0]
        # The worker's contract, not Den's own copy of it, admits the clip.
        accepted = worker.validate_request(request, profile=fixtures.PROFILE, now=case.clock())
        self.assertEqual(request["sampling"], worker.SAMPLING)
        self.assertEqual(worker.SAMPLING, intent.SAMPLING)
        self.assertEqual(fixtures.PROFILE["inputs"]["sampling"], worker.SAMPLING)
        self.assertEqual(len(accepted["frames"]), 32)
        self.assertTrue(.4 < max(fixtures.holds(request)) <= worker.MAX_HOLD_SECONDS)
        self.assertEqual({f["source_frame"]["transform_id"] for f in request["frames"]}, {fixtures.REGISTERED})
        self.assertEqual(request["dependencies"]["transform_id"], fixtures.REGISTERED)
        frames = worker.load_rgb_frames(accepted, case.root)  # The worker can read every held blob.
        self.assertEqual([len(rgb) for rgb in frames], [fixtures.WIDTH * fixtures.HEIGHT * 3] * 32)
        # The worker answers with its own hint for exactly that request.
        scores = {key: float(-i) for i, key in enumerate(intent.CHOICES)}
        case.clock.value += .3
        hint = worker.make_hint(accepted, fixtures.PROFILE, "d" * 64,
                                {"intent": "continue", "raw_option_scores": scores, "text": None}, case.clock()).as_dict()
        self.assertEqual(hint["evidence"]["sampling"], worker.SAMPLING)
        case.client.poll_hook = lambda req: fixtures.packet(req, "succeeded", hint)
        case.clock.value += .05
        bridge.step()
        self.assertEqual(len(case.hints), 1)
        bound = case.hints[0]
        self.assertEqual((bound.request_id, bound.intent), (request["request_id"], "continue"))
        self.assertEqual(bound.dependencies["transform_id"], fixtures.REGISTERED)
        self.assertEqual(bound.evidence["sampling"], intent.SAMPLING)
        snapshot = fixtures.live_snapshot(source)
        self.assertTrue(bound.matches(snapshot, snapshot.tracks[0]))
        event = bridge.decorate({"tracks": [], "health": {}}, snapshot)
        public = event["intent_hints"]
        self.assertEqual(len(public), 1)
        self.assertEqual((public[0]["intent"], public[0]["disposition"], public[0]["consumed"]),
                         ("continue", "advisory_only", False))
        self.assertEqual(public[0]["dependencies"]["transform_id"], fixtures.REGISTERED)
        self.assertEqual(bridge.counts["completed"], 1)
        self.assertEqual(case.client.refused, [])


if __name__ == "__main__":
    unittest.main()

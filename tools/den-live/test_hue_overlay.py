"""Exercise the Phase 2 worker without a Home Assistant connection."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import coordinator_live
import ha_link
import sofa_check
from hue_overlay import HueOverlay


class HueOverlayClearTest(unittest.TestCase):
    def test_clear_recovers_when_ha_restarts_after_a_lost_response(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            core, learn = coordinator_live.load_core(sofa_check.archive(root / "trees", "coordinator"))
            ha = Mock()
            context = Mock(side_effect=AssertionError("Clear must run before planning or learning"))
            overlay = HueOverlay(core, learn, ha, context, root)
            overlay.learner.entries["sitting/evening"] = {"light.den": {"on": True}}
            overlay.latest["learned"] = overlay.learner.entries
            old = {"epoch": "old", "active": True, "publisher": overlay.publisher}
            new = {"epoch": "new", "active": True, "publisher": None, "claim_revision": 0}
            ha.state.side_effect = [{"attributes": {"coordinator": row}} for row in (old, new, new)]
            ha.call.side_effect = [ha_link.HaError("timeout"), ha_link.HaError("stale authority"),
                                   {**new, "publisher": overlay.publisher}, {"stream_id": "stream", "watermark": 9}]

            overlay.request_clear()
            for _ in range(2):
                with self.assertRaises(ha_link.HaError):
                    overlay.step()
                self.assertTrue(overlay.readback()["clear_pending"])
            overlay.step()

            self.assertEqual([(call.args[1], call.args[2]["epoch"]) for call in ha.call.call_args_list],
                             [("coordinator_clear", "old"), ("coordinator_clear", "old"),
                              ("coordinator_claim", "new"), ("coordinator_clear", "new")])
            self.assertEqual(overlay.readback()["learned"], {})
            self.assertFalse(overlay.readback()["clear_pending"])
            restored = HueOverlay(core, learn, ha, context, root)
            self.assertEqual(restored.state["correction"], {"stream_id": "stream", "seq": 9})
            self.assertEqual(restored.learner.entries, {})


if __name__ == "__main__":
    unittest.main()

"""Blocking repair gate: actual factory, authority, recording and replay.

Run locally with loopback networking permitted. External I/O is guarded by the
fixture, and every camera/model result is newly authored fictional test input.
"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("blocking_gate_service", ROOT / "tools/fixtures/home-live-blocking-gate-service.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class BlockingJoinTests(unittest.TestCase):
    def test_actual_factory_reaches_stable_service_correction_withdrawal_drain_and_replay(self):
        # Retain the bounded authored archive for review, including failures.
        if directory := tempfile.mkdtemp(prefix="blocking-gate-test-", dir=ROOT / "artifacts"):
            join = fixture.BlockingJoin(directory)
            self.addCleanup(join.close)
            bootstrap = join.bootstrap()
            self.assertFalse(bootstrap["decision"]["person_hazards"]["items"])
            decisions, cycles = [], []
            lineage = []
            for _ in range(17):
                row = join.advance()
                cycles.append(row["cycle"])
                decisions.append(row["decision"])
                lineage.append(row["la"])
                self.assertEqual(row["cycle"]["interaction"], "completed", row)
            lit = [d for d in decisions if d["execution"]["status"] == "virtual"
                   and d["execution"]["readback"][0]["brightness_fraction"] > 0]
            self.assertGreaterEqual(len(lit), 3, [(d["scene"]["status"], d["allocation"].get("reason")) for d in decisions])
            reading = next(row for row in lineage if row["refinement"] == "reading")
            self.assertEqual(reading["frame_bindings"][-1]["capture_id"], reading["capture_id"])
            unavailable = next(row for row in lineage if row["outstanding"])
            self.assertTrue(unavailable["local_use"])
            self.assertIsNone(unavailable["refinement"], "late LA cannot refresh previous interpretation")
            scene = lit[-1]["scene"]
            self.assertEqual(len({d["scene"]["scene_id"] for d in lit}), 1)
            self.assertEqual(len({(d["execution"]["assignments"][0]["pan_deg"], d["execution"]["assignments"][0]["tilt_deg"]) for d in lit}), 1)
            from live_lifecycle_test_helpers import brightness
            join.io.now += .01
            body = brightness("blocking-gate-brightness", scene, join.io.now, .25)
            accepted = join.request("POST", "/intent-events", body)
            self.assertEqual(join.request("POST", "/intent-events", body), accepted, "exact intent retry")
            for _ in range(3):
                corrected = join.advance()["decision"]
            self.assertEqual(corrected["scene"]["scene_id"], scene["scene_id"])
            self.assertEqual(corrected["scene"]["example_ref"], scene["example_ref"])
            self.assertEqual(corrected["scene"]["nominal"]["cct_kelvin"], scene["nominal"]["cct_kelvin"])
            self.assertEqual(corrected["execution"]["readback"][0]["brightness_fraction"], .25)
            settlement = join.request("GET", "/intent-events/blocking-gate-brightness?site_id=victoria&room_id=den")
            self.assertEqual(settlement["application_result"]["status"], "applied")
            restarted = join.restart_worker()
            self.assertEqual(restarted["scene"]["scene_id"], scene["scene_id"])
            self.assertEqual(restarted["scene"]["nominal"]["brightness_fraction"], .25)
            self.assertEqual(join.request("POST", "/intent-events", body), accepted)
            missing = join.advance("missing_protection")["decision"]
            self.assertEqual(missing["execution"]["status"], "unavailable")
            final = join.close()
            self.assertEqual(final["status"], "stopped", final)
            self.assertEqual(final["worker"]["status"], "settled", final)
            self.assertEqual(final["recorder"]["status"], "drained", final)
            self.assertEqual(final["replay_exit_code"], 0, final)
            self.assertEqual(final["replay"]["status"], "reproduced", final)
            # Rehashing an edited archive does not manufacture missing original
            # evidence. Ordinary replay must check causal material as well.
            records = [json.loads(line) for line in join.archive.read_text(encoding="utf8").splitlines()]
            first = next(row for row in records if row["record"]["kind"] == "journal_prepare"
                         and row["record"]["data"]["original_receipts"])
            missing_ref = first["record"]["data"]["original_receipts"][0]["ref"]
            for row in records:
                if row["record"]["kind"] == "journal_prepare":
                    row["record"]["data"]["original_receipts"] = [original for original in row["record"]["data"]["original_receipts"]
                        if original["ref"] != missing_ref]
            previous = None
            for index, row in enumerate(records):
                row.update(seq=index, previous_digest=previous)
                row.pop("digest", None)
                row["digest"] = fixture.digest(row)
                previous = row["digest"]
            modified = Path(directory) / "blocking-gate-missing-originals.jsonl"
            modified.write_text("".join(fixture.canonical(row) + "\n" for row in records), encoding="utf8")
            replay = subprocess.run([shutil.which("node"), str(ROOT / "tools/den-live-runtime/replay.cjs"), str(modified)],
                cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(replay.returncode, 2, replay.stdout)
            self.assertEqual(json.loads(replay.stdout)["reason"], "archive_missing_original_receipt")

    def test_actual_factory_qualified_return_and_absence_do_not_transfer_reset_epoch(self):
        directory = tempfile.mkdtemp(prefix="blocking-gate-lifecycle-", dir=ROOT / "artifacts")
        join = fixture.BlockingJoin(directory)
        self.addCleanup(join.close)
        join.bootstrap()
        for _ in range(12):
            current = join.advance()["decision"]
        self.assertEqual(current["scene"]["status"], "selected", current)
        original_scene = deepcopy(current["scene"])
        target = next(row["payload"] for row in join.io.snapshot()["items"]
                      if row["payload"].get("method_ref") == join.io.spatial_method)
        gap = join.advance("gap")["decision"]
        self.assertEqual(gap["scene"]["scene_id"], original_scene["scene_id"])
        returned = join.advance()["decision"]
        self.assertEqual(returned["scene"]["scene_id"], original_scene["scene_id"])
        self.assertEqual(returned["scene"]["person_ref"], target["person_ref"])
        join.advance("empty")
        join.tick(5)
        reset = join.advance("empty")["decision"]
        self.assertEqual(reset["execution"]["status"], "unavailable")
        self.assertTrue(any(h["person_ref"] == target["person_ref"] for h in reset["person_hazards"]["items"]))
        join.io.qualified_receipt_during_detector("absence_qualified", target)
        absence = join.advance("empty")
        absent = absence["decision"]
        self.assertFalse(any(h["person_ref"] == target["person_ref"] for h in absent["person_hazards"]["items"]),
                         dict(cycle=absence["cycle"], inputs=absent["input_results"], hazards=absent["person_hazards"]))
        replacement = join.advance()["decision"]
        row = max((row["payload"] for row in join.io.snapshot()["items"]
                   if row["payload"].get("method_ref") == join.io.spatial_method), key=lambda value: value["observed_at"])
        self.assertNotEqual(row["person_ref"], target["person_ref"])
        self.assertNotEqual(row["subject_ref"], target["subject_ref"])
        self.assertFalse(replacement["scene"].get("scene_id") == original_scene["scene_id"])
        final = join.close()
        self.assertEqual(final["status"], "stopped", final)
        self.assertEqual(final["replay_exit_code"], 0, final)


if __name__ == "__main__":
    unittest.main()

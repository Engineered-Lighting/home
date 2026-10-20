"""Actual factory/worker hardware join with fictional external I/O only.

The test retains bounded originals under artifacts. It needs the fixture's local
loopback listeners and the already sealed portable C++ packet; no host/device I/O.
"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("home_fixture_hardware_join", ROOT/"tools/fixtures/home-fixture-hardware-join.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class HardwareJoinTests(unittest.TestCase):
    def test_preparation_without_current_durable_commit_cannot_supply_authority(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute("CREATE TABLE live_worker_current(payload_type TEXT,body TEXT)")
        prepared = {"commit_id":"never-committed","decision":{"execution":{"status":"virtual"},
                    "scene":{"status":"selected"},"goal":{"geometry_refs":[{"id":"fictional","payload_digest":"00"*32}]}}}
        owner = fixture.HardwareJoin.__new__(fixture.HardwareJoin)
        owner.join = SimpleNamespace(records=lambda:[{"kind":"worker_prepared","data":{"prepared":prepared}}],
            graph=SimpleNamespace(journal=SimpleNamespace(db=db,lock=threading.RLock())))
        with self.assertRaisesRegex(ValueError,"not_current_committed"):
            owner._worker_source()

    def test_actual_factory_worker_issuer_outbox_core_correction_and_original_replay(self):
        directory = Path(tempfile.mkdtemp(prefix="hardware-join-test-",dir=ROOT/"artifacts"))
        join = fixture.HardwareJoin(directory)
        self.addCleanup(join.close)
        result = join.run()
        self.assertEqual(result["status"],"partial")
        self.assertFalse(result["physical_permission"])
        first, second = result["first"],result["correction"]
        for row in (first,second):
            self.assertEqual(row["dispatch"]["state"],"terminal",row["dispatch"])
            self.assertEqual(row["dispatch"]["outcome"]["device_disposition"],"ADMITTED")
            self.assertEqual(row["native"]["submit_result"],"ok")
            self.assertEqual(row["native"]["snapshot"]["phase"],"lit")
            self.assertFalse(row["native"]["snapshot"]["physical_pass"])
        self.assertEqual(first["native"]["snapshot"]["applied"][21:], [1638,0,0])
        self.assertEqual(second["native"]["snapshot"]["applied"][21:], [1024,0,0])
        self.assertEqual(result["intent_settlement"]["application_result"]["status"],"applied")
        self.assertEqual(result["original_profile_refusal"],"insufficient_remaining_service_lease")
        for key in ("motion_id","motion_started_us","motion_deadline_us"):
            self.assertEqual(first["native"]["snapshot"][key], second["native"]["snapshot"][key])
        self.assertEqual(first["draft"]["target_centideg"],second["draft"]["target_centideg"])
        self.assertEqual(first["draft"]["support_expiry_home_us"],second["draft"]["support_expiry_home_us"])
        self.assertEqual(second["draft"]["lineage"]["correction_ref"]["intent_id"],"hardware-join-brightness")
        self.assertIs(join.harness.authority.journal,join.join.graph.journal)
        final = join.close()
        self.assertEqual(final["controlled_gate_status"],"passed")
        self.assertEqual(final["runtime"]["status"],"drained")
        self.assertEqual(final["runtime_replay"]["status"],"causal_archive_verified")
        self.assertEqual(final["runtime_replay"]["attempts"],3)
        self.assertEqual(final["authority"]["status"],"drained")
        self.assertEqual(final["worker"]["replay"]["status"],"reproduced")
        self.assertEqual(final["native_ordinary_replay"],{"status":"reproduced","cases":3,"physical_verified":False})
        for filename,digest in final["archive_hashes"].items():
            self.assertEqual(fixture.sha256((directory/filename).read_bytes()),digest,filename)
        inspection = final["browser_inspection"]
        raw = (directory/inspection["path"]).read_bytes()
        self.assertEqual(fixture.sha256(raw),inspection["sha256"])
        envelope = json.loads(raw)["report"]
        self.assertEqual(envelope["schema"],"home-fixture-execution-report-envelope-1")
        native_event = json.loads(bytes.fromhex(envelope["original_hex"]))
        self.assertEqual(native_event["schema"],"home-fixture-execution-report-1")
        self.assertGreater(int(native_event["revision_id"]),0)
        self.assertIsNotNone(native_event["revision_sha256"])
        self.assertEqual(envelope["scope"]["pins"]["home_schema"],
            json.loads((ROOT/"contracts/home-fixture-execution/schemas/DIGESTS.json").read_text())["interface_sha256"])
        self.assertEqual(envelope["scope"]["pins"]["home_source"],final["archive_hashes"]["hardware-home-source-manifest.json"])


if __name__ == "__main__":
    unittest.main()

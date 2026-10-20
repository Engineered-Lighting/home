"""Focused actual-journal carrier admission; fictional local scene only."""
import copy
import hashlib
import json
import subprocess
import unittest
import zlib
from pathlib import Path

from journal import canonical
from test_adaptive_feedback_geometry_hold_2_helpers import Host, WorkerProcess, ROOT


class CarrierHost(Host):
    def input_cut(self, t):
        self.clock.advance(t)
        row = self.j.db.execute("SELECT input_cursor FROM workers WHERE worker_id=?",
                                (self.worker.expected_worker_id,)).fetchone()
        return self.j.dynamic.input_cut(self.worker, self.j.dynamic.identity["site_id"],
            self.j.dynamic.identity["room_id"], consumer_id=self.worker.expected_consumer_id,
            after=row[0] if row else 0, t=t, limit=128)

    def prepare_boundary(self, t):
        cut = self.input_cut(t)
        for publication in cut["publications"]:
            self.native_worker().call("ingest", publication, t)
        if cut["committed_outputs"]:
            self.native_worker().call("ingestCommittedOutputs", cut["committed_outputs"], t)
        return self.native_worker().call("prepareBoundary", {"t": t, "cut": cut})

    def restart_and_recover(self):
        checkpoint = self.native_worker().call("snapshot")
        rows = self.native_worker().call("receiptLog")
        originals = [{"publication": row["publication"], "first_received_at": row["received_at"]}
                     for row in rows]
        recovery = self.j.dynamic._keyframe_recovery(checkpoint, self.worker)
        self.native.close()
        self.native = WorkerProcess(self.fixture["options"])
        self.native.call("restore", checkpoint, {"original_rows": originals})
        self.prior_originals = originals
        if recovery is not None:
            self.native.call("admitControlKeyframeRecovery", recovery)
        return checkpoint, recovery


class CarrierAdmissionTests(unittest.TestCase):
    def bridge(self, arguments):
        process = subprocess.run(["node", "--max-old-space-size=512",
            str(ROOT / "tools/home-adaptive-feedback-geometry-hold-2-validation-bridge.cjs")],
            input=canonical({"method": "phase_transition", "arguments": arguments}),
            text=True, encoding="utf-8", capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertTrue(process.stdout, process.stderr)
        return json.loads(process.stdout)

    def test_actual_restarted_carrier_keeps_raw_publication_and_parent_worker_FIRST(self):
        host = CarrierHost(views=True)
        records = []
        try:
            for kind, t in (("bootstrap", 0), ("semantic", 0), ("control", .2),
                            ("control", .4), ("control", .6)):
                if t == .6:
                    checkpoint, recovery = host.restart_and_recover()
                    self.assertIsNotNone(recovery)
                commit = host.prepare_bootstrap() if kind == "bootstrap" else host.prepare_boundary(t)
                record = {"kind": kind, "t": t, "commit": commit}
                records.append(record)
                record["receipt"] = host.accept(commit)
                record["metrics"] = copy.deepcopy(host.j.dynamic._g2_last_worker_commit_metrics)
                self.assertLess(record["metrics"]["elapsed_ms"], 5000)
                if t == .2:
                    before = host.j.db.execute("SELECT count(*) FROM feedback_worker_first_receipts").fetchone()[0]
                    self.assertIsNone(host.j.dynamic._keyframe_recovery(commit["checkpoint"], host.worker))
                    self.assertEqual(host.j.db.execute("SELECT count(*) FROM feedback_worker_first_receipts").fetchone()[0], before)
            carriers = [output for record in records for output in record["commit"]["outputs"]
                        if output["schema"] == "home-lighting-control-carrier-1"]
            self.assertTrue(carriers, "actual Worker2 must select a carrier in this focused scene")
            for body in carriers:
                row = host.j.db.execute("SELECT body FROM publications WHERE payload_type=? AND json_extract(body,'$.id')=?",
                                       (body["schema"], body["id"])).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(json.loads(row[0])["payload"], body)
            for record in records:
                saved = host.j.db.execute("SELECT request_blob FROM feedback_worker_commit_originals WHERE producer_id=? AND producer_epoch=? AND id=?",
                    (host.worker.producer_id, host.worker.producer_epoch, record["commit"]["id"])).fetchone()
                self.assertEqual(zlib.decompress(saved[0]).decode("utf-8"), canonical(record["commit"]))
            actual = records[-1]["commit"]
            descriptors = actual["checkpoint"]["dependency_receipts"]
            originals = []
            for descriptor in descriptors:
                cursor = descriptor["source_ref"]["publication_ref"]["cursor"]
                publication = json.loads(host.j.db.execute("SELECT body FROM publications WHERE cursor=?", (cursor,)).fetchone()[0])
                first = host.j.db.execute("SELECT received_at,eligible_at FROM feedback_worker_first_receipts WHERE principal_id=? AND consumer_id=? AND cursor=?",
                    (host.worker.principal_id, host.worker.expected_consumer_id, cursor)).fetchone()
                self.assertEqual(tuple(first), (descriptor["received_at"], descriptor["eligible_at"]))
                originals.append({"publication": publication, "first_received_at": first[0]})
            prior_cursors = {d["source_ref"]["publication_ref"]["cursor"] for d in checkpoint["dependency_receipts"]}
            arguments = {"options": host.fixture["options"], "commit": actual, "prior": checkpoint,
                         "originals": originals, "prior_originals": host.prior_originals,
                         "input_rows": [{"publication": row["publication"], "received_at": row["first_received_at"]}
                                        for row in originals if row["publication"]["cursor"] not in prior_cursors],
                         "keyframe_recovery": recovery}
            absent = copy.deepcopy(arguments); absent["keyframe_recovery"] = None
            self.assertFalse(self.bridge(absent)["ok"], "carrier cannot invent missing keyframe recovery")
            old = copy.deepcopy(arguments); del old["keyframe_recovery"]
            self.assertFalse(self.bridge(old)["ok"], "bridge2 must refuse old exact6 phase arguments")
            forged = copy.deepcopy(arguments)
            for lane in (forged["keyframe_recovery"]["worker_original"]["worker_receipt"],
                         forged["keyframe_recovery"]["output_proof"]["worker_receipt"]):
                lane["received_at"] += .01; lane["eligible_at"] += .01
                lane["digest"] = hashlib.sha256(canonical({key: lane[key] for key in
                    ("consumer_id", "received_at", "eligible_at")}).encode("utf-8")).hexdigest()
            forged["keyframe_recovery"]["worker_original"]["original"]["resolved_at"] = .6
            forged["keyframe_recovery"]["output_proof"]["resolved_at"] = .6
            self.assertFalse(self.bridge(forged)["ok"], "coherently rehashed FIRST must match the actual restored dependency")
            print(json.dumps({"scope": "actual fictional Journal/Worker2 first carrier; no Native admission",
                              "commits": [{"kind": r["kind"], "t": r["t"],
                                           "families": [b["schema"] for b in r["commit"]["outputs"]],
                                           "metrics": r["metrics"]} for r in records]}, sort_keys=True))
        except BaseException:
            directory = Path("C:/Users/Marcelo/.codex/visualizations/2026/10/10/01a12766-912b-7242-86d7-d7e454809ab7")
            revision = 2
            while (directory / ("geometry-hold.v2.carrier-journal-diagnostic.r" + str(revision) + ".json")).exists():
                revision += 1
            path = directory / ("geometry-hold.v2.carrier-journal-diagnostic.r" + str(revision) + ".json")
            path.write_text(canonical({"scope": "failed bounded fictional Journal/Worker2 carrier diagnostic; not acceptance",
                                       "records": records}), encoding="utf-8")
            raise
        finally:
            host.close()


if __name__ == "__main__":
    unittest.main()

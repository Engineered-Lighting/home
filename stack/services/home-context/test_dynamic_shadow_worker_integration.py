"""Real loopback HTTP qualification of fresh synthetic cut/commit boundaries."""
import json
import copy
import tempfile
import threading
import subprocess
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

from journal import DYNAMIC_PROFILE, Journal, PREFIX
from server import LocalServer
from test_dynamic_context_service import HERE, ROOT, SCOPE, bootstrap, content_digest, example, grant_config, envelope, next_commit
from test_dynamic_thread_audit import recovery_parity


class DynamicHTTPTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="synthetic-dynamic-http-", dir=HERE)
        self.now = 0
        notification = grant_config(operations=["read"], types=["home-engagement-state-3"], token="notification-only", worker="notification")
        self.j = Journal(Path(self.work.name) / "fresh-http.db", grants=[grant_config(), notification], profile=DYNAMIC_PROFILE, clock=lambda: self.now)
        self.grant = self.j.authenticate("synthetic-backend-token")
        self.server = LocalServer(("127.0.0.1", 0), self.j)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.j.close()
        self.work.cleanup()

    def request(self, path, body=None, token="synthetic-backend-token"):
        headers = {"Authorization": "Bearer " + token}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = Request(self.base + PREFIX + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with self.opener.open(req, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def cut_path(self, consumer="synthetic-worker"):
        return "/dynamic-input-cut?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"] + "&consumer_id=" + consumer + "&after=0&t=0&limit=128"

    def recovery_proofs(self):
        return {table: [tuple(row) for row in self.j.db.execute("SELECT * FROM " + table + " ORDER BY 1,2,3").fetchall()]
                for table in ("dynamic_snapshot_masks", "dynamic_deliveries", "consumers")}

    def assert_recovery_parity(self, snapshot):
        code, page = self.request("/events?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"])
        self.assertEqual(code, 200)
        states = recovery_parity(snapshot, page["items"], self.now)
        self.assertEqual(states["recovered"], states["uninterrupted"])

    def test_loopback_cut_commit_retry_and_authority_race(self):
        code, fence = self.request(self.cut_path())
        self.assertEqual(code, 200)
        self.assertTrue(fence["complete"])
        prepared = bootstrap(self.j, self.grant)
        prepared["input_cut"]["cut_id"] = fence["cut_id"]
        prepared["input_cut"]["digest"] = content_digest(prepared["input_cut"])
        code, receipt = self.request("/worker-commits", prepared)
        self.assertEqual(code, 200)
        self.assertEqual(receipt["commit_seq"], 1)
        intent = example("home-intent-event-2")
        intent.update(id="http-correction", kind="association_correction", available_at=0, valid_until=30)
        self.assertEqual(self.request("/intent-events", intent)[0], 200)
        self.assertEqual(self.request("/worker-commits", prepared), (200, receipt))
        query = "/worker-commits/synthetic-worker?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"]
        code, saved = self.request(query)
        self.assertEqual(code, 200)
        self.assertEqual(saved["checkpoint"], prepared["checkpoint"])
        self.assertGreater(saved["authority"]["generation"], saved["checkpoint"]["authority"]["generation"])

    def test_http_notification_cannot_request_control_cut(self):
        code, error = self.request(self.cut_path("notification"), token="notification-only")
        self.assertEqual(code, 403)
        self.assertEqual(error["error"]["code"], "forbidden")

    def test_http_partial_snapshot_originals_then_later_events(self):
        geometry = example("home-geometry-3")
        geometry.update(id="snapshot-original", observed_at=0, available_at=0, valid_until=1)
        geometry["capture"].update(capture_seq=1, observed_at=0)
        other = copy.deepcopy(geometry["objects"][0])
        other["object_ref"]["oid"] = "book-2"; other["person_ref"]["pid"] = 2
        geometry["objects"].append(other)
        self.assertEqual(self.request("/events", envelope(geometry))[0], 200)
        self.now = .2
        partial = copy.deepcopy(geometry)
        partial.update(id="snapshot-partial", observed_at=.2, available_at=.2, valid_until=1.2, objects=[other])
        partial["capture"].update(capture_seq=2, observed_at=.2)
        self.assertEqual(self.request("/events", envelope(partial))[0], 200)
        scope = "site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"]
        code, snapshot = self.request("/snapshot?" + scope + "&consumer_id=synthetic-worker")
        self.assertEqual(code, 200)
        self.assertEqual(snapshot["schema"], "home-context-snapshot-2")
        self.assertEqual([(h["object_ref"]["oid"], h["publication_ref"]["cursor"]) for h in snapshot["component_heads"]], [("book-1", 1), ("book-2", 2)])
        self.assertEqual(snapshot["items"][0]["payload"], geometry)
        self.now = .4
        later = copy.deepcopy(geometry)
        later.update(id="snapshot-subsequent", observed_at=.4, available_at=.4, valid_until=1.4, objects=[geometry["objects"][0]])
        later["capture"].update(capture_seq=3, observed_at=.4)
        self.assertEqual(self.request("/events", envelope(later))[0], 200)
        code, page = self.request("/events?" + scope + "&after=" + str(snapshot["cursor"]) + "&stream_epoch=" + snapshot["stream_epoch"])
        self.assertEqual(code, 200)
        self.assertEqual([p["payload"] for p in page["items"]], [later])

    def test_http_current_closed_head_evidence_lookup_never_revives_open(self):
        geometry = example("home-geometry-3")
        geometry.update(id="cached-open", observed_at=0, available_at=0, valid_until=1)
        geometry["capture"].update(capture_seq=1, observed_at=0)
        self.assertEqual(self.request("/events", envelope(geometry))[0], 200)
        self.now = .2
        closed = copy.deepcopy(geometry)
        closed.update(id="cached-closed", observed_at=.2, available_at=.2, valid_until=1.2)
        closed["capture"].update(capture_seq=2, observed_at=.2)
        closed["objects"][0].update(state="closed", patches=[])
        self.assertEqual(self.request("/events", envelope(closed))[0], 200)
        request = example("home-evidence-request-2")
        request.update(id="http-current-head", available_at=.2, deadline=.7)
        self.assertEqual(self.request("/evidence-requests", request)[0], 200)
        code, lookup = self.request("/evidence-requests/http-current-head?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"])
        self.assertEqual(code, 200)
        self.assertEqual((lookup["terminal_reply"]["status"], lookup["terminal_reply"]["estimate_ref"]), ("unavailable", None))

    def test_http_pending_future_geometry_snapshot_refuses_without_partial_proof(self):
        geometry = example("home-geometry-3")
        geometry.update(id="pending-snapshot-old", observed_at=0, available_at=0, valid_until=1)
        geometry["capture"].update(capture_seq=1, observed_at=0)
        self.assertEqual(self.request("/events", envelope(geometry))[0], 200)
        query = "/snapshot?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"] + "&consumer_id=synthetic-worker"
        code, original = self.request(query)
        self.assertEqual(code, 200)
        self.now = .2
        pending = copy.deepcopy(geometry)
        pending.update(id="pending-snapshot-later", observed_at=.2, available_at=.4, valid_until=1.2)
        pending["capture"].update(capture_seq=2, observed_at=.2)
        pending["objects"][0].update(state="withdrawn", patches=[])
        self.assertEqual(self.request("/events", envelope(pending))[0], 200)
        before = self.recovery_proofs()
        code, error = self.request(query)
        self.assertEqual((code, error["error"]["code"]), (409, "snapshot_pending_inputs"))
        self.assertEqual(self.recovery_proofs(), before)
        self.assertEqual(before["consumers"][0][-1], original["cursor"])
        self.now = .4
        code, snapshot = self.request(query)
        self.assertEqual(code, 200)
        self.assertEqual(snapshot["component_heads"][0]["publication_ref"]["cursor"], 2)
        self.assertEqual(snapshot["items"][0]["payload"], pending)
        self.assert_recovery_parity(snapshot)

    def test_http_pending_fact_child_snapshot_refuses_then_recovers_all_eligible(self):
        batch = example("home-fact-batch-2")
        positive = batch["facts"][0]
        positive.update(id="pending-positive", observed_at=0, available_at=0, valid_until=1)
        positive["capture"].update(capture_seq=1, observed_at=0)
        delayed = copy.deepcopy(positive)
        delayed.update(id="pending-closed", kind="closed", value=True, available_at=.1)
        batch.update(id="pending-child-batch", observed_at=0, available_at=.1, valid_until=1, facts=[positive, delayed])
        self.assertEqual(self.request("/events", envelope(batch))[0], 200)
        query = "/snapshot?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"] + "&consumer_id=synthetic-worker"
        before = self.recovery_proofs()
        code, error = self.request(query)
        self.assertEqual((code, error["error"]["code"]), (409, "snapshot_pending_inputs"))
        self.assertEqual(self.recovery_proofs(), before)
        self.assertFalse(before["dynamic_snapshot_masks"])
        self.assertFalse(before["consumers"])
        self.now = .1
        code, snapshot = self.request(query)
        self.assertEqual(code, 200)
        self.assertEqual({h["publication_ref"]["component_id"] for h in snapshot["component_heads"]}, {positive["id"], delayed["id"]})
        self.assertEqual(snapshot["items"][0]["payload"], batch)
        self.assert_recovery_parity(snapshot)

    def test_http_conflicting_first_intent_rejects_atomic_commit(self):
        self.assertEqual(self.request("/worker-commits", bootstrap(self.j, self.grant))[0], 200)
        intent = example("home-intent-event-2")
        intent.update(id="http-terminal-intent", available_at=0, valid_until=30)
        self.assertEqual(self.request("/intent-events", intent)[0], 200)
        first = next_commit(self.j, self.grant, t=0, kind="semantic", intent_results=[{"id": intent["id"], "status": "rejected", "reason": "no_qualified_relation"}])
        self.assertEqual(self.request("/worker-commits", first)[0], 200)
        path = "/intent-events/http-terminal-intent?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"]
        code, original = self.request(path)
        self.assertEqual(code, 200)
        self.now = 1
        changed = next_commit(self.j, self.grant, t=1, kind="semantic", intent_results=[{"id": intent["id"], "status": "applied", "reason": "declared_output"}])
        code, error = self.request("/worker-commits", changed)
        self.assertEqual((code, error["error"]["code"]), (409, "intent_result_conflict"))
        self.assertEqual(self.request(path), (200, original))

    def test_http_actual_worker_forged_mask_cannot_hide_withdrawal_authentic_snapshot_can_commit(self):
        geometry = example("home-geometry-3")
        geometry.update(id="mask-initial", observed_at=0, available_at=0, valid_until=1)
        geometry["capture"].update(capture_seq=1, observed_at=0)
        other = copy.deepcopy(geometry["objects"][0]); other["object_ref"]["oid"] = "book-2"; other["person_ref"]["pid"] = 2
        geometry["objects"].append(other)
        self.assertEqual(self.request("/events", envelope(geometry))[0], 200)
        script = r"""
const W=require('./tools/home-dynamic-context-shadow-worker.cjs'),F=require('./tools/fixtures/home-dynamic-book-synthetic.cjs');
let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',async()=>{
try{const x=JSON.parse(input),f=F.makeFixture({duration_s:2}),w=W.createWorker({...f,runtime_epoch:'synthetic-runtime-1'}),transport=W.createHttpTransport({base_url:x.url,token:'synthetic-backend-token',identity:f.identity,receivedClock:()=>x.t});
if(x.checkpoint)w.restore(x.checkpoint);
async function step(bootstrap){const cut=await transport.inputCut(w.snapshot()?.input_cursor||0,x.t);for(const p of cut.publications){let ids;if(x.mask&&p.payload_type==='home-geometry-3')ids=p.payload.objects.filter(o=>x.mask==='authentic'||o.object_ref.oid==='book-2').map(o=>W.componentId(o,'geometry')).sort();w.ingest(p,x.t,ids);}const commit=bootstrap?w.prepareBootstrap({t:x.t,cut}):w.prepareBoundary({t:x.t,cut});try{await transport.commit(commit);w.acknowledge(commit);return {accepted:true,checkpoint:w.snapshot(),geometry:commit.outputs.find(o=>o.geometry_snapshot)?.geometry_snapshot};}catch(error){return {accepted:false,error:error.message,geometry:commit.outputs.find(o=>o.geometry_snapshot)?.geometry_snapshot};}}
if(!x.checkpoint){await step(true);process.stdout.write(JSON.stringify(await step(false)));}else process.stdout.write(JSON.stringify(await step(false)));
}catch(error){process.stdout.write(JSON.stringify({setup_error:error.stack}));process.exitCode=1;}});
"""

        def worker(t, checkpoint=None, mask=None):
            run = subprocess.run(["node", "-e", script], input=json.dumps({"url": self.base, "t": t, "checkpoint": checkpoint, "mask": mask}), cwd=ROOT, text=True, capture_output=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            return json.loads(run.stdout)

        initial = worker(0)
        self.assertTrue(initial["accepted"], initial)
        self.now = .2
        newer = copy.deepcopy(geometry)
        newer.update(id="mask-withdrawn", observed_at=.2, available_at=.2, valid_until=1.2)
        newer["capture"].update(capture_seq=2, observed_at=.2)
        newer["objects"][0].update(state="withdrawn", patches=[])
        self.assertEqual(self.request("/events", envelope(newer))[0], 200)
        forged = worker(.2, initial["checkpoint"], "forged")
        self.assertEqual((forged["accepted"], forged["error"]), (False, "invalid_reference"))
        book1 = next(h for h in forged["geometry"]["object_heads"] if h["object_ref"]["oid"] == "book-1")
        self.assertEqual((book1["status"], book1["estimate"]["state"], book1["observed_at"]), ("fresh", "open", 0))
        code, snapshot = self.request("/snapshot?site_id=" + SCOPE["site_id"] + "&room_id=" + SCOPE["room_id"] + "&consumer_id=synthetic-worker")
        self.assertEqual(code, 200)
        self.assertEqual(next(h for h in snapshot["component_heads"] if h["object_ref"]["oid"] == "book-1")["observed_at"], .2)
        authenticated = worker(.2, initial["checkpoint"], "authentic")
        self.assertTrue(authenticated["accepted"], authenticated)
        book1 = next(h for h in authenticated["geometry"]["object_heads"] if h["object_ref"]["oid"] == "book-1")
        self.assertEqual((book1["status"], book1["estimate"]["state"], book1["observed_at"]), ("held", "withdrawn", .2))


if __name__ == "__main__":
    unittest.main()

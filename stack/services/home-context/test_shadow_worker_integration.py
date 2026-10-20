"""Local synthetic HTTP boundary test; no camera, host, model or private data."""
import hashlib
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from journal import Journal
from server import LocalServer
from schema_registry import schema_digest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


class WorkerIntegrationTests(unittest.TestCase):
    def test_node_worker_journal_intent_commit_and_restart(self):
        # A fresh synthetic DB owned by this invocation, never an existing ledger.
        with tempfile.TemporaryDirectory(prefix="synthetic-http-", dir=HERE) as folder:
            grant = {"principal_id": "synthetic-worker", "producer_id": "synthetic-worker", "token_sha256": hashlib.sha256(b"synthetic-only-token").hexdigest(),
                     "scopes": [{"site_id": "victoria", "room_id": "den"}], "operations": ["read", "intent", "feedback", "evidence_request", "worker_commit"],
                     "payload_types": ["home-intent-event-1", "home-lighting-shadow-decision-1", "home-engagement-state-1", "home-context-consumer-feedback-1", "home-evidence-request-1", "home-evidence-reply-1"],
                     "revisions": ["1.0", "provisional-r1"], "expected_actor_ref": "synthetic-owner"}
            grant["schema_pins"] = {name: schema_digest(name) for name in grant["payload_types"]}
            grant["clock_mappings"] = {}
            grant["registration_bindings"] = {}
            grant["expected_worker_id"] = "synthetic-worker"
            journal = Journal(Path(folder) / "synthetic.db", grants=[grant])
            server = LocalServer(("127.0.0.1", 0), journal)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            script = r"""
const fixture=require('./tools/fixtures/home-context-shadow-synthetic.cjs');
const W=require('./tools/home-context-shadow-worker.cjs');
const service_url=process.argv[1];
const config={...fixture,registry:fixture.registry(),service_url,token:'synthetic-only-token',worker_id:'synthetic-worker',producer_id:'synthetic-worker'};
const now=Date.now()/1000;
const intent={schema:'home-intent-event-1',version:1,id:'intent:synthetic-http',...fixture.identity,actor_ref:'synthetic-owner',kind:'declared_surface_use',surface_id:'desk_work_area',region_id:'desk-fixed',value:'using_surface',set_at:now,available_at:now,valid_until:now+30,origin:'human_declaration',truth_role:'runtime_intent'};
(async()=>{
 const url=service_url+'/api/home-context/v1/intent-events';
 const r=await fetch(url,{method:'POST',headers:{Authorization:'Bearer synthetic-only-token','Content-Type':'application/json'},body:JSON.stringify(intent)});
 if(!r.ok)throw Error(JSON.stringify(await r.json()));
 const receipt=await r.json();let outputs=[],statuses=[];
 let runtime;
 await new Promise(async(resolve,reject)=>{
  const timeout=setTimeout(()=>{runtime?.stop();reject(Error(JSON.stringify(statuses)));},7000);
  try{runtime=await W.start(config,{onStatus:s=>statuses.push(s),onDecision:d=>{outputs.push(d);if(outputs.length===2){setTimeout(()=>{runtime.stop();clearTimeout(timeout);resolve();},1);}}});}catch(e){clearTimeout(timeout);reject(e);}
 });
 let second;
 await new Promise(async(resolve,reject)=>{
  const timeout=setTimeout(()=>{second?.stop();reject(Error(JSON.stringify(statuses)));},5000);
  try{second=await W.start(config,{onStatus:s=>statuses.push(s),onDecision:d=>{outputs.push(d);setTimeout(()=>{second.stop();clearTimeout(timeout);resolve();},1);}});}catch(e){clearTimeout(timeout);reject(e);}
 });
 const state=await fetch(service_url+'/api/home-context/v1/snapshot?site_id=victoria&room_id=den',{headers:{Authorization:'Bearer synthetic-only-token'}}).then(r=>r.json());
 process.stdout.write(JSON.stringify({receipt,outputs,statuses,snapshot:state}));
})().catch(e=>{process.stderr.write(e.stack+'\n'+JSON.stringify(e.cause));process.exitCode=2;});
"""
            try:
                result = subprocess.run(["node", "-e", script, f"http://127.0.0.1:{server.server_port}"], capture_output=True, text=True, cwd=ROOT, timeout=18)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                self.assertEqual(data["receipt"]["status"], "accepted")
                self.assertEqual([d["seq"] for d in data["outputs"]], [1, 2, 3])
                for d in data["outputs"]:
                    self.assertFalse(d["actuated"])
                    self.assertFalse(d["acceptance_eligible"])
                self.assertTrue(any(r["attribution"]["origin"] == "declared" for d in data["outputs"] for r in d["requests"]["requests"]))
                self.assertTrue(any(p["payload_type"] == "home-lighting-shadow-decision-1" for p in data["snapshot"]["items"]))
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()
                journal.close()

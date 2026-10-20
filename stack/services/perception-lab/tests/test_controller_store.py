import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from config import Settings
from auth import Authorizer,AuthorizationError
from controller import Controller
from recorder import SampleRecorder,StreamCopySupervisor,StreamSource,validate_npz
from store import Store,LabError


class Runtime:
    def __init__(self):
        self.starts=0; self.switches=0; self.stops=0
    def capabilities(self): return {}
    def start_session(self,session): self.starts+=1; return {"state":"recording"}
    def stop_session(self,session): self.stops+=1; return {"state":"stopped"}
    def switch(self,session,request): self.switches+=1; return {"state":"blocked","reason":"fixture_no_model"}
    def run_job(self,job): return {"state":"blocked","reason":"fixture_no_worker"}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.now=1000.0
        self.settings=Settings(Path(self.temp.name),min_free_bytes=0,quota_bytes=64*1024**2,max_object_bytes=1024**2,cameras=({"id":"den"},),developer_fixture_mode=True)
        self.store=Store(self.settings,clock=lambda:self.now)
        self.runtime=Runtime()
        self.controller=Controller(self.store,self.runtime)
    def tearDown(self):
        self.store.close(); self.temp.cleanup()
    def start(self,request_id="request-1"):
        # The root experiment catalog may be added independently.
        catalog=self.controller.capabilities()["experiments"]
        experiment=catalog[0].get("id",catalog[0].get("experiment_id")) if catalog else "geometry"
        return self.controller.start({"camera_id":"den","experiment_id":experiment,"name":"Test","request_id":request_id})
    def metadata(self,**updates):
        return {"site_id":"victoria","camera_id":"den","capture_epoch":"boot-1","stream":"depth","sequence":1,"source_timestamp_seconds":55.0,"source_clock":"device_monotonic","timestamp_semantics":"device_frame","uncertainty_seconds":None,"format":"depth_u16_le","width":2,"height":2,"units":"millimetres","invalid_value":0,"alignment_frame":"rectified_left","calibration_id":"cal-1",**updates}
    def test_live_snapshot_reports_session_without_archive(self):
        s=self.start()
        result=self.controller.live(s['id'])
        self.assertEqual(result['session']['id'],s['id'])
        self.assertEqual(result['session']['state'],'recording')
        self.assertFalse({'events','outputs','results','media'} & result.keys())
        self.controller.stop(s['id'])
        self.assertEqual(self.controller.live(s['id'])['session']['state'],'stopped')
    def test_runtime_absent_blocks_without_claiming_recording(self):
        self.controller.runtime=None
        s=self.start()
        self.assertEqual(s["state"],"blocked"); self.assertIsNone(s["started_at"])
        self.assertEqual(self.controller.live(s["id"])["frames"],[])
    def test_time_filtered_media_retains_init_and_separates_unverified_clocks(self):
        sid=self.start()["id"]
        init=self.store.add_media(sid,b"init","video/mp4",{"role":"init"})
        self.store.add_media(sid,b"early","video/mp4",{"role":"media","start_seconds":0,"end_seconds":2})
        late=self.store.add_media(sid,b"late","video/mp4",{"role":"media","start_seconds":100,"end_seconds":102})
        page=self.store.list_media(sid,kind="video",encoded_at_ms=101000,radius_ms=0)
        self.assertEqual({x["id"] for x in page["items"]},{init["id"],late["id"]})
        verified=self.store.list_media(sid,kind="video",at_ms=101000,radius_ms=0)
        self.assertEqual([x["id"] for x in verified["items"]],[init["id"]])
        with self.assertRaises(LabError): self.store.list_media(sid,kind="depth",encoded_at_ms=1)
        with self.assertRaises(LabError): self.store.list_media(sid,at_ms=1,encoded_at_ms=1)

    def test_start_is_idempotent_and_request_content_bound(self):
        first=self.start(); second=self.start()
        self.assertEqual(first["id"],second["id"]); self.assertEqual(self.runtime.starts,1)
        with self.assertRaises(LabError): self.store.create_session("den","other","different","request-1")
    def test_only_one_recording_per_camera(self):
        self.start()
        with self.assertRaises(LabError): self.start("request-2")
    def test_unknown_camera_is_rejected_before_runtime(self):
        with self.assertRaises(LabError): self.controller.start({"camera_id":"other","experiment_id":"geometry","name":"Test","request_id":"r"})
        self.assertEqual(self.runtime.starts,0)
    def test_runtime_exception_reserves_camera_as_indeterminate(self):
        def fail(session): raise OSError("secret upstream URL")
        self.runtime.start_session=fail
        s=self.start()
        self.assertEqual(s["state"],"indeterminate")
        self.assertNotIn("secret",json.dumps(self.store.list_events(s["id"])))
        with self.assertRaises(LabError): self.start("request-2")
    def test_crash_recovery_retains_uncertainty_and_media(self):
        s=self.start(); media=self.store.add_media(s["id"],b"data","application/octet-stream",{})
        job,_=self.store.create_job("analysis","j",{},s["id"])
        self.store.update_job(job["id"],"running")
        self.store.close(); self.store=Store(self.settings,clock=lambda:self.now)
        self.assertEqual(self.store.get_session(s["id"])["state"],"indeterminate")
        self.assertEqual(self.store.get_job(job["id"])["state"],"indeterminate")
        self.assertEqual(self.store.get_media(s["id"],media["id"])["size"],4)
        with self.assertRaises(LabError): self.store.delete_session(s["id"],self.store.get_session(s["id"])["revision"])
    def test_annotation_revision_conflict_and_independent_labels(self):
        s=self.start(); payload={"at_ms":10,"end_ms":20,"label":"reading","origin":"human"}
        a=self.store.save_annotation(s["id"],"a",payload,0,"reviewer")
        self.assertEqual(a["revision"],1)
        with self.assertRaises(LabError): self.store.save_annotation(s["id"],"a",payload,0,"reviewer2")
        deleted=self.store.save_annotation(s["id"],"a",{},1,"reviewer",delete=True)
        self.assertEqual(deleted["revision"],2); self.assertEqual(self.store.list_annotations(s["id"]),[])
        self.assertEqual(len([e for e in self.store.list_events(s["id"]) if e["kind"].startswith("annotation")]),2)
    def test_annotation_rejects_nan_time(self):
        s=self.start()
        with self.assertRaises(LabError): self.store.save_annotation(s["id"],"a",{"at_ms":float("nan"),"label":"x","origin":"human"},0,"reviewer")
    def test_bookmark_idempotency(self):
        s=self.start(); payload={"at_ms":10,"label":"transition","request_id":"b"}
        a=self.controller.bookmark(s["id"],payload,"reviewer")
        self.assertEqual(a,self.controller.bookmark(s["id"],payload,"reviewer"))
    def test_switch_does_not_report_blocked_worker_as_switched(self):
        s=self.start(); payload={"arm":"A","profile_id":"pose","request_id":"switch"}
        a=self.controller.switch(s["id"],payload)
        self.assertEqual(a["outcome"]["state"],"blocked")
        self.controller.switch(s["id"],payload)
        self.assertEqual(self.runtime.switches,1)
    def test_expiry_stops_independently_of_ingestion(self):
        s=self.start(); self.now+=1800
        self.controller.expire()
        self.assertEqual(self.store.get_session(s["id"])["state"],"stopped")
        self.assertEqual(self.runtime.stops,1)
    def test_source_sample_dedup_is_exact(self):
        s=self.start(); recorder=SampleRecorder(self.store); data=b"\0\0"*4
        a=recorder.ingest(s["id"],data,self.metadata())
        b=recorder.ingest(s["id"],data,self.metadata())
        self.assertEqual(a["id"],b["id"])
        with self.assertRaises(LabError): recorder.ingest(s["id"],b"\1\0"*4,self.metadata())
    def test_sample_source_and_geometry_mismatch_are_rejected(self):
        s=self.start(); recorder=SampleRecorder(self.store)
        for updates in ({"site_id":"echo"},{"camera_id":"other"},{"alignment_frame":"rgb"},{"units":"metres"},{"sequence":True},{"width":3}):
            with self.subTest(updates=updates),self.assertRaises(LabError): recorder.ingest(s["id"],b"\0\0"*4,self.metadata(**updates))
    def test_out_of_order_samples_preserve_source_clock(self):
        s=self.start(); recorder=SampleRecorder(self.store)
        recorder.ingest(s["id"],b"\0\0"*4,self.metadata(sequence=2,source_timestamp_seconds=70))
        a=recorder.ingest(s["id"],b"\0\0"*4,self.metadata(sequence=1,source_timestamp_seconds=55))
        self.assertEqual(a["metadata"]["source_timestamp_seconds"],55)
    def test_quota_rejection_preserves_existing_media(self):
        s=self.start(); a=self.store.add_media(s["id"],b"existing","application/octet-stream",{})
        # Fill only the temporary test workspace, not the real host storage.
        # Bytes the Store does not own are measured at every check: refused at
        # once, with no reconciliation needed.
        with (self.store.root/"retained.bin").open("wb") as file: file.truncate(self.settings.quota_bytes)
        with self.assertRaises(LabError) as error: self.store.add_media(s["id"],b"new","application/octet-stream",{})
        self.assertEqual(self.store.accounting["drift_corrections"],0)
        self.assertEqual(error.exception.code,"quota_exceeded")
        self.assertEqual(self.store.media_path(s["id"],a["id"]).read_bytes(),b"existing")
        stopped=self.controller.stop(s["id"],"quota_exceeded")
        self.assertEqual(stopped["state"],"stopped")
        self.assertEqual(self.runtime.stops,1)
    def test_explicit_delete_requires_stop_and_invalidates_generation(self):
        s=self.start(); a=self.store.add_media(s["id"],b"x","application/octet-stream",{})
        with self.assertRaises(LabError): self.store.delete_session(s["id"],s["revision"])
        stopped=self.controller.stop(s["id"])
        deleted=self.store.delete_session(s["id"],stopped["revision"])
        self.assertEqual(deleted["generation"],1)
        with self.assertRaises(LabError) as error: self.store.get_media(s["id"],a["id"],0)
        self.assertEqual(error.exception.code,"generation_conflict")
    def test_uncertain_paired_worker_blocks_delete_after_capture_stop(self):
        s=self.start()
        with self.store.transaction() as db:
            db.execute("CREATE TABLE paired_requests(session_id TEXT,state TEXT)")
            db.execute("INSERT INTO paired_requests VALUES(?, 'indeterminate')",(s["id"],))
        stopped=self.controller.stop(s["id"])
        with self.assertRaises(LabError) as error: self.store.delete_session(s["id"],stopped["revision"])
        self.assertEqual(error.exception.code,"execution_pending")
    def test_manifest_pages_are_complete_without_unbounded_response(self):
        s=self.start()
        expected=[]
        for i in range(5): expected.append(self.store.add_media(s["id"],bytes([i]),"application/octet-stream",{"index":i})["id"])
        first=self.store.list_media(s["id"],limit=2)
        second=self.store.list_media(s["id"],first["next_cursor"],limit=2)
        third=self.store.list_media(s["id"],second["next_cursor"],limit=2)
        self.assertEqual(first["total_items"],5)
        self.assertIsNone(third["next_cursor"])
        self.assertEqual([x["id"] for page in (first,second,third) for x in page["items"]],expected)
        stopped=self.controller.stop(s["id"])
        self.store.delete_session(s["id"],stopped["revision"])
        self.assertEqual(self.store.list_media(s["id"])["total_items"],0)
    def test_cross_session_media_denied(self):
        s=self.start(); a=self.store.add_media(s["id"],b"x","application/octet-stream",{})
        self.controller.stop(s["id"]); second=self.start("second")
        with self.assertRaises(LabError): self.store.get_media(second["id"],a["id"])
    def test_paths_are_opaque(self):
        for bad in ("../secret","C:/secret","a/b","a\\b"):
            with self.subTest(bad=bad),self.assertRaises(LabError): self.store.media_path("session",bad)
    def test_storage_cannot_be_reopened_under_another_site(self):
        with self.assertRaises(ValueError): Store(Settings(Path(self.temp.name),site_id="echo",min_free_bytes=0))
    def test_job_absent_runtime_blocks_and_cancel_is_safe(self):
        self.controller.runtime=None
        job=self.controller.job({"kind":"analysis","request_id":"j","parameters":{}})
        self.assertEqual(job["state"],"blocked")
        self.assertEqual(self.controller.cancel_job(job["id"])["state"],"cancelled")
    def test_running_job_without_cancel_ack_remains_indeterminate(self):
        job,_=self.store.create_job("analysis","j",{})
        self.store.update_job(job["id"],"running")
        self.assertEqual(self.controller.cancel_job(job["id"])["state"],"indeterminate")
    def test_artifact_is_not_claimed_loaded_or_verified(self):
        a=self.controller.artifacts.register({"id":"pose-v1","checkpoint":"exact-model","version":"1","license":"Apache-2.0","runtime":"TensorRT","precision":"fp16","device_id":"jetson","sha256":"0"*64,"path":"private"})
        self.assertFalse(a["loaded"]); self.assertEqual(a["on_disk"],"unverified"); self.assertNotIn("path",a)
    def test_stream_copy_requires_admission_and_never_constructs_depthai(self):
        calls=[]
        source=StreamSource("source","den","rtsp://127.0.0.1:8554/den/rgb")
        supervisor=StreamCopySupervisor(self.store,source,popen=lambda *a,**kw:calls.append(a))
        self.assertEqual(supervisor.start({"id":"s","camera_id":"den"})["state"],"blocked")
        self.assertEqual(calls,[])
        command=supervisor.command(Path(self.temp.name))
        self.assertIn("copy",command); self.assertNotIn("+genpts",command)


class AuthAndNpzTests(unittest.TestCase):
    def test_credentials_are_site_and_capability_scoped(self):
        token="test-only-credential-"+"a"*32
        auth=Authorizer("victoria",[{"subject":"test","site_id":"victoria","token_sha256":hashlib.sha256(token.encode()).hexdigest(),"capabilities":["read"]}])
        principal=auth.authenticate("Bearer "+token); principal.require("read")
        with self.assertRaises(AuthorizationError): principal.require("record")
        with self.assertRaises(AuthorizationError): auth.authenticate("Bearer "+"b"*40)
        with self.assertRaises(ValueError): Authorizer("echo",auth.grants)
    def test_empty_grants_are_fail_closed_even_in_development(self):
        with self.assertRaises(AuthorizationError): Authorizer("victoria",[]).authenticate("Bearer "+"a"*40)
    def npz(self,descr="<u2",shape=(2,2),name="depth.npy",payload=b"\0"*8):
        header=repr({"descr":descr,"fortran_order":False,"shape":shape}).encode()+b"\n"
        array=b"\x93NUMPY\x01\x00"+struct.pack("<H",len(header))+header+payload
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,"w",zipfile.ZIP_DEFLATED) as archive: archive.writestr(name,array)
        return stream.getvalue()
    def test_numeric_npz_accepted_without_numpy_or_pickle(self):
        validate_npz(self.npz(),1024)
    def test_npz_rejects_pickle_paths_shape_mismatch_and_bombs(self):
        for data,bound in ((self.npz(descr="|O8"),1024),(self.npz(name="../depth.npy"),1024),(self.npz(shape=(2,3)),1024),(self.npz(),4),(b"not zip",1024)):
            with self.subTest(bound=bound),self.assertRaises(LabError): validate_npz(data,bound)


if __name__=="__main__": unittest.main()

"""Real in-process ASGI HTTP tests; no hosts, media processes, or models contacted."""
import base64
import hashlib
import json
import io
import tarfile
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import create_app,parse_range,PREFIX
from config import Settings
from store import Store
from test_controller_store import Runtime


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.token="http-test-"+"a"*40
        self.read_token="read-test-"+"b"*40
        grants=[]
        for token,scopes in ((self.token,["read","record","review","run","prepare","export","delete","ingest"]),(self.read_token,["read"])):
            grants.append({"subject":"test","site_id":"victoria","token_sha256":hashlib.sha256(token.encode()).hexdigest(),"capabilities":scopes})
        self.settings=Settings(Path(self.temp.name),min_free_bytes=0,max_object_bytes=65536,max_read_bytes=1024,quota_bytes=8*1024**2,token_grants=tuple(grants),cameras=({"id":"den"},),developer_fixture_mode=True)
        self.store=Store(self.settings)
        self.runtime=Runtime()
        self.client=TestClient(create_app(self.settings,self.runtime,self.store),raise_server_exceptions=False)
        self.client.__enter__()
        self.headers={"Authorization":"Bearer "+self.token,"X-Home-Lab-Site":"victoria"}
    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.store.close(); self.temp.cleanup()
    def request(self,method,path,**kwargs):
        return self.client.request(method,PREFIX+path,headers=kwargs.pop("headers",self.headers),**kwargs)
    def start(self):
        cap=self.request("GET","/capabilities").json()
        eid=cap["experiments"][0]["id"] if cap["experiments"] else "geometry"
        result=self.request("POST","/sessions",json={"camera_id":"den","experiment_id":eid,"name":"HTTP session","request_id":"session-start"})
        self.assertEqual(result.status_code,201,result.text)
        return result.json()
    def test_no_credential_and_wrong_site_fail_closed(self):
        self.assertEqual(self.request("GET","/capabilities",headers={}).status_code,401)
        result=self.request("GET","/capabilities",headers={**self.headers,"X-Home-Lab-Site":"echo"})
        self.assertEqual(result.status_code,403)
        self.assertNotIn(self.token,result.text)

    def test_full_erasure_requires_reconciled_runtime_and_blocks_later_reads(self):
        from service_runtime import ServiceRuntime
        sid=self.start()['id']
        self.request('POST',f'/sessions/{sid}/stop',json={'request_id':'erase-stop'})
        payload={'expected_revision':self.store.get_session(sid)['revision'],'erase_records':True}
        route=f'/sessions/{sid}'
        self.assertEqual(self.request('DELETE',route,json=payload).status_code,409)
        runtime=ServiceRuntime(self.store,{},lambda *_:None,None)
        self.client.app.state.controller.runtime=runtime
        try:
            self.assertEqual(self.request('DELETE',route,json={**payload,'erase_records':'yes'}).status_code,422)
            self.assertEqual(self.request('DELETE',route,headers={'Authorization':'Bearer '+self.read_token},json=payload).status_code,403)
            result=self.request('DELETE',route,json=payload)
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json(),{'id':sid,'deleted':True,'scope':'session'})
            self.assertEqual(self.request('GET',route).status_code,410)
            self.assertEqual(self.request('GET','/sessions').json()['items'],[])
        finally:runtime.close()

    def test_deletion_during_media_read_prevents_delivery(self):
        sid=self.start()['id']
        media=self.store.add_media(sid,b'private fixture bytes','image/jpeg',{})
        self.request('POST',f'/sessions/{sid}/stop',json={'request_id':'stop-before-delete'})
        original=hashlib.sha256
        store=self.store
        class DeleteOnHash:
            def __init__(self):self.inner=original();self.deleted=False
            def update(self,data):
                self.inner.update(data)
                if not self.deleted:
                    self.deleted=True
                    # An open Windows file may prevent unlink. The durable
                    # deletion intent must still invalidate the response.
                    with patch.object(Path,'unlink',side_effect=OSError('fixture open handle')):
                        try:store.delete_session(sid,store.get_session(sid)['revision'])
                        except OSError:pass
            def hexdigest(self):return self.inner.hexdigest()
        def hashing(data=None):return original(data) if data is not None else DeleteOnHash()
        with patch('app.hashlib.sha256',hashing):
            response=self.request('GET',f"/sessions/{sid}/media/{media['id']}?generation=0")
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(response.json()['error']['code'],'generation_conflict')
        self.assertNotIn(b'private fixture bytes',response.content)

    def test_export_pages_require_capability_and_stable_finalized_snapshot(self):
        sid=self.start()["id"]
        path=f"/sessions/{sid}/export"
        self.assertEqual(self.request("GET",path).status_code,409)
        self.assertEqual(self.request("POST",f"/sessions/{sid}/stop",json={"request_id":"export-stop"}).status_code,200)
        self.assertEqual(self.request("GET",path,headers={"Authorization":"Bearer "+self.read_token}).status_code,403)
        first=self.request("GET",path+"?limit=1")
        self.assertEqual(first.status_code,200)
        snapshot=first.json()
        next_path=path+f"?snapshot_id={snapshot['snapshot_id']}&cursor={snapshot['next_cursor']}&limit=1"
        self.assertEqual(self.request("GET",next_path).status_code,200)
        self.store.create_job("analysis","export-new-job",{},sid)
        changed=self.request("GET",next_path)
        self.assertEqual(changed.status_code,409)
        self.assertEqual(changed.json()["error"]["code"],"export_snapshot_changed")

    def test_portable_archive_requires_export_and_bound_final_snapshot(self):
        sid=self.start()["id"]
        self.store.add_media(sid,b'fixture recorded bytes','video/mp4',{'fixture':True})
        self.request("POST",f"/sessions/{sid}/stop",json={"request_id":"archive-stop"})
        page=self.request("GET",f"/sessions/{sid}/export?limit=1").json()
        path=f"/sessions/{sid}/archive?generation={page['generation']}&snapshot_id={page['snapshot_id']}"
        self.assertEqual(self.request('GET',path,headers={'Authorization':'Bearer '+self.read_token}).status_code,403)
        response=self.request('GET',path)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['content-type'],'application/x-tar')
        self.assertEqual(response.headers['x-lab-snapshot'],page['snapshot_id'])
        with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
            self.assertEqual(json.load(archive.extractfile('COMPLETE.json'))['media_count'],1)
        self.assertEqual(self.request('GET',path+'&url=bad').status_code,422)
        self.store.save_annotation(sid,'changed',{'at_ms':0,'label':'reading','origin':'human'},0,'fixture')
        self.assertEqual(self.request('GET',path).status_code,409)
        # Failed preflight releases the single-transfer slot.
        fresh=self.request('GET',f'/sessions/{sid}/export?limit=1').json()
        path=f"/sessions/{sid}/archive?generation={fresh['generation']}&snapshot_id={fresh['snapshot_id']}"
        self.assertEqual(self.request('GET',path).status_code,200)
    def test_replay_cursor_reads_beyond_first_event_page_without_future_delivery(self):
        session=self.start(); sid=session["id"]
        with self.store.transaction() as db:
            for n in range(1005):
                self.store._event(db,sid,"perception.offered",{"frame":n})
            for frame_time,delivery in ((120,121),(125,130)):
                self.store._event(db,sid,"perception.output",{"mode":"live","run_id":"profileA","arm":"a","source_end_s":frame_time,"available_at_s":delivery,"valid_until":session["started_at"]+140,"arm_snapshot":{"status":"running"}})
        result=self.request("GET",f"/sessions/{sid}?at_ms=127000")
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["outputs"][0]["payload"]["source_end_s"],120)
        result=self.request("GET",f"/sessions/{sid}?at_ms=131000")
        self.assertEqual(result.json()["outputs"][0]["payload"]["source_end_s"],125)
        result=self.request("GET",f"/sessions/{sid}?at_ms=141000")
        self.assertEqual(result.json()["outputs"][0]["payload"]["status"],"stale")
        self.assertEqual(self.request("GET",f"/sessions/{sid}?at_ms=-1").status_code,422)
    def test_read_credential_cannot_record(self):
        result=self.request("POST","/sessions",headers={"Authorization":"Bearer "+self.read_token},json={})
        self.assertEqual(result.status_code,403)
    def test_session_navigation_and_stop_do_not_restart_runtime(self):
        s=self.start()
        for _ in range(3): self.assertEqual(self.request("GET","/sessions/"+s["id"]).status_code,200)
        self.assertEqual(self.runtime.starts,1)
        self.assertEqual(self.request("POST",f"/sessions/{s['id']}/stop",json={}).json()["state"],"stopped")
        self.assertEqual(self.runtime.stops,1)
    def test_invalid_body_limits_and_extra_fields(self):
        result=self.request("POST","/sessions",content=b"x"*65537,headers={**self.headers,"Content-Type":"application/json"})
        self.assertEqual(result.status_code,413)
        result=self.request("POST","/sessions",json={"camera_id":"den","experiment_id":"geometry","name":"a","request_id":"r","command":"shell"})
        self.assertEqual(result.status_code,422)
        result=self.request("POST","/jobs",content=b'{"kind":"analysis","request_id":"r","parameters":{"x":NaN}}')
        self.assertEqual(result.status_code,422)
    def test_body_site_cannot_override_credential(self):
        result=self.request("POST","/jobs",json={"kind":"analysis","request_id":"r","site_id":"echo"})
        self.assertEqual(result.status_code,403)
    def test_http_annotation_revision_and_review_are_explicit(self):
        s=self.start(); path=f"/sessions/{s['id']}/annotations/ann"
        payload={"expected_revision":0,"at_ms":0,"end_ms":1000,"label":"reading","origin":"human"}
        a=self.request("PUT",path,json=payload)
        self.assertEqual(a.status_code,200,a.text); self.assertFalse(a.json()["reviewed"])
        self.assertEqual(self.request("PUT",path,json=payload).status_code,409)
        value=self.request("GET",f"/sessions/{s['id']}/findings").json()
        self.assertEqual(value["status"],"insufficient_reviewed_data")
        payload.update(expected_revision=1,reviewed=True)
        self.assertTrue(self.request("PUT",path,json=payload).json()["reviewed"])
    def test_numerical_sample_provenance_dedup_and_size_validation(self):
        s=self.start(); path=f"/sessions/{s['id']}/samples"
        meta={"site_id":"victoria","camera_id":"den","capture_epoch":"capture-1","stream":"depth","sequence":8,"source_timestamp_seconds":2.5,"source_clock":"device_monotonic","timestamp_semantics":"device_frame","uncertainty_seconds":None,"format":"depth_u16_le","width":2,"height":2,"units":"millimetres","invalid_value":0,"alignment_frame":"rectified_left","calibration_id":"cal-1"}
        encoded=base64.urlsafe_b64encode(json.dumps(meta).encode()).decode().rstrip("=")
        headers={**self.headers,"X-Perception-Sample":encoded,"Content-Type":"application/octet-stream"}
        a=self.request("POST",path,headers=headers,content=b"\0"*8)
        self.assertEqual(a.status_code,201,a.text)
        b=self.request("POST",path,headers=headers,content=b"\0"*8)
        self.assertEqual(a.json()["id"],b.json()["id"])
        self.assertEqual(a.json()["metadata"]["source_timestamp_seconds"],2.5)
        self.assertEqual(self.request("POST",path,headers=headers,content=b"\0"*7).status_code,422)
        self.assertEqual(self.request("POST",path,headers=headers,content=b"x"*65537).status_code,413)
    def test_media_ranges_generation_cross_session_and_integrity(self):
        s=self.start(); item=self.store.add_media(s["id"],b"abcdefgh"*256,"video/mp4",{"role":"media"})
        path=f"/sessions/{s['id']}/media/{item['id']}?generation=0"
        self.assertEqual(self.request("GET",path).status_code,416)
        result=self.request("GET",path,headers={**self.headers,"Range":"bytes=2-5"})
        self.assertEqual(result.status_code,206); self.assertEqual(result.content,b"cdef")
        self.assertEqual(result.headers["Content-Range"],"bytes 2-5/2048")
        self.assertEqual(self.request("GET",path,headers={**self.headers,"Range":"bytes=0-1024"}).status_code,416)
        self.assertEqual(self.request("GET",path.replace("generation=0","generation=1"),headers={**self.headers,"Range":"bytes=0-5"}).status_code,409)
        self.store.media_path(s["id"],item["id"]).write_bytes(b"wrong!!!"*256)
        self.assertEqual(self.request("GET",path,headers={**self.headers,"Range":"bytes=0-5"}).status_code,409)
    def test_media_descriptor_is_bound_and_manifest_is_paginated(self):
        s=self.start()
        a=self.store.add_media(s["id"],b"one","video/mp4",{})
        self.store.add_media(s["id"],b"two","video/mp4",{})
        first=self.request("GET",f"/sessions/{s['id']}/media?limit=1").json()
        self.assertEqual(first["total_items"],2)
        self.assertIsNotNone(first["next_cursor"])
        second=self.request("GET",f"/sessions/{s['id']}/media?limit=1&cursor={first['next_cursor']}").json()
        self.assertIsNone(second["next_cursor"])
        descriptor=self.request("GET",f"/sessions/{s['id']}/media/{a['id']}/descriptor?generation=0")
        self.assertEqual(descriptor.status_code,200)
        self.assertEqual(descriptor.json()["sha256"],a["sha256"])
        self.assertEqual(self.request("GET",f"/sessions/{s['id']}/media/{a['id']}/descriptor?generation=1").status_code,409)
    def test_explicit_delete_and_export_descriptor(self):
        s=self.start(); self.store.add_media(s["id"],b"x","video/mp4",{})
        self.assertEqual(self.request("GET",f"/sessions/{s['id']}/export").status_code,409)
        stopped=self.request("POST",f"/sessions/{s['id']}/stop",json={}).json()
        exported=self.request("GET",f"/sessions/{s['id']}/export").json()
        self.assertTrue(exported["metadata_only"])
        self.assertFalse(exported["media_bytes_included"])
        self.assertEqual(exported["counts"]["media"],1)
        result=self.request("DELETE",f"/sessions/{s['id']}",json={"expected_revision":stopped["revision"]})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["generation"],1)
        self.assertEqual(self.request("GET",f"/sessions/{s['id']}/media").json()["items"],[])
    def test_jobs_do_not_claim_runtime_success(self):
        result=self.request("POST","/jobs",json={"kind":"analysis","request_id":"job","parameters":{}})
        self.assertEqual(result.status_code,201); self.assertEqual(result.json()["state"],"blocked")
        jid=result.json()["id"]
        self.assertEqual(self.request("POST",f"/jobs/{jid}/cancel",json={}).json()["state"],"cancelled")
    def test_atomic_experiment_switch_passed_as_one_call(self):
        s=self.start()
        result=self.request("POST",f"/sessions/{s['id']}/switch",json={"request_id":"switch","experiment_id":s["experiment_id"]})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.runtime.switches,1)
    def test_api_headers_and_private_errors(self):
        result=self.request("GET","/sessions/not-found")
        self.assertEqual(result.status_code,404)
        self.assertEqual(result.headers["Cache-Control"],"no-store")
        self.assertEqual(result.headers["X-Content-Type-Options"],"nosniff")
        self.assertNotIn(self.temp.name,result.text)


if __name__=="__main__": unittest.main()

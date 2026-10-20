import unittest
import test_paired_runtime as fixtures
from exports import export_page
from store import LabError, encode


class ExportTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown

    def stop(self):
        self.store.transition(self.session["id"],"stopping")
        self.store.transition(self.session["id"],"stopped")

    def test_exports_all_events_jobs_media_and_label_provenance(self):
        sid=self.session["id"]
        with self.store.transaction() as db:
            for n in range(1105):self.store._event(db,sid,"fixture.observation",{"index":n})
            for n in range(205):
                db.execute("INSERT INTO media VALUES(?,?,?,?,?,?,?,?,?)",(f"m{n}",sid,0,1,"a"*64,"image/png",encode({"fixture":True}),100,None))
        for n in range(205):self.store.create_job("analysis",f"job{n}",{},sid)
        for aid,origin,reviewed in (("human","human",True),("model","model",True),("draft","human",False)):
            self.store.save_annotation(sid,aid,{"at_ms":0,"end_ms":5000,"label":"reading","origin":origin,"reviewed":reviewed},0,"fixture")
        self.stop()
        first=export_page(self.store,sid,limit=17);page=first;records=[]
        while True:
            records.extend(page["records"])
            if page["next_cursor"] is None:break
            page=export_page(self.store,sid,snapshot_id=first["snapshot_id"],cursor=page["next_cursor"],limit=17)
        self.assertEqual(len(records),first["total_records"])
        self.assertEqual(len([r for r in records if r["kind"]=="media"]),206)
        self.assertEqual(len([r for r in records if r["kind"]=="job"]),205)
        values={r["value"]["id"]:r["value"] for r in records if r["kind"]=="annotation"}
        self.assertEqual(values["human"]["label_status"],"reviewed_human")
        self.assertEqual(values["model"]["label_status"],"unverified")
        self.assertEqual(values["draft"]["label_status"],"unverified")
        self.assertFalse(first["media_bytes_included"])
        self.assertFalse(first["training_ready"])

    def test_edit_job_change_and_delete_invalidate_snapshot(self):
        sid=self.session["id"]
        job,_=self.store.create_job("analysis","j1",{},sid)
        self.stop()
        first=export_page(self.store,sid,limit=1)
        self.store.update_job(job["id"],"blocked")
        with self.assertRaisesRegex(LabError,"changed"):
            export_page(self.store,sid,snapshot_id=first["snapshot_id"],cursor=1)
        first=export_page(self.store,sid,limit=1)
        self.store.save_annotation(sid,"a1",{"at_ms":0,"label":"reading","origin":"human"},0,"fixture")
        with self.assertRaises(LabError):export_page(self.store,sid,snapshot_id=first["snapshot_id"],cursor=1)
        first=export_page(self.store,sid,limit=1)
        self.store.delete_session(sid,self.store.get_session(sid)["revision"])
        with self.assertRaises(LabError):export_page(self.store,sid,snapshot_id=first["snapshot_id"],cursor=1)

    def test_page_byte_budget_and_deleted_annotation_history(self):
        sid=self.session["id"]
        with self.store.transaction() as db:
            for n in range(12):self.store._event(db,sid,"fixture.large",{"value":"x"*200000})
        value=self.store.save_annotation(sid,"a1",{"at_ms":0,"label":"reading","origin":"human","reviewed":True},0,"fixture")
        self.store.save_annotation(sid,"a1",{},value["revision"],"fixture",delete=True)
        self.stop()
        first=export_page(self.store,sid,limit=200)
        self.assertLess(len(encode(first).encode()),1100000)
        self.assertIsNotNone(first["next_cursor"])
        annotation=next(r["value"] for r in first["records"] if r["kind"]=="annotation")
        self.assertEqual(annotation["label_status"],"deleted")

    def test_active_sessions_and_unbound_continuations_fail(self):
        sid=self.session["id"]
        with self.assertRaises(LabError):export_page(self.store,sid)
        self.stop()
        with self.assertRaises(LabError):export_page(self.store,sid,cursor=1)
        with self.assertRaises(LabError):export_page(self.store,sid,snapshot_id="../x")


if __name__=="__main__":unittest.main()

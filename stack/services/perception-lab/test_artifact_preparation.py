import hashlib
from pathlib import Path
import unittest
import test_paired_runtime as fixtures
from artifacts import ArtifactRegistry,ArtifactPreparation
from analysis_jobs import AnalysisJobs
from service_runtime import ServiceRuntime
from controller import Controller
from store import LabError


class ArtifactPreparationTests(unittest.TestCase):
    setUp=fixtures.PairedRuntimeTests.setUp
    tearDown=fixtures.PairedRuntimeTests.tearDown
    settle=fixtures.PairedRuntimeTests.settle

    def artifact(self):
        root=Path(self.directory.name)/'model-cache'
        root.mkdir()
        (root/'fixture.bin').write_bytes(b'non-model fixture')
        manifest={"id":"fixture","checkpoint":"test-only","version":"1","license":"test-fixture","runtime":"none","precision":"none","device_id":"workstation-fixture","sha256":hashlib.sha256(b'non-model fixture').hexdigest()}
        registry=ArtifactRegistry(self.store)
        registry.register(manifest)
        return root,registry,manifest

    def test_verified_file_is_stored_not_loaded_and_manifest_identity_is_immutable(self):
        root,registry,manifest=self.artifact()
        result=registry.verify('fixture',root,'fixture.bin',maximum_bytes=1024)
        self.assertEqual(result['state'],'stored')
        self.assertFalse(result['loaded'])
        self.assertFalse(result['runtime_validated'])
        self.assertEqual(registry.register(manifest)['state'],'stored')
        with self.assertRaises(LabError):registry.register({**manifest,'runtime':'another-runtime'})

    def test_checksum_path_and_byte_budget_fail_without_model_import(self):
        root,registry,_=self.artifact()
        with self.assertRaises(LabError):registry.verify('fixture',root,'../outside',maximum_bytes=1024)
        with self.assertRaises(LabError):registry.verify('fixture',root,'fixture.bin',maximum_bytes=1)
        (root/'fixture.bin').write_bytes(b'changed fixture')
        self.assertEqual(registry.verify('fixture',root,'fixture.bin',maximum_bytes=1024)['on_disk'],'checksum_mismatch')

    def test_preparation_api_job_verifies_only_registered_device_artifact(self):
        root,registry,_=self.artifact()
        worker=ArtifactPreparation(registry,root,{'fixture':'fixture.bin'},device_id='workstation-fixture',quota_bytes=1024)
        jobs=AnalysisJobs(self.store,worker,lambda job:(None,None),kind='preparation')
        controller=Controller(self.store,ServiceRuntime(self.store,{},lambda *_:None,None,preparation_jobs=jobs))
        try:
            job=controller.job({'kind':'preparation','request_id':'prepare1','parameters':{'artifact_ids':['fixture'],'keep_ready':False}})
            self.settle(lambda:self.store.get_job(job['id'])['state']=='completed')
            self.assertEqual(self.store.get_job(job['id'])['result']['phase'],'stored_only')
            self.assertEqual(controller.capabilities()['artifacts'][0]['state'],'stored')
        finally:
            jobs.close();jobs.pool.shutdown(wait=True)

    def test_residency_and_wrong_device_requests_do_not_verify_or_load(self):
        root,registry,_=self.artifact()
        worker=ArtifactPreparation(registry,root,{'fixture':'fixture.bin'},device_id='other-device',quota_bytes=1024)
        for parameters in ({'artifact_ids':['fixture']},{'artifact_ids':['fixture'],'keep_ready':True}):
            self.assertEqual(worker.run({'parameters':parameters})['state'],'blocked')
        self.assertEqual(registry.get('fixture')['on_disk'],'unverified')

    def test_unknown_artifact_is_blocked_before_io_and_budget_is_validated(self):
        root,registry,_=self.artifact()
        worker=ArtifactPreparation(registry,root,{'fixture':'fixture.bin'},device_id='workstation-fixture',quota_bytes=1024)
        result=worker.run({'parameters':{'artifact_ids':['unknown']}})
        self.assertEqual(result,{'state':'blocked','reason':'artifact_not_registered'})
        self.assertEqual(registry.get('fixture')['on_disk'],'unverified')
        for budget in (True,0,-1,1.5):
            with self.assertRaises(ValueError):
                ArtifactPreparation(registry,root,{},device_id='workstation-fixture',quota_bytes=budget)


if __name__=='__main__':unittest.main()

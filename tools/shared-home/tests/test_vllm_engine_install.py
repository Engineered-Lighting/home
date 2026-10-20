"""Installer contract fixtures; not installed vLLM or GPU acceptance."""
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from home_shared.engine_receipts import EngineReceiptStore, ReceiptUnavailable
from home_shared.vllm_engine_hook import install_engine_hook


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.modules={}
        for name in ('vllm','vllm.version','vllm.v1','vllm.v1.engine','vllm.v1.engine.core',
                     'vllm.v1.core','vllm.v1.core.sched','vllm.v1.core.sched.scheduler',
                     'vllm.v1.core.sched.interface','vllm.v1.core.sched.async_scheduler'):
            module=ModuleType(name)
            self.modules[name]=module
            if '.' in name:
                parent,child=name.rsplit('.',1)
                setattr(self.modules[parent],child,module)
        self.modules['vllm.version'].__version__='0.20.2'
        class Scheduler:
            connector=None
            def __init__(self): self.requests={}
            def schedule(self): return SimpleNamespace(free_encoder_mm_hashes=[])
            def _free_blocks(self, request): del self.requests[request.request_id]
        class AsyncScheduler(Scheduler):
            def _update_after_schedule(self, output): pass
            def _update_request_with_output(self, request, tokens): pass
        class EngineCore:
            def add_request(self, request, wave=0): self.scheduler.requests[request.request_id]=request
        class EngineCoreProc(EngineCore):
            def has_work(self): return False
            def is_running(self): return True
        self.modules['vllm.v1.core.sched.scheduler'].Scheduler=Scheduler
        self.modules['vllm.v1.core.sched.async_scheduler'].AsyncScheduler=AsyncScheduler
        self.modules['vllm.v1.engine.core'].EngineCore=EngineCore
        self.modules['vllm.v1.engine.core'].EngineCoreProc=EngineCoreProc
        self.sync,self.async_type,self.core_type=Scheduler,AsyncScheduler,EngineCoreProc
        self.authority=object()
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store=EngineReceiptStore(Path(temporary.name)/'engine.db',manifest_digest='a'*64,
            engine_id='engine',engine_epoch='epoch',model_revision='model',
            cleanup_authority=self.authority,create=True)
        self.addCleanup(self.store.close)

    def core(self, asynchronous):
        core=self.core_type()
        core.scheduler=(self.async_type if asynchronous else self.sync)()
        core.async_scheduling=asynchronous
        core.is_pooling_model=False
        core.vllm_config=SimpleNamespace(kv_transfer_config=None,ec_transfer_config=None,
            speculative_config=None,parallel_config=SimpleNamespace(tensor_parallel_size=1,
                pipeline_parallel_size=1,data_parallel_size=1))
        return core

    def install(self, core):
        with patch.dict(sys.modules,self.modules),patch('home_shared.vllm_engine_hook._pin'):
            return install_engine_hook(core,self.store,cleanup_authority=self.authority,rpc_timeout_seconds=1)

    def test_exact_sync_and_async_classes_are_supported(self):
        for asynchronous in (False,True):
            with self.subTest(asynchronous=asynchronous):
                core=self.core(asynchronous)
                self.install(core)
                self.assertTrue(callable(core.shared_home_terminal))

    def test_mode_mismatch_unknown_and_custom_subclass_rejected(self):
        for mode in (False,None,1):
            core=self.core(True);core.async_scheduling=mode
            with self.subTest(mode=mode),self.assertRaises(ReceiptUnavailable):self.install(core)
        core=self.core(True)
        core.scheduler=type('CustomScheduler',(self.async_type,),{})()
        with self.assertRaises(ReceiptUnavailable):self.install(core)

    def test_modified_async_instance_rejected(self):
        core=self.core(True)
        core.scheduler._update_after_schedule=lambda output:None
        with self.assertRaises(ReceiptUnavailable):self.install(core)


if __name__=='__main__':unittest.main()

"""Explicit pinned startup composition; no plugin discovery or admission.

Install before starting the standard vLLM CLI. Provisioning supplies a unique
epoch and existing ledger. An engine launch burns its marker even on failure;
reusing that epoch requires maintenance, never an automatic restart here.
"""
from dataclasses import dataclass, field
import asyncio
import hashlib
import importlib
import inspect
import math
import os
from pathlib import Path

from .admission import ResourceManifest
from .dispatch_token import DispatchTokenAuthority
from .engine_receipts import EngineReceiptStore, ReceiptUnavailable
from .service_config import protected_file
from .vllm_api_hook import install_api_hook, install_input_route
from .vllm_engine_hook import install_engine_hook

PINS = {
    'vllm.entrypoints.cli.main': '3996728fb60cd936d7d92ce59e067d0994a6a8520f81da563a51bf37d3870ce5',
    'vllm.entrypoints.cli.serve': '9bc134bed396a670b9e6b4de67eaebbe5755f4c573d05c8078c6eaaa5a2e920f',
    'vllm.plugins': '4be66190ceaee9d0465f62ade801a8e94a907d7ab9fdb0a67fa14ce87448ae9f',
    'vllm.v1.utils': 'b055bc61ca08d13fcca7c47dd805776f9abb6210c1b2026e21b7a79eda9cbe87',
    'vllm.utils.system_utils': '0baef4c945c236c160e6902fa28305bfd1c858c217126e6739abf04a617fd27a',
    'vllm.v1.engine.core': 'e1486de709675fa64109bb79f2b35b19c974b38a8bc2616b5e177ea3ff96c421',
    'vllm.entrypoints.openai.api_server': '9db8cc7c7668c941ba08f18632d6da8f962c03234a3fdfbb20e9e02c7810a59f',
    'vllm.entrypoints.launcher': '3100c00334d9b383e78ba641477c370bede951a17ad8b9b111d722acb0482c9b',
}


@dataclass(frozen=True)
class StartupConfig:
    manifest: ResourceManifest
    engine_epoch: str
    ledger_path: str
    signer_key: bytes = field(repr=False)
    rpc_timeout_seconds: float = 5.
    max_database_bytes: int = 64 * 1024 * 1024

    def __post_init__(self):
        if not isinstance(self.manifest, ResourceManifest):
            raise ValueError('fixed resource manifest required')
        DispatchTokenAuthority(self.manifest.digest, self.engine_epoch, self.signer_key,
                               max_deadline_seconds=self.manifest.max_deadline_seconds)
        if (not isinstance(self.ledger_path, str) or not Path(self.ledger_path).is_absolute()
                or type(self.rpc_timeout_seconds) not in (int, float)
                or not math.isfinite(self.rpc_timeout_seconds)
                or not 0 < self.rpc_timeout_seconds <= 15
                or type(self.max_database_bytes) is not int
                or not 65536 <= self.max_database_bytes <= 1024 ** 3):
            raise ValueError('bounded startup provisioning required')


def _targets():
    import vllm
    if vllm.__version__ != '0.20.2':
        raise ReceiptUnavailable('unreviewed startup version')
    modules = {}
    for name, digest in PINS.items():
        module = importlib.import_module(name)
        with Path(inspect.getsourcefile(module)).open('rb') as source:
            raw = source.read(1000001)
        if len(raw) > 1000000 or hashlib.sha256(raw).hexdigest() != digest:
            raise ReceiptUnavailable('startup source differs from reviewed pin')
        modules[name] = module
    core = modules['vllm.v1.engine.core'].EngineCoreProc
    api = modules['vllm.entrypoints.openai.api_server']
    launcher = modules['vllm.entrypoints.launcher']
    loop = core.run_busy_loop
    if (loop.__module__ != 'vllm.v1.engine.core'
            or loop.__qualname__ != 'EngineCoreProc.run_busy_loop'
            or inspect.getsourcefile(loop) != inspect.getsourcefile(modules['vllm.v1.engine.core'])
            or api.serve_http is not launcher.serve_http
            or launcher.serve_http.__module__ != 'vllm.entrypoints.launcher'
            or launcher.serve_http.__qualname__ != 'serve_http'
            or inspect.getsourcefile(launcher.serve_http) != inspect.getsourcefile(launcher)
            or not inspect.iscoroutinefunction(api.serve_http)):
        raise ReceiptUnavailable('startup targets already modified')
    return core, api


def _claim_launch(path, epoch):
    # The protected parent cannot be modified by other users. O_EXCL forbids
    # following an existing marker; it is intentionally never removed.
    marker = path.with_name(path.name + '.engine-started')
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(epoch.encode('ascii'))
        output.flush()
        os.fsync(output.fileno())
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def register_startup_hooks(config):
    """Wrap fresh standard targets. Does not start services or open admission."""
    if not isinstance(config, StartupConfig):
        raise ValueError('explicit startup config required')
    path = protected_file(config.ledger_path, secret=True, enforce_linux=True)
    core_class, api = _targets()
    if getattr(core_class, '_shared_home_startup_registered', False):
        raise ReceiptUnavailable('startup already registered')
    original_loop, original_serve = core_class.run_busy_loop, api.serve_http
    authority = DispatchTokenAuthority(config.manifest.digest, config.engine_epoch,
        config.signer_key, max_deadline_seconds=config.manifest.max_deadline_seconds)
    api_started = False

    def run_busy_loop(core):
        protected_file(path, secret=True, enforce_linux=True)
        _claim_launch(path, config.engine_epoch)
        cleanup_authority = object()
        store = EngineReceiptStore(path, manifest_digest=config.manifest.digest,
            engine_id=config.manifest.engine_id, engine_epoch=config.engine_epoch,
            model_revision=config.manifest.model_revision,
            cleanup_authority=cleanup_authority, create=False,
            max_records=config.manifest.max_ledger_records,
            max_database_bytes=config.max_database_bytes)
        try:
            install_engine_hook(core, store, cleanup_authority=cleanup_authority,
                                rpc_timeout_seconds=config.rpc_timeout_seconds)
            return original_loop(core)
        finally:
            store.close()

    async def serve_http(app, *args, **kwargs):
        nonlocal api_started
        if api_started:
            raise ReceiptUnavailable('API startup already attempted')
        api_started = True
        serving = getattr(app.state, 'openai_serving_chat', None)
        if serving is None:
            raise ReceiptUnavailable('initialized chat serving object required')
        expected = dict(version=1, manifest_digest=config.manifest.digest,
            engine_id=config.manifest.engine_id, engine_epoch=config.engine_epoch,
            model_revision=config.manifest.model_revision)
        try:
            runtime = await asyncio.wait_for(
                serving.engine_client.engine_core.call_utility_async('shared_home_runtime'),
                timeout=config.rpc_timeout_seconds)
        except Exception:
            raise ReceiptUnavailable('engine startup authority unavailable') from None
        if (type(runtime) is not dict or runtime != expected
                or any(type(runtime[key]) is not type(value) for key, value in expected.items())):
            raise ReceiptUnavailable('engine startup authority binding mismatch')
        hook = install_api_hook(serving, config.manifest, authority)
        install_input_route(app, hook)
        return await original_serve(app, *args, **kwargs)

    core_class.run_busy_loop = run_busy_loop
    api.serve_http = serve_http
    core_class._shared_home_startup_registered = True

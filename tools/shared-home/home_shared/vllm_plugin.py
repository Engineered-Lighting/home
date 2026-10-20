"""Explicit general-plugin entry point; the guarded launcher is mandatory.

No work occurs on import. Configuration and keys are local protected files.
The API startup wrapper also requires an affirmative engine-hook handshake.
"""
import os

from .engine_receipts import ReceiptUnavailable

PLUGIN_NAME = 'home_shared_admission'
ENTRY_POINT = 'home_shared.vllm_plugin:register'
_registered = None


def register():
    global _registered
    if os.environ.get('HOME_SHARED_VLLM_GUARDED') != '1':
        raise ReceiptUnavailable('use the shared-home guarded launcher')
    descriptor = os.environ.get('HOME_SHARED_VLLM_LAUNCH')
    if not descriptor:
        raise ReceiptUnavailable('protected launch descriptor required')
    from .vllm_launch_config import load_launch_config
    from .vllm_startup import register_startup_hooks
    config = load_launch_config(descriptor)
    if _registered is not None:
        if config != _registered:
            raise ReceiptUnavailable('launch configuration changed within process')
        return
    register_startup_hooks(config)
    _registered = config

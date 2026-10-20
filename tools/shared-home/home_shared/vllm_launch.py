"""Guarded standard `vllm serve` entry; never creates keys, epochs or ledgers."""
import importlib.metadata
import os
import sys

from .engine_receipts import ReceiptUnavailable
from .vllm_plugin import ENTRY_POINT, PLUGIN_NAME, register


def main():
    specification = getattr(sys.modules.get('__main__'), '__spec__', None)
    if getattr(specification, 'name', None) != 'home_shared.vllm_launch':
        raise ReceiptUnavailable('launch with python -m home_shared.vllm_launch')
    if len(sys.argv) < 2 or sys.argv[1] != 'serve':
        raise ReceiptUnavailable('only the standard serve command is supported')
    allowed = os.environ.get('VLLM_PLUGINS')
    if allowed is not None and PLUGIN_NAME not in allowed.split(','):
        raise ReceiptUnavailable('existing plugin allowlist must include shared-home admission')
    installed = [entry for entry in importlib.metadata.entry_points(group='vllm.general_plugins')
                 if entry.name == PLUGIN_NAME]
    if len(installed) != 1 or installed[0].value != ENTRY_POINT:
        raise ReceiptUnavailable('one installed shared-home plugin entry point required')
    # Spawned processes inherit the descriptor and explicit launch selection.
    # This is configuration, not request authority or an admission override.
    os.environ['HOME_SHARED_VLLM_GUARDED'] = '1'
    register()  # Direct call: plugin discovery cannot swallow this failure.
    from vllm.entrypoints.cli.main import main as standard_main
    return standard_main()


if __name__ == '__main__':
    main()
elif __name__ == '__mp_main__':
    # multiprocessing spawn re-executes this module before its target. API
    # children must not depend on plugin discovery (which can swallow import
    # failures) to acquire the pre-listen handshake and request enforcement.
    register()

"""Separate pinned production client; explicit at-most-four native starts.

Imports/constructors are inert. A reviewed external Windows Job owns resource
limits when the Home source owner explicitly invokes a member's start().
No historical cache, process policy, platform selection or authority is changed.
"""
from copy import deepcopy
import importlib.util
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import room_production_canonical_provider as provider

CLIENT_SOURCE_PINS = provider.CLIENT_SOURCE_PINS
MAX_LAUNCHES = 4
_CLIENT = None
_LOAD_LOCK = threading.Lock()


class _ReadOnlyProtocolLoader:
    """Compile the exact grammar original without consulting/writing a pyc."""
    def create_module(self, spec): return None

    def exec_module(self, module):
        path = provider.BUILD/'source'/provider.GRAMMAR_SOURCE
        raw = provider._read(path, 262144)
        provider.require(provider._sha(raw) == provider.RAW_SOURCE_PINS[provider.GRAMMAR_SOURCE],
                         'production protocol source changed')
        exec(compile(raw, str(path), 'exec'), module.__dict__)


def _protocol_spec(name, path):
    provider.require(name == '_c12_selected_production_protocol'
        and Path(path) == provider.BUILD/'source'/provider.GRAMMAR_SOURCE,
        'fixed production protocol module origin required')
    return importlib.util.spec_from_file_location(name, path, loader=_ReadOnlyProtocolLoader())


def _client_originals():
    profile, _, _ = provider.verified_production_provider_export()
    originals = {}
    for relative, expected in CLIENT_SOURCE_PINS:
        filename = provider.SOURCE_ROOT/relative
        raw = provider._read(filename, 262144)
        provider.require(provider._sha(raw) == expected, 'production client source changed: '+relative)
        originals[Path(relative).stem] = (filename, raw)
    return profile, originals


def load_production_canonical_client():
    """Return the exact isolated production class without creating a process."""
    global _CLIENT
    with _LOAD_LOCK:
        profile, originals = _client_originals()
        if _CLIENT is not None:
            provider.require(_CLIENT[0] == profile.provider_pins and _CLIENT[1] == dict(CLIENT_SOURCE_PINS),
                             'cached production client differs')
            return _CLIENT[2]
        imported = provider._isolated_modules(originals, tuple(originals),
            '_home_production_client_'+profile.manifest_sha256[:16]+'_')
        runtime = imported['c12_production_canonical']
        # The exact producer function privately imports its DATA grammar. Its
        # local importlib port uses original source only; global importlib and
        # bytecode policies remain unchanged for every other Home owner.
        runtime.importlib = SimpleNamespace(util=SimpleNamespace(
            spec_from_file_location=_protocol_spec, module_from_spec=importlib.util.module_from_spec))
        result = runtime.ProductionCanonicalCaptureProcess
        _CLIENT = (profile.provider_pins, dict(CLIENT_SOURCE_PINS), result)
        return result


def production_protocol_validator(manifest, header, index):
    """Exact producer DATA grammar only; never a source-born Home binding.

    Both recorder passes must independently apply their actual owner/context
    checks. Supplying a valid header here grants no recorder or writer right.
    """
    from room_canonical_plant_recording import validate_header
    profile, verified, _ = provider.verified_production_provider_export()
    provider.require(type(manifest) is dict and provider._canonical(manifest) == provider._canonical(verified),
                     'exact fixed production protocol manifest required')
    validate_header(header)
    expected = dict(abi=provider.ABI, build_id=verified['build_id'],
        manifest_sha256=profile.manifest_sha256, source_sha256=provider.SOURCE_SHA256,
        config_sha256=provider.CONFIG_SHA256, executable_sha256=profile.executable_sha256)
    provider.require(header['producer'] == expected, 'production protocol header identity differs')
    config = provider._strict(provider._read(profile.build/'source'/provider.CONFIG, 65536))
    for recorded, fixed in zip(header['fixtures'], config['fixtures']):
        provider.require(recorded['fixture_index'] == fixed['index']
            and recorded['device_id'] == fixed['device_id'] and recorded['boot'] == fixed['boot'],
            'production protocol fixture identity differs')
    provider.require(type(index) is int and 0 <= index < 4, 'production protocol fixture index refused')
    cls = load_production_canonical_client()
    actual = sys.modules[cls.__module__]
    provider.require(actual.ProductionCanonicalCaptureProcess is cls,
                     'production protocol runtime origin differs')
    return actual.protocol_validator(deepcopy(verified), deepcopy(header), index)


class ProductionCanonicalClientFactory:
    """Fixed inert capability. Construction does not assert Home qualification."""
    def __init__(self):
        self._profile = provider.selected_production_provider()
        self._binding = dict(selection=provider.FAMILY, provider_pins=self._profile.provider_pins,
            client_source_pins=dict(CLIENT_SOURCE_PINS), launch_policy=dict(
                schema='home-production-canonical-client-launch-policy-1', version=1,
                backend='existing_windows_popen', maximum_launches=MAX_LAUNCHES,
                resource_owner='external_windows_job_guard_not_installed_here',
                physical_permission=False, physical_truth=False))
        self._lock = threading.Lock(); self._launches = 0

    def binding(self): return deepcopy(self._binding)

    def __call__(self, manifest_path, expected_manifest_sha256, *, raw_observer):
        provider.require(provider.selected_production_provider() == self._profile
            and Path(manifest_path) == self._profile.build/'manifest.json'
            and expected_manifest_sha256 == self._profile.manifest_sha256 and callable(raw_observer),
            'fixed production canonical client binding required')
        client_type = load_production_canonical_client()
        return client_type(manifest_path, expected_manifest_sha256,
            process_factory=self.process_factory, timeout=2.0, raw_observer=raw_observer)

    def process_factory(self, argv, **options):
        expected = [str(self._profile.build/self._profile.executable_name)]
        provider.require(type(argv) is list and argv == expected
            and set(options) == {'stdin','stdout','stderr','text','bufsize'}
            and all(type(options[k]) is int and options[k] == subprocess.PIPE for k in ('stdin','stdout','stderr'))
            and options['text'] is False and type(options['bufsize']) is int and options['bufsize'] == 0,
            'closed production canonical process arguments required')
        provider.require(provider.selected_production_provider() == self._profile,
                         'production canonical platform changed')
        _client_originals()  # Changed originals refuse before the irreversible launch reservation.
        with self._lock:
            provider.require(self._launches < MAX_LAUNCHES, 'production canonical launch budget exhausted')
            self._launches += 1
        return subprocess.Popen(argv, **options)

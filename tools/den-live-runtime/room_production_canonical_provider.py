"""Explicit production software export; no source qualification or native I/O.

This family never changes the historical provider. Exact installed originals
are reread before returning cached isolated SDK types. Physical authority is
absent; a separate Home owner must qualify any recording/execution context.
"""
from __future__ import annotations
import builtins
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform
import sys
import threading
from types import ModuleType

FAMILY = 'production-canonical-1'
ABI = 'c12-canonical-capture-1'
SCHEMA = 'c12-production-canonical-build-1'
PROTOCOL = 'c12-production-canonical-protocol-1'
FIRMWARE_SHA256 = '18145f267dbef7ed93954eab96b576df226f44c0c44580080673f936ab1dad83'
SOURCE_SHA256 = 'c29188d9dcc13dbb4fdae3e3509c79442fb0bbadd75c55fb960b64b0f7663ab8'
CONFIG_SHA256 = '08ff5c69286d45bfd75400b3186b95c9724e842e9e06edf875c0e012aa0713b3'
CONFIG_FILE_SHA256 = '6c7ebbe682c1e8c2971e100eb9551776dee53d2390ab596c5c2b29ade083f72d'
HEADER_SHA256 = '89a70efdf5da276bee69a1a45e5d00fc490ae4140beb9bdd95e56e22d0a6fd9d'
PRODUCTION_MANIFEST_SHA256 = 'e422f6c4a85bd77673ce7e21db715f3e77f955e5ce0663194dfa41ca6b2ee1ef'
SOURCE_ROOT = Path(r'C:\Users\Marcelo\Documents\Engineered Light Fixture\implementation\c12-unified-offline-20261019')
BUILD = Path(r'C:\Users\Marcelo\Documents\Engineered Light Fixture\reviews\component-followthrough-2026-10-17\coordinator\home-contract-close-20261019\production-canonical-build-02')
PRODUCTION_MANIFEST = Path(r'C:\Users\Marcelo\Documents\Engineered Light Fixture\reviews\component-followthrough-2026-10-17\coordinator\integrated-offline-20261018\build\live-production-runtime-02\image\manifest.json')
CONFIG = 'contracts/c12/canonical-capture-config-v1.json'
HEADER = 'tools/runtime/c12_canonical_capture_build.generated.h'
GRAMMAR_SOURCE = 'vendor/home-current11/tools/den-live-runtime/room_current11_candidate_protocol.py'
GRAMMAR_DEFINITIONS = GRAMMAR_SOURCE.replace('.py', '.definitions.json')
# Exact raw working-tree pins. Compiled maps below use their declared text domain.
RAW_SOURCE_PINS = {'contracts/c12/canonical-capture-config-v1.json': '6c7ebbe682c1e8c2971e100eb9551776dee53d2390ab596c5c2b29ade083f72d', 'contracts/c12/production-canonical-v1.json': '9b4ccd38c42057330bc174604ec835b8b3b8e9951e0e0a05768bcd2232c471f0', 'contracts/c12/runtime-capabilities-v3.json': '6a346b73022e395389c01a0cc93528d918257149ce901dbf9303bb431f28d450', 'contracts/c12/runtime-hardware-source-v1.json': 'f58309fda4f27e8d95c3340dfb9f4d583e91b2a80bc30eb1d0e0cb4a502961f8', 'contracts/c12/runtime-sdk-v1.json': '30959350f89a3e8ff9244a7a9b71c062982dcf52814b41b918e8a180876f2123', 'contracts/c12/spotlight-committed-sample-v1.json': '98eced7d14e7c8029c9dda73d378386d4c633154be4fbc45b50cbaf01b024383', 'sketches/c12_runtime/runtime_motor.h': '196a604beaae17ae47f52fbd2f3235a45a75dbf147f517a68d22d499525343ce', 'sketches/c12_runtime_core/runtime_engine.h': '743601b5db357d230c434108acd78a67a600e61a87a1c6001870f2eac778e084', 'sketches/c12_runtime_core/runtime_lighting_driver.h': '732338f00aea11338ac3965840ae0a8499b81e32bcf0f8d524ab4075320efdb5', 'sketches/c12_runtime_core/runtime_plan_codec.h': '8e0c3eda12c1c4e8f08795cd6062314f2fe85c71f9a5c402281908c3b257e71a', 'tools/c12_current11_native.py': '7c8aad8e5f320ae7a895a9761b8a71d4b58177d4f67b1f13f5cf3306fccc65a1', 'tools/c12_production_canonical.py': '0e69be2cb55b145bcd0a0141a71c2a0e0c93a588c88ab74f8ae20fee74d48e96', 'tools/c12_runtime_adapter.py': '108750e169938051662df7c729d34891c014ce702725ebc60e31e0522bbbe11b', 'tools/c12_runtime_authority.py': '1e1f827efc4e782b7f2823be96b0cbcb46417ae022fb5e9b159e5b10517e15a2', 'tools/c12_runtime_clock_maintenance.py': 'f85c58ddefefd5037d984402867260d11ba95ce33d0af2d86ca9cfcf25de004b', 'tools/c12_runtime_ledger.py': 'c29035f4ac217b6d6f2fedb3986933cb596ead03c41d9b9fc75ff116e692f400', 'tools/c12_runtime_manual.py': '5bb3514bd64ed50cedb8fac887d053fe5852ae15d9c8cd9e0180bd9c83bc852e', 'tools/c12_runtime_outputs.py': '9572a340f2aadbabd95de8f977f145e8160e75e4b236720cbaf2dd68073d1066', 'tools/c12_runtime_plan.py': '4f2e5c49480749f6d1820b4209fbab03980ed20c4b4027f66dc38cf2bbd1ecc2', 'tools/c12_runtime_session.py': '45d32f603bdefcedac3bf245d5e61a84608912911eb05dae30b8883f16afb003', 'tools/c12_runtime_usb.py': '7c44d66e3cfb3b9817d108340c25e65d9cc4c66ac2ae98183aef267185ce3045', 'tools/c12_runtime_wire.py': 'fe9c3a71ea433586636367806027e57184f51961aeff35fdd0727a4c72aad56e', 'tools/c12_runtime_zha.py': 'ffa935d7c3b2f248f47b2834f70c01a7c6b0d8b5cca56493198c961d273218cd', 'tools/c12_spotlight_sample.py': '1a7d961bb5ebca2749dd0b65b96256d46241519fbc7fa5b993ac0151e9e08307', 'vendor/home-current11/tools/den-live-runtime/room_canonical_framed_replay.py': 'f61551b1ba55258017c2708c32d0d42a91f9f6569d0cf63fcaaa8e3615e18eae', 'vendor/home-current11/tools/den-live-runtime/room_current11_candidate_protocol.definitions.json': '57ecccae4aa1b88be9a47448176747f480a73cbb3218ea3f6b52b3596334caef', 'vendor/home-current11/tools/den-live-runtime/room_current11_candidate_protocol.py': '91da972adcdb082eaf06646d342d158b2d23e0fa20de36ca9bc3fdd70cf4664e', 'vendor/home-current11/tools/den-live-runtime/room_current11_candidate_recording.py': 'b9efef1162019b0584ccf0479675beffc114aa57f44b194aedf541d533a00128'}
MODULE_NAMES = ('c12_runtime_plan', 'c12_runtime_wire', 'c12_runtime_zha',
    'c12_runtime_authority', 'c12_runtime_adapter', 'c12_runtime_usb',
    'c12_runtime_session', 'c12_runtime_outputs')
SOURCE_PINS = (('c12_runtime_plan', '4f2e5c49480749f6d1820b4209fbab03980ed20c4b4027f66dc38cf2bbd1ecc2'), ('c12_runtime_wire', 'fe9c3a71ea433586636367806027e57184f51961aeff35fdd0727a4c72aad56e'), ('c12_runtime_zha', 'ffa935d7c3b2f248f47b2834f70c01a7c6b0d8b5cca56493198c961d273218cd'), ('c12_runtime_authority', '1e1f827efc4e782b7f2823be96b0cbcb46417ae022fb5e9b159e5b10517e15a2'), ('c12_runtime_adapter', '108750e169938051662df7c729d34891c014ce702725ebc60e31e0522bbbe11b'), ('c12_runtime_usb', '7c44d66e3cfb3b9817d108340c25e65d9cc4c66ac2ae98183aef267185ce3045'), ('c12_runtime_session', '45d32f603bdefcedac3bf245d5e61a84608912911eb05dae30b8883f16afb003'), ('c12_runtime_outputs', '9572a340f2aadbabd95de8f977f145e8160e75e4b236720cbaf2dd68073d1066'))
CLIENT_SOURCE_PINS = (('tools/c12_fake_plant.py', 'ebe8c865f84852dce60ad23f04d9304ca4a8a229810ac612609e7c9e2e9fd0d4'), ('tools/c12_canonical_export_evidence.py', 'dbec0e7612f8498725e6a196ba5f1f6cc3ee0aec35c43a768c556fdb337f3965'), ('tools/test_fixture_c12_native.py', '301e23a4ee8c353f64dcd4ddf8e52d37792d8d139d75b7e7199fc9796545a287'), ('tools/c12_canonical_capture.py', '3fccbd1c9cefe39fde15cbe65ef6bbd4a73acbaf67ac6b4da8bfb439717fe8ef'), ('tools/c12_production_canonical.py', '0e69be2cb55b145bcd0a0141a71c2a0e0c93a588c88ab74f8ae20fee74d48e96'))


def require(ok, reason):
    if not ok: raise ValueError(reason)


def _sha(raw): return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def _strict(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate production original key')
            result[key] = value
        return result
    def constant(value): raise ValueError('nonfinite production original')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _read(path, maximum):
    require(path.is_absolute() and path.is_file() and not path.is_symlink()
        and not any(p.is_symlink() for p in path.parents)
        and 0 < path.stat().st_size <= maximum, 'bounded regular production original required')
    raw = path.read_bytes()
    require(0 < len(raw) <= maximum, 'production original size changed')
    return raw


@dataclass(frozen=True)
class ProductionCanonicalProvider:
    selection: str = FAMILY
    name: str = 'windows-amd64-production'
    build_directory: str = str(BUILD)
    manifest_sha256: str = '7c0875bf7b8454d03953a4a04d35e7027a3a93b828a6128bae05a8d9e7c646d9'
    executable_name: str = 'c12-production-canonical.exe'
    executable_sha256: str = '7fbad15175841c7defc7d3a4245cc5c6f2978f001914fee589d958e43df6e2f8'
    executable_bytes: int = 546304

    @property
    def build(self): return Path(self.build_directory)

    @property
    def provider_pins(self):
        return dict(manifest_sha256=self.manifest_sha256, source_sha256=SOURCE_SHA256,
                    executable_sha256=self.executable_sha256, configuration_sha256=CONFIG_SHA256)

    def matches(self, pins): return type(pins) is dict and pins == self.provider_pins


WINDOWS_PRODUCTION_PROVIDER = ProductionCanonicalProvider()


def selected_production_provider():
    require(platform.system() == 'Windows' and platform.machine().lower() in ('amd64', 'x86_64'),
            'unsupported production canonical platform')
    return WINDOWS_PRODUCTION_PROVIDER


def verified_production_provider_export():
    """Read all fixed native/firmware/raw SDK originals; this issues no authority."""
    profile = selected_production_provider()
    raw = _read(profile.build/'manifest.json', 131072)
    require(_sha(raw) == profile.manifest_sha256, 'production manifest changed')
    manifest = _strict(raw); stage = profile.build/'source'
    require(manifest['schema'] == SCHEMA and type(manifest['version']) is int
        and manifest['version'] == 1 and manifest['abi'] == ABI
        and manifest['base_revision'] == manifest['production_firmware_source_sha256'] == FIRMWARE_SHA256
        and manifest['protocol_selection'] == PROTOCOL
        and manifest['source_digest'] == SOURCE_SHA256
        and manifest['build_id'] == 'c12-production-canonical-' + SOURCE_SHA256[:16]
        and manifest['source']['dir'] == str(stage)
        and all(manifest[k] is False for k in ('physical_permission','physical_truth','home_qualified'))
        and type(manifest['hardware_operations']) is int and manifest['hardware_operations'] == 0,
        'exact unqualified production software export required')
    files = manifest['source']['files']; firmware = manifest['compiled_runtime_source']
    require(type(files) is dict and len(files) == 38 and _sha(_canonical(files)) == SOURCE_SHA256
        and type(firmware) is dict and len(firmware) == 27 and _sha(_canonical(firmware)) == FIRMWARE_SHA256
        and all(files.get(n) == h for n, h in firmware.items()), 'production source maps changed')
    for name, expected in files.items():
        require(type(name) is str and not Path(name).is_absolute() and '..' not in Path(name).parts,
                'production source member path refused')
        require(_sha(_read(stage/name, 4*1024*1024)) == expected, 'production staged source changed: '+name)
    require({p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()}
        == set(files)|{HEADER}, 'production staged inventory changed')
    require(manifest['production_manifest'] == dict(path=str(PRODUCTION_MANIFEST), sha256=PRODUCTION_MANIFEST_SHA256),
            'production firmware receipt binding changed')
    receipt_raw = _read(PRODUCTION_MANIFEST, 262144)
    require(_sha(receipt_raw) == PRODUCTION_MANIFEST_SHA256, 'production firmware receipt changed')
    receipt = _strict(receipt_raw)
    require(receipt['schema'] == 'c12-runtime-build-2' and type(receipt['version']) is int
        and receipt['version'] == 2 and receipt['source_digest'] == FIRMWARE_SHA256
        and receipt['firmware_source_policy'] == 'c12-runtime-compiled-closure-1'
        and receipt['firmware_files'] == firmware, 'production firmware closure differs')
    exe = manifest['executable']
    require(exe == dict(path=str(profile.build/profile.executable_name), bytes=profile.executable_bytes,
        sha256=profile.executable_sha256), 'production executable binding changed')
    binary = _read(profile.build/profile.executable_name, profile.executable_bytes)
    require(len(binary) == profile.executable_bytes and _sha(binary) == profile.executable_sha256,
            'production executable changed')
    config = manifest['config']; cfg = _read(stage/CONFIG, 65536)
    require(config == dict(path=str(stage/CONFIG), file_sha256=CONFIG_FILE_SHA256, sha256=CONFIG_SHA256)
        and manifest['config_sha256'] == CONFIG_SHA256 and files[CONFIG] == CONFIG_FILE_SHA256
        and _sha(cfg) == CONFIG_FILE_SHA256 and _sha(_canonical(_strict(cfg))) == CONFIG_SHA256
        and manifest['generated_header_sha256'] == HEADER_SHA256
        and _sha(_read(stage/HEADER, 65536)) == HEADER_SHA256, 'production config/header changed')
    originals = {}
    for relative, expected in RAW_SOURCE_PINS.items():
        raw = _read(SOURCE_ROOT/relative, 262144)
        require(_sha(raw) == expected, 'production raw source changed: '+relative)
        originals[relative] = (SOURCE_ROOT/relative, raw)
    return profile, manifest, {name: originals['tools/'+name+'.py'] for name in MODULE_NAMES}


def _isolated_modules(originals, order, namespace, *, preloaded=None):
    imported, created = dict(preloaded or {}), []
    def fixed_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and (name.startswith('c12_') or name == 'test_fixture_c12_native'):
            require(name in imported, 'production dependency outside fixed closure')
            return imported[name]
        return builtins.__import__(name, globals, locals, fromlist, level)
    try:
        for name in order:
            path, raw = originals[name]; unique = namespace + name
            require(unique not in sys.modules, 'production isolated module identity occupied')
            module = ModuleType(unique); module.__file__ = str(path)
            module.__dict__['__builtins__'] = {**vars(builtins), '__import__': fixed_import}
            sys.modules[unique] = module; created.append(unique)
            exec(compile(raw, str(path), 'exec'), module.__dict__)
            imported[name] = module
    except BaseException:
        for unique in created: sys.modules.pop(unique, None)
        raise
    return imported


@dataclass(frozen=True)
class ProductionCanonicalModules:
    plan: object
    wire: object
    zha: object
    adapter: object
    session: object
    outputs: object
    authority: object
    usb: object
    provider_pins: dict
    source_pins: dict


_MODULES = None
_TRANSLATOR = None
_MANUAL = None
_LOAD_LOCK = threading.Lock()


def load_production_canonical_modules():
    global _MODULES
    with _LOAD_LOCK:
        profile, _, originals = verified_production_provider_export()
        if _MODULES is not None:
            require(profile.matches(_MODULES.provider_pins) and _MODULES.source_pins == dict(SOURCE_PINS),
                    'cached production SDK differs')
            return _MODULES
        imported = _isolated_modules(originals, MODULE_NAMES, '_home_production_sdk_'+profile.manifest_sha256[:16]+'_')
        names = ('plan','wire','zha','adapter','session','outputs','authority','usb')
        _MODULES = ProductionCanonicalModules(*(imported['c12_runtime_'+n] for n in names),
                                               profile.provider_pins, dict(SOURCE_PINS))
        return _MODULES


def load_production_spotlight_translator():
    global _TRANSLATOR
    with _LOAD_LOCK:
        profile, _, _ = verified_production_provider_export()
        if _TRANSLATOR is None:
            relative = 'tools/c12_spotlight_sample.py'; path = SOURCE_ROOT/relative
            raw = _read(path, 262144)
            require(_sha(raw) == RAW_SOURCE_PINS[relative], 'production translator changed')
            _TRANSLATOR = _isolated_modules({'c12_spotlight_sample': (path, raw)},
                ('c12_spotlight_sample',), '_home_production_translator_'+profile.manifest_sha256[:16]+'_')['c12_spotlight_sample']
        return _TRANSLATOR


@dataclass(frozen=True)
class ProductionManualModules:
    sdk: ProductionCanonicalModules
    ledger: object
    manual: object
    source_pins: dict


def load_production_manual_modules():
    """Exact producer manual/ledger types sharing the already isolated SDK.

    Loading creates no ledger or permission. Only an independently authorized
    source owner may provision and hand off an actual manual session.
    """
    global _MANUAL
    sdk = load_production_canonical_modules()
    with _LOAD_LOCK:
        profile, _, _ = verified_production_provider_export()
        pins = {name: RAW_SOURCE_PINS['tools/'+name+'.py']
                for name in ('c12_runtime_ledger','c12_runtime_manual')}
        if _MANUAL is not None:
            require(_MANUAL.sdk is sdk and _MANUAL.source_pins == pins, 'cached production manual SDK differs')
            return _MANUAL
        originals = {}
        for name, expected in pins.items():
            path = SOURCE_ROOT/'tools'/(name+'.py'); raw = _read(path, 262144)
            require(_sha(raw) == expected, 'production manual source changed: '+name)
            originals[name] = (path, raw)
        shared = {'c12_runtime_'+name: getattr(sdk,name)
                  for name in ('plan','wire','zha','adapter','session','outputs','authority','usb')}
        imported = _isolated_modules(originals, tuple(originals),
            '_home_production_manual_'+profile.manifest_sha256[:16]+'_', preloaded=shared)
        _MANUAL = ProductionManualModules(sdk, imported['c12_runtime_ledger'],
            imported['c12_runtime_manual'], pins)
        return _MANUAL

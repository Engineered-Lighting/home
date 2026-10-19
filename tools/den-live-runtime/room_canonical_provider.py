"""Code-installed canonical software exports; no caller-selected provider.

Selection uses the running platform only. Reading these immutable originals
does not create a process, grant, session, physical permission or conformance.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform

from fixture_execution_validation import require

ABI = 'c12-canonical-capture-1'
BASE_REVISION = 'beda4eca32a9cccf7d0b5d35d91077810b23d6b2'
SOURCE_SHA256 = 'ed74458bd47afc6a04c6ba83394dab8d55f06f7e6395ad8feb9e23fe36592b83'
CONFIG_SHA256 = '0bbbf0802ec9d396057285ef4fb8fe50a8bfa010e2afc39783fefd451989a9af'
CONFIG_FILE_SHA256 = 'e2def2d9790ab1ca3c9315529b9c5eb708da69446e129c60b226a20e2fc0113e'
HEADER_SHA256 = '4a97e9b6e94639569efe99b0069fbbd66b8a9641881b7d17e59ae80989f5f35f'
MIXER_SOURCE_SHA256 = '848f21eda12ed0ef6d44680d7379704ab9fbd3b08b824cbc34e1aa4277253e2f'
CONFIG = 'contracts/c12/canonical-capture-config-v1.json'
HEADER = 'tools/runtime/c12_canonical_capture_build.generated.h'
SOURCE_PINS = (
    ('c12_runtime_plan','477de391670b0b85a39df41135ae5e66fe68e87b1416a2e2792cd4780af7379d'),
    ('c12_runtime_wire','a7afb1c3f43ea0421987138154bc049068eff17f2dfc9de7a6ec2e7b8697f3b3'),
    ('c12_runtime_zha','d56f2df2768798c63cc03bae0c01a1e3c3c0ec3e954b1d5c8d54daae938c8464'),
    ('c12_runtime_adapter','15fad9d0093c9451ccda2c578cc6578685cc72459361ea2fc1bcb0a24cf9bb4d'),
    ('c12_runtime_session','024ed7e9ffdd9656328238f6878e3ae6455ac48ba344b6807fe7e2a5b2d5b81b'),
    ('c12_runtime_outputs','2399b44850846c4d237924386feeb17b116114e05dd1dceab7c298d0b40047d6'),
)
MODULE_NAMES = tuple(name for name, _ in SOURCE_PINS)
PIN_KEYS = ('manifest_sha256','source_sha256','executable_sha256','configuration_sha256')


@dataclass(frozen=True)
class CanonicalProvider:
    name: str
    build_directory: str
    manifest_sha256: str
    executable_name: str
    executable_sha256: str
    executable_bytes: int

    @property
    def build(self): return Path(self.build_directory)

    @property
    def provider_pins(self):
        return dict(manifest_sha256=self.manifest_sha256,source_sha256=SOURCE_SHA256,
            executable_sha256=self.executable_sha256,configuration_sha256=CONFIG_SHA256)

    def matches(self, pins):
        return type(pins) is dict and pins == self.provider_pins


WINDOWS_PROVIDER = CanonicalProvider('windows-amd64',
    r'C:\Users\Marcelo\Documents\Engineered Light Fixture\validation\c12-offline\canonical-capture-6d5f2bc\build',
    '789533e2af58b4bf1a7f62be75db3ef118c96d8c4336b57cbd3bdd963d2bacf2',
    'c12-canonical-capture-bridge.exe',
    'e6f897255090b77b9b6f3dcb0321a09affee5df2b9821b20642820bb26dc32a3',504320)
LINUX_ARM64_PROVIDER = CanonicalProvider('linux-aarch64',
    '/home/engineeredlightingllm/perception-lab/private/review/'
    'den-canonical-arm64-ed74458bd47afc6a-20261017/arm64-build-02',
    '85c6471a882d71b43d734e00e0428b4a9bfa081d816ac7698cc18d5e3b510425',
    'c12-canonical-capture-bridge',
    '7b1330a8f4c2a25f8e7c1062c57bb86017610378106b5d10d07c94dfe4b6f23e',258232)


def selected_provider():
    """No argument, environment, discovery or manifest can select a profile."""
    system, machine = platform.system(), platform.machine().lower()
    if system == 'Windows' and machine in ('amd64','x86_64'): return WINDOWS_PROVIDER
    if system == 'Linux' and machine == 'aarch64': return LINUX_ARM64_PROVIDER
    require(False, 'unsupported fixed canonical provider platform')


def _read(filename, maximum):
    require(filename.is_absolute() and not filename.is_symlink() and filename.is_file()
        and not any(p.is_symlink() for p in filename.parents)
        and 0 < filename.stat().st_size <= maximum, 'regular bounded fixed canonical original required')
    raw = filename.read_bytes()
    require(0 < len(raw) <= maximum, 'fixed canonical original size changed')
    return raw


def _sha(raw): return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('ascii')


def verified_provider_export():
    """Read the selected full identity and six pure modules without executing it."""
    profile = selected_provider()
    raw = _read(profile.build/'manifest.json',131072)
    require(_sha(raw) == profile.manifest_sha256, 'fixed canonical manifest changed')
    manifest = json.loads(raw)
    source = profile.build/'source'
    pins = dict(manifest_sha256=profile.manifest_sha256,source_sha256=manifest['source_digest'],
        executable_sha256=manifest['executable']['sha256'],configuration_sha256=manifest['config_sha256'])
    require(profile.matches(pins) and manifest['schema'] == 'c12-canonical-capture-build-1'
        and type(manifest['version']) is int and manifest['version'] == 1 and manifest['abi'] == ABI
        and manifest['base_revision'] == BASE_REVISION
        and manifest['build_id'] == 'c12-canonical-capture-' + SOURCE_SHA256[:16]
        and Path(manifest['source']['dir']) == source and manifest['physical_permission'] is False
        and type(manifest['hardware_operations']) is int and manifest['hardware_operations'] == 0,
        'fixed simulated canonical export required')
    files = manifest['source']['files']
    require(type(files) is dict and len(files) == 49 and _sha(_canonical(files)) == SOURCE_SHA256,
        'fixed canonical source inventory changed')
    executable = manifest['executable']
    require(Path(executable['path']) == profile.build/profile.executable_name
        and type(executable['bytes']) is int and executable['bytes'] == profile.executable_bytes,
        'fixed canonical executable path or size changed')
    binary = _read(profile.build/profile.executable_name,profile.executable_bytes)
    require(len(binary) == profile.executable_bytes and _sha(binary) == profile.executable_sha256,
        'fixed canonical executable changed')
    config = manifest['config']
    require(Path(config['path']) == source/CONFIG and config['sha256'] == CONFIG_SHA256
        and config['file_sha256'] == files[CONFIG] == CONFIG_FILE_SHA256
        and manifest['generated_header_sha256'] == HEADER_SHA256,
        'fixed canonical config or header binding changed')
    config_raw = _read(source/CONFIG,65536)
    require(_sha(config_raw) == CONFIG_FILE_SHA256 and _sha(_canonical(json.loads(config_raw))) == CONFIG_SHA256
        and _sha(_read(source/HEADER,65536)) == HEADER_SHA256,
        'fixed canonical config or header changed')
    originals = {}
    for name, expected in SOURCE_PINS:
        relative = 'tools/' + name + '.py'
        filename = source/relative
        data = _read(filename,256*1024)
        require(files[relative] == expected and _sha(data) == expected, 'pinned canonical pure source changed')
        originals[name] = (filename,data)
    return profile, manifest, originals

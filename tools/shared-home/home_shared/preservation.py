"""Bounded exact-byte source capture; never a complete runtime recovery set.

Linux capture uses descriptor-relative no-follow reads. The portable serializer
writes inert content-addressed blobs, not executable source paths. Fixed content
rejection is conservative, and never a proof that source is publication-safe.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

MAX_FILES = 256
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
SOURCE_SUFFIXES = frozenset({'.py', '.js', '.jsx', '.mjs', '.cjs', '.md', '.html', '.css', '.rs', '.sql', '.sh', '.ps1'})
CONTENT_POLICY = 'conservative-source-content-v1'
_STAT_FIELDS = frozenset({'device', 'inode', 'mode', 'mtime_ns', 'ctime_ns'})
_RESERVED = frozenset({'con', 'prn', 'aux', 'nul'} | {f'{p}{n}' for p in ('com', 'lpt') for n in range(1, 10)})
_DENIED_PARTS = frozenset({'data', 'secrets', 'credentials', 'node_modules', 'backups', 'media', 'recordings'})
_CREDENTIAL_KEY = r'[A-Za-z0-9_.$-]*(?:password|passwd|secret|token|api[_-]?key|credential)[A-Za-z0-9_.$-]*'
_SENSITIVE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r'-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----',
    r'''(?:["']?\b''' + _CREDENTIAL_KEY + r'''["']?\s*[:=]\s*)(["'])(?:(?!\1)[^\r\n])+?\1''',
    r'\bBearer\s+[A-Za-z0-9._~+/=-]{8,}',
    r'\b(?:https?|rtsp)://[^\s/@:]+:[^\s/@]+@',
    r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    r'\b(?:gh[pousr]_|github_pat_|sk-proj-)[A-Za-z0-9_-]{16,}',
    r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}',
    r'(?m)^\s*(?:export\s+)?' + _CREDENTIAL_KEY + r'\s*=\s*[A-Za-z0-9_./+=:-]{8,}\s*(?:#.*)?$',
))


class PreservationError(ValueError):
    """Stable content-free reason for a skipped path or rejected artifact."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def rejection_reason(error):
    """Never forward filesystem/codec exception messages into public receipts."""
    if isinstance(error, PreservationError):
        return error.code
    if isinstance(error, FileNotFoundError):
        return 'missing_source'
    if isinstance(error, PermissionError):
        return 'unreadable_source'
    if isinstance(error, OSError) and error.errno in (errno.ELOOP, errno.ENOTDIR):
        return 'symlink_or_non_directory'
    return 'capture_unavailable'


def _fail(code):
    raise PreservationError(code)


def source_path(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 512 or '\\' in value or ':' in value:
        _fail('excluded_path')
    path = PurePosixPath(value)
    if (path.is_absolute() or str(path) != value or not path.parts
            or any(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_. -]*', part) is None
                   or part.endswith((' ', '.')) or part.split('.')[0].rstrip(' ').lower() in _RESERVED
                   or part.lower() in _DENIED_PARTS for part in path.parts)
            or path.suffix.lower() not in SOURCE_SUFFIXES):
        _fail('excluded_path')
    return path


def _paths(paths):
    if not isinstance(paths, (list, tuple)) or not 0 < len(paths) <= MAX_FILES:
        _fail('invalid_path_list')
    normalized = [str(source_path(path)) for path in paths]
    if len({path.casefold() for path in normalized}) != len(normalized):
        _fail('duplicate_or_colliding_paths')
    return sorted(normalized)


def validate_source_content(data):
    """Reject suspicious/binary content before transport; never silently redact.

    Literal test/example credentials also require review. Environment lookups
    and variable references are not literal credentials. This policy does not
    establish the absence of confidential information in arbitrary source.
    """
    if not isinstance(data, bytes) or len(data) > MAX_FILE_BYTES:
        _fail('oversized_source')
    if b'\0' in data:
        _fail('non_text_source')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        _fail('non_text_source')
    if any(pattern.search(text) for pattern in _SENSITIVE):
        _fail('sensitive_content')


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)


def _metadata(value):
    return {'device': value.st_dev, 'inode': value.st_ino, 'mode': stat.S_IMODE(value.st_mode),
            'mtime_ns': value.st_mtime_ns, 'ctime_ns': value.st_ctime_ns}


def _require_linux():
    if (not sys.platform.startswith('linux') or any(not hasattr(os, key) for key in
            ('O_NOFOLLOW', 'O_DIRECTORY', 'O_CLOEXEC', 'O_NONBLOCK'))
            or os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd
            or os.stat not in os.supports_follow_symlinks):
        _fail('unsupported_capture_platform')


@contextmanager
def _root_descriptor(root):
    """Every absolute root component is opened without following symlinks."""
    _require_linux()
    value = os.fspath(root)
    if (not isinstance(value, str) or len(value) > 4096 or '\0' in value
            or not value.startswith('/') or value.startswith('//')
            or str(PurePosixPath(value)) != value or value == '/'
            or any(part in {'.', '..'} for part in value.split('/'))):
        _fail('invalid_capture_root')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    current = os.open('/', flags)
    try:
        for part in PurePosixPath(value).parts[1:]:
            child = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = child
        yield current
    finally:
        os.close(current)


def _read(root_fd, relative):
    """Only regular, singly linked files; never block on a FIFO or device."""
    parts = source_path(relative).parts
    parent = os.dup(root_fd)
    opened = None
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        for part in parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=parent)
            os.close(parent)
            parent = child
        opened = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        before = os.fstat(opened)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            _fail('non_regular_or_linked_source')
        if not 0 <= before.st_size <= MAX_FILE_BYTES:
            _fail('oversized_source')
        chunks, size = [], 0
        while True:
            chunk = os.read(opened, min(64 * 1024, MAX_FILE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                _fail('oversized_source')
        after = os.fstat(opened)
        named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if _identity(before) != _identity(after) or _identity(after) != _identity(named) or size != after.st_size:
            _fail('unstable_source')
        data = b''.join(chunks)
        validate_source_content(data)
        return data, _metadata(after)
    finally:
        if opened is not None:
            os.close(opened)
        os.close(parent)


def snapshot_sources(root, paths):
    """Two-pass observed stability, not an atomic tree or full runtime backup.

    Caller separately binds checkout revision, staged state, skipped paths and
    inventory. Unsupported platforms fail closed; there is no pathname fallback.
    """
    paths = _paths(paths)
    files, total = [], 0
    with _root_descriptor(root) as root_fd:
        root_identity = os.fstat(root_fd)
        for relative in paths:
            data, metadata = _read(root_fd, relative)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                _fail('total_capture_oversized')
            files.append({'path': relative, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                          'content_base64': base64.b64encode(data).decode('ascii'), 'source_stat': metadata})
        for item in files:
            data, metadata = _read(root_fd, item['path'])
            if (len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']
                    or metadata != item['source_stat']):
                _fail('unstable_source')
        with _root_descriptor(root) as verified_root:
            after = os.fstat(verified_root)
            if (root_identity.st_dev, root_identity.st_ino) != (after.st_dev, after.st_ino):
                _fail('unstable_root')
    return {'version': 1, 'kind': 'explicit-source-capture', 'files': files,
            'content_policy': CONTENT_POLICY, 'stability': 'observed-two-pass',
            'complete_deployed_baseline': False, 'deployment_authorized': False}


def _validate_snapshot(snapshot):
    if (not isinstance(snapshot, dict) or set(snapshot) != {'version', 'kind', 'files', 'content_policy',
            'stability', 'complete_deployed_baseline', 'deployment_authorized'}
            or type(snapshot['version']) is not int or snapshot['version'] != 1
            or snapshot['kind'] != 'explicit-source-capture' or snapshot['content_policy'] != CONTENT_POLICY
            or snapshot['stability'] != 'observed-two-pass'
            or snapshot['complete_deployed_baseline'] is not False or snapshot['deployment_authorized'] is not False
            or not isinstance(snapshot['files'], list) or not 0 < len(snapshot['files']) <= MAX_FILES):
        _fail('invalid_capture_schema')
    validated, paths, total = [], [], 0
    for item in snapshot['files']:
        if not isinstance(item, dict) or set(item) != {'path', 'bytes', 'sha256', 'content_base64', 'source_stat'}:
            _fail('invalid_record_schema')
        relative = str(source_path(item['path']))
        paths.append(relative)
        metadata = item['source_stat']
        if (not isinstance(metadata, dict) or set(metadata) != _STAT_FIELDS
                or any(type(value) is not int for value in metadata.values())
                or not 0 <= metadata['mode'] <= 0o7777
                or any(not 0 <= metadata[key] <= 2**64 - 1 for key in ('device', 'inode'))
                or any(not -(2**63) <= metadata[key] < 2**63 for key in ('mtime_ns', 'ctime_ns'))):
            _fail('invalid_source_metadata')
        if (type(item['bytes']) is not int or not 0 <= item['bytes'] <= MAX_FILE_BYTES
                or not isinstance(item['sha256'], str) or re.fullmatch(r'[0-9a-f]{64}', item['sha256']) is None):
            _fail('invalid_source_size_or_digest')
        encoded = item['content_base64']
        if not isinstance(encoded, str) or len(encoded) > ((MAX_FILE_BYTES + 2) // 3) * 4:
            _fail('oversized_encoding')
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeError):
            _fail('invalid_encoding')
        if base64.b64encode(data).decode('ascii') != encoded:
            _fail('invalid_encoding')
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != item['bytes'] or digest != item['sha256']:
            _fail('checksum_mismatch')
        validate_source_content(data)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            _fail('total_capture_oversized')
        validated.append((relative, digest, data, dict(metadata)))
    _paths(paths)
    return validated


def _write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def save_snapshot(snapshot, destination):
    """Validate everything before creating a new inert private recovery directory.

    A partial directory has no manifest.json and cannot be reused. Manifest
    publication is exclusive after its full bytes and every blob are flushed.
    Source mode metadata is recorded, never applied to inert blobs. Windows
    privacy depends on the operator's destination ACL, not POSIX mode bits.
    """
    validated = _validate_snapshot(snapshot)
    destination = Path(destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    blobs = destination / 'blobs'
    blobs.mkdir(mode=0o700)
    records, written = [], set()
    for relative, digest, data, metadata in validated:
        if digest not in written:
            _write_private(blobs / (digest + '.source'), data)
            written.add(digest)
        records.append({'path': relative, 'bytes': len(data), 'sha256': digest,
                        'source_stat': metadata, 'blob': 'blobs/' + digest + '.source'})
    manifest = {'version': 1, 'kind': 'explicit-source-capture', 'files': records,
                'content_policy': CONTENT_POLICY, 'stability': 'observed-two-pass',
                'complete_deployed_baseline': False, 'deployment_authorized': False,
                'private_source_review_required': True}
    pending = destination / '.manifest.pending'
    _write_private(pending, (json.dumps(manifest, indent=2, allow_nan=False) + '\n').encode('utf-8'))
    os.link(pending, destination / 'manifest.json')
    pending.unlink()
    if sys.platform.startswith('linux'):
        for path in (blobs, destination, destination.parent):
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    return manifest

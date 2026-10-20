"""Private exact Store-media retention for the explicit stereo successor.

This spool retains originals, not geometric authority. Importing it performs no
I/O. Only fixed server composition supplies its Store, scope and current guard;
there is no HTTP route, caller-selected source path or live replay fallback.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import threading


class StereoMediaRefusal(ValueError):
    pass


REF_KEYS = {'id', 'sha256', 'size'}
ROW_KEYS = {'id', 'session_id', 'generation', 'size', 'sha256', 'mime_type',
            'metadata', 'created_at', 'source_key'}
BINDING_KEYS = {'scope', 'lab_binding_digest', 'media_generation', 'camera_id'}
ASSESSMENT_BINDING_KEYS = {'original_family', 'assessment_policy_sha256'}
ASSESSMENT_FAMILY = 'existing-api-assessment-1'
BUNDLED_BINDING_KEYS = {'original_family', 'bundle_input_policy_sha256'}
BUNDLED_FAMILY = 'existing-api-bundled-rgb-1'
FAMILIES = {('image/jpeg', 'rgb', 'jpeg'): 256 * 1024,
            ('application/x-npz', 'rgbd_bundle', 'numeric_npz'): 4 * 1024**2,
            ('application/json', 'paired_preview_original', 'home-lab-paired-preview-original-1'): 256 * 1024,
            ('application/octet-stream', 'depth', 'depth_u16_le'): 640 * 400 * 2}
ASSESSMENT_FAMILIES = {('image/jpeg', 'rgb', 'jpeg'): 256 * 1024,
            ('application/x-npz', 'rgbd_bundle', 'numeric_npz'): 4 * 1024**2,
            ('application/json', 'assessment_existing_api_association', None): 256 * 1024,
            ('application/json', 'assessment_existing_api_calibration_original', 'raw_json'): 24 * 1024,
            ('application/octet-stream', 'depth', 'depth_u16_le'): 640 * 400 * 2}
BUNDLED_FAMILIES = {('application/json', 'bundled_rgb_input_original', None): 256 * 1024,
            ('image/jpeg', 'bundled_rgb_clock_anchor', 'jpeg'): 256 * 1024,
            ('application/x-npz', 'bundled_rgb_raw_npz', None): 4 * 1024**2,
            ('application/json', 'bundled_rgb_raw_calibration', None): 24 * 1024,
            ('application/x-npz', 'rgbd_bundle', 'numeric_npz'): 4 * 1024**2,
            ('image/png', 'rgb', 'png'): 1024**2,
            ('application/octet-stream', 'depth', 'depth_u16_le'): 640 * 400 * 2}
MAX_RECORD_BYTES = 80 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
TERMINAL_RETAIN_REASONS = frozenset({
    'stereo_media_byte_limit', 'stereo_media_disk_headroom', 'stereo_media_object_limit',
    'stereo_media_identity_conflict', 'stereo_media_immutable_file_conflict',
    'stereo_media_source_digest_mismatch', 'stereo_media_original_row_mismatch',
    'stereo_media_binary_digest_mismatch', 'stereo_media_record_digest_mismatch'})


def encoded(value):
    """Private original JSON preserves integer nanoseconds without float casts."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def _need(condition, reason):
    if not condition:
        raise StereoMediaRefusal(reason)


def _id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', value) is not None


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _json(raw):
    def unique(items):
        result = {}
        for key, value in items:
            _need(key not in result, 'stereo_media_duplicate_json_field')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(StereoMediaRefusal('stereo_media_finite_json_required')))


def media_ref(item):
    """Describe an actual Store row using the closed producer callback shape."""
    value = {key: item[key] for key in ('id', 'sha256', 'size')}
    return _ref(value)


def _ref(value):
    _need(isinstance(value, dict) and set(value) == REF_KEYS and _id(value['id'])
          and _sha(value['sha256']) and type(value['size']) is int and 0 < value['size'] <= 4 * 1024**2,
          'stereo_media_exact_ref_required')
    return deepcopy(value)


def _refs(values):
    _need(isinstance(values, list) and 0 < len(values) <= 128, 'stereo_media_bounded_refs_required')
    result = [_ref(value) for value in values]
    _need(len({value['id'] for value in result}) == len(result), 'stereo_media_unique_refs_required')
    return result


def _binding(value):
    _need(isinstance(value, dict) and set(value) in (BINDING_KEYS, BINDING_KEYS | ASSESSMENT_BINDING_KEYS,
                                                    BINDING_KEYS | BUNDLED_BINDING_KEYS)
          and (set(value) == BINDING_KEYS
               or set(value) == BINDING_KEYS | ASSESSMENT_BINDING_KEYS
               and value['original_family'] == ASSESSMENT_FAMILY and _sha(value['assessment_policy_sha256'])
               or set(value) == BINDING_KEYS | BUNDLED_BINDING_KEYS
               and value['original_family'] == BUNDLED_FAMILY and _sha(value['bundle_input_policy_sha256']))
          and isinstance(value['scope'], dict)
          and set(value['scope']) == {'site_id', 'room_id', 'session_id', 'domain_epoch'}
          and all(_id(item) for item in value['scope'].values())
          and _sha(value['lab_binding_digest']) and _id(value['camera_id'])
          and type(value['media_generation']) is int and value['media_generation'] >= 0,
          'stereo_media_fixed_binding_required')
    return deepcopy(value)


def _row(value, ref, binding):
    _need(isinstance(value, dict) and set(value) == ROW_KEYS and media_ref(value) == ref
          and value['session_id'] == binding['scope']['session_id']
          and value['generation'] == binding['media_generation'] and type(value['generation']) is int
          and isinstance(value['metadata'], dict)
          and type(value['created_at']) in (int, float) and math.isfinite(value['created_at'])
          and value['created_at'] >= 0 and isinstance(value['source_key'], str) and 0 < len(value['source_key']) <= 512,
          'stereo_media_original_row_mismatch')
    metadata = value['metadata']
    family = (value['mime_type'], metadata.get('stream'), metadata.get('format'))
    families = (BUNDLED_FAMILIES if binding.get('original_family') == BUNDLED_FAMILY else
                ASSESSMENT_FAMILIES if binding.get('original_family') == ASSESSMENT_FAMILY else FAMILIES)
    _need(family in families and value['size'] <= families[family]
          and metadata.get('site_id', binding['scope']['site_id']) == binding['scope']['site_id']
          and metadata.get('camera_id', binding['camera_id']) == binding['camera_id']
          and len(encoded(metadata)) <= 65536 and len(encoded(value)) <= MAX_RECORD_BYTES,
          'stereo_media_family_or_scope_refused')
    return deepcopy(value)


def sync_directory(path):
    """A required durability port; unsupported directory fsync fails closed."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read(path, maximum):
    _need(not path.is_symlink() and path.is_file(), 'stereo_media_missing_or_unsafe_original')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    _need(0 < len(raw) <= maximum, 'stereo_media_original_size_refused')
    return raw


def _verified(directory, entry, binding):
    _need(isinstance(entry, dict) and set(entry) == {'ref', 'record_digest'}
          and _sha(entry['record_digest']), 'stereo_media_manifest_entry_refused')
    ref = _ref(entry['ref'])
    raw = _read(directory / 'records' / (entry['record_digest'] + '.json'), MAX_RECORD_BYTES)
    _need(hashlib.sha256(raw).hexdigest() == entry['record_digest'], 'stereo_media_record_digest_mismatch')
    record = _row(_json(raw), ref, binding)
    data = _read(directory / 'bytes' / (ref['sha256'] + '.bin'), ref['size'])
    _need(len(data) == ref['size'] and hashlib.sha256(data).hexdigest() == ref['sha256'],
          'stereo_media_binary_digest_mismatch')
    return {'record': record, 'data': data}


class LabStereoMedia:
    def __init__(self, directory, *, store, scope, lab_binding_digest, media_generation, current,
                 max_objects=96, max_bytes=32 * 1024**2, min_free_bytes=1,
                 original_family=None, assessment_policy_sha256=None, bundle_input_policy_sha256=None):
        from store import Store
        _need(isinstance(store, Store) and callable(current)
              and isinstance(directory, (str, Path)) and Path(directory).is_absolute()
              and type(max_objects) is int and 1 <= max_objects <= 128
              and type(max_bytes) is int and 1024 <= max_bytes <= 128 * 1024**2
              and type(min_free_bytes) is int and min_free_bytes >= 1,
              'stereo_media_fixed_configuration_required')
        session = store.get_session(scope['session_id'])
        extra = {}
        if original_family == BUNDLED_FAMILY or bundle_input_policy_sha256 is not None:
            _need(assessment_policy_sha256 is None, 'stereo_media_mixed_original_families_refused')
            extra = {'original_family': original_family, 'bundle_input_policy_sha256': bundle_input_policy_sha256}
        elif original_family is not None or assessment_policy_sha256 is not None:
            extra = {'original_family': original_family, 'assessment_policy_sha256': assessment_policy_sha256}
        self.binding = _binding({'scope': scope, 'lab_binding_digest': lab_binding_digest,
                                 'media_generation': media_generation, 'camera_id': session['camera_id'],
                                 **extra})
        _need(session['site_id'] == scope['site_id'], 'stereo_media_store_scope_mismatch')
        self.binding_digest = digest(self.binding)
        self.directory, self.store, self.current = Path(directory), store, current
        self.max_objects, self.max_bytes, self.min_free_bytes = max_objects, max_bytes, min_free_bytes
        self.lock, self.cancel = threading.Lock(), threading.Event()
        self.records, self.bytes = {}, 0
        self.created = self.failed = self.sealed = False
        self.final = None

    def _current(self):
        _need(not self.cancel.is_set() and not self.failed and not self.sealed
              and digest(self.binding) == self.binding_digest and self.current() is True,
              'stereo_media_admission_not_current')
        session = self.store.get_session(self.binding['scope']['session_id'])
        _need(session['state'] == 'recording' and session['generation'] == self.binding['media_generation']
              and session['camera_id'] == self.binding['camera_id']
              and session['site_id'] == self.binding['scope']['site_id']
              and not self.cancel.is_set() and not self.failed and not self.sealed
              and digest(self.binding) == self.binding_digest,
              'stereo_media_session_or_generation_changed')

    def _directory(self):
        if not self.created:
            _need(not self.directory.exists() and not self.directory.is_symlink()
                  and self.directory.parent.is_dir() and not self.directory.parent.is_symlink(),
                  'stereo_media_new_private_directory_required')
            self.directory.mkdir(mode=0o700)
            self.created = True
            for name in ('bytes', 'records'):
                (self.directory / name).mkdir(mode=0o700)
            sync_directory(self.directory)
            sync_directory(self.directory.parent)
        _need(not self.directory.is_symlink()
              and all(not (self.directory / name).is_symlink() for name in ('bytes', 'records')),
              'stereo_media_unsafe_directory')

    def _write(self, path, raw):
        if path.exists():
            _need(_read(path, len(raw)) == raw, 'stereo_media_immutable_file_conflict')
            return
        _need(self.bytes + len(raw) <= self.max_bytes, 'stereo_media_byte_limit')
        _need(shutil.disk_usage(self.directory).free - len(raw) >= self.min_free_bytes,
              'stereo_media_disk_headroom')
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0), 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(raw)
                self.bytes += len(raw)
                stream.flush()
                os.fsync(stream.fileno())
            sync_directory(path.parent)
        except BaseException:
            self.failed = True
            raise

    def retain_media(self, media_refs):
        refs = _refs(media_refs)
        with self.lock:
            self._current()
            before_bytes, before_records, completed = self.bytes, len(self.records), 0
            try:
                self._directory()
                for ref in refs:
                    item = self.store.get_media(self.binding['scope']['session_id'], ref['id'],
                                                generation=self.binding['media_generation'])
                    record = _row({key: deepcopy(item[key]) for key in ROW_KEYS}, ref, self.binding)
                    entry = {'ref': deepcopy(ref), 'record_digest': digest(record)}
                    old = self.records.get(ref['id'])
                    _need(old is None or old == entry, 'stereo_media_identity_conflict')
                    _need(old is not None or len(self.records) < self.max_objects, 'stereo_media_object_limit')
                    data = _read(self.store.media_path(self.binding['scope']['session_id'], ref['id']), ref['size'])
                    _need(len(data) == ref['size'] and hashlib.sha256(data).hexdigest() == ref['sha256'],
                          'stereo_media_source_digest_mismatch')
                    self._write(self.directory / 'bytes' / (ref['sha256'] + '.bin'), data)
                    self._write(self.directory / 'records' / (entry['record_digest'] + '.json'), encoded(record))
                    _verified(self.directory, entry, self.binding)
                    self.records[ref['id']] = entry
                    completed += 1
                # Durability never preserves a retired session or a Stop that
                # overtook retention. Bytes remain available for final evidence.
                self._current()
                return {'status': 'retained', 'binding_digest': self.binding_digest, 'media_refs': refs}
            except BaseException as error:
                # A partially durable request cannot later be hidden by sealing
                # just the preceding successful subset. Fully retained inputs
                # overtaken by Stop remain historical originals, not selection.
                partial = (completed < len(refs)
                           and (self.bytes != before_bytes or len(self.records) != before_records))
                if (partial or isinstance(error, OSError)
                        or isinstance(error, StereoMediaRefusal) and str(error) in TERMINAL_RETAIN_REASONS):
                    self.failed = True
                raise

    def media_refs(self):
        """Snapshot ALL acknowledged durable entries, including unused inputs.

        This is historical metadata only and remains readable after quiesce.
        A failed partial attempt still prevents finalization of this subset.
        """
        with self.lock:
            _need(digest(self.binding) == self.binding_digest, 'stereo_media_fixed_binding_changed')
            return [deepcopy(self.records[key]['ref']) for key in sorted(self.records)]

    def quiesce(self):
        self.cancel.set()
        busy = self.lock.locked()
        return {'status': 'unconfirmed' if busy else 'quiescent', 'in_flight': busy}

    def resolve_retained(self, media_ref):
        """Private live verification of an acknowledged immutable original.

        This does not fetch, repair or substitute a Store object. A Stop or
        generation change refuses selection even when historical bytes remain.
        """
        ref = _ref(media_ref)
        with self.lock:
            self._current()
            entry = self.records.get(ref['id'])
            _need(entry is not None and entry['ref'] == ref, 'stereo_media_retained_ref_mismatch')
            try:
                result = _verified(self.directory, entry, self.binding)
            except (OSError, ValueError, KeyError, TypeError):
                self.failed = True
                raise
            self._current()
            return result

    def finalize(self, *, expected_refs):
        self.quiesce()
        if not self.lock.acquire(False):
            return {'status': 'incomplete', 'reason': 'stereo_media_in_flight'}
        try:
            _need(self.records and expected_refs, 'stereo_media_no_retained_originals')
            refs = _refs(expected_refs)
            if self.final is not None:
                _need(sorted(refs, key=lambda r: r['id']) == self.final['media_refs'],
                      'stereo_media_expected_refs_changed')
                return deepcopy(self.final['receipt'])
            _need(not self.failed, 'stereo_media_durability_unconfirmed')
            self._directory()
            entries = [self.records[key] for key in sorted(self.records)]
            _need(sorted(refs, key=lambda r: r['id']) == [entry['ref'] for entry in entries],
                  'stereo_media_missing_expected_original')
            for entry in entries:
                _verified(self.directory, entry, self.binding)
            manifest = {'schema': 'home-lab-stereo-media-manifest-1', 'version': 1,
                        'binding': deepcopy(self.binding), 'binding_digest': self.binding_digest,
                        'objects': entries}
            raw = encoded(manifest)
            _need(len(raw) <= MAX_MANIFEST_BYTES, 'stereo_media_manifest_limit')
            self._write(self.directory / 'manifest.json', raw)
            receipt = {'status': 'finalized', 'binding_digest': self.binding_digest,
                       'manifest_digest': hashlib.sha256(raw).hexdigest(), 'objects': len(entries), 'bytes': self.bytes}
            self.sealed = True
            self.final = {'media_refs': deepcopy(refs), 'receipt': receipt}
            self.final['media_refs'].sort(key=lambda r: r['id'])
            return deepcopy(receipt)
        except (OSError, ValueError, KeyError, TypeError) as error:
            return {'status': 'incomplete', 'reason': str(error)}
        finally:
            self.lock.release()


class SealedStereoMedia:
    """Read-only resolver after complete manifest and binary verification."""
    def __init__(self, directory, *, expected_binding, expected_refs, manifest_digest):
        self.directory = Path(directory)
        self.binding = _binding(expected_binding)
        _need(self.directory.is_absolute() and not self.directory.is_symlink()
              and all(not (self.directory / name).is_symlink() for name in ('bytes', 'records'))
              and _sha(manifest_digest), 'stereo_media_replay_binding_required')
        raw = _read(self.directory / 'manifest.json', MAX_MANIFEST_BYTES)
        _need(hashlib.sha256(raw).hexdigest() == manifest_digest, 'stereo_media_manifest_digest_mismatch')
        manifest = _json(raw)
        _need(isinstance(manifest, dict) and set(manifest) == {'schema', 'version', 'binding', 'binding_digest', 'objects'}
              and manifest['schema'] == 'home-lab-stereo-media-manifest-1'
              and type(manifest['version']) is int and manifest['version'] == 1
              and manifest['binding'] == self.binding and manifest['binding_digest'] == digest(self.binding)
              and isinstance(manifest['objects'], list) and 0 < len(manifest['objects']) <= 128,
              'stereo_media_sealed_manifest_required')
        self.entries = {}
        for entry in manifest['objects']:
            _verified(self.directory, entry, self.binding)
            identity = entry['ref']['id']
            _need(identity not in self.entries, 'stereo_media_duplicate_original')
            self.entries[identity] = deepcopy(entry)
        _need(sorted(_refs(expected_refs), key=lambda r: r['id'])
              == sorted((entry['ref'] for entry in self.entries.values()), key=lambda r: r['id']),
              'stereo_media_missing_expected_original')
        self.manifest_digest = manifest_digest

    def resolve_media(self, media_ref):
        ref = _ref(media_ref)
        entry = self.entries.get(ref['id'])
        _need(entry is not None and entry['ref'] == ref, 'stereo_media_replay_ref_mismatch')
        return _verified(self.directory, entry, self.binding)

    __call__ = resolve_media

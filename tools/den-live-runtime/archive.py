"""Owner-local bounded causal archive. One writer; household payloads stay local."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = Path(__file__).resolve().parents[2]
CONTEXT = ROOT / "stack/services/home-context"
if str(CONTEXT) not in sys.path:
    sys.path.insert(0, str(CONTEXT))
from journal import canonical, digest

KINDS = frozenset({"journal_prepare", "journal_commit", "journal_abort", "worker_restore", "worker_cold_start",
                   "worker_cut", "worker_tick", "worker_prepared", "worker_commit_result", "session_end"})
TASK_ORIGINAL_KIND = "room_task_assessment_original"
TASK_ORIGINAL_FIELDS = frozenset({"fixture_id", "assessment", "assessment_sha256", "profile_sha256", "room_commit_ref"})
TASK_ORIGINAL_FIELDS_V2 = TASK_ORIGINAL_FIELDS | {"schema", "version", "path_witness", "assessor_ref"}
ROOM_COMMIT_REF_FIELDS = frozenset({"configuration_digest", "manifest_digest", "worker_version", "room_output_schema",
    "commit_id", "commit_body_sha256", "decision_sha256", "room_output_revision_id", "room_output_sha256"})
SECRET_KEYS = frozenset({"token", "token_file", "authorization", "password", "cookie", "client_key", "jpeg", "image_base64", "raw_depth"})


class ArchiveRefusal(ValueError):
    pass


def validate_task_assessment_original(data):
    """Validate recorded original material, never confer execution authority."""
    def pin(value):
        return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)
    successor = type(data) is dict and set(data) == TASK_ORIGINAL_FIELDS_V2
    if (type(data) is not dict or set(data) not in (TASK_ORIGINAL_FIELDS, TASK_ORIGINAL_FIELDS_V2)
            or successor and (data['schema'] != 'home-room-task-assessment-original-2'
                or type(data['version']) is not int or data['version'] != 2)
            or type(data['fixture_id']) is not str or not 0 < len(data['fixture_id']) <= 160
            or type(data['assessment']) is not dict or not pin(data['assessment_sha256'])
            or digest(data['assessment']) != data['assessment_sha256'] or not pin(data['profile_sha256'])
            or type(data['room_commit_ref']) is not dict or set(data['room_commit_ref']) != ROOM_COMMIT_REF_FIELDS):
        raise ArchiveRefusal('archive_task_original_binding_refused')
    ref = data['room_commit_ref']
    if (any(not pin(ref[k]) for k in ('configuration_digest', 'manifest_digest', 'commit_body_sha256',
                                     'decision_sha256', 'room_output_sha256'))
            or ref['worker_version'] != 'home-live-context-worker-12.0.0'
            or ref['room_output_schema'] != 'home-live-room-output-1'
            or any(type(ref[k]) is not str or not 0 < len(ref[k]) <= 160 for k in ('commit_id', 'room_output_revision_id'))):
        raise ArchiveRefusal('archive_task_original_binding_refused')
    _safe(data)
    request = data['assessment']
    if (request.get('schema') != 'home-fixture-retained-angle-request-2'
            or type(request.get('version')) is not int or request['version'] != 2
            or type(request.get('assessment')) is not dict
            or request['assessment'].get('profile_sha256') != data['profile_sha256']
            or type(request.get('retained_aim_ref')) is not dict
            or request['retained_aim_ref'].get('room_commit_ref') != ref
            or len(canonical(data).encode('utf-8')) > 512 * 1024):
        raise ArchiveRefusal('archive_task_original_binding_refused')
    if successor:
        from room_retained_task_assessor import validate_archived_path_witness
        try:
            validate_archived_path_witness(data)
        except (ValueError, KeyError, TypeError, OverflowError):
            raise ArchiveRefusal('archive_task_path_original_binding_refused') from None
    elif (type(request['assessment'].get('assessment_id')) is str
          and request['assessment']['assessment_id'].startswith('retained-path-')):
        raise ArchiveRefusal('archive_task_path_original_required')
    return data


def _safe(value, depth=0):
    if depth > 24:
        raise ArchiveRefusal("archive_depth_limit")
    if isinstance(value, dict):
        if set(value) & SECRET_KEYS:
            raise ArchiveRefusal("archive_secret_or_media_refused")
        for item in value.values():
            _safe(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _safe(item, depth + 1)
    elif not (value is None or type(value) in {str, int, float, bool}):
        raise ArchiveRefusal("archive_json_required")


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ArchiveRefusal("archive_duplicate_key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ArchiveRefusal("archive_finite_required")))


class Recorder:
    def __init__(self, filename, manifest, *, max_records, max_bytes, min_free_bytes):
        assessed = (type(manifest) is dict and manifest.get('schema') == 'home-live-session-manifest-2'
                    and type(manifest.get('version')) is int and manifest['version'] == 2)
        manifest_version = 2 if assessed else 1
        if (not Path(filename).is_absolute() or not Path(filename).parent.is_dir()
                or type(max_records) is not int or not 2 <= max_records <= 16384
                or type(max_bytes) is not int or not 1024 <= max_bytes <= 64 * 1024 * 1024
                or type(min_free_bytes) is not int or min_free_bytes < 1
                or not isinstance(manifest, dict) or manifest.get("schema") != "home-live-session-manifest-" + str(manifest_version)
                or manifest.get("version") != manifest_version
                or assessed and (set(manifest) != {'schema', 'version', 'session_ref', 'configuration_digest',
                    'source_bundle_digest', 'worker_config', 'worker_description', 'runtime_profile'}
                    or manifest['runtime_profile'] != 'home-live-runtime-config-3')):
            raise ArchiveRefusal("archive_explicit_configuration_required")
        _safe(manifest)
        self.path, self.manifest = Path(filename), _json(canonical(manifest))
        self.task_originals = assessed
        self.max_records, self.max_bytes, self.min_free_bytes = max_records, max_bytes, min_free_bytes
        self.lock = threading.RLock()
        self.seq, self.bytes, self.previous = 0, 0, None
        self.failed, self.closed, self.ended = None, False, False
        self.identities = {}
        self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0), 0o600)
        try:
            self._append({"kind": "session_manifest", "data": {"manifest": self.manifest,
                "limits": {"max_records": max_records, "max_bytes": max_bytes, "min_free_bytes": min_free_bytes}}})
        except BaseException:
            os.close(self.fd)
            self.closed = True
            raise

    def _append(self, record):
        if self.failed or self.closed or self.ended:
            raise ArchiveRefusal(self.failed or "archive_closed")
        _safe(record)
        entry = {"schema": "home-live-session-record-1", "version": 1, "seq": self.seq,
                 "previous_digest": self.previous, "record": record}
        entry["digest"] = digest(entry)
        raw = (canonical(entry) + "\n").encode()
        if self.seq >= self.max_records or self.bytes + len(raw) > self.max_bytes:
            self.failed = "archive_budget_exhausted"
            raise ArchiveRefusal(self.failed)
        if shutil.disk_usage(self.path.parent).free - len(raw) < self.min_free_bytes:
            self.failed = "archive_storage_headroom"
            raise ArchiveRefusal(self.failed)
        try:
            offset = 0
            while offset < len(raw):
                count = os.write(self.fd, raw[offset:])
                if count <= 0:
                    raise OSError("short archive write")
                offset += count
            os.fsync(self.fd)
        except OSError:
            self.failed = "archive_write_unconfirmed"
            raise ArchiveRefusal(self.failed) from None
        self.seq += 1
        self.bytes += len(raw)
        self.previous = entry["digest"]
        return {"schema": "home-live-recorded-1", "version": 1, "seq": entry["seq"], "digest": entry["digest"]}

    def record(self, record, *, record_id=None):
        if (not isinstance(record, dict) or set(record) != {"kind", "data"}
                or record["kind"] not in KINDS and not (self.task_originals and record['kind'] == TASK_ORIGINAL_KIND)):
            raise ArchiveRefusal("archive_record_kind_refused")
        if record['kind'] == TASK_ORIGINAL_KIND:
            validate_task_assessment_original(record['data'])
            if record_id != 'room-task-assessment:' + digest(record['data']):
                raise ArchiveRefusal('archive_task_original_exact_identity_required')
        with self.lock:
            if record_id is not None:
                if not isinstance(record_id, str) or not 1 <= len(record_id) <= 160:
                    raise ArchiveRefusal("archive_record_identity_invalid")
                prior = self.identities.get(record_id)
                if prior:
                    if prior[0] != digest(record):
                        raise ArchiveRefusal("archive_record_identity_conflict")
                    return dict(prior[1])
            receipt = self._append(record)
            if record_id is not None:
                self.identities[record_id] = (digest(record), receipt)
            if record["kind"] == "session_end":
                self.ended = True
            return receipt

    def status(self):
        with self.lock:
            return {"schema": "home-live-recorder-status-1", "version": 1,
                    "status": "incomplete" if self.failed else "drained" if self.ended else "ready" if not self.closed else "incomplete",
                    "reason": self.failed, "records": self.seq, "bytes": self.bytes, "last_digest": self.previous,
                    "manifest_digest": digest(self.manifest), "physical_authority": False}

    def drain(self, terminal):
        with self.lock:
            if not self.closed:
                try:
                    if not self.ended:
                        self.record({"kind": "session_end", "data": terminal})
                except ArchiveRefusal:
                    pass
                os.close(self.fd)
                self.closed = True
            result = self.status()
            if terminal.get("status") != "complete" and result["status"] == "drained":
                result["status"] = "incomplete"
            return result


class RecorderRPC:
    """Private write-only loopback capability shared with the actual Node worker."""
    def __init__(self, recorder):
        self.recorder, self.token = recorder, secrets.token_hex(32)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, *_):
                pass

            def do_POST(self):
                status, value = 503, {"error": "recorder_unavailable"}
                try:
                    supplied = self.headers.get_all("Authorization", [])
                    if len(supplied) != 1 or not hmac.compare_digest(supplied[0], "Bearer " + owner.token):
                        status = 401
                        raise ArchiveRefusal("recorder_credential_denied")
                    if self.path != "/record" or self.headers.get("Transfer-Encoding"):
                        raise ArchiveRefusal("recorder_route_denied")
                    lengths = self.headers.get_all("Content-Length", [])
                    if len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= 1024 * 1024:
                        raise ArchiveRefusal("recorder_request_bound")
                    data = _json(self.rfile.read(int(lengths[0])))
                    if not isinstance(data, dict) or set(data) != {"id", "record"}:
                        raise ArchiveRefusal("recorder_request_invalid")
                    value = owner.recorder.record(data["record"], record_id=data["id"])
                    status = 200
                except (ValueError, OSError) as error:
                    value = {"error": str(error) if isinstance(error, ArchiveRefusal) else "recorder_unavailable"}
                raw = canonical(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.server.timeout = 2
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)

    def start(self):
        self.thread.start()
        return {"service_url": "http://127.0.0.1:" + str(self.server.server_port), "token": self.token}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

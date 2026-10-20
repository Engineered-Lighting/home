"""Owner-approved limited camera admission; not global LA resource isolation.

Only the pinned Victoria interactive camera credential is accepted. Metadata is
durable; images and captions remain volatile. Uncertain upstream execution is
never retried or released by expiry/restart. This deliberately does not relax
the existing common-admission implementation or its attestation requirements.
"""
import json
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import sys

from .admission import (_LifetimeLock, AdmissionClosed, AdmissionError,
                        CapacityExceeded, RequestConflict)
from .gateway import validate_inference_request, verify_snapshot_image


REPLY_WAIT_SECONDS = 8      # upper bound of a synchronous reply (S0.4)
REPLY_MARGIN_SECONDS = .5   # a synchronous reply leaves this much of the request deadline


class BeforeDispatchRejected(RuntimeError):
    """Trusted backend proved rejection before starting the model transport."""


def bounded_reply_wait(value):
    if type(value) not in (int, float) or not 0 <= value <= REPLY_WAIT_SECONDS:
        raise ValueError('reply wait must be 0-%d seconds' % REPLY_WAIT_SECONDS)
    return float(value)


class CameraPilot:
    legacy_backend = None

    def __init__(self, path, *, binding, backend, model_revision, clock=time.time,
                 image_validator=verify_snapshot_image, free_space_floor=1024**3, reply_wait_seconds=0):
        if binding.site_id != 'victoria' or dict(binding.cameras) != {'den_oak': 'den'} or not binding.allow_interactive:
            raise ValueError('fixed Victoria interactive binding required')
        # 0 replies with the reservation only (status polling); the service
        # composes REPLY_WAIT_SECONDS so a fast answer arrives in the same 202.
        self.reply_wait_seconds = bounded_reply_wait(reply_wait_seconds)
        self.binding, self.backend, self.model_revision = binding, backend, model_revision
        self.clock, self.image_validator = clock, image_validator
        self.path, self.free_space_floor = Path(path), free_space_floor
        self.lock, self.closed, self.results, self.worker = threading.RLock(), False, {}, None
        self.controller = self  # Transport failures close this pilot only.
        self.owner = _LifetimeLock(Path(str(path) + '.lock'))
        try:
            self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript('''
              CREATE TABLE IF NOT EXISTS pilot_binding(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS pilot_requests(
                id TEXT PRIMARY KEY, digest TEXT NOT NULL, accepted REAL NOT NULL,
                deadline REAL NOT NULL, state TEXT NOT NULL, outcome TEXT);
              CREATE UNIQUE INDEX IF NOT EXISTS pilot_one_active ON pilot_requests((1)) WHERE state!='terminal';
            ''')
            identity = json.dumps([binding.site_id, binding.device_id, binding.consumer_id,
                                   dict(binding.cameras), binding.configuration_revision, model_revision], sort_keys=True)
            previous = self.db.execute('SELECT value FROM pilot_binding WHERE id=1').fetchone()
            if previous and previous[0] != identity:
                raise AdmissionClosed('pilot identity changed; reconciliation required')
            self.db.execute('INSERT OR IGNORE INTO pilot_binding VALUES(1,?)', (identity,))
            self.db.execute("UPDATE pilot_requests SET state='indeterminate',outcome='dispatcher_restart' WHERE state!='terminal'")
        except Exception:
            if hasattr(self, 'db'):
                self.db.close()
            self.owner.close()
            raise

    def close_admission(self, reason=None):
        with self.lock:
            self.closed = True

    def readiness(self):
        with self.lock:
            active = self.db.execute("SELECT 1 FROM pilot_requests WHERE state!='terminal'").fetchone()
            return {'ready': not self.closed and not active, 'mode': 'limited-camera-pilot'}

    def _authorized(self, binding):
        if binding != self.binding:
            raise AdmissionError('camera credential mismatch')

    def status(self, request_id, binding):
        self._authorized(binding)
        with self.lock:
            row = self.db.execute('SELECT * FROM pilot_requests WHERE id=?', (request_id,)).fetchone()
            if row is None:
                raise AdmissionError('unknown request')
            result = dict(version=1, request_id=request_id, site_id='victoria', status=row['state'], outcome=row['outcome'])
            cached = self.results.get(request_id)
            if cached and self.clock() < cached['valid_until']:
                result['observation'] = dict(cached)
            return result

    def submit(self, payload, binding):
        self._authorized(binding)
        request = validate_inference_request(payload, binding, self.clock(), max_output_tokens=128, enforce_freshness=False)
        if request.task_class != 'interactive':
            raise AdmissionError('pilot accepts explicit requests only')
        with self.lock:
            old = self.db.execute('SELECT digest FROM pilot_requests WHERE id=?', (request.request_id,)).fetchone()
            if old:
                if old[0] != request.digest:
                    raise RequestConflict('request content changed')
                return self.status(request.request_id, binding)
            now = self.clock()
            if self.closed:
                raise AdmissionClosed('pilot admission closed')
            validate_inference_request(payload, binding, now, max_output_tokens=128)
            active = self.db.execute("SELECT 1 FROM pilot_requests WHERE state!='terminal'").fetchone()
            last = self.db.execute('SELECT MAX(accepted) FROM pilot_requests').fetchone()[0]
            if active or (last is not None and now - last < 30):
                raise CapacityExceeded('pilot busy or cooling down')
            if shutil.disk_usage(self.path.parent).free < self.free_space_floor:
                self.closed = True
                raise AdmissionClosed('pilot storage floor')
            # Decode before reserving or sending anything upstream.
            self.image_validator(request.image)
            if self.clock() >= request.request_deadline:
                raise AdmissionError('capture expired before admission')
            # Metadata retention is 30 days; never delete unresolved reservations.
            self.db.execute("DELETE FROM pilot_requests WHERE state='terminal' AND accepted<?", (now-30*86400,))
            if self.db.execute('SELECT COUNT(*) FROM pilot_requests').fetchone()[0] >= 100000:
                raise AdmissionClosed('pilot ledger full')
            self.results.clear()
            self.db.execute('INSERT INTO pilot_requests VALUES(?,?,?,?,?,NULL)',
                            (request.request_id, request.digest, now, request.request_deadline, 'dispatching'))
            # Persisted reservation precedes thread creation and all upstream bytes.
            worker = self.worker = threading.Thread(target=self._execute, args=(request,), daemon=True)
            try:
                worker.start()
            except Exception:
                self.closed = True
                raise AdmissionClosed('pilot dispatch unavailable') from None
            pending = self.status(request.request_id, binding)
        # Outside the lock: the worker takes it to record its outcome.
        return self._reply(worker, request, binding, pending)

    def _reply(self, worker, request, binding, pending):
        """Synchronous reply: wait for this request's own worker for at most
        min(deadline - now - margin, reply_wait_seconds), then re-read its row.

        Never raises: an exception here would reach the ingress worker and
        close admission (ingress.py:196-203). On a timeout or any error the
        reservation-time proof is returned and status polling recovers.
        """
        try:
            wait = min(request.request_deadline - self.clock() - REPLY_MARGIN_SECONDS, self.reply_wait_seconds)
            if not wait > 0:
                return pending
            worker.join(wait)
            if worker.is_alive():
                return pending
            return self.status(request.request_id, binding)
        except Exception:
            return pending

    def _execute(self, request):
        try:
            # A complete validated response is application-level completion for
            # this limited pilot, not evidence of global GPU isolation.
            text = self.backend(request)
            if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                raise ValueError('invalid model response')
            with self.lock:
                late = self.clock() >= request.request_deadline
                if not late:
                    keys = ('site_id','device_id','camera_id','room_id','producer_session','producer_sequence',
                            'occupancy_generation','capture_session','frame_id','configuration_revision')
                    observation = {key: getattr(request, key) for key in keys}
                    observation.update(description=text, observed_at=request.captured_at, valid_until=request.valid_until,
                                       uncertainty_seconds=request.clock_uncertainty_seconds, model_revision=self.model_revision)
                    self.results[request.request_id] = observation
                self.db.execute("UPDATE pilot_requests SET state='terminal',outcome=? WHERE id=?",
                                ('completed_after_deadline' if late else 'completed', request.request_id))
        except BeforeDispatchRejected:
            with self.lock:
                self.db.execute("UPDATE pilot_requests SET state='terminal',outcome='preflight_rejected' WHERE id=?",
                                (request.request_id,))
        except Exception as error:
            with self.lock:
                try:
                    print('camera pilot execution unresolved: '+type(error).__name__,file=sys.stderr,flush=True)
                    self.db.execute("UPDATE pilot_requests SET state='indeterminate',outcome='upstream_outcome_unknown' WHERE id=?",
                                    (request.request_id,))
                finally:
                    self.closed = True

    def close(self):
        self.close_admission()
        if self.worker and self.worker.ident is not None:
            self.worker.join(timeout=20)
            if self.worker.is_alive():
                return False
        with self.lock:
            self.db.close()
            self.owner.close()
        return True

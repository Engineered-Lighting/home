"""Durable, single-worker lifecycle; launcher is fixed by trusted composition."""
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid


class LifecycleError(Exception):
    def __init__(self, reason, status=409):
        self.reason, self.status = reason, status


# Content-free gate identities a host adapter may prove; thresholds live there.
PREFLIGHT_REASONS=frozenset({'preflight_memory','preflight_vram','preflight_thermal','preflight_disk',
    'preflight_failed_unit','preflight_kernel_fault','preflight_model_health','preflight_source',
    'preflight_worker_active','preflight_worker_readiness'})


class PreflightRejected(Exception):
    """Host adapter proves rejection occurred before any launch attempt."""
    def __init__(self,*args,reason=None):
        super().__init__(*args)
        self.reason=reason if reason in PREFLIGHT_REASONS else 'preflight_rejected'


class Lifecycle:
    def __init__(self, database, launcher, *, clock=time.time, site_id='echo', epoch_prefix='la-live-', lease_seconds=115):
        if {'echo':'la-live-','victoria':'jetson-live-'}.get(site_id)!=epoch_prefix:
            raise ValueError('fixed lifecycle host binding required')
        if type(lease_seconds) is not int or lease_seconds not in (115,175,355):
            raise ValueError('Bounded commissioning lease required')
        self.lease_seconds=lease_seconds
        self.site_id,self.epoch_prefix=site_id,epoch_prefix
        self.launcher, self.clock = launcher, clock
        self.lock = threading.RLock()
        self.inspect_lock = threading.Lock()
        self.owner_file = open(str(database)+'.owner','a+b')
        try:
            if os.name=='nt':
                import msvcrt
                self.owner_file.seek(0);self.owner_file.write(b'0');self.owner_file.flush();self.owner_file.seek(0)
                msvcrt.locking(self.owner_file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.owner_file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except Exception:
            self.owner_file.close();raise
        self.closed = False
        self.thread = None
        self.db = sqlite3.connect(database, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS host_binding (site TEXT, prefix TEXT)')
        binding=self.db.execute('SELECT site,prefix FROM host_binding').fetchone()
        if binding and tuple(binding)!=(site_id,epoch_prefix):
            self.db.close();self.owner_file.close();raise ValueError('lifecycle host binding changed')
        if not binding:self.db.execute('INSERT INTO host_binding VALUES(?,?)',(site_id,epoch_prefix))
        self.db.execute('''CREATE TABLE IF NOT EXISTS leases (
            request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, epoch TEXT NOT NULL,
            state TEXT NOT NULL, expires REAL NOT NULL, invocation TEXT, profile TEXT, reason TEXT)''')
        if 'variant' not in {r[1] for r in self.db.execute('PRAGMA table_info(leases)')}:
            self.db.execute("ALTER TABLE leases ADD COLUMN variant TEXT NOT NULL DEFAULT 'detector'")
        self.db.execute('CREATE TABLE IF NOT EXISTS lease_reconciliations (request_id TEXT PRIMARY KEY, checked_at REAL NOT NULL, evidence TEXT NOT NULL)')
        # Preserve a durable stop request across supervisor restart. Replacing
        # stopping with indeterminate could incorrectly restore a ready worker.
        self.db.execute("UPDATE leases SET state='indeterminate',reason='supervisor_restarted' WHERE state IN ('starting','ready')")

    @staticmethod
    def request_id(value):
        if not isinstance(value,str):
            raise LifecycleError('invalid_request',400)
        try:
            if str(uuid.UUID(value)) != value: raise ValueError()
        except ValueError:
            raise LifecycleError('invalid_request',400) from None
        return value

    def _row(self, request_id):
        return self.db.execute('SELECT * FROM leases WHERE request_id=?',(request_id,)).fetchone()

    def _receipt(self,row):
        return dict(version=1,request_id=row['request_id'],session_id=row['session_id'],site_id=self.site_id,
                    state=row['state'],engine_epoch=row['epoch'],expires_at=row['expires'],
                    profile=json.loads(row['profile']) if row['profile'] else None,reason=row['reason'],variant=row['variant'])

    def start(self, request_id, session_id, variant='detector'):
        self.request_id(request_id)
        if variant not in {'detector','pose'}:raise LifecycleError('invalid_variant',400)
        if not isinstance(session_id,str) or re.fullmatch('[a-f0-9]{32}',session_id) is None:
            raise LifecycleError('invalid_session',400)
        with self.lock:
            prior=self._row(request_id)
            if prior:
                if prior['session_id'] != session_id or prior['variant'] != variant: raise LifecycleError('request_conflict')
                return self._receipt(prior)
            if self.closed or self.db.execute("SELECT 1 FROM leases WHERE state NOT IN ('stopped','failed')").fetchone():
                raise LifecycleError('worker_busy')
            if self.db.execute('SELECT count(*) FROM leases').fetchone()[0]>=10000:
                raise LifecycleError('ledger_capacity',503)
            epoch=self.epoch_prefix+uuid.uuid4().hex
            expires=self.clock()+self.lease_seconds
            # Full-sync commit precedes the only launcher invocation.
            self.db.execute('INSERT INTO leases VALUES(?,?,?,\'starting\',?,NULL,NULL,NULL,?)',
                            (request_id,session_id,epoch,expires,variant))
            self.thread=threading.Thread(target=self._launch,args=(request_id,),daemon=True)
            self.thread.start()
            return self._receipt(self._row(request_id))

    def _launch(self, request_id):
        with self.lock:
            row=self._row(request_id)
        try:
            invocation,profile=(self.launcher.start(row['epoch'],row['expires']) if row['variant']=='detector'
                                else self.launcher.start(row['epoch'],row['expires'],variant=row['variant']))
            if not invocation or profile.get('engine_epoch')!=row['epoch']:
                raise RuntimeError('unproven launch identity')
            with self.lock:
                self.db.execute('UPDATE leases SET invocation=?,profile=? WHERE request_id=?',
                                (invocation,json.dumps(profile),request_id))
        except PreflightRejected as error:
            with self.lock:
                self.db.execute("UPDATE leases SET state='failed',reason=? WHERE request_id=?",(error.reason,request_id))
            return
        except Exception:
            with self.lock:
                self.db.execute("UPDATE leases SET state='indeterminate',reason='launch_unproven' WHERE request_id=?",(request_id,))
            return
        self._inspect(request_id)

    def _inspect(self, request_id):
        with self.inspect_lock:
            self._inspect_serial(request_id)

    def _inspect_serial(self, request_id):
        with self.lock:
            row=self._row(request_id)
            if not row or row['state'] in ('stopped','failed') or not row['invocation']:
                return
        try:
            identity=getattr(self.launcher,'identity',None)
            if identity is not None and row['state']=='ready' and self.clock()<row['expires'] and not self.closed:
                # Readiness was already proven for this unit. Re-probing would
                # take the worker's single ingress slot and can turn a concurrent
                # execute into 429; the unit identity alone keeps it ready.
                status=identity(row['invocation'])
                status='ready' if status=='running' else status
            else:
                status=self.launcher.inspect(row['invocation'],row['epoch'])
            if status=='ended':
                state,reason='stopped',None
            elif status=='ready' and self.clock()<row['expires'] and row['state']!='stopping':
                state,reason='ready',None
            elif status in ('starting','ready'):
                if row['state']=='stopping' or self.clock()>=row['expires'] or self.closed:
                    self.launcher.stop(row['invocation'],row['epoch'])
                    status=self.launcher.inspect(row['invocation'],row['epoch'])
                    state,reason=('stopped',None) if status=='ended' else ('stopping',None)
                else:
                    state,reason='starting',None
            else:
                state,reason='indeterminate','unit_identity_unproven'
        except Exception:
            state,reason='indeterminate','unit_state_unavailable'
        with self.lock:
            latest=self._row(request_id)
            # A concurrent stop must not be overwritten by an earlier ready read.
            if latest['state']=='stopping' and state in ('ready','starting'):
                return
            self.db.execute('UPDATE leases SET state=?,reason=? WHERE request_id=?',(state,reason,request_id))

    def status(self, request_id):
        self.request_id(request_id)
        self._inspect(request_id)
        with self.lock:
            row=self._row(request_id)
            if row is None: raise LifecycleError('request_not_found',404)
            return self._receipt(row)

    def stop(self, request_id, epoch):
        self.request_id(request_id)
        with self.lock:
            row=self._row(request_id)
            if row is None: raise LifecycleError('request_not_found',404)
            if epoch != row['epoch']: raise LifecycleError('epoch_mismatch')
            if row['state'] not in ('stopped','failed'):
                self.db.execute("UPDATE leases SET state='stopping' WHERE request_id=?",(request_id,))
        return self.status(request_id)

    def reconcile_unlaunched(self, request_id, absence):
        """Operator-only: fail an expired indeterminate lease that never proved a unit.

        absence(epoch) inspects, never starts or stops, the fixed worker unit. Its
        evidence commits with the transition; any doubt keeps the lease blocking.
        The reason claims only present absence: a unit may have run and exited.
        """
        self.request_id(request_id)
        with self.lock:
            row=self._row(request_id)
            if row is None: raise LifecycleError('request_not_found',404)
            # Also a stop requested before launch was proven: stop() sets such a
            # lease 'stopping' and _inspect can never settle it without an invocation.
            if row['state'] not in ('indeterminate','stopping') or row['invocation'] is not None:
                raise LifecycleError('reconcile_not_applicable')
            if self.clock()<row['expires']: raise LifecycleError('lease_not_expired')
            evidence=absence(row['epoch'])
            if not isinstance(evidence,dict) or evidence.get('worker_absent') is not True:
                raise LifecycleError('worker_absence_unproven')
            checked=self.clock()
            record=json.dumps({**evidence,'epoch':row['epoch'],'prior_reason':row['reason'],'checked_at':checked},sort_keys=True)
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.execute('INSERT INTO lease_reconciliations VALUES(?,?,?)',(request_id,checked,record))
                self.db.execute("UPDATE leases SET state='failed',reason='reconciled_worker_absent' WHERE request_id=? AND state IN ('indeterminate','stopping') AND invocation IS NULL",(request_id,))
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK');raise
            return self._receipt(self._row(request_id))

    def tick(self):
        with self.lock:
            ids=[r[0] for r in self.db.execute("SELECT request_id FROM leases WHERE state NOT IN ('stopped','failed')")]
        for request_id in ids: self._inspect(request_id)

    def close(self):
        self.closed=True
        if self.thread: self.thread.join(50)
        if self.thread and self.thread.is_alive(): return False
        self.tick()
        with self.lock:
            if self.db.execute("SELECT 1 FROM leases WHERE state NOT IN ('stopped','failed')").fetchone(): return False
            self.db.close()
            self.owner_file.close()
        return True

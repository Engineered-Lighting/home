"""Offline, operator-only recovery after affirmative engine-stop evidence.

Never imported by an HTTP handler. The maintenance operator must collect the
proof from the local container runtime while the pilot service is stopped.
Original request state is retained in an immutable audit row; no result is
invented and no request is resubmitted.
"""
import hashlib
import json
import sqlite3
from .admission import _LifetimeLock
from pathlib import Path


def reconcile_stopped_engine(path, *, request_id, expected_digest, proof):
    if (proof.get('version') != 1 or proof.get('running') is not False
            or proof.get('pid') != 0 or proof.get('old_processes_gone') is not True
            or not proof.get('container_id') or not proof.get('old_started_at')
            or not proof.get('finished_at') or not proof.get('checked_at')):
        raise ValueError('affirmative stopped-engine proof required')
    encoded=json.dumps(proof,sort_keys=True,separators=(',',':'))
    proof_digest=hashlib.sha256(encoded.encode()).hexdigest()
    lock=_LifetimeLock(Path(str(path)+'.lock'))
    db=None
    try:
        db=sqlite3.connect(path,isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM pilot_requests WHERE id=?',(request_id,)).fetchone()
        if row is None or row['digest']!=expected_digest or row['state']=='terminal':
            raise ValueError('exact unresolved request required')
        db.execute('CREATE TABLE IF NOT EXISTS pilot_recovery_audit(request_id TEXT PRIMARY KEY, original_record TEXT NOT NULL, proof TEXT NOT NULL, proof_sha256 TEXT NOT NULL)')
        db.execute('INSERT INTO pilot_recovery_audit VALUES(?,?,?,?)',
                   (request_id,json.dumps(dict(row),sort_keys=True),encoded,proof_digest))
        db.execute("UPDATE pilot_requests SET state='terminal',outcome='engine_stopped' WHERE id=?",(request_id,))
        db.execute('COMMIT')
        return proof_digest
    finally:
        if db is not None:db.close()
        lock.close()

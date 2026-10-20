import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from home_shared.pilot_recovery import reconcile_stopped_engine


class RecoveryTests(unittest.TestCase):
    def test_requires_proof_and_exact_binding_preserves_unknown_and_never_replays(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'pilot.sqlite'
            with closing(sqlite3.connect(path,isolation_level=None)) as db:
                db.execute('CREATE TABLE pilot_requests(id TEXT PRIMARY KEY,digest TEXT,state TEXT,outcome TEXT)')
                db.execute("INSERT INTO pilot_requests VALUES('request','digest','indeterminate','upstream_outcome_unknown')")
            proof=dict(version=1,running=False,pid=0,old_processes_gone=True,
                       container_id='pinned-container',old_started_at='old',finished_at='ended',checked_at=1)
            for patch in ({'running':True},{'pid':123},{'old_processes_gone':False},{'finished_at':''}):
                with self.assertRaises(ValueError):
                    reconcile_stopped_engine(path,request_id='request',expected_digest='digest',proof={**proof,**patch})
            with self.assertRaises(ValueError):
                reconcile_stopped_engine(path,request_id='request',expected_digest='different',proof=proof)
            digest=reconcile_stopped_engine(path,request_id='request',expected_digest='digest',proof=proof)
            with closing(sqlite3.connect(path,isolation_level=None)) as db:
                self.assertEqual(db.execute('SELECT state,outcome FROM pilot_requests').fetchone(),('terminal','engine_stopped'))
                audit=db.execute('SELECT original_record,proof_sha256 FROM pilot_recovery_audit').fetchone()
                self.assertEqual(json.loads(audit[0])['outcome'],'upstream_outcome_unknown')
                self.assertEqual(audit[1],digest)
            with self.assertRaises(ValueError):
                reconcile_stopped_engine(path,request_id='request',expected_digest='digest',proof=proof)

"""Durable Lab ledger. No inference, network traffic, or automatic media deletion."""
from contextlib import contextmanager, nullcontext
from pathlib import Path
import hashlib
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
import uuid
from ownership import StoreOwnership

# SQLite's own files, measured by stat at every capacity check: every ledger
# write (events, requests, receipts, erasure) changes them by an amount only
# SQLite knows, so they are never estimated.
LEDGER_FILES = ("lab.sqlite3", "lab.sqlite3-wal", "lab.sqlite3-shm", "lab.sqlite3-journal")
RECONCILE_REASONS = ("startup", "session_start", "session_stop", "interval", "initial", "explicit")
# What only the Store writes (the running total's scope) besides SQLite's files.
OWNERSHIP_FILE = ".controller.lock"
OWNED_TREES = ("media", "staging")
OWNED_NAMES = frozenset(LEDGER_FILES) | {OWNERSHIP_FILE, *OWNED_TREES}
# Bound on the live walk of everything else under the root (e.g. derived/).
MAX_UNOWNED_ENTRIES = 4096
_storage_log = logging.getLogger("perception_lab.storage")


def _size(path):
    try:
        return os.lstat(path).st_size
    except FileNotFoundError:
        return 0


class LabError(Exception):
    def __init__(self, message, status=409, code="conflict"):
        super().__init__(message)
        self.status, self.code = status, code


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


# Bound on one events_by_ids read (the JEV v6 policy re-derives at most 32
# timeline frames plus a sidecar's records per request).
EVENTS_BY_IDS_LIMIT = 256


def events_by_ids(db, lock, session_id, ids, kinds):
    """{event id: (kind, payload)} for the given ledger ids of one session and
    kinds: one indexed IN read, bounded. Unknown ids are simply absent."""
    wanted = sorted({int(i) for i in ids if isinstance(i, (int, str)) and str(i).isdigit()})
    if len(wanted) > EVENTS_BY_IDS_LIMIT or not kinds:
        raise ValueError("events_by_ids bound exceeded")
    if not wanted:
        return {}
    marks, kind_marks = ",".join("?"*len(wanted)), ",".join("?"*len(kinds))
    with lock:
        rows = db.execute(f"SELECT id,kind,payload FROM events WHERE session_id=? AND id IN ({marks})"
                          f" AND kind IN ({kind_marks})", (session_id, *wanted, *kinds)).fetchall()
    return {str(row[0]): (row[1], json.loads(row[2])) for row in rows}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise LabError("invalid identifier", 422, "invalid_identifier")
    return value


class Store:
    def __init__(self, settings, clock=time.time):
        self.settings, self.clock = settings, clock
        root = Path(settings.storage_root)
        if root.is_symlink():
            raise ValueError("storage root must not be a symbolic link")
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()
        self.instance_id = uuid.uuid4().hex
        # Acquire before opening SQLite or running recovery. A second process
        # must not classify a healthy controller's work as crashed.
        self.ownership = StoreOwnership(self.root)
        try:
            self._open()
        except BaseException:
            if hasattr(self,"db"):
                self.db.close()
            self.ownership.close()
            raise

    def _open(self):
        settings = self.settings
        self.media_root = self.root / "media"
        self.media_root.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self._allowance = None
        # Exact storage accounting (see storage_usage): bytes of every file
        # under the root except SQLite's files and active recorder staging,
        # kept current under self.lock by each Store write/delete path and
        # reconciled against a full scan (reconcile_storage).
        self._files_bytes = None
        self._active_staging = {}
        self._mutations = 0
        self._scan_error = None
        self.accounting = {"checks": 0, "full_scans": 0, "reconciliations": 0, "drift_corrections": 0,
                           "last_drift_bytes": 0, "last_scan_ms": None, "last_reason": None, "scan_failures": 0,
                           "unowned_entries": 0}
        self.db = sqlite3.connect(self.root / "lab.sqlite3", check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;
          CREATE TABLE IF NOT EXISTS configuration(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, site_id TEXT NOT NULL,
            camera_id TEXT NOT NULL, experiment_id TEXT NOT NULL, name TEXT NOT NULL,
            state TEXT NOT NULL, started_at REAL, ended_at REAL, created_at REAL NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1, generation INTEGER NOT NULL DEFAULT 0,
            request_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, reason TEXT,
            fixture INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id), kind TEXT NOT NULL,
            created_at REAL NOT NULL, payload TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS events_session_id ON events(session_id,id);
          CREATE INDEX IF NOT EXISTS events_session_kind_id ON events(session_id,kind,id);
          CREATE INDEX IF NOT EXISTS events_output_time ON events(session_id,json_extract(payload,'$.source_end_s'))
            WHERE kind IN ('perception.output','runtime.output','analysis.output');
          CREATE TABLE IF NOT EXISTS commands(session_id TEXT NOT NULL, request_id TEXT NOT NULL,
            request_hash TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(session_id,request_id));
          CREATE TABLE IF NOT EXISTS annotations(id TEXT NOT NULL, session_id TEXT NOT NULL REFERENCES sessions(id),
            revision INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL,
            PRIMARY KEY(session_id,id));
          CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, session_id TEXT,
            state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            request_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, parameters TEXT NOT NULL,
            result TEXT, error TEXT);
          CREATE TABLE IF NOT EXISTS media(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
            generation INTEGER NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
            mime_type TEXT NOT NULL, metadata TEXT NOT NULL, created_at REAL NOT NULL,
            source_key TEXT, UNIQUE(session_id,source_key));
          CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS media_deletions(session_id TEXT PRIMARY KEY REFERENCES sessions(id),
            state TEXT NOT NULL, requested_at REAL NOT NULL, completed_at REAL);
          CREATE TABLE IF NOT EXISTS session_erasures(session_id TEXT PRIMARY KEY,
            state TEXT NOT NULL, requested_at REAL NOT NULL, completed_at REAL);
        """)
        prior = self.db.execute("SELECT value FROM configuration WHERE key='site_id'").fetchone()
        if prior and prior[0] != settings.site_id:
            self.db.close()
            raise ValueError("storage belongs to another site")
        self.db.execute("INSERT OR IGNORE INTO configuration VALUES('site_id',?)", (settings.site_id,))
        self.recover()
        # Only resume explicit, durably recorded user deletions. Never infer
        # deletion from an orphan file or retention age.
        for row in self.db.execute("SELECT session_id FROM media_deletions WHERE state='pending'").fetchall():
            self._finish_media_deletion(row[0])
        for row in self.db.execute("SELECT session_id FROM session_erasures WHERE state='pending'").fetchall():
            self._finish_session_erasure(row[0])
        # Startup: the running total begins from one exact full scan. A scan
        # that fails (e.g. a link under the root) leaves writes refused.
        self._reconcile_quietly("startup")

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def close(self):
        with self.lock:
            self.db.close()
            self.ownership.close()

    def recover(self):
        with self.transaction() as db:
            rows = db.execute("SELECT id FROM sessions WHERE state IN ('starting','recording','stopping')").fetchall()
            for row in rows:
                db.execute("UPDATE sessions SET state='indeterminate',revision=revision+1,reason='controller_restart_execution_unknown' WHERE id=?", (row[0],))
                self._event(db, row[0], "recording.indeterminate", {"reason": "controller_restart_execution_unknown"})
            db.execute("UPDATE jobs SET state='indeterminate',updated_at=?,error='controller_restart_execution_unknown' WHERE state IN ('starting','running')", (self.clock(),))
            db.execute("UPDATE jobs SET state='interrupted',updated_at=?,error='controller_restart_unsent' WHERE state='queued'", (self.clock(),))
            # These optional tables may not exist until their runtime is
            # provisioned. Recovery is performed only by the exclusive owner.
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("paired_requests","lab_reasoning_requests","lab_analysis_steps"):
                if table in tables:
                    db.execute(f"UPDATE {table} SET state='indeterminate' WHERE state IN ('dispatching','running')")
                    if table != "lab_analysis_steps":
                        # At most one pending JEV request per session arm (unique index).
                        pending=(db.execute("SELECT id,session_id,arm,generation FROM lab_reasoning_requests WHERE state='pending'").fetchall()
                                 if table=="lab_reasoning_requests" else [])
                        db.execute(f"UPDATE {table} SET state='cancelled' WHERE state='pending'")
                        now=self.clock()
                        for request_id,sid,arm,generation in pending:
                            # Same bounded receipt LiveReasoning writes; best effort,
                            # never for an erased session, never blocking recovery.
                            self.receipt(db,sid,"reasoning.lifecycle",{"request_id":request_id,"arm":arm,"generation":generation,
                                "state":"cancelled","reason":"controller_restart","at":now,"available_at":now})

    def receipt(self, db, sid, kind, payload):
        """Best-effort small receipt inside the caller's transaction.

        A SAVEPOINT isolates the insert: if it fails, only the receipt is rolled
        back, so the state change it describes still commits and the caller's
        own error (if any) is not masked. Erased sessions get no new records.
        Returns the event id, or None when nothing was written.
        """
        db.execute("SAVEPOINT lab_receipt")
        try:
            if db.execute("SELECT 1 FROM session_erasures WHERE session_id=?",(sid,)).fetchone():
                event_id=None
            else:
                event_id=self._event(db,sid,kind,payload)
        except Exception:
            db.execute("ROLLBACK TO lab_receipt")
            event_id=None
        db.execute("RELEASE lab_receipt")
        return event_id

    def _event(self, db, sid, kind, payload):
        if db.execute("SELECT 1 FROM session_erasures WHERE session_id=?",(sid,)).fetchone():
            raise LabError("session erased or erasure pending",410,"session_erased")
        cursor = db.execute("INSERT INTO events(session_id,kind,created_at,payload) VALUES(?,?,?,?)", (sid, kind, self.clock(), encode(payload)))
        return cursor.lastrowid

    def append_event(self, session_id, kind, payload, critical=False):
        self.get_session(session_id)
        if len(encode(payload)) > 256 * 1024:
            raise LabError("event exceeds limit", 413, "too_large")
        with self.transaction() as db:
            # Minimal stop/failure receipts may consume the metadata headroom
            # reserved by ordinary writes; quota must not prevent process stop.
            if not critical:
                self._reserve(len(encode(payload))+65536)
            elif len(encode(payload))>8192:
                raise LabError("critical receipt exceeds bound",413,"too_large")
            return self._event(db, session_id, kind, payload)

    def list_events(self, session_id, after_id=0, limit=1000):
        self.get_session(session_id)
        with self.lock:
            rows = self.db.execute("SELECT * FROM events WHERE session_id=? AND id>? ORDER BY id LIMIT ?", (session_id, after_id, min(max(limit,1),5000))).fetchall()
        result=[]
        used=0
        for row in rows:
            value={**dict(row),"payload":json.loads(row["payload"])}
            size=len(encode(value).encode())
            if result and used+size>1024**2:
                break
            result.append(value); used+=size
        return result

    def timeline_events(self,sid):
        session=self.get_session(sid)
        with self.lock:
            rows=self.db.execute("""SELECT id,kind,created_at,payload FROM events WHERE session_id=?
                AND kind IN ('bookmark','bookmark.added','experiment.activated','pipeline.switch')
                ORDER BY id LIMIT 2001""",(sid,)).fetchall()
        if len(rows)>2000:
            raise LabError("timeline requires a narrower event selection",413,"timeline_limit")
        has_activations=any(row["kind"]=="experiment.activated" for row in rows)
        result=[]
        for row in rows:
            payload=json.loads(row["payload"])
            if row["kind"] in {"bookmark","bookmark.added"}:
                at=payload.get("at_ms")
                projected={"label":str(payload.get("label",""))[:500]}
                kind="bookmark"
            else:
                if row["kind"]=="pipeline.switch":
                    if has_activations or payload.get("outcome",{}).get("state")!="applied":
                        continue
                stamp=payload.get("effective_at",row["created_at"])
                at=(stamp-session["started_at"])*1000 if type(stamp) in (int,float) and session["started_at"] is not None else None
                projected={k:payload[k] for k in ("generation","experiment_id") if k in payload}
                kind="switch"
            if type(at) not in (int,float) or not 0<=at<=self.settings.max_session_seconds*1000:
                continue
            result.append({"id":row["id"],"kind":kind,"t_s":at/1000,"payload":projected})
        return result

    def events_by_ids(self, session_id, ids, kinds):
        """Ledger rows by id for one session (see events_by_ids)."""
        return events_by_ids(self.db, self.lock, session_id, ids, kinds)

    def get_session(self, sid):
        identifier(sid)
        with self.lock:
            if self.db.execute("SELECT 1 FROM session_erasures WHERE session_id=?",(sid,)).fetchone():
                raise LabError("session erased or erasure pending",410,"session_erased")
            row = self.db.execute("SELECT * FROM sessions WHERE id=? AND site_id=?", (sid, self.settings.site_id)).fetchone()
        if row is None:
            raise LabError("session not found", 404, "not_found")
        result = dict(row)
        result.pop("request_hash")
        result["fixture"] = bool(result["fixture"])
        result["recording_state"] = result["state"]
        with self.lock:
            option=self.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='reasoning.setting' ORDER BY id DESC LIMIT 1",(sid,)).fetchone()
        option=json.loads(option[0]) if option else {}
        result["reasoning_enabled"]=option.get("enabled") is True
        result["reasoning_revision"]=option.get("revision",0)
        return result

    def list_sessions(self, limit=200):
        with self.lock:
            ids = self.db.execute("SELECT id FROM sessions WHERE site_id=? AND id NOT IN (SELECT session_id FROM session_erasures) ORDER BY created_at DESC LIMIT ?", (self.settings.site_id,min(max(limit,1),1000))).fetchall()
        sessions = [self.get_session(r[0]) for r in ids]
        # Read-only list summaries (additive fields): label counts by review and
        # clock state, and the owner's bookmark cues. Two grouped reads, not a
        # per-session scan; label payloads stay in the store.
        if sessions:
            wanted = tuple(s["id"] for s in sessions)
            marks = ",".join("?"*len(wanted))
            with self.lock:
                labels = self.db.execute(f"""SELECT session_id,COUNT(*),
                    SUM(json_extract(payload,'$.reviewed') IS 1),
                    SUM(json_extract(payload,'$.origin')='human'),
                    SUM(COALESCE(json_extract(payload,'$.time_basis'),'session')='session'),
                    SUM(json_extract(payload,'$.reviewed') IS 1 AND json_extract(payload,'$.origin')='human'
                        AND COALESCE(json_extract(payload,'$.time_basis'),'session')='session')
                    FROM annotations WHERE deleted=0 AND session_id IN ({marks}) GROUP BY session_id""", wanted).fetchall()
                bookmarks = self.db.execute(f"""SELECT session_id,COUNT(*) FROM events INDEXED BY events_session_kind_id
                    WHERE kind='bookmark' AND session_id IN ({marks}) GROUP BY session_id""", wanted).fetchall()
            counts = {row[0]: row[1:] for row in labels}
            cues = dict(bookmarks)
            for s in sessions:
                total, reviewed, human, session_clock, scorable = (int(v or 0) for v in counts.get(s["id"], (0, 0, 0, 0, 0)))
                s["label_summary"] = {"total": total, "reviewed": reviewed, "human": human,
                                      "session_clock": session_clock, "other_clock": total-session_clock, "scorable": scorable}
                s["bookmark_count"] = int(cues.get(s["id"], 0))
        return sessions

    def create_session(self, camera_id, experiment_id, name, request_id, reasoning_enabled=False):
        if type(reasoning_enabled) is not bool:raise LabError("reasoning_enabled must be boolean",422,"invalid_reasoning")
        for value in (camera_id, experiment_id, request_id):
            identifier(value)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
            raise LabError("name must have 1–160 characters", 422, "invalid_name")
        payload = {"camera_id":camera_id,"experiment_id":experiment_id,"name":name}
        if reasoning_enabled:payload["reasoning_enabled"]=True
        fingerprint = hashlib.sha256(encode(payload).encode()).hexdigest()
        with self.transaction() as db:
            previous = db.execute("SELECT id,request_hash FROM sessions WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if previous[1] != fingerprint:
                    raise LabError("request_id reused with different request")
                return self.get_session(previous[0]), False
            if db.execute("SELECT 1 FROM sessions WHERE camera_id=? AND state IN ('starting','recording','stopping','indeterminate')", (camera_id,)).fetchone():
                raise LabError("camera already has an active recording")
            # Session start: re-establish the running total from a full scan
            # (under the ledger lock, so nothing can race it) before admission.
            self.reconcile_storage("session_start")
            self.check_capacity(self.settings.max_object_bytes)
            sid = uuid.uuid4().hex
            db.execute("INSERT INTO sessions(id,site_id,camera_id,experiment_id,name,state,created_at,request_id,request_hash,fixture) VALUES(?,?,?,?,?,'requested',?,?,?,?)", (sid,self.settings.site_id,camera_id,experiment_id,name,self.clock(),request_id,fingerprint,int(self.settings.developer_fixture_mode)))
            self._event(db,sid,"session.requested",payload)
            self._event(db,sid,"reasoning.setting",{"enabled":reasoning_enabled,"revision":1})
        return self.get_session(sid), True

    def transition(self, sid, state, reason=None):
        allowed = {"requested":{"starting","blocked","stopped","failed"}, "blocked":{"starting","blocked","stopped","failed"}, "starting":{"recording","blocked","failed","stopping","stopped","indeterminate"}, "recording":{"stopping","stopped","interrupted","failed","indeterminate"}, "stopping":{"stopped","failed","interrupted","indeterminate"}, "indeterminate":{"stopping","stopped","failed","indeterminate"}, "stopped":set(), "interrupted":set(), "failed":set()}
        with self.transaction() as db:
            current = self.get_session(sid)
            if state == current["state"]:
                return current
            if state not in allowed.get(current["state"], set()):
                raise LabError("invalid session state transition")
            started = self.clock() if state == "recording" and current["started_at"] is None else current["started_at"]
            ended = self.clock() if state in {"stopped","interrupted","failed"} else current["ended_at"]
            db.execute("UPDATE sessions SET state=?,reason=?,started_at=?,ended_at=?,revision=revision+1 WHERE id=?", (state,reason,started,ended,sid))
            self._event(db,sid,"recording."+state,{"reason":reason})
        if state in {"stopped","interrupted","failed"}:
            # Session stop: reconcile after the terminal state has committed.
            # A scan failure never blocks the stop; it leaves writes refused.
            self._reconcile_quietly("session_stop")
        return self.get_session(sid)

    def requested_stop(self, sid):
        """Controlled reason of the latest recorded stop request, or None."""
        with self.lock:
            row=self.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='recording.stop_requested' ORDER BY id DESC LIMIT 1",(sid,)).fetchone()
        reason=json.loads(row[0]).get("reason") if row else None
        return reason if isinstance(reason,str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}",reason) else None

    def recording_sessions(self):
        """Sessions now recording, without reading every session's record."""
        with self.lock:
            ids=self.db.execute("SELECT id FROM sessions WHERE site_id=? AND state='recording' AND id NOT IN (SELECT session_id FROM session_erasures)",
                                (self.settings.site_id,)).fetchall()
        return [self.get_session(r[0]) for r in ids]

    def command_result(self, sid, request_id, payload, result=None):
        identifier(request_id)
        digest=hashlib.sha256(encode(payload).encode()).hexdigest()
        with self.transaction() as db:
            self.get_session(sid)
            row=db.execute("SELECT request_hash,result FROM commands WHERE session_id=? AND request_id=?",(sid,request_id)).fetchone()
            if row:
                if row[0] != digest:
                    raise LabError("request_id reused with different command")
                return json.loads(row[1])
            if result is not None:
                db.execute("INSERT INTO commands VALUES(?,?,?,?)",(sid,request_id,digest,encode(result)))
        return None

    def save_annotation(self, sid, aid, payload, expected_revision, actor, delete=False):
        self.get_session(sid)
        identifier(aid)
        if type(expected_revision) is not int or expected_revision < 0:
            raise LabError("expected_revision required",422,"invalid_revision")
        if not delete:
            start=payload.get("at_ms")
            end=payload.get("end_ms",start)
            if not isinstance(start,(int,float)) or isinstance(start,bool) or not math.isfinite(start) or start < 0 or start > self.settings.max_session_seconds*1000:
                raise LabError("invalid annotation time",422,"invalid_time")
            if end is not None and (not isinstance(end,(int,float)) or not math.isfinite(end) or end < start or end > self.settings.max_session_seconds*1000):
                raise LabError("invalid annotation interval",422,"invalid_time")
            if payload.get("origin") not in {"human","model"} or not isinstance(payload.get("label"),str) or not 1 <= len(payload["label"]) <= 1000:
                raise LabError("label and explicit origin required",422,"invalid_label")
            if "reviewed" in payload and type(payload["reviewed"]) is not bool:
                raise LabError("reviewed must be an explicit boolean",422,"invalid_label")
            basis=payload.get("time_basis","session")
            if basis not in {"session","encoded_video"}:
                raise LabError("unsupported annotation clock",422,"invalid_time_basis")
            if basis=="encoded_video":
                init=self.get_media(sid,payload.get("video_init_id"))
                if init["metadata"].get("kind")!="encoded_init":
                    raise LabError("encoded initialization reference required",422,"invalid_time_basis")
                with self.lock:
                    rows=self.db.execute("SELECT metadata FROM media WHERE session_id=?",(sid,)).fetchall()
                spans=[json.loads(row[0]) for row in rows]
                spans=[m for m in spans if m.get("kind")=="encoded_segment" and m.get("initialization_id")==init["id"]]
                maximum=max((m.get("end_seconds",0) for m in spans),default=0)*1000
                if end is None or end<=start or end>maximum+0.01:
                    raise LabError("label exceeds recorded video timeline",422,"invalid_time")
            elif "video_init_id" in payload:
                raise LabError("video reference requires encoded video clock",422,"invalid_time_basis")
            if len(encode(payload)) > 16384:
                raise LabError("annotation exceeds limit",413,"too_large")
        with self.transaction() as db:
            if not delete:
                self.check_capacity(len(encode(payload))+65536)
            row=db.execute("SELECT revision,payload FROM annotations WHERE session_id=? AND id=?",(sid,aid)).fetchone()
            actual=row[0] if row else 0
            if actual != expected_revision:
                raise LabError("annotation revision conflict",409,"revision_conflict")
            if delete and row is None:
                raise LabError("annotation not found",404,"not_found")
            value=json.loads(row[1]) if delete else {"reviewed":False,"time_basis":"session",**payload}
            value.update({"id":aid,"session_id":sid,"revision":actual+1,"actor":actor,"updated_at":self.clock(),"deleted":delete})
            db.execute("INSERT INTO annotations VALUES(?,?,?,?,?) ON CONFLICT(session_id,id) DO UPDATE SET revision=excluded.revision,deleted=excluded.deleted,payload=excluded.payload",(aid,sid,actual+1,int(delete),encode(value)))
            self._event(db,sid,"annotation.deleted" if delete else "annotation.saved",value)
        return value

    def list_annotations(self, sid):
        self.get_session(sid)
        with self.lock:
            rows=self.db.execute("SELECT payload FROM annotations WHERE session_id=? AND deleted=0 ORDER BY id",(sid,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def create_job(self, kind, request_id, parameters, session_id=None):
        if kind not in {"analysis","preparation","diagnostic"}:
            raise LabError("unsupported job kind",422,"invalid_job")
        identifier(request_id)
        if session_id:
            self.get_session(session_id)
        raw=encode({"kind":kind,"parameters":parameters,"session_id":session_id})
        if len(raw)>65536:
            raise LabError("job request too large",413,"too_large")
        digest=hashlib.sha256(raw.encode()).hexdigest()
        with self.transaction() as db:
            previous=db.execute("SELECT id,request_hash FROM jobs WHERE request_id=?",(request_id,)).fetchone()
            if previous:
                if previous[1]!=digest:
                    raise LabError("request_id reused with different job")
                return self.get_job(previous[0]),False
            self.check_capacity(len(raw)+65536)
            jid=uuid.uuid4().hex
            db.execute("INSERT INTO jobs(id,kind,session_id,state,created_at,updated_at,request_id,request_hash,parameters) VALUES(?,?,?,'requested',?,?,?,?,?)",(jid,kind,session_id,self.clock(),self.clock(),request_id,digest,encode(parameters)))
            if session_id:
                self._event(db,session_id,"job.created",{"job_id":jid,"kind":kind,"state":"requested"})
        return self.get_job(jid),True

    def get_job(self,jid):
        identifier(jid)
        with self.lock:
            row=self.db.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone()
        if not row:
            raise LabError("job not found",404,"not_found")
        result=dict(row)
        result.pop("request_hash")
        result["parameters"]=json.loads(result["parameters"])
        result["result"]=json.loads(result["result"]) if result["result"] else None
        return result

    def list_jobs(self,session_id=None):
        if session_id:
            self.get_session(session_id)
        with self.lock:
            rows=self.db.execute("SELECT id FROM jobs WHERE (? IS NULL OR session_id=?) ORDER BY created_at DESC LIMIT 200",(session_id,session_id)).fetchall()
        return [self.get_job(r[0]) for r in rows]

    def update_job(self,jid,state,result=None,error=None,expected_state=None):
        if state not in {"blocked","queued","starting","running","completed","failed","cancelled","interrupted","indeterminate"}:
            raise LabError("invalid job state",422,"invalid_state")
        with self.transaction() as db:
            current=self.get_job(jid)
            if expected_state is not None and current["state"] != expected_state:
                return current
            if current["state"] in {"completed","failed","cancelled","interrupted"} and state!=current["state"]:
                raise LabError("job is terminal")
            db.execute("UPDATE jobs SET state=?,updated_at=?,result=?,error=? WHERE id=?",(state,self.clock(),encode(result) if result is not None else None,error,jid))
            if current.get("session_id"):
                self._event(db,current["session_id"],"job.updated",{"job_id":jid,"kind":current["kind"],"state":state})
        return self.get_job(jid)

    @staticmethod
    def scan_tree(root, skip=frozenset(), limit=None):
        """Exact bytes of every regular file under root (a full recursive scan).

        Paths in `skip` (as os.scandir spells them) are not entered or counted.
        Links are refused before traversal or accounting. Reuses scandir's
        directory-entry metadata instead of stat'ing every path again. `limit`,
        a one-item list, bounds the entries a live measurement may visit:
        beyond it admission is refused (storage_unbounded), never estimated.
        """
        total=0
        pending=[root]
        while pending:
            directory=pending.pop()
            try:
                entries=os.scandir(directory)
            except FileNotFoundError:
                # Removed after it was listed (e.g. another session's staging
                # directory on deletion): it holds no bytes now.
                if directory==root:
                    raise
                continue
            with entries as listing:
                for entry in listing:
                    if limit is not None:
                        limit[0]-=1
                        if limit[0]<0:
                            raise LabError("out-of-band storage exceeds its live measurement bound",507,"storage_unbounded")
                    if entry.is_symlink():
                        raise LabError("unsafe storage link",503,"unsafe_storage")
                    if entry.path in skip:
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(entry.path)
                        else:
                            total+=entry.stat(follow_symlinks=False).st_size
                    except FileNotFoundError:
                        continue
        return total

    def scan_usage(self):
        """The full exact scan of the whole storage root: the accounting oracle."""
        return Store.scan_tree(self.root)

    def _lstat_owned(self,name):
        try:
            info=os.lstat(self.root/name)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode):
            raise LabError("unsafe storage link",503,"unsafe_storage")
        return info

    def _owned_bytes(self,active):
        """Full scan of what only the Store writes: the running total's scope.

        Active recorder staging directories (`active`) are skipped: they are
        measured live while FFmpeg writes them."""
        info=self._lstat_owned(OWNERSHIP_FILE)
        total=info.st_size if info is not None else 0
        skip=frozenset(str(directory) for directory in active)
        for name in OWNED_TREES:
            if self._lstat_owned(name) is None:
                continue
            try:
                total+=Store.scan_tree(str(self.root/name),skip)
            except FileNotFoundError:
                continue
        return total

    def _ledger_bytes(self):
        total=0
        for name in LEDGER_FILES:
            info=self._lstat_owned(name)
            if info is not None:
                total+=info.st_size
        return total

    def _staging_bytes(self,measured=None):
        # Active recorder staging is written by FFmpeg, not by the Store, so
        # it is measured, never estimated: the caller's own fresh measurement
        # of its directory, or one scan of that (small) directory.
        total=0
        for sid,directory in self._active_staging.items():
            if measured is not None and sid in measured:
                value=measured[sid]
                if type(value) is not int or value<0:
                    raise LabError("invalid staging measurement",500,"invalid_measurement")
                total+=value
                continue
            try:
                total+=Store.scan_tree(str(directory))
            except FileNotFoundError:
                continue
        return total

    def _unowned_bytes(self):
        """Everything under the root the Store does not own, measured now.

        Operator tools (e.g. tools/extract-lab-detector-inputs.py writing
        derived/) and any other top-level entry are walked at every check, so
        their bytes count immediately. The walk is bounded: beyond
        MAX_UNOWNED_ENTRIES entries admission is refused, never estimated."""
        total=0
        budget=[MAX_UNOWNED_ENTRIES]
        with os.scandir(self.root) as listing:
            for entry in listing:
                if entry.name in OWNED_NAMES:
                    continue
                if entry.is_symlink():
                    raise LabError("unsafe storage link",503,"unsafe_storage")
                try:
                    if entry.is_dir(follow_symlinks=False):
                        total+=Store.scan_tree(entry.path,limit=budget)
                    else:
                        total+=entry.stat(follow_symlinks=False).st_size
                except FileNotFoundError:
                    continue
        self.accounting["unowned_entries"]=MAX_UNOWNED_ENTRIES-budget[0]
        return total

    def _account(self,delta):
        # Caller holds self.lock: every Store change to a counted file.
        if self._files_bytes is not None:
            self._files_bytes+=delta
        self._mutations+=1

    def _unlink_accounted(self,path,*,measured_live=False):
        try:
            size=os.lstat(path).st_size
        except FileNotFoundError:
            return
        path.unlink(missing_ok=True)
        if not measured_live:
            self._account(-size)

    def track_staging(self,sid,directory):
        """A recorder's staging directory is measured live while FFmpeg writes it."""
        identifier(sid)
        directory=Path(directory)
        with self.lock:
            if sid in self._active_staging:
                return
            try:
                existing=Store.scan_tree(str(directory))
            except FileNotFoundError:
                existing=0
            self._active_staging[sid]=directory
            self._account(-existing)

    def release_staging(self,sid):
        """After FFmpeg has terminated: its remaining staging bytes join the total."""
        with self.lock:
            directory=self._active_staging.get(sid)
            if directory is None:
                return
            try:
                remaining=Store.scan_tree(str(directory))
            except FileNotFoundError:
                remaining=0
            del self._active_staging[sid]
            self._account(remaining)

    def _scan_failed(self,error,*,phase):
        """Count and log a failed measurement; return the LabError to raise.

        Caller holds self.lock. Content-free: phase, code and exception class."""
        self.accounting["scan_failures"]+=1
        failure=error if isinstance(error,LabError) else LabError("storage scan failed",503,"storage_scan_failed")
        _storage_log.warning("perception-lab storage scan failed: phase=%s code=%s error_class=%s failures=%d",
                             phase,failure.code,type(error).__name__,self.accounting["scan_failures"])
        return failure

    def reconcile_storage(self,reason="explicit"):
        """Re-establish the running total from one exact scan of the Store's trees.

        The scan runs without the ledger lock unless a Store write lands during
        it (then it retries, finally under the lock). Drift is corrected, counted
        and logged without content. A corrected total over quota fails closed:
        every later admission is refused until usage is back within quota. A
        failed scan (unsafe link, permission or I/O error) also fails closed
        until a scan succeeds.
        """
        if reason not in RECONCILE_REASONS:
            reason="explicit"
        try:
            for attempt in range(3):
                with (self.lock if attempt==2 else nullcontext()):
                    with self.lock:
                        mutations,active=self._mutations,tuple(self._active_staging.values())
                    started=time.perf_counter()
                    files=self._owned_bytes(active)
                    elapsed=time.perf_counter()-started
                    with self.lock:
                        if self._mutations!=mutations:
                            continue
                        return self._settle(files,reason,elapsed)
            raise RuntimeError("storage reconciliation raced its own lock")
        except (LabError,OSError) as error:
            with self.lock:
                failure=self._scan_failed(error,phase="reconcile")
                self._scan_error=(str(failure),failure.status,failure.code)
            raise failure from None

    def _settle(self,files,reason,elapsed):
        previous=self._files_bytes
        drift=0 if previous is None else files-previous
        self._files_bytes=files
        self._scan_error=None
        stats=self.accounting
        stats["full_scans"]+=1
        stats["reconciliations"]+=1
        stats["last_scan_ms"]=round(elapsed*1000,3)
        stats["last_reason"]=reason
        if drift:
            stats["drift_corrections"]+=1
            stats["last_drift_bytes"]=drift
            # Counters only: no path, name or content.
            _storage_log.warning("perception-lab storage accounting corrected: reason=%s drift_bytes=%d corrections=%d",
                                 reason,drift,stats["drift_corrections"])
        used=self._usage()
        return {"reason":reason,"used_bytes":used,"quota_bytes":self.settings.quota_bytes,"drift_bytes":drift,
                "within_quota":used<=self.settings.quota_bytes}

    def _reconcile_quietly(self,reason):
        try:
            return self.reconcile_storage(reason)
        except Exception:
            return None

    def _usage(self,measured_staging=None):
        # Caller holds self.lock.
        if self._scan_error is not None:
            # Fail closed until a full scan succeeds again.
            message,status,code=self._scan_error
            raise LabError(message,status,code)
        if self._files_bytes is None:
            self.reconcile_storage("initial")
        return (self._files_bytes+self._ledger_bytes()+self._staging_bytes(measured_staging)
                +self._unowned_bytes())

    def storage_usage(self,measured_staging=None):
        """Exact bytes under the storage root, without rescanning the Store's trees.

        The running total of what only the Store writes (media/, staging/, the
        lock file), plus SQLite's files by stat, plus active recorder staging
        and every entry the Store does not own (e.g. derived/) measured now.
        Equal to scan_usage() whenever no write is in progress and nothing
        outside the Store changed media/ or staging/ (that is drift, found and
        corrected by reconciliation).
        """
        with self.lock:
            self.accounting["checks"]+=1
            try:
                return self._usage(measured_staging)
            except LabError:
                raise
            except OSError as error:
                raise self._scan_failed(error,phase="live") from None

    def check_capacity(self,incoming=0,measured_staging=None):
        used=self.storage_usage() if measured_staging is None else self.storage_usage(measured_staging)
        if used+incoming>self.settings.quota_bytes or shutil.disk_usage(self.root).free-incoming<self.settings.min_free_bytes:
            raise LabError("Lab storage limit reached; existing sessions retained",507,"quota_exceeded")
        return {"used_bytes":used,"quota_bytes":self.settings.quota_bytes,"policy":"keep_within_quota","auto_delete":False}

    @contextmanager
    def capacity_batch(self,incoming):
        """One exact capacity check admits a bounded batch of ledger writes.

        The ledger lock is held for the batch, so the admitted bytes cannot be
        shared with another writer. Every batch checks the exact current usage
        and a write beyond the admitted allowance checks again. Nested batches
        share it.
        """
        with self.lock:
            if self._allowance is not None:
                yield
                return
            self.check_capacity(incoming)
            self._allowance=incoming
            try:
                yield
            finally:
                self._allowance=None

    def _reserve(self,incoming):
        # Caller holds self.lock. Unadmitted remainder is still counted.
        if self._allowance is not None and incoming<=self._allowance:
            self._allowance-=incoming
        else:
            self.check_capacity(incoming+(self._allowance or 0))

    def media_path(self,sid,mid):
        identifier(sid); identifier(mid)
        path=self.media_root/sid/(mid+".bin")
        if self.media_root.is_symlink() or path.parent.is_symlink() or path.is_symlink():
            raise LabError("unsafe media path",403,"unsafe_path")
        if not path.resolve().is_relative_to(self.root):
            raise LabError("unsafe media path",403,"unsafe_path")
        return path

    def add_media(self,sid,data,mime_type,metadata,source_key=None,*,finalized_fragment=False,derived_frame=False):
        # Internal recorder-only path; HTTP ingestion never supplies this flag.
        # It permits a bounded drain, not new capture or arbitrary media writes.
        if finalized_fragment and (mime_type!="video/mp4" or metadata.get("kind") not in {"encoded_init","encoded_segment"}
                or metadata.get("format")!="fmp4" or not source_key):
            raise LabError("invalid finalized fragment",422,"invalid_finalization")
        if not isinstance(data,bytes) or not data or len(data)>self.settings.max_object_bytes:
            raise LabError("media object size outside bound",413,"too_large")
        if mime_type not in {"application/octet-stream","application/x-npz","application/json","video/mp2t","video/mp4","image/jpeg","image/png"}:
            raise LabError("unsupported media type",422,"invalid_media_type")
        raw=encode(metadata)
        if len(raw)>65536:
            raise LabError("media metadata too large",413,"too_large")
        digest=hashlib.sha256(data).hexdigest()
        with self.transaction() as db:
            session=self.get_session(sid)
            if derived_frame:
                # Operator importer only; never enabled by HTTP media ingestion.
                if finalized_fragment or mime_type!='image/png' or not source_key or session['state']!='stopped':
                    raise LabError('invalid saved derivative',422,'invalid_derivative')
                parent=self.get_media(sid,metadata.get('initialization_id'))
                pts=metadata.get('encoded_video_pts_seconds')
                if (parent['metadata'].get('kind')!='encoded_init'
                        or metadata.get('site_id')!=session['site_id'] or metadata.get('camera_id')!=session['camera_id']
                        or metadata.get('time_basis')!='encoded_video'
                        or metadata.get('clock_mapping_status')!='unverified'
                        or type(pts) not in (int,float) or not math.isfinite(pts) or not 0<=pts<=self.settings.max_session_seconds):
                    raise LabError('invalid saved derivative provenance',422,'invalid_derivative')
            if not derived_frame and session["state"] not in ({"recording","stopping"} if finalized_fragment else {"recording"}):
                raise LabError("session is not recording")
            if not derived_frame and self.clock()-session["started_at"]>=self.settings.max_session_seconds+(30 if finalized_fragment else 0):
                raise LabError("session duration limit reached",409,"duration_limit")
            if source_key:
                if len(source_key)>512:
                    raise LabError("source key too long",422,"invalid_source")
                previous=db.execute("SELECT id,sha256,metadata FROM media WHERE session_id=? AND source_key=?",(sid,source_key)).fetchone()
                if previous:
                    if previous[1]!=digest or previous[2]!=raw:
                        raise LabError("source frame replay differs",409,"source_conflict")
                    return self.get_media(sid,previous[0])
            self._reserve(len(data)+65536)
            mid=uuid.uuid4().hex
            path=self.media_path(sid,mid)
            path.parent.mkdir(exist_ok=True)
            temp=path.with_suffix(".partial")
            created,written=False,None
            try:
                with temp.open("xb") as handle:
                    created=True
                    handle.write(data); handle.flush(); os.fsync(handle.fileno())
                os.replace(temp,path)
                written=len(data)
            finally:
                # The bytes actually left on disk, completed or not: a failed
                # write or rename leaves its partial file counted, as a scan would.
                if created:
                    self._account(written if written is not None else _size(temp))
            db.execute("INSERT INTO media VALUES(?,?,?,?,?,?,?,?,?)",(mid,sid,session["generation"],len(data),digest,mime_type,raw,self.clock(),source_key))
        return self.get_media(sid,mid)

    def get_media(self,sid,mid,generation=None):
        session=self.get_session(sid); identifier(mid)
        if generation is not None and generation!=session["generation"]:
            raise LabError("media generation changed",409,"generation_conflict")
        with self.lock:
            if self.db.execute("SELECT 1 FROM media_deletions WHERE session_id=? AND state='pending'",(sid,)).fetchone():
                raise LabError("media deletion pending",410,"media_deleted")
            row=self.db.execute("SELECT * FROM media WHERE session_id=? AND id=?",(sid,mid)).fetchone()
        if row is None:
            raise LabError("media not found",404,"not_found")
        result=dict(row); result["metadata"]=json.loads(result["metadata"])
        result.update({"media_id":result["id"],"site_id":self.settings.site_id,"size_bytes":result["size"],"content_type":result["mime_type"]})
        result["url"]=f"/api/perception-lab/v1/sessions/{sid}/media/{mid}?generation={result['generation']}"
        return result

    def outputs_at(self,sid,at_ms):
        session=self.get_session(sid)
        if type(at_ms) is not int or not 0<=at_ms<=self.settings.max_session_seconds*1000:
            raise LabError("invalid replay cursor",422,"invalid_cursor")
        cutoff=at_ms/1000
        with self.lock:
            activation=self.db.execute("""SELECT payload FROM events WHERE session_id=? AND kind='experiment.activated'
                AND json_extract(payload,'$.effective_at')<=? ORDER BY id DESC LIMIT 1""",
                (sid,(session.get('started_at') or 0)+cutoff)).fetchone()
            active_generation=json.loads(activation[0]).get('generation') if activation else None
            rows=self.db.execute("""WITH candidates AS (
                SELECT id,session_id,kind,created_at,payload,
                  row_number() OVER (
                    PARTITION BY json_extract(payload,'$.job_id'),json_extract(payload,'$.run_id'),json_extract(payload,'$.arm'),json_extract(payload,'$.mode')
                    ORDER BY json_extract(payload,'$.source_end_s') DESC,id DESC) AS position
                FROM events WHERE session_id=? AND kind IN ('perception.output','runtime.output','analysis.output','reasoning.output')
                  AND json_type(payload,'$.source_end_s') IN ('integer','real')
                  AND json_extract(payload,'$.source_end_s')<=?
                  AND (json_extract(payload,'$.mode')!='live' OR
                    (json_type(payload,'$.available_at_s') IN ('integer','real') AND json_extract(payload,'$.available_at_s')<=?))
              ) SELECT id,session_id,kind,created_at,payload FROM candidates WHERE position=1 ORDER BY id LIMIT 129""",(sid,cutoff,cutoff)).fetchall()
        if len(rows)>128:
            raise LabError("select fewer analysis runs for replay",413,"replay_run_limit")
        result=[]
        used=0
        for row in rows:
            value={**dict(row),"payload":json.loads(row["payload"])}
            payload=value["payload"]
            if value["kind"]=="analysis.output":
                end=payload.get("judgment_end_s")
                if type(end) in (int,float) and cutoff>=end and cutoff>payload["source_end_s"]:
                    payload["status"]="stale"
            if payload.get("mode")=="live":
                if active_generation is not None and payload.get('generation')!=active_generation:
                    payload['status']='stale'
                    payload['stale_reason']='experiment_generation_changed'
                    if isinstance(payload.get('arm_snapshot'),dict):
                        payload['arm_snapshot']['status']='stale'
                expiry=payload.get("valid_until")
                start=session.get("started_at")
                if type(expiry) not in (int,float) or type(start) not in (int,float) or expiry<start+cutoff:
                    payload["status"]="stale"
                    if isinstance(payload.get("arm_snapshot"),dict):
                        payload["arm_snapshot"]["status"]="stale"
            used+=len(encode(value).encode())
            if used>1024**2:
                raise LabError("replay evidence exceeds response budget",413,"replay_size_limit")
            result.append(value)
        return result

    def list_media(self,sid,cursor=0,limit=100,*,kind=None,at_ms=None,encoded_at_ms=None,radius_ms=30000):
        session=self.get_session(sid)
        if type(cursor) is not int or cursor<0 or type(limit) is not int or not 1<=limit<=200:
            raise LabError("invalid media page",422,"invalid_page")
        if kind not in {None,"video","depth","image"} or (at_ms is not None and encoded_at_ms is not None):
            raise LabError("invalid media filter",422,"invalid_filter")
        if type(radius_ms) is not int or not 0<=radius_ms<=120000 or any(type(x) is not int or not 0<=x<=1800000 for x in (at_ms,encoded_at_ms) if x is not None):
            raise LabError("invalid media window",422,"invalid_filter")
        where="session_id=?"
        params=[sid]
        if kind:
            where+={"video":" AND mime_type='video/mp4'","image":" AND mime_type IN ('image/jpeg','image/png')","depth":" AND json_extract(metadata,'$.format')='depth_u16_le'"}[kind]
        if encoded_at_ms is not None:
            if kind!="video":
                raise LabError("encoded window requires video",422,"invalid_filter")
            where+=" AND (json_extract(metadata,'$.role')='init' OR (json_extract(metadata,'$.start_seconds')*1000<=? AND json_extract(metadata,'$.end_seconds')*1000>=?))"
            params.extend([encoded_at_ms+radius_ms,max(0,encoded_at_ms-radius_ms)])
        if at_ms is not None:
            where+=" AND (json_extract(metadata,'$.role')='init' OR ((json_extract(metadata,'$.clock_mapping_status')='verified' OR json_extract(metadata,'$.timeline_basis')='controller_receipt') AND coalesce(json_extract(metadata,'$.timeline_start_ms'),json_extract(metadata,'$.session_time_ms'))<=? AND coalesce(json_extract(metadata,'$.timeline_end_ms'),json_extract(metadata,'$.session_time_ms'))>=?))"
            params.extend([at_ms+radius_ms,max(0,at_ms-radius_ms)])
        with self.lock:
            if self.db.execute("SELECT 1 FROM media_deletions WHERE session_id=? AND state='pending'",(sid,)).fetchone():
                return {"generation":session["generation"],"items":[],"next_cursor":None,"total_items":0}
            ids=self.db.execute("SELECT rowid,id FROM media WHERE "+where+" AND rowid>? ORDER BY rowid LIMIT ?",(*params,cursor,limit+1)).fetchall()
            total=self.db.execute("SELECT COUNT(*) FROM media WHERE "+where,params).fetchone()[0]
            # This describes the whole closed encoded track, not the current
            # cursor/window. A page ending is never proof that capture ended.
            final_video=None
            if session['state']=='stopped':
                final_video=self.db.execute("""SELECT id,metadata FROM media WHERE session_id=?
                    AND mime_type='video/mp4' AND json_extract(metadata,'$.role')='media'
                    AND json_type(metadata,'$.end_seconds') IN ('integer','real')
                    ORDER BY json_extract(metadata,'$.end_seconds') DESC,rowid DESC LIMIT 1""",(sid,)).fetchone()
        items=[]
        used=0
        last=cursor
        for row in ids[:limit]:
            value=self.get_media(sid,row[1])
            size=len(encode(value).encode())
            if items and used+size>1024**2:
                break
            items.append(value); used+=size; last=row[0]
        more=len(ids)>len(items)
        track={"finalized":bool(final_video),"final_media_id":final_video[0] if final_video else None}
        if final_video:
            final_metadata=json.loads(final_video[1])
            track.update(duration_seconds=final_metadata["end_seconds"],initialization_id=final_metadata.get("initialization_id"))
        return {"generation":session["generation"],"items":items,"next_cursor":last if more else None,"total_items":total,"encoded_track":track}

    def delete_session(self,sid,expected_revision,*,erase_records=False):
        if type(erase_records) is not bool:
            raise LabError("explicit erasure flag required",422,"invalid_scope")
        if type(expected_revision) is not int or expected_revision<1:
            raise LabError("valid expected_revision required",422,"invalid_revision")
        with self.transaction() as db:
            prior=db.execute("SELECT state FROM session_erasures WHERE session_id=?",(sid,)).fetchone()
            if prior:
                raise LabError("session erased or erasure pending",410,"session_erased")
            session=self.get_session(sid)
            if session["revision"]!=expected_revision:
                raise LabError("session revision conflict",409,"revision_conflict")
            if session["state"] in {"requested","starting","recording","stopping","indeterminate"}:
                raise LabError("stop session before deleting media")
            if any(j["state"] in {"requested","starting","running","queued","indeterminate"} for j in self.list_jobs(sid)):
                raise LabError("cancel analysis before deletion")
            has_paired=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paired_requests'").fetchone()
            if has_paired and db.execute("SELECT 1 FROM paired_requests WHERE session_id=? AND state IN ('dispatching','running','indeterminate') LIMIT 1",(sid,)).fetchone():
                raise LabError("reconcile paired execution before deletion",409,"execution_pending")
            has_reasoning=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lab_reasoning_requests'").fetchone()
            if has_reasoning and db.execute("SELECT 1 FROM lab_reasoning_requests WHERE session_id=? AND state IN ('pending','dispatching','indeterminate') LIMIT 1",(sid,)).fetchone():
                raise LabError("reconcile reasoning execution before deletion",409,"execution_pending")
            has_analysis=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lab_analysis_steps'").fetchone()
            if has_analysis and db.execute("SELECT 1 FROM lab_analysis_steps s JOIN jobs j ON s.job_id=j.id WHERE j.session_id=? AND s.state IN ('dispatching','indeterminate') LIMIT 1",(sid,)).fetchone():
                raise LabError("reconcile analysis execution before deletion",409,"execution_pending")
            has_shared=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shared_detector_stage'").fetchone()
            if has_shared and db.execute("SELECT 1 FROM shared_detector_stage WHERE session_id=? AND state!='completed' LIMIT 1",(sid,)).fetchone():
                raise LabError("reconcile shared detector before deletion",409,"execution_pending")
            if erase_records:
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                # Until distributed erasure is provisioned, do not claim a
                # complete deletion while external execution copies may remain.
                remote=('remote_specialist_dispatch' in tables and db.execute("SELECT 1 FROM remote_specialist_dispatch WHERE json_extract(reference,'$.session_id')=? LIMIT 1",(sid,)).fetchone())
                reasoning=(has_reasoning and db.execute("SELECT 1 FROM lab_reasoning_requests WHERE session_id=? AND dispatched_at IS NOT NULL LIMIT 1",(sid,)).fetchone())
                analysis=(has_analysis and db.execute("SELECT 1 FROM lab_analysis_steps s JOIN jobs j ON j.id=s.job_id WHERE j.session_id=? LIMIT 1",(sid,)).fetchone())
                if remote or reasoning or analysis:
                    raise LabError("external erasure propagation not provisioned",409,"external_erasure_required")
                db.execute("INSERT INTO session_erasures VALUES(?,'pending',?,NULL)",(sid,self.clock()))
            deletion=db.execute("SELECT state FROM media_deletions WHERE session_id=?",(sid,)).fetchone()
            if deletion is None:
                db.execute("INSERT INTO media_deletions VALUES(?,'pending',?,NULL)",(sid,self.clock()))
                db.execute("UPDATE sessions SET generation=generation+1,revision=revision+1,reason='media_deletion_pending' WHERE id=?",(sid,))
                if not erase_records:self._event(db,sid,"media.deletion_requested",{"actor_action":"explicit_user_delete"})
        self._finish_media_deletion(sid)
        if erase_records:
            self._finish_session_erasure(sid)
            return {"id":sid,"deleted":True,"scope":"session"}
        return self.get_session(sid)

    def _finish_session_erasure(self,sid):
        with self.transaction() as db:
            row=db.execute("SELECT state FROM session_erasures WHERE session_id=?",(sid,)).fetchone()
            if not row or row[0]!='pending':return
            if db.execute("SELECT 1 FROM media WHERE session_id=?",(sid,)).fetchone():
                raise LabError("media deletion incomplete",409,"deletion_pending")
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'specialist_chains' in tables and 'paired_requests' in tables:
                db.execute("DELETE FROM specialist_chains WHERE request_id IN (SELECT id FROM paired_requests WHERE session_id=?)",(sid,))
            if 'lab_analysis_manifests' in tables:
                db.execute("DELETE FROM lab_analysis_manifests WHERE job_id IN (SELECT id FROM jobs WHERE session_id=?)",(sid,))
            for table in ('annotations','events','commands','jobs','paired_requests','paired_sampling','paired_dispatch',
                          'shared_detector_stage','lab_reasoning_requests','lab_experiment_state'):
                if table in tables:db.execute(f"DELETE FROM {table} WHERE session_id=?",(sid,))
            # Keep opaque request identity to reject replay; remove household
            # metadata. The independent tombstone survives application rollback.
            db.execute("UPDATE sessions SET state='deleted',name='',camera_id='',experiment_id='',started_at=NULL,ended_at=NULL,request_hash='',reason='session_erased',revision=revision+1 WHERE id=?",(sid,))
            db.execute("UPDATE session_erasures SET state='completed',completed_at=? WHERE session_id=?",(self.clock(),sid))

    def _finish_media_deletion(self,sid):
        # The intent and generation invalidation commit before any unlink.
        # A failed unlink leaves metadata/intent available for exact recovery.
        with self.transaction() as db:
            deletion=db.execute("SELECT state FROM media_deletions WHERE session_id=?",(sid,)).fetchone()
            if not deletion or deletion[0]!='pending':
                return
            identifier(sid)
            staging=self.root/'staging'/sid
            if staging.exists():
                if staging.is_symlink() or staging.parent.is_symlink() or staging.resolve()!=staging or staging.parent.resolve()!=staging.parent:
                    raise LabError("unsafe staging deletion path",403,"unsafe_path")
                files=list(staging.iterdir())
                if any(p.is_symlink() or not p.is_file() or not re.fullmatch(r'(?:init\.mp4|index\.m3u8|[0-9]{8}\.m4s)(?:\.tmp)?',p.name) for p in files):
                    raise LabError("unrecognized staging content",409,"staging_review_required")
                live=sid in self._active_staging
                for path in files:self._unlink_accounted(path,measured_live=live)
                staging.rmdir()
                if live:
                    del self._active_staging[sid]
                    self._account(0)
            rows=db.execute("SELECT id FROM media WHERE session_id=?",(sid,)).fetchall()
            for row in rows:
                self._unlink_accounted(self.media_path(sid,row[0]))
            db.execute("DELETE FROM media WHERE session_id=?",(sid,))
            db.execute("UPDATE sessions SET revision=revision+1,reason='media_deleted_by_user' WHERE id=?",(sid,))
            db.execute("UPDATE media_deletions SET state='completed',completed_at=? WHERE session_id=?",(self.clock(),sid))
            if not db.execute("SELECT 1 FROM session_erasures WHERE session_id=?",(sid,)).fetchone():
                self._event(db,sid,"media.deleted",{"actor_action":"explicit_user_delete"})

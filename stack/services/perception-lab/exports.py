"""Complete bounded metadata export pages with optimistic snapshot integrity.

Snapshot IDs are content revision checks, never credentials. Every page still
requires the endpoint's authenticated export capability and site binding.
Media bytes remain separate registered objects; this is not a video archive.
"""
import hashlib
import json
import re

from store import LabError, encode


def export_page(store,sid,*,snapshot_id=None,cursor=0,limit=100):
    if type(cursor) is not int or cursor<0 or type(limit) is not int or not 1<=limit<=200:
        raise LabError("invalid export cursor",422,"invalid_export_cursor")
    if snapshot_id is not None and (not isinstance(snapshot_id,str) or not re.fullmatch(r"[a-f0-9]{64}",snapshot_id)):
        raise LabError("invalid export snapshot",422,"invalid_export_snapshot")
    if cursor and snapshot_id is None:
        raise LabError("continued export requires snapshot identity",422,"invalid_export_snapshot")
    with store.lock:
        session=store.get_session(sid)
        if store.db.execute("SELECT 1 FROM media_deletions WHERE session_id=? AND state='pending'",(sid,)).fetchone():
            raise LabError("media deletion must finish before export",409,"deletion_pending")
        if session["state"] not in {"stopped","interrupted","failed"}:
            raise LabError("finalize recording before exporting metadata",409,"export_session_active")
        groups=(("annotation","annotations"),("job","jobs"),("media","media"),("event","events"))
        counts={kind:store.db.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id=?",(sid,)).fetchone()[0] for kind,table in groups}
        event_max=store.db.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE session_id=?",(sid,)).fetchone()[0]
        stamp={"session_id":sid,"site_id":session["site_id"],"revision":session["revision"],
               "generation":session["generation"],"event_max":event_max,"counts":counts,"owner_epoch":store.instance_id}
        current=hashlib.sha256(encode(stamp).encode()).hexdigest()
        if snapshot_id is not None and current!=snapshot_id:
            raise LabError("session changed during export; restart export",409,"export_snapshot_changed")
        total=1+sum(counts.values())
        if cursor>total or total>1000000:
            raise LabError("export cursor or collection outside bound",413,"export_collection_limit")
        records=[]; used=0; position=cursor

        def add(kind,value):
            nonlocal used,position
            record={"kind":kind,"value":value}
            size=len(encode(record).encode())
            if size>1024**2:
                raise LabError("export record exceeds page budget",413,"export_record_limit")
            if records and used+size>1024**2:return False
            records.append(record);used+=size;position+=1
            return True

        if cursor==0:add("session",session)
        base=1
        full=False
        for kind,table in groups:
            count=counts[kind]
            if position>=base+count:
                base+=count;continue
            offset=max(0,position-base)
            remaining=limit-len(records)
            if not remaining:break
            rows=store.db.execute(f"SELECT * FROM {table} WHERE session_id=? ORDER BY id LIMIT ? OFFSET ?",(sid,remaining,offset)).fetchall()
            for row in rows:
                value=dict(row)
                if kind=="annotation":
                    value=json.loads(value["payload"])
                    value["label_status"]=("deleted" if value.get("deleted") else "reviewed_human" if value.get("origin")=="human" and value.get("reviewed") is True else "unverified")
                elif kind=="event":value["payload"]=json.loads(value["payload"])
                elif kind=="job":
                    value.pop("request_hash",None)
                    value["parameters"]=json.loads(value["parameters"])
                    value["result"]=json.loads(value["result"]) if value["result"] else None
                else:
                    value["metadata"]=json.loads(value["metadata"])
                    value["bytes_included"]=False
                    value["media_id"]=value["id"]
                if not add(kind,value):full=True;break
            base+=count
            if full or len(records)>=limit:break
        return {"format":"perception-lab-session-v2","site_id":session["site_id"],"session_id":sid,
            "snapshot_id":current,"generation":session["generation"],"cursor":cursor,
            "next_cursor":position if position<total else None,"total_records":total,
            "counts":{"session":1,**counts},"records":records,"metadata_only":True,
            "media_bytes_included":False,"contains_sensitive_context":True,
            "training_ready":False,"dataset_review_required":True}

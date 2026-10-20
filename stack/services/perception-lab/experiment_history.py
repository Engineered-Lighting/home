"""Read controller-authored experiment boundaries for saved comparisons."""
import json
from reasoning import finite


def history(store, session, observations):
    with store.lock:
        rows=store.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='experiment.activated' ORDER BY id LIMIT 1001",
                              (session["id"],)).fetchall()
    if len(rows)>1000:
        raise ValueError("experiment history exceeds analysis bound")
    changes=[]
    for row in rows:
        change=json.loads(row[0])
        from evidence_policy import ha_policy, source_policy
        ha_policy(change.get('ha_evidence'))
        # Recorded family statuses (one-lever recipes) agree with the switches.
        if change.get('source_policy') is not None:
            source_policy(change['source_policy'],ha=change.get('ha_evidence'),caption=change.get('caption_evidence'))
        windows=change.get("reasoning_windows")
        if windows is not None and (not isinstance(windows,dict) or set(windows)!={"a","b"}
                or any(not finite(v) or not 0<=v<=120 for v in windows.values())):
            raise ValueError("invalid recorded reasoning windows")
        if (change.get("site_id")!=session["site_id"] or change.get("camera_id")!=session["camera_id"]
                or type(change.get("generation")) is not int or change["generation"]!=len(changes)+1
                or not finite(change.get("effective_at"))
                or not session["started_at"]<=change["effective_at"]<=session["ended_at"]
                or (changes and change["effective_at"]<changes[-1]["effective_at"])):
            raise ValueError("invalid experiment activation history")
        changes.append(change)
    if changes:
        return changes,"recorded_activation"
    generations={r.get("generation") for r in observations
        if r.get("site_id")==session["site_id"] and r.get("session_id")==session["id"]
        and r.get("arm") in {"a","b"} and r.get("kind") in {None,"observation"}
        and type(r.get("generation")) is int}
    if len(generations)>1:
        raise ValueError("multiple generations require recorded experiment activation history")
    # Legacy data cannot establish switch times. A single observed generation is
    # usable only with an explicit provenance limitation; never infer a switch
    # from the first result (which could be delayed or absent).
    return ([{"generation":next(iter(generations)),"effective_at":session["started_at"],
              "experiment_id":None}] if generations else []),"legacy_unverified"


def at_cutoff(changes, session, cutoff):
    current=None
    end=session["ended_at"]
    for change in changes:
        if change["effective_at"]<=cutoff:
            current=change
        else:
            end=change["effective_at"]
            break
    return current,end

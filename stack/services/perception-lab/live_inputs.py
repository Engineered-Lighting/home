"""Bounded causal reads of persisted specialist/HA evidence for live reasoning."""
import json
from persisted_evidence import observation_record

KINDS = ("perception.output", "ha.observation", "room.observation")
# Bound on the rows a cached read parses or keeps: a seed (first read, a clock
# step back, a window change) or a catch-up that would exceed it is not
# cached; that call uses the plain bounded query and the next call re-seeds.
INCREMENTAL_ROWS = 4096


def live_records(store, session, generation, cutoff, *, window_seconds=10, retain_history=False, cache=None):
    """Causal window of recorded observations, oldest first.

    `cache` (optional, one dict per recording session, used by the live
    scheduler) makes each call cost the rows appended since the previous call
    instead of a walk over the whole session: ledger events are append-only and
    immutable, so each row is parsed once and kept only while a later cutoff
    can still admit it. The rows, filters, order and 512-row bound are exactly
    those of the plain query; a cutoff earlier than the previous call's (a
    clock step) or another window re-reads from scratch, parsing at most
    INCREMENTAL_ROWS rows, else that call falls back to the plain query.
    """
    from reasoning import finite
    if not finite(window_seconds) or not 0<=window_seconds<=120:
        raise ValueError("invalid live evidence window")
    width=max(3,window_seconds)
    low=cutoff-width
    rows=_incremental(store,session,cutoff,low,width,cache) if cache is not None else None
    if rows is None:
        # Kind-bounded: walk only these three kinds' index ranges, not every
        # event of the session (drops, deliveries, JEV packets). Same rows and order.
        with store.lock:
            rows=store.db.execute("""SELECT id,kind,payload FROM events INDEXED BY events_session_kind_id WHERE session_id=?
              AND kind IN ('perception.output','ha.observation','room.observation')
              AND json_extract(payload,'$.observed_at')>=?
              AND json_extract(payload,'$.observed_at')<=?
              AND json_extract(payload,'$.available_at')<=? ORDER BY id DESC LIMIT 513""",
              (session["id"],low,cutoff,cutoff)).fetchall()
        if len(rows)>512:
            raise ValueError("live evidence window exceeds bound")
        rows=[_entry(row["id"],row["kind"],json.loads(row["payload"]),session) for row in reversed(rows)]
    records=[]
    for entry in rows:
        kind,base,valid_until,value_generation,value_arm,status=entry[1:7]
        if base is None or (valid_until<cutoff and not retain_history):
            continue
        if kind in {"perception.output","room.observation"}:
            if value_generation!=generation or value_arm not in {"a","b"} or status!="completed":
                continue
            arms=[value_arm]
        else:
            arms=["a","b"]  # Identical shared HA fact, not independent evidence.
        for arm in arms:
            record={**base,"arm":arm,"generation":generation}
            records.append(record)
    return records


def _sql_number(value):
    """The number SQLite's json_extract compares for this JSON value, else None.

    JSON true/false extract as 1/0; strings, arrays and objects extract as
    TEXT, which never satisfies both a lower and an upper numeric bound, and
    null never compares true.
    """
    if value is True or value is False:
        return int(value)
    if isinstance(value, (int, float)):
        return value
    return None


def _entry(event_id, kind, value, session):
    base=observation_record(event_id,kind,value,session)
    observed=_sql_number(value.get("observed_at")) if isinstance(value,dict) else None
    available=_sql_number(value.get("available_at")) if isinstance(value,dict) else None
    get=value.get if isinstance(value,dict) else (lambda _key:None)
    return (event_id,kind,base,get("valid_until") if base is not None else None,
            get("generation"),get("arm"),get("status"),observed,available)


def _admitted(entry, low, cutoff):
    observed,available=entry[7],entry[8]
    return (observed is not None and available is not None
            and low<=observed<=cutoff and available<=cutoff)


def _incremental(store, session, cutoff, low, width, cache):
    identity=(session["id"],session["site_id"],session["camera_id"])
    state=cache.get("state")
    # Monotone cutoffs and one window width only: then the window start never
    # moves back and a row dropped as too old can never be admitted again.
    if (state is None or state["identity"]!=identity or state["width"]!=width
            or cutoff<state["cutoff"] or low<state["low"]):
        state=None
    with store.lock:
        if state is None:
            # Seed: every row a present or later cutoff can admit (observed at
            # or after the window start; later availability included).
            seed=store.db.execute("""SELECT id,kind,payload FROM events INDEXED BY events_session_kind_id WHERE session_id=?
              AND kind IN ('perception.output','ha.observation','room.observation')
              AND json_extract(payload,'$.observed_at')>=? ORDER BY id LIMIT ?""",(session["id"],low,INCREMENTAL_ROWS+1)).fetchall()
            if len(seed)>INCREMENTAL_ROWS:
                cache.pop("state",None)
                return None
            high=store.db.execute("""SELECT max(id) FROM events INDEXED BY events_session_kind_id WHERE session_id=?
              AND kind IN ('perception.output','ha.observation','room.observation')""",(session["id"],)).fetchone()[0] or 0
            fresh=seed
        else:
            fresh=store.db.execute("""SELECT id,kind,payload FROM events INDEXED BY events_session_kind_id WHERE session_id=?
              AND kind IN ('perception.output','ha.observation','room.observation') AND id>? ORDER BY id LIMIT ?""",
              (session["id"],state["high"],INCREMENTAL_ROWS+1)).fetchall()
            if len(fresh)>INCREMENTAL_ROWS:
                cache.pop("state",None)
                return None
            high=max([state["high"]]+[row["id"] for row in fresh])
    kept=[] if state is None else [entry for entry in state["entries"] if entry[7] is not None and entry[7]>=low]
    for row in fresh:
        entry=_entry(row["id"],row["kind"],json.loads(row["payload"]),session)
        # A row observed before this window start can never be admitted again:
        # later cutoffs only raise the window start.
        if entry[7] is not None and entry[7]>=low:
            kept.append(entry)
    if len(kept)>INCREMENTAL_ROWS:
        cache.pop("state",None)
        return None
    cache["state"]={"identity":identity,"width":width,"cutoff":cutoff,"low":low,"high":high,"entries":kept}
    admitted=[entry for entry in kept if _admitted(entry,low,cutoff)]
    if len(admitted)>512:
        raise ValueError("live evidence window exceeds bound")
    return admitted

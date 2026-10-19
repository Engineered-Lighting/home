"""Specialist input coverage, distinct from activity-label accuracy."""
import math


def _summary(values):
    # Nearest-rank percentiles in milliseconds; no interpolation or smoothing.
    values=sorted(values)
    if not values:
        return None
    rank=lambda q:values[max(0,math.ceil(q*len(values))-1)]
    return {'count':len(values),'p50_ms':rank(.5),'p95_ms':rank(.95),'max_ms':values[-1]}


def specialist_delivery(store, session_id):
    with store.lock:
        if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paired_requests'").fetchone():
            return []
        # Local terminals store {"reason"}; remote receipts store it under result.
        rows=store.db.execute("""SELECT generation,arm,state,COUNT(*) AS n,
            json_extract(payload,'$.profile.profile_id') AS profile_id,
            json_extract(payload,'$.sampling_hz') AS sampling_hz,
            CASE WHEN state IN ('dropped','cancelled','expired','rejected')
                THEN coalesce(json_extract(result,'$.reason'),json_extract(result,'$.result.reason')) END AS reason,
            SUM(CASE WHEN state='completed' AND updated_at<=coalesce(json_extract(payload,'$.preview_timing.original_valid_until'),json_extract(payload,'$.frame.valid_until')) THEN 1 ELSE 0 END) AS fresh
            FROM paired_requests WHERE session_id=?
            GROUP BY generation,arm,state,profile_id,sampling_hz,reason
            ORDER BY generation,arm""",(session_id,)).fetchall()
        completions=store.db.execute("""SELECT generation,arm,
            json_extract(payload,'$.profile.profile_id') AS profile_id,
            json_extract(payload,'$.sampling_hz') AS sampling_hz,
            updated_at,json_extract(payload,'$.frame.observed_at') AS observed_at
            FROM paired_requests WHERE session_id=? AND state='completed' ORDER BY updated_at""",(session_id,)).fetchall()
    groups={}
    for row in rows:
        key=(row['generation'],row['arm'],row['profile_id'],row['sampling_hz'])
        group=groups.setdefault(key,{'generation':key[0],'arm':key[1],'profile_id':key[2],
            'sampling_hz':key[3],'offered':0,'completed':0,'fresh_completed':0,'states':{},'drop_reasons':{},
            'terminal_reasons':{}})
        group['offered']+=row['n']
        group['states'][row['state']]=group['states'].get(row['state'],0)+row['n']
        if row['state']=='completed':group['completed']+=row['n']
        group['fresh_completed']+=row['fresh'] or 0
        if row['state'] in {'dropped','cancelled','expired','rejected'}:
            reason=row['reason'] if isinstance(row['reason'],str) else 'unspecified'
            reasons=group['terminal_reasons'].setdefault(row['state'],{})
            reasons[reason]=reasons.get(reason,0)+row['n']
            if row['state']=='dropped':
                group['drop_reasons'][reason]=group['drop_reasons'].get(reason,0)+row['n']
    times={}
    for row in completions:
        key=(row['generation'],row['arm'],row['profile_id'],row['sampling_hz'])
        times.setdefault(key,[]).append((row['updated_at'],row['observed_at']))
    for key,group in groups.items():
        group['completed_fraction']=group['completed']/group['offered']
        group['fresh_completed_fraction']=group['fresh_completed']/group['offered']
        group['coverage_kind']='specialist_offered_inputs'
        done=times.get(key,[])
        # Controller completion clock: cadence of delivered results, and source
        # observation to controller commit. Not display or paint time.
        group['completion_interval']=_summary([(b[0]-a[0])*1000 for a,b in zip(done,done[1:])])
        group['source_to_controller']=_summary([(at-observed)*1000 for at,observed in done
            if isinstance(observed,(int,float)) and at>=observed])
    return list(groups.values())

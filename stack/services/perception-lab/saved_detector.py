"""Import completed detector receipts into normal authenticated replay storage.

Operator-only: caller must own the controller ledger. No inference or network.
"""
import hashlib
import io
import json
from pathlib import Path
from PIL import Image
from presentation import arm_snapshot
from reasoning import finite
from store import LabError, encode


def import_receipt(store, path, arm):
    if arm not in {'A', 'B'}:
        raise ValueError('Explicit comparison arm required')
    path=Path(path)
    if path.is_symlink() or not path.resolve().is_relative_to(store.root/'derived'):
        raise ValueError('Receipt must be a local session derivative')
    raw=path.read_bytes()
    if len(raw)>1024**2:
        raise ValueError('Receipt too large')
    receipt=json.loads(raw)
    sid=receipt['session_id']; session=store.get_session(sid)
    if (session['state']!='stopped' or receipt['site_id']!=session['site_id']
            or receipt['camera_id']!=session['camera_id'] or receipt['mode']!='saved_session'
            or receipt['time_basis']!='encoded_video' or receipt['exposure_mapping']!='unverified'):
        raise ValueError('Saved session binding mismatch')
    manifest=json.loads((path.parent/'manifest.json').read_text())
    if manifest['session_id']!=sid or manifest['site_id']!=session['site_id'] or manifest['camera_id']!=session['camera_id']:
        raise ValueError('Input manifest binding mismatch')
    if not 1<=len(receipt['results'])<=128:
        raise ValueError('Bounded results required')
    profile=receipt['profile']
    if arm=='B':
        with store.lock:
            latest=store.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='detector.imported' AND json_extract(payload,'$.arm')='A' ORDER BY id DESC LIMIT 1",(sid,)).fetchone()
            if not latest:raise ValueError('Import the reference arm before its comparison')
            reference_key=json.loads(latest[0])['key']
            references=[json.loads(r[0]) for r in store.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='perception.output' AND json_extract(payload,'$.import_key')=?",(sid,reference_key))]
        if sorted(r['frame_id'] for r in references)!=sorted(r['source']['frame_id'] for r in receipt['results']):
            raise ValueError('Comparison must use the same offered frame identities')
        for reference in references:
            processing=reference['processing']
            if (processing.get('artifact_sha256')!=profile.get('sha256')
                    or any(processing.get(k)!=profile.get(k) for k in ('checkpoint','precision','preprocessing'))
                    or processing.get('device_id')==profile.get('device_id')):
                raise ValueError('Placement comparison requires identical model settings on different devices')
    key='saved-detector-'+arm+'-'+hashlib.sha256(raw).hexdigest()
    with store.lock:
        previous=store.db.execute("SELECT id FROM events WHERE session_id=? AND kind='detector.imported' AND json_extract(payload,'$.key')=?",(sid,key)).fetchone()
    if previous:return {'already_imported':True}
    prepared=[]
    for row in receipt['results']:
        source=row['source']; result=row['result']
        if source not in manifest['frames'] or result.get('frame_id')!=source['frame_id']:
            raise ValueError('Result does not match offered frame')
        processing=result.get('processing',{})
        if any(processing.get(k)!=profile.get(k) for k in ('checkpoint','precision','preprocessing','device_id','engine_epoch')) or processing.get('artifact_sha256')!=profile.get('sha256'):
            raise ValueError('Result profile mismatch')
        image_path=path.parent/source['filename']
        if image_path.is_symlink() or image_path.resolve().parent!=path.parent.resolve():
            raise ValueError('Unsafe derived image')
        data=image_path.read_bytes()
        if len(data)>store.settings.max_object_bytes or hashlib.sha256(data).hexdigest()!=source['sha256']:
            raise ValueError('Derived image integrity failed')
        with Image.open(io.BytesIO(data)) as image:
            if image.format!='PNG' or image.size!=(source['width'],source['height']) or image.width*image.height>3840*2160:
                raise ValueError('Invalid frame dimensions')
            if hashlib.sha256(image.convert('RGB').tobytes()).hexdigest()!=source['rgb_sha256']:
                raise ValueError('Decoded frame integrity failed')
        pts=source['encoded_video_pts_seconds']
        if not finite(pts) or not 0<=pts<=store.settings.max_session_seconds:
            raise ValueError('Invalid video time')
        prepared.append((row,data))
    # Media registration is idempotent; expose results atomically only after all
    # source-associated overlays validate. A retry reuses registered media.
    outputs=[]
    with store.lock:
        for row,data in prepared:
            source=row['source']; pts=source['encoded_video_pts_seconds']
            meta={**source,'site_id':session['site_id'],'camera_id':session['camera_id'],
                'capture_epoch':'encoded-'+manifest['initialization_id'],
                'initialization_id':manifest['initialization_id'],'time_basis':'encoded_video',
                'clock_mapping_status':'unverified','observed_at':None,'kind':'derived_rgb'}
            media=store.add_media(sid,data,'image/png',meta,
                source_key='derived-'+source['sha256'],derived_frame=True)
            request={'session_id':sid,'arm':arm,'profile':profile,
                'frame':{**meta,'media_ids':[media['id']]}}
            snapshot=arm_snapshot(store,request,row['result'],completed_at=None,current=False)
            if snapshot['validation']:
                raise ValueError('Invalid source-associated overlay')
            snapshot['status']='saved result'
            snapshot['frame'].update(session_time_s=pts,time_basis='encoded_video')
            snapshot['timing']={'worker_wall_ms':row['worker_wall_ms'],
                'stages_ms':row['result'].get('timings_ms',{}),'live_latency_ms':None}
            outputs.append({'run_id':'detector'+arm,'arm':arm,
                'mode':'saved_detector','status':'completed','time_basis':'encoded_video',
                'source_end_s':pts,'frame_id':source['frame_id'],'arm_snapshot':snapshot,
                'processing':row['result']['processing'],'runtime':receipt.get('runtime'), 'import_key':key})
        with store.transaction() as db:
            store.check_capacity(sum(len(encode(value)) for value in outputs)+65536)
            for value in outputs:store._event(db,sid,'perception.output',value)
            store._event(db,sid,'detector.imported',{'key':key,'arm':arm,'count':len(prepared)})
    return {'imported':len(prepared)}


if __name__=='__main__':
    import argparse
    from config import Settings
    from store import Store
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings',required=True)
    parser.add_argument('--receipt',required=True)
    parser.add_argument('--arm',required=True,choices=['A','B'])
    args=parser.parse_args()
    ledger=Store(Settings.from_file(args.settings))
    try:print(json.dumps(import_receipt(ledger,args.receipt,args.arm)))
    finally:ledger.close()

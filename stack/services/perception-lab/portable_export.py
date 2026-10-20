"""Bounded TAR stream of one optimistic session snapshot and registered media.

No disk spool, model invocation or arbitrary output path. A transport must supply
a live authorization/cancellation guard and consume incrementally. COMPLETE.json
is emitted only after every byte hash and the final snapshot check pass.
"""
import hashlib
import tarfile
from exports import export_page
from store import LabError,encode

CHUNK=64*1024


def archive_chunks(store,sid,*,authorize,maximum_bytes=64*1024**3,snapshot_id=None):
    if not callable(authorize) or type(maximum_bytes) is not int or not 1024<=maximum_bytes<=128*1024**3:
        raise ValueError('live export authorization and bounded transfer required')
    if authorize() is not True: raise LabError('export no longer authorized',403,'export_revoked')
    first=export_page(store,sid,limit=100,snapshot_id=snapshot_id)
    snapshot=first['snapshot_id']; generation=first['generation']
    total=0; media_count=0; media_bytes=0; pages=0
    def guard():
        if authorize() is not True: raise LabError('export no longer authorized',403,'export_revoked')
        if store.get_session(sid)['generation']!=generation:
            raise LabError('session deleted during export',409,'export_snapshot_changed')
    def emit(data):
        nonlocal total
        for offset in range(0,len(data),CHUNK):
            guard()
            block=data[offset:offset+CHUNK]
            total+=len(block)
            if total>maximum_bytes: raise LabError('archive exceeds transfer budget',413,'export_size_limit')
            yield block
    def header(name,size):
        info=tarfile.TarInfo(name); info.size=size; info.mode=0o600
        return info.tobuf(format=tarfile.GNU_FORMAT)
    def document(name,value):
        data=encode(value).encode('utf-8')
        yield from emit(header(name,len(data)))
        yield from emit(data)
        if len(data)%512: yield from emit(b'\0'*(512-len(data)%512))
    yield from document('README.json',{
        'format':'perception-lab-portable-v1','site_id':first['site_id'],'session_id':sid,
        'snapshot_id':snapshot,'generation':generation,'contains_sensitive_context':True,
        'training_ready':False,'dataset_review_required':True,
        'completion_required':'COMPLETE.json plus all registered media size/SHA-256 checks',
        'metadata':'metadata/page-*.json preserves original observations, labels and timing',
        'media':'Each media record supplies archive_path; raw segments are not transcoded',
        'deletion':'This copy is external to controller deletion. Preserve source deletion lineage; review before reuse.'})
    page=first
    while True:
        guard()
        for record in page['records']:
            if record['kind']=='media':
                record['value']['archive_path']='media/'+record['value']['media_id']+'.bin'
                record['value']['bytes_included']=True
        # Page envelope remains a metadata page; archive_path is explicit and
        # inclusion is provisional until the final completion record exists.
        yield from document('metadata/page-'+str(pages).zfill(6)+'.json',page)
        pages+=1
        for record in page['records']:
            if record['kind']!='media': continue
            value=record['value']
            item=store.get_media(sid,value['media_id'],generation=generation)
            if item['size_bytes']!=value['size'] or item['sha256']!=value['sha256']:
                raise LabError('registered media changed',409,'export_snapshot_changed')
            size=item['size_bytes']
            if total+512+size+(-size%512)+1024>maximum_bytes:
                raise LabError('archive exceeds transfer budget',413,'export_size_limit')
            path=store.media_path(sid,item['id'])
            h=hashlib.sha256(); actual=0
            with path.open('rb') as source:
                yield from emit(header(value['archive_path'],size))
                while actual<size:
                    guard()
                    block=source.read(min(CHUNK,size-actual))
                    if not block: raise LabError('media is truncated',409,'export_media_integrity')
                    h.update(block); actual+=len(block)
                    yield from emit(block)
                if source.read(1) or h.hexdigest()!=item['sha256']:
                    raise LabError('media integrity check failed',409,'export_media_integrity')
            if size%512: yield from emit(b'\0'*(512-size%512))
            media_count+=1; media_bytes+=size
        if page['next_cursor'] is None: break
        page=export_page(store,sid,snapshot_id=snapshot,cursor=page['next_cursor'],limit=100)
    # Changes to annotations/jobs/evidence, not just deletion, invalidate export.
    export_page(store,sid,snapshot_id=snapshot,limit=1)
    yield from document('COMPLETE.json',{'format':'perception-lab-portable-v1',
        'snapshot_id':snapshot,'generation':generation,'pages':pages,'media_count':media_count,
        'media_bytes':media_bytes,'total_records':first['total_records'],
        'media_integrity':'sha256_verified','training_ready':False})
    yield from emit(b'\0'*1024)

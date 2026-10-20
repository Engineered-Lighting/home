"""Demand-driven display of existing OAK numerical outputs; no recording/models."""
import ast
import base64
import io
import json
import threading
import time
import zipfile
from recorder import validate_npz
from store import LabError


def plane_bundle(data):
    validate_npz(data,4*1024**2)
    def array(archive,name,shape,dtype):
        raw=archive.read(name+'.npy')
        n=2 if raw[6]==1 else 4
        length=int.from_bytes(raw[8:8+n],'little')
        header=ast.literal_eval(raw[8+n:8+n+length].decode('latin1'))
        if header.get('shape')!=shape or header.get('descr')!=dtype or header.get('fortran_order') is not False:
            raise ValueError('Unexpected numerical layout')
        return raw[8+n+length:]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if set(archive.namelist())!={'depth_mm.npy','left.npy','right.npy','rgb_nv12.npy','metadata_json.npy'}:
            raise ValueError('Unexpected bundle members')
        raw=archive.read('metadata_json.npy');n=2 if raw[6]==1 else 4
        length=int.from_bytes(raw[8:8+n],'little')
        header=ast.literal_eval(raw[8+n:8+n+length].decode('latin1'))
        if header.get('descr')!='|u1' or len(header.get('shape',()))!=1 or header['shape'][0]>32768:
            raise ValueError('Unexpected metadata')
        meta=json.loads(raw[8+n+length:])
        if meta.get('depth_alignment')!='rectified_left' or meta.get('depth_units')!='millimetres' or meta.get('invalid_depth')!=0:
            raise ValueError('Unknown depth semantics')
        return meta,{'depth':array(archive,'depth_mm',(400,640),'<u2'),
            'left':array(archive,'left',(400,640),'|u1'),'right':array(archive,'right',(400,640),'|u1')}


class LivePlanes:
    def __init__(self,client,site_id,camera_id):
        self.client,self.site_id,self.camera_id=client,site_id,camera_id
        self.lock=threading.Lock();self.cache=None;self.fetched=0
        self.identity=None;self.changed=0

    def read(self,modality):
        if modality not in {'depth','left','right'}:raise LabError('Plane unavailable',404,'unsupported_plane')
        if not self.lock.acquire(blocking=False):raise LabError('Plane read busy',429,'preview_busy')
        try:
            now=time.monotonic()
            if self.cache is None or now-self.fetched>=1:
                data,_=self.client._read('/frame.npz')
                meta,planes=plane_bundle(data)
                identity=(meta['session'],meta['frames']['depth']['sequence'])
                if identity!=self.identity:self.identity=identity;self.changed=time.monotonic()
                self.cache=(meta,planes);self.fetched=time.monotonic()
            if time.monotonic()-self.changed>3:raise ValueError('Numerical bundle stopped advancing')
            meta,planes=self.cache
            return dict(version=1,site_id=self.site_id,camera_id=self.camera_id,modality=modality,
                width=640,height=400,data_base64=base64.b64encode(planes[modality]).decode(),
                format='depth_u16_le' if modality=='depth' else 'gray_u8',units='millimetres' if modality=='depth' else None,
                alignment_frame='rectified_left' if modality=='depth' else 'rectified_'+modality,
                sequence=meta['frames']['depth' if modality=='depth' else modality]['sequence'],
                capture_epoch=meta['session'],sample_interval_ms=1000,recorded=False,exposure_timing='unverified')
        except Exception:
            self.cache=None
            raise LabError('Live numerical plane unavailable',503,'plane_unavailable') from None
        finally:self.lock.release()

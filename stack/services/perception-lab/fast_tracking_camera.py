"""RAM-only, latest-frame camera bridge for the independent viewing lane.

The existing camera owner supplies synchronized RGB/depth groups. This module
opens no camera, loads no model and writes no media. Projection is explicitly
nominal EEPROM geometry, with no lighting, identity or physical authority.
"""
import base64
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import math
import threading
import time


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def sha(raw):return hashlib.sha256(raw).hexdigest()


class FastCamera:
    maximum_age_s=.75
    maximum_retained=8

    def __init__(self, owner, metadata, encoder, *, clock=time.monotonic):
        self.owner,self.metadata,self.encoder,self.clock=owner,metadata,encoder,clock
        self.lock=threading.Lock();self.encoding=threading.Lock()
        self.latest=None;self.retained=OrderedDict();self.epoch=None
        from fast_tracking_depth import DepthProjector
        self.projector=DepthProjector()

    def offer(self, frames):
        """Capture thread: immutable copies only; no JPEG, NPZ or projection."""
        import numpy as np
        rgb,depth=frames['rgb'],frames['depth']
        meta={'frames':{k:self.metadata(frames[k]) for k in ('rgb','depth')}}
        a,b=meta['frames']['rgb'],meta['frames']['depth']
        if (not a.get('transformation_valid') or not b.get('transformation_valid')
                or (a['width'],a['height'])!=(640,360) or (b['width'],b['height'])!=(640,400)
                or abs(a['device_timestamp_seconds']-b['device_timestamp_seconds'])>.020001):
            with self.lock:self.latest=None
            return
        with self.owner.lock:
            epoch=self.owner.session
            generation=self.owner.clock_sample_generation
            samples=list(self.owner.clock_pairs)
            calibration=deepcopy(self.owner.calibration)
        if not samples or not calibration or calibration['session']!=epoch:return
        sample=samples[-1];now=self.clock()
        if not 0<=now-sample['wall_after_mono_ns']/1e9<=2:return
        # Raw SDK host-time mapping is diagnostic. It is not an exposure-sync
        # qualification. Carry the bracket and semantics to the final receipt.
        source=a['host_monotonic_timestamp_seconds']
        offsets=[(sample['source_before_mono_ns']-sample['source_ns']-1000)/1e9,
                 (sample['source_after_mono_ns']-sample['source_ns']+1000)/1e9]
        source_interval=[source+v for v in offsets]
        if not 0<=now-source_interval[1]<=self.maximum_age_s:return
        nv12=np.array(rgb.getFrame(),copy=True);plane=np.array(depth.getFrame(),copy=True)
        if nv12.shape!=(540,640) or nv12.dtype!=np.uint8 or plane.shape!=(400,640) or plane.dtype!=np.uint16:return
        nv12.flags.writeable=plane.flags.writeable=False
        from bundle_ingest import calibration_content_id
        identity={'epoch':epoch,'generation':generation,'sequence':a['sequence'],
            'source_timestamp_s':source,'rgb_device_timestamp_s':a['device_timestamp_seconds'],
            'depth_sequence':b['sequence'],'depth_device_timestamp_s':b['device_timestamp_seconds'],
            'calibration_content_id':calibration_content_id(calibration),
            'depth_sha256':sha(plane.astype('<u2',copy=False).tobytes(order='C')),
            'projection_metadata_sha256':sha(encoded(meta))}
        key=sha(encoded(identity))
        value={'identity':identity,'key':key,'source_interval_s':source_interval,
               'metadata':meta,'calibration':calibration,'nv12':nv12,'depth':plane}
        with self.lock:
            scope=(epoch,generation,identity['calibration_content_id'])
            if scope!=self.epoch:
                self.retained.clear();self.projector.model=None;self.projector.model_key=None;self.epoch=scope
            if self.latest and source==self.latest['identity']['source_timestamp_s']:return
            if self.latest and source<self.latest['identity']['source_timestamp_s']:
                self.latest=None;self.retained.clear();return
            self.latest=value

    def _current(self,value):
        with self.owner.lock:
            if (self.owner.session!=value['identity']['epoch']
                    or self.owner.clock_sample_generation!=value['identity']['generation']):
                raise ValueError('tracking_source_changed')
        if not 0<=self.clock()-value['source_interval_s'][1]<self.maximum_age_s:
            raise ValueError('tracking_source_expired')

    def frame(self):
        if not self.encoding.acquire(False):raise ValueError('tracking_encoder_busy')
        try:
            with self.lock:value=self.latest
            if value is None:raise ValueError('tracking_frame_unavailable')
            self._current(value)
            jpeg=self.encoder(value['nv12'].tobytes())
            if not isinstance(jpeg,bytes) or not 4<=len(jpeg)<=262144:raise ValueError('tracking_jpeg_bound')
            self._current(value)
            with self.lock:
                self.retained[value['key']]=value
                while len(self.retained)>self.maximum_retained:self.retained.popitem(last=False)
                for key in list(self.retained):
                    if self.clock()-self.retained[key]['source_interval_s'][1]>=self.maximum_age_s:self.retained.pop(key)
            from fast_tracking_depth import encode_paired_depth_frame
            paired=encode_paired_depth_frame(value)
            self._current(value)
            return dict(schema='home-fast-camera-2',frame_key=value['key'],identity=value['identity'],
                source_interval_s=value['source_interval_s'],camera_reply_s=self.clock(),
                source_clock='sdk_rgb_host_timestamp',exposure_accuracy='unqualified',
                jpeg_sha256=sha(jpeg),jpeg_b64=base64.b64encode(jpeg).decode(),
                paired_depth=paired,projection_metadata=value['metadata'],calibration=value['calibration'])
        finally:self.encoding.release()

    def project(self, request):
        if not isinstance(request,dict) or set(request)!={'frame_key','identity','poses'}:raise ValueError('tracking_projection_contract')
        with self.lock:value=self.retained.get(request['frame_key'])
        if value is None or request['identity']!=value['identity']:raise ValueError('tracking_depth_frame_mismatch')
        self._current(value)
        instances=self.projector.project(value,request['poses'])
        self._current(value)
        return dict(schema='home-fast-depth-1',frame_key=value['key'],identity=value['identity'],instances=instances,
            source_interval_s=value['source_interval_s'],camera_reply_s=self.clock(),
            projection='unqualified_nominal_eeprom',lighting_authority=False,physical_permission=False)

"""Read-only, demand-driven preview from the existing Victoria camera owner.

No recorder, inference, database writes, background polling or DepthAI import.
The short memory cache bounds camera reads across concurrent viewers.
"""
import base64
import json
import math
import threading
import time
from store import LabError


class LivePreview:
    def __init__(self, fetch, *, site_id, camera_id, clock=time.time, monotonic=time.monotonic):
        self.fetch,self.site_id,self.camera_id=fetch,site_id,camera_id
        self.clock,self.monotonic=clock,monotonic
        self.lock=threading.Lock()
        self.next_read=0
        self.cached=None

    def read(self):
        if not self.lock.acquire(blocking=False):
            raise LabError('Preview read in progress',429,'preview_busy')
        try:
            now=self.monotonic()
            if now>=self.next_read:
                self.cached=None
                self.next_read=now+1
                try:
                    data,header=self.fetch()
                    if not isinstance(data,bytes) or not 4<=len(data)<=256*1024 or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
                        raise ValueError()
                    if not isinstance(header,str) or not 0<len(header)<=16384:raise ValueError()
                    meta=json.loads(base64.b64decode(header+'='*((-len(header))%4),altchars=b'-_',validate=True))
                    stamp=meta.get('observed_at_unix_ns')
                    uncertainty=meta.get('uncertainty_seconds')
                    if (meta.get('schema')!=2 or meta.get('width')!=640 or meta.get('height')!=360
                            or type(stamp) is not int or type(uncertainty) not in (int,float)
                            or not math.isfinite(uncertainty) or not 0<=uncertainty<=1):raise ValueError()
                    self.cached={'version':1,'site_id':self.site_id,'camera_id':self.camera_id,
                        'content_type':'image/jpeg','jpeg_base64':base64.b64encode(data).decode('ascii'),
                        'width':640,'height':360,'observed_at':stamp/1e9,
                        'uncertainty_seconds':uncertainty,'capture_epoch':meta.get('session'),
                        'sequence':meta.get('sequence'),'timing_generation':meta.get('timing_generation'),
                        'sample_interval_ms':1000,'recorded':False}
                except Exception:
                    raise LabError('Camera preview unavailable',503,'preview_unavailable') from None
            if self.cached is None:
                raise LabError('Camera preview unavailable',503,'preview_unavailable')
            age=self.clock()-self.cached['observed_at']
            if age<0 or age+self.cached['uncertainty_seconds']>3:
                self.cached=None
                raise LabError('Camera preview is stale',503,'preview_stale')
            return {**self.cached,'source_age_seconds':age}
        finally:
            self.lock.release()

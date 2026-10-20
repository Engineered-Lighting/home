"""Display-only RTSP reader: one bounded decoder, RAM only, no inference.

Decoded receipt time is not exposure time and cannot establish inference
freshness. No camera ownership, NTP sampling or recording is involved.
"""
import base64
import os
import select
import subprocess
import threading
import time
from store import LabError


class RTSPPreview:
    def __init__(self,source,site_id,*,popen=subprocess.Popen):
        self.source,self.site_id,self.popen=source,site_id,popen
        self.lock=threading.RLock()
        self.thread=None
        self.process=None
        self.stop=threading.Event()
        self.closed=False
        self.last_request=0
        self.frame=None
        self.sequence=0

    def command(self):
        return [self.source.ffmpeg,'-nostdin','-hide_banner','-loglevel','error',
            '-rtsp_transport','tcp','-threads','1','-i',self.source.url,
            '-map','0:v:0','-an','-vf','fps=1,scale=640:360',
            '-threads','1','-c:v','mjpeg','-q:v','5','-f','image2pipe','pipe:1']

    def read(self):
        with self.lock:
            if self.closed:raise LabError('Preview closed',503,'preview_unavailable')
            self.last_request=time.monotonic()
            if self.thread is None or not self.thread.is_alive():
                if self.process is not None and self.process.poll() is None:
                    raise LabError('Preview shutdown pending',503,'preview_unavailable')
                self.frame=None
                self.stop.clear()
                self.thread=threading.Thread(target=self._run,name='lab-display-preview',daemon=True)
                self.thread.start()
            if self.frame is None:raise LabError('Preview connecting',503,'preview_connecting')
            stamp,jpeg,sequence=self.frame
            age=time.monotonic()-stamp
            if age>3:raise LabError('Preview stale',503,'preview_stale')
            return {'version':1,'site_id':self.site_id,'camera_id':self.source.camera_id,
                'content_type':'image/jpeg','jpeg_base64':base64.b64encode(jpeg).decode(),
                'width':640,'height':360,'sequence':sequence,'sample_interval_ms':1000,
                'recorded':False,'timestamp_semantics':'decode_receipt_only',
                'exposure_timing':'unverified','decoded_age_seconds':age,
                'source_age_seconds':None,'uncertainty_seconds':None}

    def _run(self):
        process=None
        try:
            process=self.popen(self.command(),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,bufsize=0)
            with self.lock:self.process=process
            buffer=bytearray()
            while not self.stop.is_set():
                with self.lock:
                    if time.monotonic()-self.last_request>10:break
                if not select.select([process.stdout],[],[],.5)[0]:
                    if process.poll() is not None:break
                    continue
                part=os.read(process.stdout.fileno(),16384)
                if not part:break
                buffer.extend(part)
                while True:
                    end=buffer.find(b'\xff\xd9')
                    if end<0:break
                    jpeg=bytes(buffer[:end+2]);del buffer[:end+2]
                    if not jpeg.startswith(b'\xff\xd8') or len(jpeg)>256*1024:raise ValueError()
                    with self.lock:
                        self.sequence+=1
                        self.frame=(time.monotonic(),jpeg,self.sequence)
                if len(buffer)>256*1024:raise ValueError()
        except Exception:
            pass  # Never emit RTSP credentials or source content in logs.
        finally:
            if process is not None:
                try:
                    if process.poll() is None:process.terminate()
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:process.wait(timeout=2)
                    except subprocess.TimeoutExpired:pass
                if process.stdout is not None:process.stdout.close()
            with self.lock:self.frame=None

    def close(self):
        with self.lock:self.closed=True;self.stop.set();thread=self.thread
        if thread is not None:thread.join(timeout=5)
        return {'drained':thread is None or not thread.is_alive()}

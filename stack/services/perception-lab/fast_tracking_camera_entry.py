"""Run the pinned incumbent camera with the opt-in RAM tracking bridge.

No second device owner. The original camera source remains untouched. Exact
source/patch anchors fail closed. Removing the reviewed ExecStart override is
the complete camera rollback. No import-time execution.
"""
import hashlib
from pathlib import Path
import runpy
import sys
import threading

BASELINE='/home/engineeredlightingllm/victoria-camera-pilot/pilot-20260926/capture.py'
BASELINE_SHA='d052fc217b2d412cb877cfb49608e7dcef4a52526932b8d036a51380accb6132'


def patched_source(raw):
    if hashlib.sha256(raw).hexdigest()!=BASELINE_SHA:raise ValueError('camera_source_drift')
    text=raw.decode('utf-8')
    anchors=[
        ("                if group is not None:\n                    last_group = now\n",
         "                if group is not None:\n                    last_group = now\n                    try:\n                        state.fast_tracking.offer({name:group[name] for name in ('rgb','depth')})\n                    except Exception:\n                        with state.fast_tracking.lock:state.fast_tracking.latest=None\n"),
        ("    class Handler(BaseHTTPRequestHandler):\n", "    class Handler(BaseHTTPRequestHandler):\n        protocol_version='HTTP/1.1'\n"),
        ("            if self.path == '/frame.jpg':\n",
         "            if self.path == '/tracking/frame':\n                try:\n                    body=json.dumps(state.fast_tracking.frame(),separators=(',',':'),allow_nan=False).encode()\n                    status,content_type=200,'application/json'\n                except (ValueError,TypeError,KeyError):\n                    status,content_type,body=503,'application/json',b'{\"error\":\"tracking_unavailable\"}'\n            elif self.path == '/frame.jpg':\n"),
        ("        def log_message(self, *args):\n",
         "        def do_POST(self):\n            if (self.path!='/tracking/project' or not token or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)):\n                self.send_error(403);return\n            try:\n                length=self.headers.get('Content-Length','')\n                if (not length.isdecimal() or not 0<int(length)<=32768 or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type')!='application/json'):\n                    raise ValueError('body')\n                raw=self.rfile.read(int(length))\n                if len(raw)!=int(length):raise ValueError('body')\n                value=json.loads(raw,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))\n                body=json.dumps(state.fast_tracking.project(value),separators=(',',':'),allow_nan=False).encode();status=200\n            except (ValueError,TypeError,KeyError):\n                body=b'{\"error\":\"tracking_projection_unavailable\"}';status=409\n            self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers()\n            try:self.wfile.write(body)\n            except (BrokenPipeError,ConnectionResetError,TimeoutError):pass\n\n        def log_message(self, *args):\n"),
        ("            if ((self.path in ('/frame.jpg', '/timing') and not token) or\n",
         "            if ((self.path in ('/frame.jpg', '/timing', '/tracking/frame') and not token) or\n"),
    ]
    for before,after in anchors:
        if text.count(before)!=1:raise ValueError('camera_patch_anchor_drift')
        text=text.replace(before,after)
    return text


def main():
    source=Path(BASELINE)
    if source.is_symlink():raise ValueError('camera_source_link')
    code=patched_source(source.read_bytes())
    # Execute definitions without running its main, then install adapters.
    namespace={'__name__':'fast_tracking_camera_incumbent','__file__':str(source)}
    import types
    module=types.ModuleType(namespace['__name__']);module.__dict__.update(namespace)
    sys.modules[module.__name__]=module
    exec(compile(code,str(source),'exec'),module.__dict__)
    from fast_tracking_camera import FastCamera
    original_init=module.State.__init__;original_response=module.State.response
    original_encode=module.encode_bundle
    class LazyBundle:
        def __init__(self,arrays,metadata):
            import numpy as np
            self.arrays={k:np.array(v,copy=True) for k,v in arrays.items()}
            self.metadata=metadata;self.lock=threading.Lock();self.raw=None
        def bytes(self):
            with self.lock:
                if self.raw is None:self.raw=original_encode(self.arrays,self.metadata)
                return self.raw
    def initialize(state,*args,**kwargs):
        original_init(state,*args,**kwargs)
        state.fast_tracking=FastCamera(state,module.frame_metadata,module.encode_preview)
    def response(state,*args,**kwargs):
        status,mime,body=original_response(state,*args,**kwargs)
        if isinstance(body,LazyBundle):body=body.bytes()
        return status,mime,body
    module.State.__init__=initialize;module.State.response=response
    # Compression is deferred to the original /frame.npz consumer. Bytes and
    # metadata stay identical; fast capture cannot wait on an idle NPZ archive.
    module.encode_bundle=LazyBundle
    module.main()


if __name__=='__main__':main()

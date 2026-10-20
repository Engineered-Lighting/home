"""Inspected LA human-mode YOLOX detector; no loading on construction."""
from copy import deepcopy
from importlib.metadata import distribution,version
import threading
from worker_artifacts import verified_artifact
from yolox_loader import SOURCE_SHA256,construct
from yolox_worker import YoloXWorker,normalize_result

CHECKPOINT_SHA256='3dea6513388889f0fff4b77bf7a26013600321b9eb9ceb0e9a400a82572f5f23'


def load_humanart(path,manifest,*,intra_op_threads=4):
    target=verified_artifact(path,CHECKPOINT_SHA256,'.onnx',maximum_bytes=512*1024**2)
    if version('rtmlib')!='0.0.16':raise ValueError('HumanArt runtime version mismatch')
    package=distribution('rtmlib')
    for relative,sha in SOURCE_SHA256.items():
        verified_artifact(package.locate_file('rtmlib/'+relative),sha,'.py',maximum_bytes=1024**2)
    import onnxruntime as ort
    if ort.__version__!='1.23.2':raise ValueError('HumanArt ONNX runtime mismatch')
    if manifest['device']=='cuda:0':ort.preload_dlls()
    from rtmlib import YOLOX
    detector=construct(ort,YOLOX,target,manifest['device'],intra_op_threads=intra_op_threads)
    detector.det_mode,detector.nms_thr,detector.score_thr='human',.45,.7
    return detector


class HumanArtWorker(YoloXWorker):
    checkpoint_sha256=CHECKPOINT_SHA256
    def __init__(self,manifest,authorize,*,loader=load_humanart):
        self.manifest=deepcopy(manifest);m=self.manifest
        if (m.get('checkpoint')!='yolox_m_humanart_detector.onnx' or m.get('sha256')!=CHECKPOINT_SHA256
                or m.get('runtime_version')!='0.0.16' or m.get('onnxruntime_version')!='1.23.2'
                or m.get('precision')!='fp32' or m.get('device') not in {'cpu','cuda:0'}
                or m.get('scope')!='full_frame_person_detector' or m.get('score_threshold')!=.7
                or m.get('nms_threshold')!=.45
                or not all(isinstance(m.get(k),str) and 0<len(m[k])<=256 for k in
                           ('license','model_version','profile_id','device_id','engine_epoch'))
                or not callable(authorize) or not callable(loader)):
            raise ValueError('pinned HumanArt detector manifest required')
        self.authorize,self.loader=authorize,loader
        self.model=None;self.warmed=False;self.lock=threading.RLock()

    def normalize(self,raw,**source):
        boxes=raw.tolist() if hasattr(raw,'tolist') else raw
        return normalize_result((boxes,[0]*len(boxes)),['person']+['unused']*79,**source)

    def infer(self,*args,**kwargs):
        result=super().infer(*args,**kwargs)
        result['processing']['detector_mode']='human'
        result['processing']['reference_scope']='detector_only_without_incumbent_tracker_or_pose_scheduler'
        return result

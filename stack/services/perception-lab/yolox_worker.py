"""Full-frame incumbent detector comparison, not the crop/tracking pipeline."""
from copy import deepcopy
import threading
import time
from reasoning import finite
from ultralytics_worker import image_shape
from worker_artifacts import verified_artifact
from yolox_loader import CHECKPOINT_SHA256,load_sdk


def normalize_result(raw,names,*,frame_id,width,height):
    boxes,classes=[v.tolist() if hasattr(v,'tolist') else v for v in raw]
    if (len(boxes)!=len(classes) or len(boxes)>128 or len(names)!=80
            or any(not isinstance(n,str) or not 0<len(n)<=128 for n in names)):
        raise ValueError('bounded COCO detection arrays required')
    output={'schema_version':1,'frame_id':frame_id,'coordinate_space':'source_pixels',
        'objects':[],'boxes':[],'quality':{'confidence':'not_returned_by_incumbent',
        'metric_depth':'not_measured','clipped_boxes':0,'outside_frame_boxes':0}}
    for index,(box,category) in enumerate(zip(boxes,classes)):
        if (len(box)!=4 or not all(finite(v) for v in box)
                or not finite(category) or int(category)!=category or not 0<=category<80):
            raise ValueError('invalid incumbent detection')
        x1,y1,x2,y2=box
        if x2<=x1 or y2<=y1: raise ValueError('inverted incumbent box')
        clipped=[max(0,min(width,x1)),max(0,min(height,y1)),
                 max(0,min(width,x2)),max(0,min(height,y2))]
        if clipped[2]<=clipped[0] or clipped[3]<=clipped[1]:
            output['quality']['outside_frame_boxes']+=1
            continue
        changed=list(box)!=clipped
        output['quality']['clipped_boxes']+=int(changed)
        x1,y1,x2,y2=clipped
        identity=frame_id+':'+str(index)
        label=names[int(category)]
        # Deliberately omit confidence: a threshold is not a returned score.
        output['objects'].append({'label':label,'instance_id':identity})
        output['boxes'].append({'x':x1,'y':y1,'w':x2-x1,'h':y2-y1,
            'label':label,'instance_id':identity,'clipped_to_frame':changed})
    return output


class YoloXWorker:
    checkpoint_sha256=CHECKPOINT_SHA256
    def __init__(self,manifest,authorize,*,loader=load_sdk):
        self.manifest=deepcopy(manifest); m=self.manifest
        if (m.get('checkpoint')!='yolox_l.onnx' or m.get('sha256')!=CHECKPOINT_SHA256
                or m.get('runtime_version')!='0.0.16' or m.get('onnxruntime_version')!='1.23.2'
                or m.get('precision')!='fp32' or m.get('device') not in {'cpu','cuda:0'}
                or m.get('scope')!='full_frame_detector' or m.get('score_threshold')!=.45
                or m.get('nms_threshold')!=.45
                or not all(isinstance(m.get(k),str) and 0<len(m[k])<=256 for k in
                    ('license','model_version','profile_id','device_id','engine_epoch'))
                or not callable(authorize) or not callable(loader)):
            raise ValueError('pinned full-frame YOLOX manifest required')
        self.authorize,self.loader=authorize,loader
        self.model=None; self.warmed=False; self.lock=threading.RLock()

    def _admit(self,phase,request_id):
        if self.authorize(phase,deepcopy(self.manifest),request_id) is not True:
            raise ValueError('YOLOX workload not admitted')

    def load(self,path):
        with self.lock:
            if self.model is not None: return
            self._admit('load',None)
            path=verified_artifact(path,self.checkpoint_sha256,'.onnx',maximum_bytes=512*1024**2)
            self._admit('load',None)
            self.model=self.loader(path,deepcopy(self.manifest))

    def infer(self,image,*,frame_id,request_id):
        if not all(isinstance(v,str) and 0<len(v)<=128 for v in (frame_id,request_id)):
            raise ValueError('bounded source/request identity required')
        height,width=image_shape(image)
        with self.lock:
            if self.model is None: raise ValueError('YOLOX model not loaded')
            self._admit('infer' if self.warmed else 'first_infer',request_id)
            start=time.monotonic()
            raw=self.model(image)
            self.warmed=True
            result=self.normalize(raw,frame_id=frame_id,width=width,height=height)
            result['processing']={k:self.manifest[k] for k in ('checkpoint','runtime_version',
                'onnxruntime_version','precision','device_id','scope','score_threshold','nms_threshold')}
            result['processing'].update(artifact_sha256=self.checkpoint_sha256,
                input_format='BGR_uint8',input_size=[640,640],tracking='not_applied',
                crop_scheduling='not_applied',person_class='included',
                runtime_threads=deepcopy(getattr(self.model,'runtime_threads',None)),
                worker_compute_ms=(time.monotonic()-start)*1000)
            return result

    def normalize(self,raw,**source):
        return normalize_result(raw,self.model.COCO_CLASSES,**source)

    def execute(self,request,image_loader):
        for key,value in (('artifact_sha256',self.checkpoint_sha256),
                *((k,self.manifest[k]) for k in ('engine_epoch','model_version','profile_id','device_id'))):
            if request['profile'].get(key)!=value: raise ValueError('resident YOLOX profile mismatch')
        source=request['frame']
        image,meta=image_loader(deepcopy(source))
        for key in ('site_id','session_id','camera_id','capture_epoch','frame_id'):
            if not source.get(key) or meta.get(key)!=source[key]:
                raise ValueError('YOLOX input belongs to another observation')
        if meta.get('media_id') not in source['media_ids']:
            raise ValueError('YOLOX image not registered for request')
        result=self.infer(image,frame_id=source['frame_id'],request_id=request['request_id'])
        return {'request_id':request['request_id'],'engine_epoch':self.manifest['engine_epoch'],
            'artifact_sha256':self.checkpoint_sha256,'terminal':True,'state':'completed','result':result}

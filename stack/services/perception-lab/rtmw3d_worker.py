"""Pinned installed RTMW3D shared-crop reference; predicted Z is not stereo."""
from copy import deepcopy
from importlib.metadata import version,distribution
import threading
from reasoning import finite
from worker_artifacts import verified_artifact
from yolox_loader import construct
from rtmpose_worker import RTMPoseWorker,normalize_pose,JOINTS

CHECKPOINT='rtmw3d-x-installed-384x288'
SHA256='4a289c0e99d47eb595e99679d9d4a2d1def1b4241f9adcbafba44b9ff585ebcd'
SOURCE_SHA256={
    'tools/base.py':'0d287088838eee78cc8cb5ef722a53148f7c100bf73eed16457ce3f9f40b272a',
    'tools/pose_estimation/rtmpose3d.py':'94b143e4760173b056ef85dd95baaca89ca084756df0ecca88aa5735dc4daabf',
    'tools/pose_estimation/pre_processings.py':'2bd84ad2300c3bb43a831e18aff30588c6f8e570d41d492826cf540cfe8c0fcb',
    'tools/pose_estimation/post_processings.py':'6c372b34c0021a808c28cbc49206b1908e4cf9c820e859fde18c22a5362bcf05'}


def load_sdk(path,manifest,*,intra_op_threads=4):
    target=verified_artifact(path,SHA256,'.onnx',maximum_bytes=512*1024**2)
    if version('rtmlib')!='0.0.16':raise ValueError('RTMW runtime mismatch')
    package=distribution('rtmlib')
    for relative,sha in SOURCE_SHA256.items():
        verified_artifact(package.locate_file('rtmlib/'+relative),sha,'.py',maximum_bytes=1024**2)
    import onnxruntime as ort
    if ort.__version__!='1.23.2':raise ValueError('RTMW ONNX runtime mismatch')
    if manifest['device']=='cuda:0':ort.preload_dlls()
    from rtmlib import RTMPose3d
    tool=construct(ort,RTMPose3d,target,manifest['device'],intra_op_threads=intra_op_threads)
    tool.model_input_size=(288,384)
    tool.mean=(123.675,116.28,103.53);tool.std=(58.395,57.12,57.375)
    tool.to_openpose=False;tool.z_range=2.1744869
    return tool


class RTMW3DWorker(RTMPoseWorker):
    # The installed RTMPose3d route receives BGR (la-rtmw3d-reference); unlike
    # the Body7 export there is no RGB conversion, so it has its own identity.
    preprocessing_revision='rtmw3d-bgr-v1'
    model_input_channel_order='BGR'

    def __init__(self,manifest,authorize,*,loader=load_sdk):
        self.manifest=deepcopy(manifest);m=self.manifest
        if (m.get('checkpoint')!=CHECKPOINT or m.get('sha256')!=SHA256
                or m.get('input_channel_order')!=self.model_input_channel_order
                or m.get('source_sha256')!=SOURCE_SHA256 or m.get('runtime_version')!='0.0.16'
                or m.get('onnxruntime_version')!='1.23.2' or m.get('precision')!='fp32'
                or m.get('device') not in {'cpu','cuda:0'} or m.get('input_size')!=[288,384]
                or m.get('layout')!='coco_wholebody133' or m.get('format')!='onnx'
                or m.get('score_threshold')!=.5 or type(m.get('max_people')) is not int or not 1<=m['max_people']<=4
                or not all(isinstance(m.get(k),str) and 0<len(m[k])<=256 for k in
                           ('license','model_version','profile_id','device_id','engine_epoch'))
                or not callable(authorize) or not callable(loader)):
            raise ValueError('pinned installed RTMW3D manifest required')
        self.authorize,self.loader=authorize,loader
        self.model=None;self.warmed=False;self.lock=threading.RLock()

    def model_input(self,image):
        return image.copy()  # Registered BGR, unconverted; the SDK never holds the shared array.

    def session_threads(self):
        # As applied by yolox_loader.construct; unknown stays unknown, never assumed.
        threads=getattr(self.model,'runtime_threads',None)
        return deepcopy(threads) if isinstance(threads,dict) else None

    def normalize(self,raw,crops,*,frame_id,width,height,threshold):
        if raw is None:
            depths,scores,points=[],[],[]
        else:
            if len(raw)!=4:raise ValueError('RTMW expects four output arrays')
            depths,scores,_,points=raw
            depths=depths.tolist() if hasattr(depths,'tolist') else depths
            scores=scores.tolist() if hasattr(scores,'tolist') else scores
            points=points.tolist() if hasattr(points,'tolist') else points
        if not len(depths)==len(scores)==len(points)==len(crops):raise ValueError('RTMW person count mismatch')
        for xyz,xy,values in zip(depths,points,scores):
            if len(xyz)!=133 or len(xy)!=133 or len(values)!=133 or any(len(p)!=3 for p in xyz) or any(len(p)!=2 for p in xy):
                raise ValueError('RTMW requires 133 joints with 2D and predicted coordinates')
        output=normalize_pose([p[:17] for p in points],[s[:17] for s in scores],crops,
            frame_id=frame_id,width=width,height=height,threshold=threshold)
        for instance,crop,xyz,xy,values in zip(output['pose_instances'],crops,depths,points,scores):
            hips=[xyz[i][2] for i in (11,12)]
            hips_supported=all(finite(v) for v in hips) and all(
                all(finite(v) for v in (*xy[i],values[i])) and values[i]>=threshold
                and 0<=xy[i][0]<=width and 0<=xy[i][1]<=height for i in (11,12))
            origin=sum(v/2 for v in hips) if hips_supported else None
            joints=[]
            for i,(pixel,score,predicted) in enumerate(zip(xy,values,xyz)):
                valid=all(finite(v) for v in (*pixel,score))
                supported=valid and score>=threshold and 0<=pixel[0]<=width and 0<=pixel[1]<=height
                joints.append({'joint_id':i,'name':JOINTS[i] if i<17 else 'wholebody_'+str(i),
                    'pixel':pixel if valid else None,'score':score if finite(score) else None,
                    'status':'supported' if supported else 'uncertain',
                    'relative_z_model_units':predicted[2]-origin if supported and origin is not None and finite(predicted[2]) else None})
            instance.update(layout='coco_wholebody133',joints=joints,
                depth_semantics='monocular_model_prediction_relative_to_supported_hips_not_stereo')
        output['quality'].update(predicted_depth_frame='hip_relative_model_units',
            metric_depth='not_measured',room_position='not_available',
            overlay_edges='coco17_body_only',wholebody_joint_count=133)
        return output

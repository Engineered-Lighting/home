"""Fixed CPU pose stage for admitted detector workers; no implicit installation."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import sys

POLICY={'minimum_confidence':.5,'max_people':2,'revision':'person-crops-v1'}
POSE_SHA='5c0a4bf67953e6d2ac43ce15e77dc9d5d354ae18430a47d2c5963a7bc5683e3c'
MODEL_VERSION='detector-pose-v1'  # Pinned by live_detector_app pose_profiles.


def validate_pose(config):
    if set(config)!={'root','manifest','runtime_revision'}:
        raise ValueError('Exact pose provisioning required')
    root=Path(config['root'])
    permitted={Path('/home/engineeredlightingllm/perception-lab/model-cache/pose-preparation-20260927'),
               Path('/home/marcelo-lima/home-lab-yolo26-trial/pose-preparation-20260927')}
    if root not in permitted or root.is_symlink() or root.resolve()!=root:
        raise ValueError('Fixed pose cache required')
    m=config['manifest']
    if (m.get('sha256')!=POSE_SHA or m.get('device')!='cpu' or m.get('onnxruntime_version')!='1.23.2'
            or m.get('max_people')!=2 or not isinstance(config['runtime_revision'],str)
            or len(config['runtime_revision'])!=64):
        raise ValueError('Pinned bounded CPU pose stage required')
    return deepcopy(config)


def profile(detector,config):
    from rtmpose_worker import RTMPoseWorker
    from staged_specialist import composite_manifest
    config=validate_pose(config)
    value=composite_manifest(detector,config['manifest'],profile_id='yolo26s-rtmpose-cpu-v1',
        engine_epoch=detector['engine_epoch'],policy=POLICY,runtime_revision=config['runtime_revision'],
        model_version=MODEL_VERSION,pose_preprocessing_revision=RTMPoseWorker.preprocessing_revision)
    return {**value,'artifact_sha256':value['sha256']}


def compose(store,detector,config,fresh,pending):
    config=validate_pose(config)
    root=Path(config['root'])
    sites=list((root/'venv/lib').glob('python*/site-packages'))
    if len(sites)!=1 or sites[0].is_symlink():raise ValueError('Isolated pose runtime missing')
    # Existing detector dependencies retain precedence. Only missing ORT/RTMLib
    # packages resolve here; no package changes in the incumbent environment.
    sys.path.append(str(sites[0]))
    from rtmpose_worker import RTMPoseWorker
    from staged_specialist import DetectorPoseWorker
    from reasoning import digest
    m=config['manifest']
    pose_pending=set()
    def pose_admit(phase,proposed,rid):
        if proposed!=m or not fresh():return False
        if phase=='load':return rid is None
        if phase in {'infer','first_infer'} and rid in pose_pending:
            pose_pending.remove(rid);return True
        return False
    pose=RTMPoseWorker(m,pose_admit)
    def chain_admit(phase,manifest,rid):
        if not fresh() or rid not in pending:return False
        if phase=='detector':pending.add(digest([rid,0]))
        elif phase=='pose':
            pose_pending.add(digest([rid,1]));pending.remove(rid)
        return phase in {'pipeline','detector','pose'}
    worker=DetectorPoseWorker(store,detector,pose,profile_id='yolo26s-rtmpose-cpu-v1',
        engine_epoch=detector.manifest['engine_epoch'],authorize=chain_admit,model_version=MODEL_VERSION,
        minimum_confidence=POLICY['minimum_confidence'],max_people=2,policy_revision=POLICY['revision'],
        reservation_kind='remote',runtime_revision=config['runtime_revision'])
    pose.load(root/'rtmpose-m-body7.onnx')
    return worker

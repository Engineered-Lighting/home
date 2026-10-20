"""Sequential local detector/pose execution inside one durable paired reservation.

No retry after an uncertain stage. Individual workers and the composite both
require admission. RemoteReceiver can supply the remote execution ledger;
shared-crop experiments still require a separately provisioned crop source.
"""
from copy import deepcopy
import json
from reasoning import digest,finite
from keypoint_worker import KeypointWorker
from yolox_worker import YoloXWorker
from ultralytics_worker import UltralyticsWorker,image_shape
from rtmpose_worker import RTMPoseWorker


class StageExpired(Exception):
    """No next stage dispatched; any previous stage has terminal proof."""


def composite_manifest(detector,pose,*,profile_id,engine_epoch,policy,model_version,pose_preprocessing_revision,runtime_revision=None):
    # Epochs identify executions, not different weight artifacts. Keep the
    # registered profile stable across independently prepared sessions. The
    # pose worker class, not this function, names its preprocessing identity.
    stages=[{k:v for k,v in m.items() if k!='engine_epoch'} for m in (detector,pose)]
    result={'profile_id':profile_id,'engine_epoch':engine_epoch,
        'model_version':model_version,'device_id':detector['device_id']+'+'+pose['device_id'],
        'sha256':digest({'stages':stages,'crop_policy':policy,'pose_preprocessing_revision':pose_preprocessing_revision})}
    if runtime_revision is not None:result['runtime_revision']=runtime_revision
    return result


class DetectorPoseWorker:
    def __init__(self,store,detector,pose,*,profile_id,engine_epoch,authorize,model_version,
                 minimum_confidence,max_people,policy_revision,reservation_kind='paired',runtime_revision=None):
        if (not isinstance(detector,(KeypointWorker,YoloXWorker,UltralyticsWorker))
                or not isinstance(pose,RTMPoseWorker) or not callable(authorize)
                or type(max_people) is not int or not 1<=max_people<=pose.manifest['max_people']
                or not all(isinstance(v,str) and 0<len(v)<=128 for v in (profile_id,engine_epoch,policy_revision,model_version))):
            raise ValueError('explicit bounded detector/pose pipeline required')
        if isinstance(detector,YoloXWorker):
            if minimum_confidence is not None: raise ValueError('YOLOX does not return confidence')
        elif not finite(minimum_confidence) or not 0<=minimum_confidence<=1:
            raise ValueError('explicit returned-score threshold required')
        if reservation_kind not in {'paired','remote'}:
            raise ValueError('explicit execution ledger required')
        if (reservation_kind=='remote' or runtime_revision is not None) and (
                not isinstance(runtime_revision,str) or not 1<=len(runtime_revision)<=128):
            raise ValueError('remote composite needs a pinned runtime revision')
        self.reservation_kind=reservation_kind
        self.store,self.detector,self.pose,self.authorize=store,detector,pose,authorize
        self.stages=[deepcopy(detector.manifest),deepcopy(pose.manifest)]
        self.policy={'minimum_confidence':minimum_confidence,'max_people':max_people,'revision':policy_revision}
        self.identity={'model_version':model_version,'pose_preprocessing_revision':type(pose).preprocessing_revision}
        self.manifest=composite_manifest(*self.stages,profile_id=profile_id,engine_epoch=engine_epoch,
                                         policy=self.policy,runtime_revision=runtime_revision,**self.identity)
        with store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS specialist_chains (request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL)')

    def _admit(self,phase,request):
        if self.authorize(phase,deepcopy(self.manifest),request['request_id']) is not True:
            raise ValueError('detector/pose chain not admitted')

    def execute(self,request,image_loader):
        if [self.detector.manifest,self.pose.manifest]!=self.stages:
            raise ValueError('stage manifest changed')
        if composite_manifest(*self.stages,profile_id=self.manifest['profile_id'],engine_epoch=self.manifest['engine_epoch'],
                policy=self.policy,runtime_revision=self.manifest.get('runtime_revision'),**self.identity)!=self.manifest:
            raise ValueError('chain policy changed')
        for key in ('profile_id','engine_epoch','model_version','device_id'):
            if request['profile'].get(key)!=self.manifest[key]: raise ValueError('chain profile mismatch')
        if request['profile'].get('artifact_sha256')!=self.manifest['sha256']:
            raise ValueError('chain artifact manifest mismatch')
        self._admit('pipeline',request)
        # Parent reservation persists across both stages. Never dispatch a chain
        # outside PairedRunner or repeat it following an exception/crash.
        with self.store.transaction() as db:
            if self.reservation_kind=='paired':
                parent=db.execute('SELECT state,payload FROM paired_requests WHERE id=?',(request['request_id'],)).fetchone()
            else:
                parent=db.execute('SELECT e.state,i.payload FROM remote_execution e JOIN remote_inputs i ON e.site=i.site AND e.id=i.id WHERE e.site=? AND e.id=?',
                    (request['frame']['site_id'],request['request_id'])).fetchone()
            if not parent or parent['state'] not in {'dispatching','running'} or json.loads(parent['payload'])!=request:
                raise ValueError('active matching paired reservation required')
            if db.execute('SELECT 1 FROM specialist_chains WHERE request_id=?',(request['request_id'],)).fetchone():
                raise ValueError('chain already dispatched; reconcile parent reservation')
            db.execute('INSERT INTO specialist_chains VALUES(?,?,?)',(request['request_id'],digest(request),'dispatching'))
        frame=request['frame']; image,meta=image_loader(deepcopy(frame))
        height,width=image_shape(image)
        def source_loader(source):
            if source!=frame: raise ValueError('stage source changed')
            return image,deepcopy(meta)
        def run(worker,index,crops=None):
            if self.store.clock()>frame['valid_until']: raise StageExpired()
            self._admit('detector' if index==0 else 'pose',request)
            profile={**self.stages[index],'artifact_sha256':self.stages[index]['sha256']}
            child={**deepcopy(request),'request_id':digest([request['request_id'],index]),'profile':profile}
            receipt=worker.execute(child,source_loader) if index==0 else worker.execute(child,source_loader,lambda _:deepcopy(crops))
            if (receipt.get('request_id')!=child['request_id'] or receipt.get('engine_epoch')!=profile['engine_epoch']
                    or receipt.get('artifact_sha256')!=profile['artifact_sha256']
                    or receipt.get('terminal') is not True or receipt.get('state')!='completed'):
                raise ValueError('stage lacks matching completion proof')
            # Remote workers have an execution ledger, not a copy of the home
            # session database. Stage provenance travels in the terminal result.
            if self.reservation_kind=='paired':
                self.store.append_event(frame['session_id'],'perception.stage',{
                    'parent_request_id':request['request_id'],'arm':request['arm'],'generation':request['generation'],
                    'frame_id':frame['frame_id'],'capture_epoch':frame['capture_epoch'],
                    'stage':'detector' if index==0 else 'pose','profile':profile,'receipt':receipt})
            return receipt['result']
        def terminal_without_next_stage(state='cancelled',reason='source_expired_before_stage'):
            with self.store.transaction() as db:
                db.execute("UPDATE specialist_chains SET state=? WHERE request_id=?",(state,request['request_id']))
            return {'request_id':request['request_id'],'engine_epoch':self.manifest['engine_epoch'],
                'artifact_sha256':self.manifest['sha256'],'terminal':True,'state':state,
                'result':{'reason':reason}}
        try:
            detection=run(self.detector,0)
        except StageExpired:
            return terminal_without_next_stage()
        if (detection.get('frame_id')!=frame['frame_id'] or detection.get('coordinate_space')!='source_pixels'
                or not isinstance(detection.get('boxes'),list) or len(detection['boxes'])>128):
            raise ValueError('frame-bound detector boxes required')
        crops=[]
        for i,box in enumerate(detection['boxes']):
            if box.get('label')!='person': continue
            threshold=self.policy['minimum_confidence']
            if threshold is not None:
                score=box.get('confidence')
                if not finite(score) or not 0<=score<=1: raise ValueError('missing detector confidence')
                if score<threshold: continue
            coords=[box.get(k) for k in ('x','y','w','h')]
            if not all(finite(v) for v in coords): raise ValueError('invalid person box')
            x,y,w,h=coords
            if not 0<=x<x+w<=width or not 0<=y<y+h<=height: raise ValueError('person box outside frame')
            identity=digest([request['request_id'],i])[:32]
            old_identity=box.get('instance_id')
            if not isinstance(old_identity,str) or not old_identity:
                raise ValueError('detector person instance identity required')
            for obj in detection.get('objects',[])+detection.get('masks',[]):
                if obj.get('instance_id')==old_identity: obj['instance_id']=identity
            box['instance_id']=identity
            crops.append({'id':identity,'frame_id':frame['frame_id'],
                'xyxy':[x,y,x+w,y+h],'detector_version':self.stages[0]['model_version'],
                'detector_artifact_sha256':self.stages[0]['sha256']})
        if len(crops)>self.policy['max_people']:
            return terminal_without_next_stage('rejected','pose_crowd_budget_exceeded')
        bundle={**{k:frame[k] for k in ('site_id','session_id','camera_id','capture_epoch','frame_id')},
            'crops':crops,'crop_set_digest':digest(crops),'policy':self.policy,
            'reference':{'parent_request_id':request['request_id'],'stage':0,'arm':request['arm']},
            'available_at':self.store.clock()}
        try:
            pose=run(self.pose,1,bundle)
        except StageExpired:
            return terminal_without_next_stage()
        result=deepcopy(detection)
        # Detector timings alone are not the latency of the composite. Pose's
        # SDK call includes crop preprocessing and decoding; don't mislabel its
        # duration as a directly comparable kernel-only inference time.
        result.pop('timings_ms',None)
        result['stage_timings_ms']={'detector':deepcopy(detection.get('timings_ms',{})),
            'pose':{'model_call':pose['processing']['model_call_ms']}}
        for key in ('keypoints','skeleton_edges','pose_instances'): result[key]=pose[key]
        result['quality'].update(pose['quality'])
        # 'pipeline' is the structural label; the identity bound into the manifest
        # travels beside it so a non-default composite never reads as the default.
        result['processing']={'pipeline':'detector-pose-v1','manifest_sha256':self.manifest['sha256'],**self.identity,
            'detector':detection['processing'],'pose':pose['processing'],'crop_policy':self.policy,
            'correlated_evidence':True,'shared_source_request':request['request_id']}
        with self.store.transaction() as db:
            db.execute("UPDATE specialist_chains SET state='completed' WHERE request_id=?",(request['request_id'],))
        return {'request_id':request['request_id'],'engine_epoch':self.manifest['engine_epoch'],
            'artifact_sha256':self.manifest['sha256'],'terminal':True,'state':'completed','result':result}

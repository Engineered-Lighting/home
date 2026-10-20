"""Admitted Body7 RTMPose-m shared-crop adapter; no import-time model loading."""
from copy import deepcopy
from importlib.metadata import version, distribution
import re
import threading
import time

from reasoning import canonical, digest, finite
from ultralytics_worker import image_shape
from worker_artifacts import verified_artifact


CHECKPOINT="rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504"
SOURCE_FILES=("tools/base.py","tools/pose_estimation/rtmpose.py",
              "tools/pose_estimation/pre_processings.py","tools/pose_estimation/post_processings.py")
JOINTS=("nose","left_eye","right_eye","left_ear","right_ear","left_shoulder","right_shoulder",
        "left_elbow","right_elbow","left_wrist","right_wrist","left_hip","right_hip",
        "left_knee","right_knee","left_ankle","right_ankle")
EDGES=((0,1),(0,2),(1,3),(2,4),(5,6),(5,7),(7,9),(6,8),(8,10),
       (5,11),(6,12),(11,12),(11,13),(13,15),(12,14),(14,16))


def normalize_pose(keypoints, scores, crops, *, frame_id, width, height, threshold):
    points=keypoints.tolist() if hasattr(keypoints,"tolist") else keypoints
    values=scores.tolist() if hasattr(scores,"tolist") else scores
    if not len(points)==len(values)==len(crops) or len(crops)>16:
        raise ValueError("pose result does not match person crop set")
    output={"schema_version":1,"frame_id":frame_id,"coordinate_space":"source_pixels",
            "keypoints":[],"skeleton_edges":[],"pose_instances":[],
            "quality":{"coordinate_space":"source_pixels","image_width":width,"image_height":height,
                       "metric_depth":"not_measured","pose_score_semantics":"SimCC_response_not_probability"}}
    for crop,joints,raw_scores in zip(crops,points,values):
        if len(joints)!=17 or len(raw_scores)!=17:
            raise ValueError("expected Body7 COCO-17 keypoint layout")
        mapping={}; retained=[]
        for index,(point,score) in enumerate(zip(joints,raw_scores)):
            if not isinstance(point,(tuple,list)) or len(point)!=2:
                raise ValueError("invalid pose coordinate shape")
            x,y=point
            valid=all(finite(v) for v in (x,y,score))
            reason="nonfinite" if not valid else "outside_frame" if not 0<=x<=width or not 0<=y<=height else "low_response" if score<threshold else None
            # Retain every joint slot and raw score without pretending an omitted
            # joint is at pixel zero or assigning calibrated probability.
            retained.append({"joint_id":index,"name":JOINTS[index],
                "pixel":[x,y] if valid else None,"score":score if finite(score) else None,
                "status":"supported" if reason is None else "uncertain","reason":reason})
            if reason is None:
                mapping[index]=len(output["keypoints"])
                output["keypoints"].append({"x":x,"y":y,"score":score,
                    "joint_id":index,"instance_id":crop["id"]})
        output["skeleton_edges"].extend([[mapping[a],mapping[b]] for a,b in EDGES if a in mapping and b in mapping])
        output["pose_instances"].append({"instance_id":crop["id"],"crop_xyxy":crop["xyxy"],
            "layout":"coco17","joints":retained,"score_semantics":"SimCC_response_not_probability"})
    return output


def load_sdk(path, manifest):
    if version("rtmlib")!="0.0.16":
        raise ValueError("unvalidated RTMLib version")
    package=distribution("rtmlib")
    # Version alone does not establish source identity; the inspected version
    # lacked a retrievable matching Git tag. Require deployed file hashes too.
    for relative,sha in manifest["source_sha256"].items():
        installed=package.locate_file("rtmlib/"+relative)
        verified_artifact(installed,sha,".py",maximum_bytes=1024**2)
    import onnxruntime as ort
    if ort.__version__!=manifest["onnxruntime_version"]:
        raise ValueError("unvalidated ONNX Runtime version")
    desired="CPUExecutionProvider" if manifest["device"]=="cpu" else "CUDAExecutionProvider"
    if desired not in ort.get_available_providers():
        raise ValueError("requested pose accelerator provider is unavailable")
    from rtmlib import RTMPose
    # The pinned SDK constructor creates an unrestricted ORT thread pool. Use
    # its verified preprocessing/decoder with our explicitly bounded session.
    # These are the attributes set by the hashed BaseTool/RTMPose constructors.
    model=RTMPose.__new__(RTMPose)
    options=ort.SessionOptions()
    options.intra_op_num_threads=1
    options.inter_op_num_threads=1
    options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
    providers=[desired] if manifest['device']=='cpu' else [
        (desired,{'device_id':int(manifest['device'].split(':')[1])})]
    model.session=ort.InferenceSession(str(path),sess_options=options,providers=providers)
    model.onnx_model=str(path)
    model.model_input_size=(192,256)
    model.to_openpose=False
    model.backend='onnxruntime'
    model.device=manifest['device']
    model.mean=(123.675,116.28,103.53)
    model.std=(58.395,57.12,57.375)
    if model.session.get_providers()[0]!=desired:
        raise ValueError("pose runtime silently selected a different provider")
    model.session.disable_fallback()
    return model


class RTMPoseWorker:
    # Composite manifests bind this preprocessing identity; subclasses override it.
    preprocessing_revision="body7-rgb-v1"
    model_input_channel_order="RGB"

    def __init__(self,manifest,authorize,*,loader=load_sdk):
        self.manifest=deepcopy(manifest); m=self.manifest
        hashes=m.get("source_sha256",{})
        if (m.get("checkpoint")!=CHECKPOINT or m.get("runtime_version")!="0.0.16"
                or m.get("precision")!="fp32" or m.get("input_size")!=[192,256]
                or m.get("layout")!="coco17" or m.get("format")!="onnx"
                or not re.fullmatch(r"[a-f0-9]{64}",str(m.get("sha256","")))
                or not isinstance(hashes,dict) or set(hashes)!=set(SOURCE_FILES)
                or any(not re.fullmatch(r"[a-f0-9]{64}",str(v)) for v in hashes.values())
                or not all(isinstance(m.get(k),str) and m[k] for k in
                    ("license","model_version","profile_id","device_id","engine_epoch","onnxruntime_version"))
                or not re.fullmatch(r"cpu|cuda:\d+",str(m.get("device","")))
                or not finite(m.get("score_threshold")) or m["score_threshold"]<=0
                or type(m.get("max_people")) is not int or not 1<=m["max_people"]<=16):
            raise ValueError("exact Body7 artifact/runtime/crop manifest required")
        if not callable(authorize) or not callable(loader):
            raise ValueError("trusted admission and loader required")
        self.authorize,self.loader=authorize,loader
        self.model=None; self.warmed=False; self.lock=threading.RLock()

    def _admit(self,phase,request_id):
        if self.authorize(phase,deepcopy(self.manifest),request_id) is not True:
            raise ValueError("pose workload not admitted")

    def load(self,path):
        with self.lock:
            if self.model is not None:return
            self._admit("load",None)
            target=verified_artifact(path,self.manifest["sha256"],".onnx")
            self._admit("load",None)
            self.model=self.loader(target,deepcopy(self.manifest))

    def infer(self,image,crops,*,frame_id,request_id):
        if not all(isinstance(x,str) and 1<=len(x)<=128 for x in (frame_id,request_id)):
            raise ValueError("bounded frame/request identity required")
        height,width=image_shape(image)
        if not isinstance(crops,list) or len(crops)>self.manifest["max_people"]:
            raise ValueError("bounded person crop set required")
        ids=set()
        for crop in crops:
            if (not isinstance(crop,dict) or not isinstance(crop.get("id"),str)
                    or not 1<=len(crop["id"])<=128 or crop["id"] in ids
                    or crop.get("frame_id")!=frame_id or not isinstance(crop.get("detector_version"),str)
                    or not crop["detector_version"]):
                raise ValueError("unique frame-bound crop provenance required")
            box=crop.get("xyxy")
            if not isinstance(box,list) or len(box)!=4 or not all(finite(v) for v in box) or not 0<=box[0]<box[2]<=width or not 0<=box[1]<box[3]<=height:
                raise ValueError("crop must be a box in original image pixels")
            ids.add(crop["id"])
            if "detector_artifact_sha256" in crop and not re.fullmatch(r"[a-f0-9]{64}",str(crop["detector_artifact_sha256"])):
                raise ValueError("invalid detector artifact identity")
            if "source_event_id" in crop and (type(crop["source_event_id"]) is not int or crop["source_event_id"]<1):
                raise ValueError("invalid detector evidence identity")
        selected=[{k:c[k] for k in ("id","frame_id","detector_version","xyxy","detector_artifact_sha256","source_event_id") if k in c} for c in crops]
        with self.lock:
            if self.model is None:raise ValueError("pose model not loaded")
            # RTMLib treats [] as full-frame fallback. Never invoke that behavior
            # when the actual person detector supplied no candidates.
            elapsed=0
            if selected:
                self._admit("infer" if self.warmed else "first_infer",request_id)
                started=time.monotonic()
                raw=self.model(self.model_input(image),bboxes=[c["xyxy"] for c in selected])
                elapsed=(time.monotonic()-started)*1000
                self.warmed=True
            else:raw=None
            output=self.normalize(raw,selected,frame_id=frame_id,width=width,height=height,
                                  threshold=self.manifest["score_threshold"])
            output["processing"]={"runtime":"rtmlib","runtime_version":"0.0.16",
                "checkpoint":self.manifest['checkpoint'],"artifact_sha256":self.manifest["sha256"],
                "source_sha256":deepcopy(self.manifest["source_sha256"]),
                "onnxruntime_version":self.manifest["onnxruntime_version"],"precision":"fp32",
                "input_size_wh":deepcopy(self.manifest['input_size']),"layout":self.manifest['layout'],"input_format":"BGR_uint8",
                "model_input_channel_order":self.model_input_channel_order,"preprocessing_revision":self.preprocessing_revision,
                "runtime_threads":self.session_threads(),
                "score_threshold":self.manifest["score_threshold"],"crop_set_digest":digest(selected),
                "crop_count":len(selected),"device_id":self.manifest["device_id"],
                "model_call_ms":elapsed,"model_invoked":bool(selected)}
            return output

    def model_input(self,image):
        # The pinned export's pipeline.json specifies to_rgb=true.
        # RegisteredImage supplies BGR, while RTMLib 0.0.16's pose
        # preprocessing normalizes channels without converting them.
        import numpy as np
        return np.ascontiguousarray(image[:,:,::-1])

    def session_threads(self):
        return {"intra_op":1,"inter_op":1,"execution":"sequential"}

    def normalize(self,raw,crops,**options):
        points,scores=raw if raw is not None else ([],[])
        return normalize_pose(points,scores,crops,**options)

    def execute(self,request,image_loader,crop_loader):
        for key,value in (("artifact_sha256",self.manifest["sha256"]),
                          *((k,self.manifest[k]) for k in ("engine_epoch","model_version","profile_id","device_id"))):
            if request["profile"].get(key)!=value:raise ValueError("resident pose profile mismatch")
        source=request["frame"]
        image,meta=image_loader(deepcopy(source))
        bundle=crop_loader(deepcopy(source))
        for key in ("site_id","session_id","camera_id","capture_epoch","frame_id"):
            if not source.get(key) or any(value.get(key)!=source[key] for value in (meta,bundle)):
                raise ValueError("pose input belongs to another observation")
        if meta.get("media_id") not in source["media_ids"]:
            raise ValueError("pose image is not registered for request")
        if bundle.get("crop_set_digest")!=digest(bundle["crops"]):
            raise ValueError("registered crop set digest mismatch")
        result=self.infer(image,bundle["crops"],frame_id=source["frame_id"],request_id=request["request_id"])
        result["processing"]["crop_reference"]={k:deepcopy(bundle[k]) for k in ("reference","source_event_id","policy","available_at") if k in bundle}
        return {"request_id":request["request_id"],"engine_epoch":self.manifest["engine_epoch"],
            "artifact_sha256":self.manifest["sha256"],"terminal":True,"state":"completed","result":result}

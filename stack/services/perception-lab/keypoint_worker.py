"""Registered-frame Keypoint R-CNN specialist; explicit load and admission only."""
from copy import deepcopy
import threading
import time
from keypoint_loader import load_sdk,CHECKPOINT_SHA256,TORCH_VERSION,VISION_VERSION
from keypoint_reference import normalize_reference
from reasoning import finite
from ultralytics_worker import image_shape
from worker_artifacts import verified_artifact


def run_sdk(model,image,device):
    import torch
    # RegisteredImage is BGR uint8; the incumbent consumes RGB CHW float / 255.
    tensor=torch.from_numpy(image[:,:,::-1].copy()).permute(2,0,1).float().div_(255).to(device)
    with torch.inference_mode():
        return model([tensor])[0]


class KeypointWorker:
    def __init__(self,manifest,authorize,*,loader=load_sdk,invoke=run_sdk):
        self.manifest=deepcopy(manifest); m=self.manifest
        if (m.get("checkpoint")!="KeypointRCNN_ResNet50_FPN_Weights.COCO_V1"
                or m.get("sha256")!=CHECKPOINT_SHA256 or m.get("precision")!="fp32"
                or m.get("torch_version")!=TORCH_VERSION or m.get("torchvision_version")!=VISION_VERSION
                or m.get("device") not in {"cpu","cuda:0"}
                or m.get("person_threshold")!=.1 or not finite(m.get("joint_threshold"))
                or not all(isinstance(m.get(k),str) and 0<len(m[k])<=256 for k in
                    ("license","model_version","profile_id","device_id","engine_epoch"))
                or not all(callable(f) for f in (authorize,loader,invoke))):
            raise ValueError("pinned incumbent manifest and explicit score policy required")
        self.authorize,self.loader,self.invoke=authorize,loader,invoke
        self.model=None; self.warmed=False; self.lock=threading.RLock()

    def _admit(self,phase,request_id):
        if self.authorize(phase,deepcopy(self.manifest),request_id) is not True:
            raise ValueError("incumbent workload not admitted")

    def load(self,path):
        with self.lock:
            if self.model is not None: return
            self._admit("load",None)
            path=verified_artifact(path,CHECKPOINT_SHA256,".pth",maximum_bytes=512*1024**2)
            self._admit("load",None)
            self.model=self.loader(path,deepcopy(self.manifest))

    def infer(self,image,*,frame_id,request_id):
        if not all(isinstance(x,str) and 0<len(x)<=128 for x in (frame_id,request_id)):
            raise ValueError("bounded frame/request identity required")
        height,width=image_shape(image)
        with self.lock:
            if self.model is None: raise ValueError("incumbent model not loaded")
            self._admit("infer" if self.warmed else "first_infer",request_id)
            started=time.monotonic()
            raw=self.invoke(self.model,image,self.manifest["device"])
            self.warmed=True
            result=normalize_reference(raw,frame_id=frame_id,width=width,height=height,
                person_threshold=.1,joint_threshold=self.manifest["joint_threshold"])
            result["processing"]={k:self.manifest[k] for k in
                ("checkpoint","torch_version","torchvision_version","precision","device_id","person_threshold","joint_threshold")}
            result["processing"].update(artifact_sha256=CHECKPOINT_SHA256,
                input_format="BGR_uint8_to_RGB_CHW_float32_div255",normalization="detector_internal_defaults",
                worker_compute_ms=(time.monotonic()-started)*1000,tracking="not_applied")
            return result

    def execute(self,request,image_loader):
        for key,value in (("artifact_sha256",CHECKPOINT_SHA256),
                *((k,self.manifest[k]) for k in ("engine_epoch","model_version","profile_id","device_id"))):
            if request["profile"].get(key)!=value: raise ValueError("resident incumbent profile mismatch")
        source=request["frame"]
        image,meta=image_loader(deepcopy(source))
        for key in ("site_id","session_id","camera_id","capture_epoch","frame_id"):
            if not source.get(key) or meta.get(key)!=source[key]:
                raise ValueError("incumbent input belongs to another observation")
        if meta.get("media_id") not in source["media_ids"]:
            raise ValueError("incumbent image not registered for request")
        result=self.infer(image,frame_id=source["frame_id"],request_id=request["request_id"])
        return {"request_id":request["request_id"],"engine_epoch":self.manifest["engine_epoch"],
            "artifact_sha256":CHECKPOINT_SHA256,"terminal":True,"state":"completed","result":result}

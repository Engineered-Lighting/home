"""YOLO26 detection/segmentation bridge for an explicitly admitted worker.

API target: ultralytics 8.4.0. No SDK import, download, load or inference on import
or construction. Device builds/checkpoints still require actual commissioned
manifests. This adapter is not the common resource-admission service.
"""
from copy import deepcopy
from importlib.metadata import version
import os
from pathlib import Path
import re
import threading

from reasoning import canonical, finite
from worker_artifacts import verified_artifact


SDK_VERSION = "8.4.0"
# Masks must leave room for provenance and receipt fields under the 256 KiB
# result bound; dropping them after the model ran beats an unproven execution.
MASK_RESULT_BUDGET = 240*1024


def _list(tensor):
    # Boxes properties are tensors; masks.xy entries are numpy arrays in 8.4.0.
    value = tensor.cpu() if hasattr(tensor, "cpu") else tensor
    return value.tolist()


def _polygon(points):
    # 0.1 px grid, then drop repeated vertices and vertices lying strictly
    # between their neighbours: the quantized outline is unchanged. This is
    # never a lossy contour approximation.
    grid = [(round(x*10), round(y*10)) for x, y in points]
    ring = [p for i, p in enumerate(grid) if p != grid[i-1]]
    def redundant(i):
        (ax, ay), (bx, by), (cx, cy) = ring[i-1], ring[i], ring[(i+1) % len(ring)]
        return (ax-bx)*(cy-by) == (ay-by)*(cx-bx) and (ax-bx)*(cx-bx)+(ay-by)*(cy-by) < 0
    kept = [p for i, p in enumerate(ring) if not redundant(i)]
    # A degenerate outline keeps its quantized vertices rather than vanishing.
    ring = kept if len(kept) >= 3 else ring if len(ring) >= 3 else grid
    return [[x/10, y/10] for x, y in ring]


def normalize_result(result, *, frame_id, width, height, task):
    if task not in {"detect", "segment"} or tuple(result.orig_shape) != (height, width):
        raise ValueError("task or original frame dimensions mismatch")
    output = {"schema_version":1,"frame_id":frame_id,"coordinate_space":"source_pixels",
              "objects":[],"boxes":[],"quality":{}}
    boxes = result.boxes
    if boxes is None:
        raise ValueError("detector did not supply boxes")
    xyxy, scores, classes = _list(boxes.xyxy), _list(boxes.conf), _list(boxes.cls)
    if not len(xyxy) == len(scores) == len(classes) or len(xyxy) > 128:
        raise ValueError("invalid bounded detection result")
    for index, (coords, score, category) in enumerate(zip(xyxy, scores, classes)):
        if (len(coords) != 4 or not all(finite(v) for v in coords)
                or not finite(score) or not 0 <= score <= 1
                or not finite(category) or int(category) != category):
            raise ValueError("invalid detection coordinates or probability")
        label = result.names[int(category)]
        if not isinstance(label,str) or not 1 <= len(label) <= 128:
            raise ValueError("invalid class name")
        x1,y1,x2,y2 = coords
        if not 0 <= x1 < x2 <= width or not 0 <= y1 < y2 <= height:
            raise ValueError("box is not in original source pixels")
        instance = frame_id+":"+str(index)
        output["objects"].append({"label":label,"confidence":score,"instance_id":instance})
        output["boxes"].append({"x":x1,"y":y1,"w":x2-x1,"h":y2-y1,
            "label":label,"confidence":score,"instance_id":instance})
    if task == "segment":
        if not xyxy and result.masks is None:
            output["masks"] = []
        elif result.masks is None:
            output["quality"]["masks"] = "not_returned"
        else:
            try:
                polygons = result.masks.xy
                if len(polygons) != len(xyxy):
                    raise ValueError()
                masks = []
                total = 0
                for index, poly in enumerate(polygons):
                    points = _list(poly)
                    total += len(points)
                    if not 3 <= len(points) <= 2048 or total > 8192:
                        raise ValueError()
                    if any(len(p)!=2 or not all(finite(v) for v in p)
                           or not 0<=p[0]<=width or not 0<=p[1]<=height for p in points):
                        raise ValueError()
                    masks.append({"instance_id":frame_id+":"+str(index),"polygons":[_polygon(points)]})
                output["masks"] = masks
                output["quality"]["mask_coordinate_precision_px"] = 0.1
            except (ValueError,TypeError,AttributeError):
                # Keep valid boxes; never label a truncated contour as complete.
                output["quality"]["masks"] = "invalid_or_exceeds_contour_budget"
            if "masks" in output and len(canonical(output).encode()) > MASK_RESULT_BUDGET:
                # All or none: a partial mask set would misstate coverage.
                del output["masks"]
                output["quality"].pop("mask_coordinate_precision_px", None)
                output["quality"]["masks"] = "exceeds_evidence_budget"
    if len(canonical(output).encode()) > 256*1024:
        raise ValueError("normalized result exceeds evidence budget")
    return output


def load_sdk(path, task):
    # These must be set by provisioned worker startup before importing the SDK.
    # Missing packages are an error, never an invitation to install them.
    if os.environ.get("YOLO_AUTOINSTALL", "").lower() != "false":
        raise ValueError("automatic dependency installation must be disabled")
    if version("ultralytics") != SDK_VERSION:
        raise ValueError("unvalidated Ultralytics runtime version")
    from ultralytics import YOLO
    from ultralytics.utils import AUTOINSTALL
    if AUTOINSTALL:
        raise ValueError("SDK was imported before automatic installation was disabled")
    return YOLO(str(path), task=task)


def image_shape(image):
    if isinstance(image,(str,Path,bytes,int)) or image is None:
        raise ValueError("decoded BGR array required; source URLs and devices are forbidden")
    import numpy as np
    if (not isinstance(image,np.ndarray) or image.dtype != np.uint8 or image.ndim != 3
            or image.shape[2] != 3 or not image.flags.c_contiguous
            or not 0 < image.shape[0] <= 2160 or not 0 < image.shape[1] <= 3840):
        raise ValueError("bounded contiguous uint8 BGR array required")
    return image.shape[:2]


class UltralyticsWorker:
    def __init__(self, manifest, authorize, *, loader=load_sdk):
        self.manifest = deepcopy(manifest)
        m = self.manifest
        if (m.get("runtime_version") != SDK_VERSION or m.get("task") not in {"detect","segment"}
                or m.get("checkpoint") != ("yolo26s" if m["task"] == "detect" else "yolo26s-seg")
                or m.get("precision") not in {"fp16","fp32"}
                or not re.fullmatch(r"[a-f0-9]{64}",str(m.get("sha256","")))
                or not all(isinstance(m.get(k),str) and m[k] for k in ("license","device_id","engine_epoch","profile_id","model_version"))
                or not isinstance(m.get("device"),str) or not re.fullmatch(r"cpu|cuda:\d+",m["device"])
                or m.get("format") not in {"pt","engine"}):
            raise ValueError("exact supported model/runtime/device manifest required")
        if m["device"] == "cpu" and (m["precision"] != "fp32" or m["format"] == "engine"):
            raise ValueError("CPU requires FP32 PyTorch baseline")
        p = m.get("preprocessing",{})
        if (set(p) != {"imgsz","confidence","iou","max_det","retina_masks"}
                or not isinstance(p["imgsz"],list) or len(p["imgsz"]) != 2
                or any(type(v) is not int or not 32<=v<=1280 or v%32 for v in p["imgsz"])
                or not all(finite(p[k]) and 0<=p[k]<=1 for k in ("confidence","iou"))
                or type(p["max_det"]) is not int or not 1<=p["max_det"]<=128
                or type(p["retina_masks"]) is not bool):
            raise ValueError("explicit bounded preprocessing contract required")
        if not callable(authorize) or not callable(loader):
            raise ValueError("trusted admission and loader required")
        self.authorize, self.loader = authorize, loader
        self.model = None
        self.warmed = False
        self.lock = threading.RLock()

    def _admit(self, phase, request_id):
        # The common admission owner must validate live reservation, device,
        # startup/warmup peaks, task authorization and exact manifest identity.
        if self.authorize(phase,deepcopy(self.manifest),request_id) is not True:
            raise ValueError("model workload not admitted")

    def load(self, path):
        with self.lock:
            if self.model is not None:
                return
            self._admit("load",None)
            target=verified_artifact(path,self.manifest["sha256"],"."+self.manifest["format"])
            self._admit("load",None)
            # Provisioning must make verified artifacts immutable while resident.
            self.model=self.loader(target,self.manifest["task"])

    def infer(self, image, *, frame_id, request_id):
        if not all(isinstance(x,str) and 1<=len(x)<=128 for x in (frame_id,request_id)):
            raise ValueError("bounded source/request identity required")
        with self.lock:
            if self.model is None:
                raise ValueError("model not loaded")
            height,width=image_shape(image)
            self._admit("infer" if self.warmed else "infer_with_warmup",request_id)
            m,p=self.manifest,self.manifest["preprocessing"]
            results=self.model.predict(source=image,stream=False,imgsz=p["imgsz"],rect=False,
                conf=p["confidence"],iou=p["iou"],max_det=p["max_det"],
                retina_masks=p["retina_masks"],half=m["precision"]=="fp16",device=m["device"],
                batch=1,augment=False,agnostic_nms=False,classes=None,
                save=False,save_txt=False,save_crop=False,show=False,verbose=False,visualize=False)
            # The model has run; a later normalization refusal cannot undo warmup.
            self.warmed=True
            if len(results)!=1:
                raise ValueError("worker must return exactly one offered frame")
            output=normalize_result(results[0],frame_id=frame_id,width=width,height=height,task=m["task"])
            output["processing"]={"runtime":"ultralytics","runtime_version":SDK_VERSION,
                "artifact_sha256":m["sha256"],"checkpoint":m["checkpoint"],"precision":m["precision"],
                "preprocessing":deepcopy(p),"input_format":"BGR_uint8","rect":False,
                "device_id":m["device_id"],"engine_epoch":m["engine_epoch"]}
            # Only documented stage timings; no fabricated upload/E2E latency.
            speed=getattr(results[0],"speed",{})
            output["timings_ms"]={k:v for k,v in speed.items() if k in {"preprocess","inference","postprocess"} and finite(v) and v>=0}
            return output

    def execute(self, request, image_loader):
        """Worker side of the common-admission request/terminal receipt contract.

        image_loader resolves only registered media and returns (BGR array,
        verified metadata). It is provisioned locally, never a caller URL fetcher.
        The upstream admission service owns reservations and transport failures.
        """
        profile=request["profile"]
        for key,expected in (("artifact_sha256",self.manifest["sha256"]),
                             ("engine_epoch",self.manifest["engine_epoch"]),
                             ("model_version",self.manifest["model_version"]),
                             ("profile_id",self.manifest["profile_id"]),
                             ("device_id",self.manifest["device_id"])):
            if profile.get(key)!=expected:
                raise ValueError("request does not match resident profile")
        source=request["frame"]
        image,meta=image_loader(deepcopy(source))
        for key in ("frame_id","capture_epoch","site_id","camera_id","session_id"):
            if not source.get(key) or source[key]!=meta.get(key):
                raise ValueError("decoded image belongs to another source")
        if meta.get("media_id") not in source["media_ids"]:
            raise ValueError("decoded image is not registered for this request")
        result=self.infer(image,frame_id=source["frame_id"],request_id=request["request_id"])
        return {"request_id":request["request_id"],"engine_epoch":self.manifest["engine_epoch"],
            "artifact_sha256":self.manifest["sha256"],"terminal":True,"state":"completed","result":result}

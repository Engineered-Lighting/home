"""Fixed local adapter routing; no implicit model preparation or remote fallback."""
from copy import deepcopy
from types import MappingProxyType
from keypoint_worker import KeypointWorker
from yolox_worker import YoloXWorker
from staged_specialist import DetectorPoseWorker
from rtmpose_worker import RTMPoseWorker
from ultralytics_worker import UltralyticsWorker
from registered_image import RegisteredImage
from remote_specialist import RemoteSpecialist


class RegisteredSpecialists:
    def __init__(self,store,workers,*,crop_loaders=None):
        workers=list(workers)
        if not 1<=len(workers)<=16:
            raise ValueError("bounded explicitly provisioned worker set required")
        self.store=store
        self.images=RegisteredImage(store)
        bindings={}
        for worker in workers:
            if not isinstance(worker,(KeypointWorker,YoloXWorker,RTMPoseWorker,UltralyticsWorker,DetectorPoseWorker,RemoteSpecialist)):
                raise ValueError("unsupported specialist adapter")
            manifest=worker.profile if isinstance(worker,RemoteSpecialist) else worker.manifest
            if isinstance(worker,RemoteSpecialist) and worker.store is not store:
                raise ValueError('remote adapter must use controller-owned ledger')
            key=manifest["profile_id"]
            if key in bindings: raise ValueError("duplicate specialist profile")
            bindings[key]=(worker,deepcopy(manifest))
        crops=dict(crop_loaders or {})
        pose_ids={key for key,(worker,_) in bindings.items() if isinstance(worker,RTMPoseWorker)}
        if set(crops)!=pose_ids or any(not callable(loader) for loader in crops.values()):
            raise ValueError("each pose profile needs its registered crop resolver")
        self.bindings=MappingProxyType(bindings)
        self.crops=MappingProxyType(crops)

    def __call__(self,request):
        # Do not let a worker or loader mutate the paired runner's durable input.
        request=deepcopy(request)
        key=request.get("profile",{}).get("profile_id")
        if key not in self.bindings: raise ValueError("specialist profile not provisioned")
        worker,manifest=self.bindings[key]
        current=worker.profile if isinstance(worker,RemoteSpecialist) else worker.manifest
        if current!=manifest:
            raise ValueError("resident specialist manifest changed; rebind explicitly")
        frame=request["frame"]
        session=self.store.get_session(frame["session_id"])
        if frame.get("site_id")!=self.store.settings.site_id or frame.get("camera_id")!=session["camera_id"]:
            raise ValueError("specialist source outside provisioned site")
        if isinstance(worker,RemoteSpecialist):
            return worker(request)
        if isinstance(worker,RTMPoseWorker):
            return worker.execute(request,self.images,self.crops[key])
        return worker.execute(request,self.images)

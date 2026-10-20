"""Lossless sampled OAK numerical archive; no inferred RGB/depth alignment."""
import io
import json
from recorder import validate_npz
from reasoning import canonical, digest, finite
from store import identifier


def calibration_content_id(calibration):
    """Session-independent identity of the camera owner's calibration content.

    /calibration embeds a per-capture-session UUID ('session') that changes on
    every watchdog/reconnect restart. It stays provenance (calibration_id);
    physical validation binds this digest of everything else (EEPROM, device,
    SDK, stereo size, semantics), so any real calibration change still differs.
    """
    return digest(["oak-calibration-content-v1",{k:v for k,v in calibration.items() if k!="session"}])


class BundleIngestor:
    def __init__(self,store,*,site_id,camera_id):
        self.store,self.site_id,self.camera_id=store,site_id,camera_id

    def ingest(self,sid,data,calibration):
        session=self.store.get_session(sid)
        if session["state"]!="recording" or session["site_id"]!=self.site_id or session["camera_id"]!=self.camera_id:
            raise ValueError("registered recording source required")
        if not isinstance(data,bytes) or not 0<len(data)<=4*1024**2:
            raise ValueError("bundle encoded size exceeds bound")
        validate_npz(data,4*1024**2)
        import numpy as np
        planes={}
        with np.load(io.BytesIO(data),allow_pickle=False) as archive:
            if set(archive.files)!={"depth_mm","left","right","rgb_nv12","metadata_json"}:
                raise ValueError("unexpected OAK bundle members")
            for name,shape,kind in (("depth_mm",(400,640),"uint16"),("left",(400,640),"uint8"),
                    ("right",(400,640),"uint8"),("rgb_nv12",(540,640),"uint8")):
                array=archive[name]
                if array.shape!=shape or array.dtype!=np.dtype(kind):
                    raise ValueError("OAK bundle shape or dtype mismatch")
                if name in {"depth_mm","left","right"}:
                    planes[name]=array
            encoded=archive["metadata_json"]
            if encoded.dtype!=np.uint8 or encoded.ndim!=1 or len(encoded)>32768:
                raise ValueError("bounded bundle metadata required")
            meta=json.loads(encoded.tobytes())
        epoch=identifier(meta.get("session"))
        if (meta.get("schema")!=1 or meta.get("depth_alignment")!="rectified_left"
                or meta.get("depth_units")!="millimetres" or meta.get("invalid_depth")!=0
                or meta.get("captured_unix_ns_semantics")!="bundle_packaging_time_not_sensor_exposure"):
            raise ValueError("explicit numerical semantics required")
        if (not isinstance(calibration,dict) or calibration.get("session")!=epoch
                or calibration.get("depth_alignment")!="rectified_left" or calibration.get("depth_units")!="millimetres"
                or len(canonical(calibration).encode())>24576):
            raise ValueError("bounded matching calibration required")
        frames=meta.get("frames",{})
        if set(frames)!={"rgb","depth","left","right"}:
            raise ValueError("all source frame metadata required")
        identities=[]
        for name,frame in sorted(frames.items()):
            if (type(frame.get("sequence")) is not int or frame["sequence"]<0
                    or not finite(frame.get("device_timestamp_seconds"))
                    or not finite(frame.get("host_monotonic_timestamp_seconds"))):
                raise ValueError("source frame identity and clocks required")
            identities.append([name,frame["sequence"]])
        cid=digest(calibration)
        metadata={"site_id":self.site_id,"camera_id":self.camera_id,"capture_epoch":epoch,
            "stream":"rgbd_bundle","format":"numeric_npz","archive_kind":"sampled_latest_bundles",
            "depth_alignment":"rectified_left","units":"millimetres","invalid_value":0,
            "calibration_id":cid,"calibration_content_id":calibration_content_id(calibration),
            "calibration":calibration,"source_metadata":meta,
            "clock_mapping_status":"unverified","geometric_validity":"unvalidated",
            "continuous_capture":False,"rgb_aligned":False}
        # Preserve original numerical bytes and transformations together. Receipt
        # time is the media created_at field, never a fabricated observation time.
        item=self.store.add_media(sid,data,"application/x-npz",metadata,canonical([epoch,"rgbd",identities]))
        # Browser/native readers consume bounded registered objects, not NPZ or
        # caller-selected paths. These reversible views retain the original
        # bundle and use its first receipt time, never pretend to be exposure
        # synchronized with encoded RGB. Retrying repairs a partial derivative
        # set using the same keys without duplicating archive data.
        self._views(sid,item,session,planes)
        return item

    def _views(self,sid,item,session,planes):
        from PIL import Image
        meta=item["metadata"]
        common={"site_id":self.site_id,"camera_id":self.camera_id,
            "capture_epoch":meta["capture_epoch"],"source_media_id":item["id"],
            "source_sha256":item["sha256"],"calibration_id":meta["calibration_id"],
            "calibration_content_id":meta["calibration_content_id"],
            "archive_kind":"sampled_latest_bundles","clock_mapping_status":"unverified",
            "timeline_basis":"controller_receipt","session_time_ms":max(0,round((item["created_at"]-session["started_at"])*1000)),
            "geometric_validity":"unvalidated","rgb_aligned":False,"width":640,"height":400}
        for name,modality in (("depth_mm","depth"),("left","left"),("right","right")):
            view={**common,"modality":modality,"stream":modality,
                "source_frame":meta["source_metadata"]["frames"]["depth" if name=="depth_mm" else name]}
            if name=="depth_mm":
                payload=planes[name].astype("<u2",copy=False).tobytes(order="C")
                mime="application/octet-stream"
                view.update(format="depth_u16_le",units="millimetres",alignment_frame="rectified_left",invalid_value=0)
            else:
                buffer=io.BytesIO()
                # Lossless either way; level 1 bounds encode time per bundle.
                Image.fromarray(planes[name]).save(buffer,format="PNG",compress_level=1)
                payload,mime=buffer.getvalue(),"image/png"
                view.update(format="gray8_png",alignment_frame="rectified_"+name)
            self.store.add_media(sid,payload,mime,view,canonical([item["id"],"view-v1",modality]))

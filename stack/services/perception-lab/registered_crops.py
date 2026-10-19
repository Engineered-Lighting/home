"""Resolve frame-bound person proposals from a pinned stored detector result.

This read path never starts detection. A live pipeline must first complete its
admitted detector stage; missing evidence is not an empty person set.
"""
from copy import deepcopy
import json
import re

from reasoning import digest, finite


class RegisteredPersonCrops:
    def __init__(self,store,reference,*,minimum_confidence,max_people,policy_revision):
        self.store=store
        self.reference=deepcopy(reference)
        if (not isinstance(reference,dict) or reference.get("arm") not in {"a","b"}
                or type(reference.get("generation")) is not int or reference["generation"]<0
                or not all(isinstance(reference.get(k),str) and reference[k] for k in
                    ("profile_id","model_version","engine_epoch"))
                or not re.fullmatch(r"[a-f0-9]{64}",str(reference.get("artifact_sha256","")))
                or minimum_confidence is not None and (not finite(minimum_confidence) or not 0<=minimum_confidence<=1)
                or type(max_people) is not int or not 1<=max_people<=16
                or not isinstance(policy_revision,str) or not policy_revision):
            raise ValueError("pinned reference detector and bounded crop policy required")
        self.minimum_confidence=minimum_confidence
        self.max_people=max_people
        self.policy_revision=policy_revision

    def __call__(self,frame):
        session=self.store.get_session(frame["session_id"])
        if any(frame.get(k)!=session[k] for k in ("site_id","camera_id")):
            raise ValueError("crop request belongs to another session source")
        if not isinstance(frame.get("media_ids"),list) or not 1<=len(frame["media_ids"])<=8:
            raise ValueError("registered source media required")
        images=[]
        for mid in frame["media_ids"]:
            item=self.store.get_media(session["id"],mid,generation=session["generation"])
            if item["mime_type"] in {"image/jpeg","image/png"} and item["metadata"].get("stream")=="rgb":
                images.append(item)
        if len(images)!=1:
            raise ValueError("one registered RGB source required")
        meta=images[0]["metadata"]
        for key in ("frame_id","capture_epoch","site_id","camera_id","observed_at"):
            if meta.get(key)!=frame.get(key) or meta.get(key) is None:
                raise ValueError("crop source metadata mismatch")
        width,height=meta.get("width"),meta.get("height")
        if type(width) is not int or type(height) is not int or not 0<width<=3840 or not 0<height<=2160:
            raise ValueError("registered source dimensions required")
        ref=self.reference
        with self.store.lock:
            rows=self.store.db.execute("""SELECT id,payload FROM events WHERE session_id=? AND kind='perception.output'
                AND json_extract(payload,'$.arm')=? AND json_extract(payload,'$.generation')=?
                AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.model_version')=?
                AND json_extract(payload,'$.artifact_sha256')=? AND json_extract(payload,'$.engine_epoch')=?
                AND json_extract(payload,'$.frame_id')=? AND json_extract(payload,'$.capture_epoch')=?
                ORDER BY id DESC LIMIT 2""",(session["id"],ref["arm"],ref["generation"],ref["profile_id"],
                    ref["model_version"],ref["artifact_sha256"],ref["engine_epoch"],frame["frame_id"],frame["capture_epoch"])).fetchall()
        if len(rows)!=1:
            raise ValueError("reference detector evidence missing or ambiguous")
        event_id=rows[0]["id"]; value=json.loads(rows[0]["payload"])
        now=self.store.clock()
        if (value.get("status")!="completed" or any(value.get(k)!=frame[k] for k in ("site_id","camera_id","observed_at"))
                or images[0]["id"] not in value.get("media_ids",[])
                or not all(finite(value.get(k)) for k in ("observed_at","available_at","valid_until"))
                or not value["observed_at"]<=value["available_at"]<=now<=value["valid_until"]):
            raise ValueError("reference detector output is invalid, unavailable or stale")
        result=value.get("result",{})
        if (not isinstance(result,dict) or result.get("schema_version")!=1 or result.get("frame_id")!=frame["frame_id"]
                or result.get("coordinate_space")!="source_pixels"
                or not isinstance(result.get("boxes"),list) or len(result["boxes"])>512):
            raise ValueError("reference detector lacks source-pixel boxes")
        crops=[]
        for index,box in enumerate(result["boxes"]):
            if not isinstance(box,dict):raise ValueError("malformed detection")
            if box.get("label")!="person":continue
            if self.minimum_confidence is None:
                # Only for detectors that return no score (HumanArt/YOLOX); a
                # returned score is never silently ignored.
                if "confidence" in box:raise ValueError("scored person detection requires an explicit threshold")
            else:
                confidence=box.get("confidence")
                if not finite(confidence) or not 0<=confidence<=1:
                    raise ValueError("person detection lacks a valid model score")
                if confidence<self.minimum_confidence:continue
            if not all(finite(box.get(k)) for k in ("x","y","w","h")):
                raise ValueError("person box has invalid coordinates")
            x,y,w,h=(box[k] for k in ("x","y","w","h"))
            if not 0<=x<x+w<=width or not 0<=y<y+h<=height:
                raise ValueError("person box outside original image")
            crops.append({"id":"crop_"+digest([session["id"],event_id,index])[:24],
                "frame_id":frame["frame_id"],"xyxy":[x,y,x+w,y+h],
                "detector_version":ref["model_version"],"detector_artifact_sha256":ref["artifact_sha256"],
                "source_event_id":event_id})
        if len(crops)>self.max_people:
            raise ValueError("person count exceeds admitted pose budget")
        policy={"minimum_confidence":self.minimum_confidence,"max_people":self.max_people,"revision":self.policy_revision}
        return {**{k:frame[k] for k in ("session_id","site_id","camera_id","capture_epoch","frame_id")},
            "crops":crops,"crop_set_digest":digest(crops),"policy":policy,
            "reference":deepcopy(ref),"source_event_id":event_id,"available_at":value["available_at"],
            "observation":"empty_person_crop_set" if not crops else "person_proposals",
            "identity":"not_inferred"}

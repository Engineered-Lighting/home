"""Translate normalized worker evidence into frame-bound UI primitives.

Never infer an activity from object labels or turn pixel geometry into metric
geometry. Workers must undo resize/letterbox transforms before declaring pixels.
"""
from copy import deepcopy
from reasoning import finite


def arm_snapshot(store, request, result, *, completed_at, current):
    source=request["frame"]
    profile=request["profile"]
    arm={"id":request["arm"].upper(),"status":"running" if current else "stale",
         "profile":{"id":profile["profile_id"],"name":profile["model_version"],"runs_on":profile.get("device_id")},
         "frame_id":source["frame_id"],"observations":[],"relations":[],"layers":{},"validation":[]}
    result=result if isinstance(result,dict) else {}
    objects=result.get("objects")
    for item in (objects if isinstance(objects,list) else [])[:512]:
        label=item if isinstance(item,str) else item.get("label") if isinstance(item,dict) else None
        if isinstance(label,str) and len(label)<=128:
            arm["observations"].append(label)
    media=None
    for mid in source["media_ids"]:
        from apartment_viewing_source import registered_media, is_viewing_id
        try:candidate=registered_media(store,request["session_id"],mid)
        except Exception:
            if not is_viewing_id(mid):raise
            # Stop/expiry withdraws RAM pixels; a late affirmative worker proof
            # still becomes terminal without reviving imagery or overlays.
            arm['validation'].append('viewing_media_retired');continue
        if candidate["mime_type"] in {"image/jpeg","image/png"}:
            media=candidate
            break
    aligned=False
    if media:
        meta=media["metadata"]
        w,h=meta.get("width"),meta.get("height")
        aligned=(type(w) is int and type(h) is int and 0<w<=8192 and 0<h<=8192
                 and meta.get("frame_id")==source["frame_id"] and meta.get("capture_epoch")==source["capture_epoch"]
                 and meta.get("site_id")==source["site_id"] and meta.get("camera_id")==source["camera_id"])
        mapped=(meta.get("clock_mapping_status")=="verified" and bool(meta.get("clock_mapping_id"))
                and meta.get("observed_at")==source["observed_at"])
        start=store.get_session(request["session_id"])["started_at"]
        arm["frame"]={"id":source["frame_id"],"media_id":media["id"],"site_id":source["site_id"],
                      "generation":media["generation"],"captured_at":source["observed_at"] if mapped else None,
                      "session_time_s":source["observed_at"]-start if mapped and finite(start) else None,
                      "width":w if aligned else None,"height":h if aligned else None}
        if media.get('viewing_only') is True:arm['frame']['viewing_only']=True
    aligned=aligned and result.get("schema_version")==1 and result.get("frame_id")==source["frame_id"] and result.get("coordinate_space")=="source_pixels"
    if not aligned:
        arm["validation"].append("overlay_source_frame_not_verified")
        return arm

    speed=result.get('timings_ms',{})
    if not isinstance(speed,dict):speed={}
    arm['timing']={'stages_ms':{k:v for k,v in speed.items()
        if k in {'preprocess','inference','postprocess'} and finite(v) and v>=0},
        'source_clock_uncertainty_ms':meta['uncertainty_seconds']*1000
            if mapped and finite(meta.get('uncertainty_seconds')) and meta['uncertainty_seconds']>=0 else None,
        'source_to_controller_ms':(completed_at-source['observed_at'])*1000
            if mapped and finite(completed_at) and completed_at>=source['observed_at'] else None}

    def point(value):
        if isinstance(value,list) and len(value) in {2,3}:
            value={"x":value[0],"y":value[1],**({"confidence":value[2]} if len(value)==3 else {})}
        if not isinstance(value,dict) or not all(finite(value.get(k)) for k in ("x","y")) or not 0<=value["x"]<=w or not 0<=value["y"]<=h:
            raise ValueError("invalid source pixel")
        p={"x":value["x"],"y":value["y"]}
        if "confidence" in value:
            if not finite(value["confidence"]) or not 0<=value["confidence"]<=1:
                raise ValueError("invalid confidence")
            p["confidence"]=value["confidence"]
        if "score" in value:
            if not finite(value["score"]):
                raise ValueError("invalid raw keypoint response")
            p["score"]=value["score"]
        if "joint_id" in value:
            if type(value["joint_id"]) is not int or not 0<=value["joint_id"]<512:
                raise ValueError("invalid joint index")
            p["joint_id"]=value["joint_id"]
        if "instance_id" in value:
            if not isinstance(value["instance_id"],str) or not 1<=len(value["instance_id"])<=256:
                raise ValueError("invalid frame-local instance")
            p["instance_id"]=value["instance_id"]
        return p

    def accept(field,layer,convert,maximum):
        if field not in result:
            return
        try:
            values=result[field]
            if not isinstance(values,list) or len(values)>maximum:
                raise ValueError("primitive bound")
            arm[field]=[convert(v) for v in values]
            arm["layers"][layer]={"available":True}
        except (ValueError,TypeError,KeyError,IndexError,AttributeError):
            arm["validation"].append("invalid_"+field)

    def box(v):
        p=point(v)
        if not all(finite(v.get(k)) for k in ("w","h")) or min(v["w"],v["h"])<=0 or p["x"]+v["w"]>w or p["y"]+v["h"]>h:
            raise ValueError("invalid box")
        return {**p,"w":v["w"],"h":v["h"],"label":str(v.get("label",""))[:128]}

    def mask(v):
        polygons=v.get("polygons")
        if not isinstance(polygons,list) or len(polygons)>32 or any(not isinstance(p,list) or not 3<=len(p)<=2048 for p in polygons):
            raise ValueError("invalid polygons")
        return {"polygons":[[point(p) for p in poly] for poly in polygons]}

    def track(v):
        trail=v.get("trail",[])
        if not isinstance(v.get("id"),str) or len(v["id"])>128 or not isinstance(trail,list) or len(trail)>256:
            raise ValueError("invalid track")
        return {"id":v["id"],"pixel":point(v["pixel"]),"trail":[point(p) for p in trail]}

    accept("boxes","boxes",box,512)
    if "boxes" in arm:arm["layers"]["labels"]={"available":True}
    accept("keypoints","skeleton",point,512)
    if "keypoints" in arm:
        edges=result.get("skeleton_edges",[])
        if isinstance(edges,list) and len(edges)<=1024 and all(isinstance(e,list) and len(e)==2 and all(type(i) is int and 0<=i<len(arm["keypoints"]) for i in e) for e in edges):
            arm["skeleton_edges"]=deepcopy(edges)
        else:arm["validation"].append("invalid_skeleton_edges")
    accept("masks","masks",mask,128)
    accept("tracks","tracks",track,64)
    accept("image_relations","spatial",lambda v:{"pixel":point(v["pixel"]),"label":str(v["label"])[:128]},256)
    if "pose_instances" in result:
        try:
            instances=result["pose_instances"]
            if not isinstance(instances,list) or len(instances)>16:
                raise ValueError("pose bound")
            summaries=[]
            for instance in instances:
                layout=instance["layout"]
                count={"coco17":17,"coco_wholebody133":133}.get(layout)
                joints=instance["joints"]
                if count is None or not isinstance(joints,list) or len(joints)!=count:
                    raise ValueError("unknown pose layout")
                if any(not isinstance(j,dict) or j.get("joint_id")!=i or j.get("status") not in {"supported","uncertain"}
                       for i,j in enumerate(joints)):
                    raise ValueError("invalid joint slots")
                identity=instance["instance_id"]
                if not isinstance(identity,str) or not 1<=len(identity)<=256:
                    raise ValueError("invalid pose instance")
                predicted=instance.get("depth_semantics")=="monocular_model_prediction_relative_to_supported_hips_not_stereo"
                summaries.append({"instance_id":identity,"layout":layout,"joint_count":count,
                    "supported_joints":sum(j["status"]=="supported" for j in joints),
                    "depth":"Predicted hip-relative depth; not stereo or room position" if predicted else "No measured depth supplied",
                    "overlay":"Body joints only; hand and face edges not drawn" if count==133 else "COCO17 body joints",
                    "scores":"Raw model responses; not calibrated probabilities"})
            arm["pose_summary"]=summaries
        except (ValueError,TypeError,KeyError,AttributeError):
            arm["validation"].append("invalid_pose_summary")
    if "pose_instances" in result or "boxes" in arm:
        try:
            # This additive detail keeps observation-local instances and every
            # supplied slot. It grants no continuity, identity or room geometry.
            instances=result.get("pose_instances",[])
            if not isinstance(instances,list) or len(instances)>16:
                raise ValueError("pose bound")
            poses={}
            for instance in instances:
                identity=instance["instance_id"]
                if not isinstance(identity,str) or not 1<=len(identity)<=256 or identity in poses:
                    raise ValueError("unique frame-local pose instance required")
                layout=instance["layout"]
                count={"coco17":17,"coco_wholebody133":133}.get(layout)
                slots=instance["joints"]
                semantics=instance["score_semantics"]
                if (count is None or not isinstance(slots,list) or len(slots)!=count
                        or not isinstance(semantics,str) or not 1<=len(semantics)<=128):
                    raise ValueError("complete known pose layout required")
                crop=instance["crop_xyxy"]
                if (not isinstance(crop,list) or len(crop)!=4 or not all(finite(v) for v in crop)
                        or not 0<=crop[0]<crop[2]<=w or not 0<=crop[1]<crop[3]<=h):
                    raise ValueError("source-pixel person crop required")
                joints=[]
                for index,joint in enumerate(slots):
                    if (not isinstance(joint,dict) or type(joint.get("joint_id")) is not int
                            or joint["joint_id"]!=index or joint.get("status") not in {"supported","uncertain"}
                            or not isinstance(joint.get("name"),str) or not 1<=len(joint["name"])<=128):
                        raise ValueError("invalid joint slot")
                    pixel,score=joint["pixel"],joint["score"]
                    if (pixel is not None and (not isinstance(pixel,list) or len(pixel)!=2
                            or not all(finite(v) for v in pixel)) or score is not None and not finite(score)):
                        raise ValueError("invalid raw joint response")
                    if joint["status"]=="supported" and (pixel is None or score is None
                            or not 0<=pixel[0]<=w or not 0<=pixel[1]<=h):
                        raise ValueError("supported joint outside source frame")
                    retained={key:deepcopy(joint[key]) for key in ("joint_id","name","pixel","score","status")}
                    if "reason" in joint:
                        reason=joint["reason"]
                        if (reason is not None and (not isinstance(reason,str) or not 1<=len(reason)<=128)
                                or joint["status"]=="supported" and reason is not None):
                            raise ValueError("invalid supplied joint reason")
                        retained["reason"]=reason
                    joints.append(retained)
                poses[identity]={"crop":crop,"layout":layout,"score_semantics":semantics,"joints":joints}

            if "boxes" in result:
                if "boxes" not in arm:
                    raise ValueError("valid detector boxes required")
                person_boxes=[v for v in arm["boxes"] if v["label"]=="person"]
            else:
                person_boxes=[{"instance_id":identity,"x":pose["crop"][0],"y":pose["crop"][1],
                    "w":pose["crop"][2]-pose["crop"][0],"h":pose["crop"][3]-pose["crop"][1]}
                    for identity,pose in poses.items()]
            people=[];seen=set()
            for value in person_boxes:
                identity=value.get("instance_id")
                if not isinstance(identity,str) or not 1<=len(identity)<=256 or identity in seen:
                    raise ValueError("unique frame-local detector instance required")
                seen.add(identity)
                person_box={key:deepcopy(value[key]) for key in ("x","y","w","h")}
                if "confidence" in value:person_box["confidence"]=value["confidence"]
                pose=poses.get(identity)
                person={"instance_id":identity,"box":person_box,"pose_status":"unavailable",
                    "pose_reason":"pose_not_supplied","layout":None,"score_semantics":None,"joints":[]}
                if pose is not None:
                    if pose["crop"]!=[value["x"],value["y"],value["x"]+value["w"],value["y"]+value["h"]]:
                        raise ValueError("pose crop does not match detector instance")
                    person.update(pose_status="available",pose_reason=None,
                        **{key:pose[key] for key in ("layout","score_semantics","joints")})
                people.append(person)
            if set(poses)-seen:
                raise ValueError("pose has no matching detector instance")
            arm["persons2d"]={"schema":"home-lab-frame-persons-1","version":1,
                "frame_id":source["frame_id"],"coordinate_space":"source_pixels","persons":people}
        except (ValueError,TypeError,KeyError,IndexError,AttributeError):
            arm["validation"].append("invalid_persons2d")
    return arm

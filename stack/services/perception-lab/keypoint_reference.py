"""Normalize a manifest-bound Torchvision Keypoint R-CNN reference receipt.

Documented output route: torchvision v0.23.0 keypoint_rcnn/roi_heads.
This does not identify the installed LA checkpoint, load weights, or change crops.
"""
from reasoning import canonical, finite
from rtmpose_worker import JOINTS, EDGES


def values(tensor):
    if hasattr(tensor,"detach"): tensor=tensor.detach()
    if hasattr(tensor,"cpu"): tensor=tensor.cpu()
    return tensor.tolist() if hasattr(tensor,"tolist") else tensor


def normalize_reference(result, *, frame_id, width, height, person_threshold, joint_threshold):
    if (type(width) is not int or type(height) is not int or not 0<width<=3840 or not 0<height<=2160
            or not finite(person_threshold) or not 0<=person_threshold<=1
            or not finite(joint_threshold)):
        raise ValueError("explicit reference shape and score policy required")
    boxes,scores,labels,points,joint_scores=[values(result[k]) for k in
        ("boxes","scores","labels","keypoints","keypoints_scores")]
    if not len(boxes)==len(scores)==len(labels)==len(points)==len(joint_scores) or len(boxes)>128:
        raise ValueError("bounded matching Keypoint R-CNN arrays required")
    output={"schema_version":1,"frame_id":frame_id,"coordinate_space":"source_pixels",
        "objects":[],"boxes":[],"keypoints":[],"skeleton_edges":[],"pose_instances":[],
        "quality":{"metric_depth":"not_measured","pose_score_semantics":"heatmap_response_not_probability",
            "visibility_semantics":"model_output_not_occlusion_measurement","coordinate_space":"source_pixels"}}
    for index,(box,score,label,joints,responses) in enumerate(zip(boxes,scores,labels,points,joint_scores)):
        if not finite(score) or not 0<=score<=1 or label!=1:
            raise ValueError("expected person detections")
        if len(box)!=4 or not all(finite(v) for v in box):
            raise ValueError("invalid reference box")
        x1,y1,x2,y2=box
        if not 0<=x1<x2<=width or not 0<=y1<y2<=height or len(joints)!=17 or len(responses)!=17:
            raise ValueError("source-pixel COCO17 output required")
        if score<person_threshold: continue
        if len(output["pose_instances"])>=16:
            raise ValueError("reference crowd budget exceeded")
        identity=frame_id+":"+str(index)
        output["objects"].append({"label":"person","confidence":score,"instance_id":identity})
        output["boxes"].append({"x":x1,"y":y1,"w":x2-x1,"h":y2-y1,"label":"person","confidence":score,"instance_id":identity})
        retained=[]; mapping={}
        for jid,(joint,response) in enumerate(zip(joints,responses)):
            if len(joint)!=3: raise ValueError("expected x/y/visibility triplet")
            x,y,visibility=joint
            valid=all(finite(v) for v in (x,y,response,visibility))
            reason="nonfinite" if not valid else "outside_frame" if not 0<=x<=width or not 0<=y<=height else "not_reported" if visibility<=0 else "low_response" if response<joint_threshold else None
            retained.append({"joint_id":jid,"name":JOINTS[jid],"pixel":[x,y] if valid else None,
                "score":response if finite(response) else None,"reported_visibility":visibility if finite(visibility) else None,
                "status":"supported" if reason is None else "uncertain","reason":reason})
            if reason is None:
                mapping[jid]=len(output["keypoints"])
                output["keypoints"].append({"x":x,"y":y,"score":response,"joint_id":jid,"instance_id":identity})
        output["skeleton_edges"].extend([[mapping[a],mapping[b]] for a,b in EDGES if a in mapping and b in mapping])
        output["pose_instances"].append({"instance_id":identity,"crop_xyxy":box,"layout":"coco17",
            "joints":retained,"score_semantics":"heatmap_response_not_probability"})
    if len(canonical(output).encode())>256*1024: raise ValueError("reference output budget exceeded")
    return output

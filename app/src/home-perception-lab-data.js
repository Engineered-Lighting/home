/* Perception Lab: presentation contracts and an explicit, isolated demo adapter.
 * No HA actions, model calls, credentials, or production fallback live here. */
(function (global) {
  "use strict";
  // index.html boots this file and the feature loader executes it again when
  // the Lab opens. Keep the booted module, controller and its single poller.
  if (global.HomePerceptionLabData && global.HomePerceptionLabController) return;

  const FAMILIES = ["Objects", "Pose", "Space", "Masks", "Placement", "Inputs & time", "Reasoning", "Recommendations"];
  const rows = [["det-swap", 0, "Does a different detector see the book and phone better?", "Models", "YOLOX-L", "YOLO26s", "Read an open book|Use your phone|Partly hide an object"], ["pose-add", 1, "Does adding pose help distinguish activities?", "Evidence", "Objects + depth", "Objects + depth + pose", "Read at the desk|Lean back|Hide your hands"], ["pose-crop", 1, "Which pose model works better on the same crops?", "Models", "RTMW3D", "RTMPose-m", "Sit|Stand|Reach|Turn sideways"], ["pose-pipe", 1, "Which complete pose pipeline works better?", "Complete pipeline", "RTMW3D pipeline", "Keypoint R-CNN pipeline", "Sit|Stand|Reach"], ["depth-pos", 2, "Does depth improve where you are placed in the room?", "Evidence", "RGB ground-plane estimate", "Stereo depth", "Sit at the desk|Stand beside the chair|Walk past"], ["map-know", 2, "Does furniture knowledge improve understanding?", "Evidence", "Measured position", "Position + furniture map", "Sit at the desk|Sit elsewhere|Stand beside the desk"], ["spatial-full", 2, "Does the complete spatial pipeline help?", "Complete pipeline", "RGB without room relations", "Depth + furniture relations", "Sit|Stand beside the desk|Walk past"], ["mask-withhold", 3, "Does using masks improve downstream evidence?", "Evidence", "Segmentation · masks withheld", "Same segmentation · masks used", "Reach over the desk|Overlap objects|Pick something up"], ["det-vs-seg", 3, "Is a segmentation pipeline more useful?", "Complete pipeline", "Detection", "Segmentation", "Reach|Pick something up|Hide an object"], ["place-matched", 4, "Does the same model behave differently on each device?", "Matched configuration", "Victoria Jetson", "LA server", "Repeat ordinary activities|Change activity"], ["place-best", 4, "Which practical deployment should I use?", "Best practical deployment", "Jetson configuration", "LA configuration", "Read|Work|Move between activities"], ["vjepa-onoff", 5, "Does V-JEPA temporal evidence help JEV?", "Evidence", "Detection + pose", "Detection + pose + V-JEPA buckets", "Read an open book|Use a phone|Sit, stand, and move between zones"], ["input-crop", 5, "Does more detail around the person help?", "Evidence", "Whole frame", "Person crop", "Hold a small object|Read|Use a phone"], ["sample-rate", 5, "Does sampling more often catch brief moments?", "Evidence", "Lower sampling rate", "Higher sampling rate", "Reach briefly|Stand then sit|Switch objects"], ["history", 5, "Does recent history make judgments steadier?", "Reasoning", "Current observation", "Recent observation history", "Stretch then resume|Change activity while seated"], ["rules-jev", 6, "Does JEV add value over rules on identical evidence?", "Reasoning", "Deterministic rules", "JEV", "Read|Sit idle at the desk|Use a phone"], ["qwen-evidence", 6, "Does perception evidence improve Qwen?", "Saved session", "Qwen · RGB + HA", "Qwen + perception evidence", "Use a saved session"], ["jev-assisted", 6, "Do Qwen observations help JEV?", "Saved session", "JEV on recorded evidence", "JEV + Qwen observations", "Use a saved session"], ["policy", 7, "Does a different policy suggest lighting I want?", "Recommendations", "Reviewed policy", "Alternative policy", "Read|Work|Rest"]];
  const IDS = {
    "det-swap": "detection",
    "pose-add": "pose-added",
    "pose-crop": "pose-crops",
    "pose-pipe": "pose-pipelines",
    "depth-pos": "depth",
    "map-know": "furniture",
    "spatial-full": "spatial-combined",
    "mask-withhold": "masks",
    "det-vs-seg": "segmentation-pipeline",
    "place-matched": "placement-same",
    "place-best": "placement-best",
    "input-crop": "input-detail",
    "sample-rate": "sampling",
    "jev-assisted": "qwen-jev",
    "policy": "preference"
  };
  const CATALOG = rows.map(r => ({
    id: IDS[r[0]] || r[0],
    family: r[1],
    question: r[2],
    kind: r[3],
    arms: {
      A: {
        name: r[4]
      },
      B: {
        name: r[5]
      }
    },
    try_this: r[6].split("|"),
    needs_depth: ["depth-pos", "map-know", "spatial-full"].includes(r[0]),
    saved_only: r[3] === "Saved session",
    held_constant: "Same source intervals, device state and unchanged stages; changes are recorded in the manifest.",
    status: "unverified"
  }));
  // Generated from stack/services/perception-lab/experiments.json; exact parity is tested.
  const STATIC_CATALOG = [
  {
    "id": "detection",
    "family_id": "detection",
    "title": "Does YOLO26s see the book and phone better than YOLOX-L?",
    "setup_name": "Detector \u00b7 YOLOX-L vs YOLO26s",
    "description": "Two LA detectors on the same frames: YOLOX-L (A) and YOLO26s (B). Only the detector changes.",
    "kind": "focused",
    "changes": [
      "detector"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolox-l",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "lever": {
      "paths": [
        "detector"
      ],
      "note": "Detector model only: YOLOX-L (A) vs YOLO26s (B) on the same frames."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "pose-added",
    "family_id": "pose",
    "title": "Does adding pose distinguish reading from phone use?",
    "setup_name": "Pose \u00b7 detection vs detection + pose",
    "description": "LA YOLO26s detections alone (A) versus the same detections plus RTMPose-m keypoints (B).",
    "kind": "focused",
    "changes": [
      "pose"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": "rtmpose-m",
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "lever": {
      "paths": [
        "pose"
      ],
      "note": "Pose stage only: none (A) vs RTMPose-m (B) on the same detector boxes."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "pose-crops",
    "family_id": "pose",
    "title": "Which estimator works better on the same person crops?",
    "setup_name": "Pose on shared crops \u00b7 RTMW3D-x vs RTMPose-m",
    "description": "RTMW3D-x (A) and RTMPose-m (B) estimate pose on the same shared person crops on LA.",
    "kind": "focused",
    "changes": [
      "pose"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": "rtmw3d-x",
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32",
      "crop_source": "shared-humanart"
    },
    "b": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": "rtmpose-m",
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32",
      "crop_source": "shared-humanart"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "variants": [
      "rtmw3d-x",
      "rtmpose-m",
      "keypoint-rcnn"
    ],
    "isolation": "Shared detector boxes and source crops; map common joints before scoring.",
    "lever": {
      "paths": [
        "pose"
      ],
      "note": "Pose model only: RTMW3D-X (A) vs RTMPose-m (B) on shared detector crops."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "pose-pipelines",
    "family_id": "pose",
    "title": "Which complete detector and pose pipeline works better?",
    "setup_name": "Pose pipeline \u00b7 HumanArt + RTMW3D vs YOLO26s + RTMPose",
    "description": "Complete LA pipelines: YOLOX-m HumanArt with RTMW3D-x (A) versus YOLO26s with RTMPose-m (B).",
    "kind": "complete_pipeline",
    "changes": [
      "detector",
      "pose"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolox-m-humanart",
      "pose": "rtmw3d-x",
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": "rtmpose-m",
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "variants": [
      "rtmw3d-x-humanart",
      "rtmpose-m-yolo26s",
      "keypoint-rcnn"
    ],
    "isolation": "Complete pipeline comparison; not an isolated keypoint-model result.",
    "lever": {
      "paths": [
        "detector",
        "pose"
      ],
      "coupled": "pose_pipeline",
      "note": "Complete detector+pose pipeline: YOLOX-M HumanArt with RTMW3D-X (A) vs YOLO26s with RTMPose-m (B); not attributable to one stage."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "depth",
    "family_id": "spatial",
    "title": "Does stereo depth improve where you are placed?",
    "setup_name": "Position \u00b7 floor projection vs stereo depth",
    "description": "YOLO26s person positions from a floor projection (A) versus stereo depth (B).",
    "kind": "focused",
    "changes": [
      "localization"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission",
      "depth",
      "calibration",
      "room_registration"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "floor_projection",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "stereo",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "lever": {
      "paths": [
        "localization"
      ],
      "note": "Localization only: RGB floor projection (A) vs stereo depth (B)."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "furniture",
    "family_id": "spatial",
    "title": "Does furniture knowledge improve understanding?",
    "setup_name": "Room knowledge \u00b7 without vs with furniture map",
    "description": "Stereo-localized people without (A) and with (B) a registered furniture map.",
    "kind": "focused",
    "changes": [
      "room_map"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission",
      "depth",
      "calibration",
      "room_registration",
      "furniture_map"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "stereo",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "stereo",
      "room_map": true,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "lever": {
      "paths": [
        "room_map"
      ],
      "note": "Room-map relations only: off (A) vs on (B)."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "spatial-combined",
    "family_id": "spatial",
    "title": "Does the combined spatial pipeline help?",
    "setup_name": "Spatial pipeline \u00b7 floor projection vs stereo + map",
    "description": "Floor projection without a room map (A) versus stereo depth with a registered furniture map (B).",
    "kind": "complete_pipeline",
    "changes": [
      "localization",
      "room_map"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission",
      "depth",
      "calibration",
      "room_registration",
      "furniture_map"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "floor_projection",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "stereo",
      "room_map": true,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "lever": {
      "paths": [
        "localization",
        "room_map"
      ],
      "coupled": "spatial_pipeline",
      "note": "Localization and room-map relations together: floor projection without map (A) vs stereo with map (B)."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "masks",
    "family_id": "segmentation",
    "title": "Does using the same model's masks improve spatial evidence?",
    "setup_name": "Masks \u00b7 withheld vs used",
    "description": "One YOLO26s-seg output shared by both arms; masks are withheld from A and used by B.",
    "kind": "focused",
    "changes": [
      "masks"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission",
      "depth",
      "calibration"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s-seg",
      "pose": null,
      "masks": false,
      "localization": "stereo",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s-seg",
      "pose": null,
      "masks": true,
      "localization": "stereo",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "shared_computation": "segmentation",
    "isolation": "One segmentation output is reused; only downstream mask access changes.",
    "lever": {
      "paths": [
        "masks"
      ],
      "note": "Mask access only: off (A) vs on (B), from one shared segmentation output."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "segmentation-pipeline",
    "family_id": "segmentation",
    "title": "Is segmentation better than a complete box-based pipeline?",
    "setup_name": "Segmentation \u00b7 boxes vs masks",
    "description": "YOLO26s boxes (A) versus YOLO26s-seg masks (B), both with stereo depth.",
    "kind": "complete_pipeline",
    "changes": [
      "detector",
      "masks"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission",
      "depth",
      "calibration"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "stereo",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s-seg",
      "pose": null,
      "masks": true,
      "localization": "stereo",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "lever": {
      "paths": [
        "detector",
        "masks"
      ],
      "coupled": "segmentation_pipeline",
      "note": "Detector with masks as one pipeline: YOLO26s (A) vs YOLO26s-seg with masks (B)."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "placement-same",
    "family_id": "placement",
    "title": "How does the same detector behave on Jetson and LA?",
    "setup_name": "Detection \u00b7 Jetson vs LA",
    "description": "The same YOLO26s FP32 detector on Victoria's Jetson (A) and on LA (B), fed the same RGB frames.",
    "kind": "focused",
    "changes": [
      "device"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission",
      "network"
    ],
    "a": {
      "input": "rgb-640",
      "ha_evidence": true,
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": null,
      "policy": null,
      "device": "jetson",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "rgb-640",
      "ha_evidence": true,
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": null,
      "policy": null,
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "model_variants": [
      "yolo26s"
    ],
    "isolation": "Same RGB frames, detector checkpoint and preprocessing on both hosts.",
    "lever": {
      "paths": [
        "device"
      ],
      "note": "Detector placement only: Jetson (A) vs LA (B), same checkpoint and preprocessing."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); ha_evidence false -> true on both arms: HA state is a shared background input, identical for both arms, and stays inert until an HA source is provisioned.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "placement-best",
    "family_id": "placement",
    "title": "How does detector plus pose behave on Jetson and LA?",
    "setup_name": "Detection + pose \u00b7 Jetson vs LA",
    "description": "YOLO26s FP32 detection plus CPU RTMPose-m body7 pose on Victoria's Jetson (A) and on LA (B), fed the same RGB frames. Despite its id, this setup compares where the detector-plus-pose pipeline runs; it is not a search for the best deployment.",
    "kind": "complete_pipeline",
    "changes": [
      "device"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission",
      "network"
    ],
    "a": {
      "input": "rgb-640",
      "detector": "yolo26s",
      "pose": "rtmpose-m-body7-cpu",
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": null,
      "policy": null,
      "device": "jetson",
      "sampling_hz": 1,
      "precision": "fp32",
      "runtime_profile": "validated-local",
      "ha_evidence": true,
      "caption_evidence": false
    },
    "b": {
      "input": "rgb-640",
      "detector": "yolo26s",
      "pose": "rtmpose-m-body7-cpu",
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": null,
      "policy": null,
      "device": "la",
      "sampling_hz": 1,
      "precision": "fp32",
      "runtime_profile": "validated-central",
      "ha_evidence": true,
      "caption_evidence": false
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "isolation": "Same RGB frames and checkpoints; GPU detector with CPU pose on both hosts.",
    "lever": {
      "paths": [
        "device",
        "runtime_profile"
      ],
      "coupled": "placement",
      "note": "Detector+pose placement with each host's validated runtime profile: Jetson local (A) vs LA central (B)."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); ha_evidence false -> true on both arms: HA state is a shared background input, identical for both arms, and stays inert until an HA source is provisioned; semantics unchanged: detector plus CPU pose placement despite the id.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "vjepa-onoff",
    "family_id": "evidence",
    "title": "Does V-JEPA temporal evidence help JEV?",
    "setup_name": "Detection + pose \u00b7 V-JEPA off vs on",
    "description": "placement-best's detector-plus-pose pair (YOLO26s FP32 with CPU RTMPose-m body7 on Victoria's Jetson (A) and on LA (B), same RGB frames), with the V-JEPA 2.1 den evidence withheld from A and granted to B: uncalibrated posture, reading, zero-shot, motion and scene-change buckets from frozen probes that LA runs over stored den preview clips while the room is occupied.",
    "kind": "focused",
    "changes": [
      "evidence"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission",
      "network",
      "jev_household_egress",
      "vjepa_worker"
    ],
    "a": {
      "input": "rgb-640",
      "detector": "yolo26s",
      "pose": "rtmpose-m-body7-cpu",
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": null,
      "policy": null,
      "device": "jetson",
      "sampling_hz": 1,
      "precision": "fp32",
      "runtime_profile": "validated-local",
      "ha_evidence": true,
      "caption_evidence": false
    },
    "b": {
      "input": "rgb-640",
      "detector": "yolo26s",
      "pose": "rtmpose-m-body7-cpu",
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": null,
      "policy": null,
      "device": "la",
      "sampling_hz": 1,
      "precision": "fp32",
      "runtime_profile": "validated-central",
      "ha_evidence": true,
      "caption_evidence": false,
      "source_policy": {
        "temporal_encoder": true
      }
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "isolation": "placement-best's arms verbatim, so its sessions are this experiment's placement baseline. The only evidence family that differs is temporal_encoder: one shared, never-visual record that never schedules a JEV call and is left out of the shared-context digest.",
    "lever": {
      "paths": [
        "device",
        "runtime_profile",
        "source_policy.temporal_encoder"
      ],
      "coupled": "placement_with_temporal_encoder",
      "note": "V-JEPA den evidence withheld (A) vs granted (B). The managed pair always runs A on the Jetson and B on LA with each host's validated runtime profile, exactly as placement-best, so the placement paths are declared too: compare against placement-best sessions to separate placement from V-JEPA. temporal_encoder is the only evidence family varied."
    },
    "revision": "2026-09-29: added (on/off experiment for the V-JEPA 2.1 den evidence source; placement-best arms unchanged plus source_policy.temporal_encoder on B)."
  },
  {
    "id": "input-detail",
    "family_id": "evidence",
    "title": "Does more detail around the person help?",
    "setup_name": "Input detail \u00b7 whole frame vs person crop",
    "description": "The whole 640 px frame (A) versus a registered detailed person crop (B). Detail must come from the capture, never from upscaling.",
    "kind": "focused",
    "changes": [
      "input"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission",
      "high_resolution_source"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "registered-person-crop",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "isolation": "Use a registered detailed source; do not fabricate detail by upscaling a preview.",
    "lever": {
      "paths": [
        "input"
      ],
      "note": "Input only: 640 px RGB preview (A) vs registered person crop (B)."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "sampling",
    "family_id": "evidence",
    "title": "Does sampling more often catch brief moments?",
    "setup_name": "Sampling \u00b7 2 Hz vs 4 Hz",
    "description": "The same LA YOLO26s detector sampled at 2 Hz (A) and at 4 Hz (B).",
    "kind": "focused",
    "changes": [
      "sampling_hz"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission",
      "source_rate_4hz"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 4,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "isolation": "4 Hz respects the current sampled numerical path; higher-rate RGB needs a separately validated source profile.",
    "lever": {
      "paths": [
        "sampling_hz"
      ],
      "note": "Specialist sampling rate only: 2 Hz (A) vs 4 Hz (B)."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "history",
    "family_id": "evidence",
    "title": "Does recent history make judgments steadier?",
    "setup_name": "History \u00b7 latest only vs 10 s",
    "description": "JEV judges the latest observation only (A) or the last 10 s of observations (B).",
    "kind": "focused",
    "changes": [
      "history"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 0,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "lever": {
      "paths": [
        "history"
      ],
      "note": "JEV evidence window only: latest observation (A) vs 10 s history (B)."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments; arm B history 20 s -> 10 s, the live JEV history bound; arm A stays latest-only.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "rules-jev",
    "family_id": "reasoning",
    "title": "Does JEV improve on rules using identical evidence?",
    "setup_name": "Reasoning \u00b7 rules vs JEV",
    "description": "Identical perception evidence judged by deterministic rules (A) and by JEV (B).",
    "kind": "focused",
    "changes": [
      "reasoner"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission",
      "jev_household_egress"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "rules",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "shared_computation": "perception",
    "lever": {
      "paths": [
        "reasoner"
      ],
      "note": "Reasoner only: transparent rules (A) vs JEV (B) on the same perception."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "qwen-evidence",
    "family_id": "reasoning",
    "title": "Does perception evidence improve Qwen beyond RGB and HA?",
    "setup_name": "Saved Qwen \u00b7 RGB + HA vs + perception",
    "description": "Saved-session Qwen analysis from RGB and HA (A) versus the same inputs plus YOLO26s evidence (B).",
    "kind": "focused",
    "changes": [
      "evidence"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission",
      "saved_session",
      "qwen_admission"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": null,
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "qwen-rgb-ha",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "qwen-with-evidence",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "mode": "saved",
    "analysis_variants": [
      "baseline",
      "a",
      "b"
    ],
    "lever": {
      "paths": [
        "detector",
        "reasoner"
      ],
      "coupled": "qwen_detector_hints",
      "note": "Saved Qwen analysis without (A) vs with (B) detector evidence; the reasoner label names that input."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "qwen-jev",
    "family_id": "reasoning",
    "title": "Do Qwen's observations help JEV?",
    "setup_name": "Saved JEV \u00b7 recorded vs + Qwen captions",
    "description": "Saved-session JEV on recorded evidence (A) versus JEV that also receives Qwen caption evidence (B).",
    "kind": "focused",
    "changes": [
      "reasoner"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission",
      "saved_session",
      "qwen_admission",
      "jev_household_egress"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "recorded-jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "rgb-ha-640",
      "caption_evidence": true,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "qwen-assisted-jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "mode": "saved",
    "lever": {
      "paths": [
        "caption_evidence",
        "reasoner"
      ],
      "coupled": "historical_caption",
      "note": "Historical Qwen caption family withheld (A) vs granted (B) to recorded JEV; the reasoner label names that input."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (A false, B true); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  },
  {
    "id": "preference",
    "family_id": "preferences",
    "title": "Which lighting suggestion would you actually want?",
    "setup_name": "Recommendation \u00b7 reviewed vs personal policy",
    "description": "One shared judgment feeding the reviewed policy (A) and a personal reviewed policy (B). Recommendations only, never actuation.",
    "kind": "focused",
    "changes": [
      "policy"
    ],
    "requirements": [
      "source",
      "model_artifacts",
      "resource_admission",
      "reviewed_preferences"
    ],
    "a": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "b": {
      "input": "rgb-ha-640",
      "caption_evidence": false,
      "detector": "yolo26s",
      "pose": null,
      "masks": false,
      "localization": "none",
      "room_map": false,
      "history": 10,
      "reasoner": "jev",
      "policy": "reviewed-personal",
      "device": "la",
      "sampling_hz": 2,
      "precision": "fp32"
    },
    "try_this": [
      "Read an open book",
      "Use a phone",
      "Sit, stand, and move between zones"
    ],
    "confusing_alternatives": [
      "Reading an open book / holding a closed book",
      "Working at a computer / sitting nearby",
      "Reaching for an object / passing a hand over it",
      "Sitting / standing behind a chair",
      "Briefly standing / leaving the activity",
      "Object removed / object occluded"
    ],
    "shared_computation": "judgment",
    "isolation": "Both policies receive the same judgment; only recommendations, never actuation.",
    "lever": {
      "paths": [
        "policy"
      ],
      "note": "Recommendation policy only: reviewed (A) vs reviewed-personal (B) on the same judgment."
    },
    "revision": "2026-09-28: setup_name and description added (display only); caption_evidence added (false on both arms); precision fp16 -> fp32 on both arms: the incumbent adapters and workers run FP32, FP32 detection with CPU pose is the baseline, and FP16, INT8 and GPU pose are separate optimization experiments.; 2026-09-29: lever declared (catalog metadata; arm settings unchanged); the evidence baseline grants HA and ambient context identically to both arms."
  }
];
  const ACTIVITY_LABELS = {
    reading: "Reading",
    computer_work: "Computer work",
    phone_use: "Phone use",
    resting: "Resting",
    transition: "Transition",
    other: "Other",
    insufficient_evidence: "Insufficient evidence"
  };
  const activityLabel = value => ACTIVITY_LABELS[value] || value;
  const activityId = value => ({
    Reading: "reading",
    "Computer use": "computer_work",
    "Phone use": "phone_use",
    "Sitting/resting": "resting",
    "Standing/walking": "transition",
    Transition: "transition",
    Other: "other",
    Unclear: "insufficient_evidence",
    "Insufficient evidence": "insufficient_evidence"
  })[value] || value;
  const ALTERNATIVES = ["Reading / holding a closed book", "Computer use / sitting beside an active display", "Reaching / moving a hand past an object", "Sitting / standing behind a chair", "Brief interruption / ending an activity", "Object left / object occluded"];
  const clone = value => JSON.parse(JSON.stringify(value));
  function uuid() {
    return global.crypto?.randomUUID?.() || "lab-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
  }
  function timestamp(value) {
    return typeof value === 'number' ? value * (value < 1e12 ? 1000 : 1) : Date.parse(value);
  }
  function seconds(session, now = Date.now()) {
    return Math.max(0, Number(session?.duration_s ?? (session?.started_at ? ((session.stopped_at || session.ended_at ? timestamp(session.stopped_at || session.ended_at) : now) - timestamp(session.started_at)) / 1000 : 0)) || 0);
  }
  function formatTime(value) {
    const n = Math.max(0, Number(value) || 0);
    return Math.floor(n / 60).toString().padStart(2, "0") + ":" + Math.floor(n % 60).toString().padStart(2, "0");
  }
  function isRecording(s) {
    return ["requested", "recording", "on", "starting", "stopping", "finalizing", "indeterminate"].includes(s?.state);
  }
  function recordingLabel(s) {
    return {
      requested: "Starting capture",
      starting: "Preparing session",
      recording: "Recording",
      on: "Recording",
      stopping: "Finishing capture",
      finalizing: "Finishing capture",
      indeterminate: "Capture status unknown"
    }[s?.state] || s?.state || "Recording";
  }
  function mergeCatalog(experiments) {
    const source = Array.isArray(experiments) ? experiments : [];
    return CATALOG.map(e => {
      const remote = {
        ...STATIC_CATALOG.find(x => x.id === e.id),
        ...source.find(x => x.id === e.id)
      };
      return {
        ...e,
        ...remote,
        question: remote.title || remote.question || e.question,
        kind: remote.kind ? {
          focused: 'Focused',
          complete_pipeline: 'Complete pipeline',
          matched_configuration: 'Matched configuration',
          best_practical: 'Best practical deployment'
        }[remote.kind] || remote.kind : e.kind,
        arms: {
          ...e.arms,
          ...remote.arms
        }
      };
    });
  }
  function experimentReadiness(experiment) {
    const reasons={
      experiment_pair_not_provisioned:'This comparison has no provisioned A/B worker pair. Configure its exact models and devices before running it.',
      pair_requires_runtime_check:'The A/B pair is configured. Runtime, input and resource checks are still required before it can run.',
      prepares_on_start:'The A/B pair is provisioned. Start prepares both workers and runs every start gate before recording.'
    };
    return reasons[experiment?.readiness_reason] || (experiment?.status==='ready'
      ? 'Available in this connection. Start is still checked by the service.'
      : 'Model runtime, input modalities, room registration and service admission must be verified.');
  }
  // Setup readiness: the service's launch-free probe (readiness) when present,
  // otherwise the older status/readiness_reason fields. An older or recording-only
  // service that reports no readiness stays 'Not checked': never shown as Ready,
  // but startable as before, because the service still runs every start gate.
  const GiB = n => (n / 1024 ** 3).toFixed(1) + ' GiB';
  const READINESS_REASONS = {
    jetson_memory_below_gate: r => 'Jetson available memory ' + GiB(r.measured) + ' is below the ' + GiB(r.threshold) + ' start gate',
    jetson_storage_below_gate: r => 'Jetson free storage ' + GiB(r.measured) + ' is below the ' + GiB(r.threshold) + ' start gate',
    jetson_thermal_above_gate: r => 'Jetson temperature ' + r.measured.toFixed(1) + ' °C is at or above the ' + r.threshold + ' °C start gate',
    jetson_thermal_unreadable: 'Jetson temperature could not be read',
    jetson_worker_active: 'A Jetson worker is already running',
    la_unreachable: 'LA worker supervisor did not answer',
    la_supervisor_unavailable: 'LA worker supervisor is not accepting requests',
    la_credential_rejected: 'LA worker supervisor rejected the Lab credential',
    la_credential_unavailable: 'The Lab credential for LA is unavailable',
    la_tls_failed: 'LA worker supervisor TLS verification failed',
    la_probe_unexpected: 'LA worker supervisor gave an unexpected answer',
    readiness_probe_failed: 'A host readiness check failed',
    readiness_probe_unavailable: 'A host readiness check is not available',
    comparison_active: 'A comparison is already preparing or recording',
    worker_cleanup_pending: 'Worker cleanup from the last session is pending',
    local_drain_pending: 'Local work from the last session is still finishing',
    service_closing: 'The Lab service is shutting down',
    experiment_pair_not_provisioned: 'No A/B worker pair is provisioned for this setup',
    readiness_not_reported: 'This service did not report readiness for this setup'
  };
  const PREREQUISITES = {
    worker_binding: 'Provisioned A/B worker binding',
    network: 'Validated network path between the hosts',
    model_artifacts: 'Pinned model artifacts on the workers',
    resource_admission: 'Resource admission for both workers',
    depth: 'Recorded numerical depth in the live pipeline',
    calibration: 'Physical camera calibration',
    room_registration: 'Room registration',
    furniture_map: 'Owner-confirmed furniture map',
    high_resolution_source: 'Registered high-resolution capture source (not upscaling)',
    source_rate_4hz: 'Validated 4 Hz source rate',
    saved_session: 'Saved-session analysis path',
    qwen_admission: 'Approved Qwen admission',
    reviewed_preferences: 'Reviewed personal preferences',
    jev_household_egress: 'Household JEV egress policy for this setup',
    vjepa_worker: 'Provisioned LA V-JEPA worker lease and household vjepa-den-1 grant',
    unrecognized_requirement: 'Unrecognized requirement'
  };
  const CODE = /^[a-z][a-z0-9_]{0,63}$/;
  const codeText = code => code.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
  function readinessReason(r) {
    const code = CODE.test(r?.code) ? r.code : 'readiness_not_reported', text = READINESS_REASONS[code];
    const numeric = Number.isFinite(r?.measured) && Number.isFinite(r?.threshold);
    return {code, arm: ['a', 'b'].includes(r?.arm) ? r.arm : null,
      text: typeof text === 'function' ? numeric ? text(r) : codeText(code) : text || codeText(code)};
  }
  function requirementCodes(experiment) {
    const codes = ['worker_binding'];
    for (const requirement of Array.isArray(experiment?.requirements) ? experiment.requirements : []) {
      const code = requirement === 'source' ? null : CODE.test(requirement) ? requirement : 'unrecognized_requirement';
      if (code && !codes.includes(code)) codes.push(code);
    }
    return codes;
  }
  function setupReadiness(experiment) {
    const reported = experiment?.readiness, legacy = experiment?.readiness_reason;
    let group, reasons = [], prerequisites = [], prepares = false;
    if (reported && ['ready', 'blocked', 'coming_later', 'saved_session'].includes(reported.state)) {
      group = reported.state === 'saved_session' ? 'saved' : reported.state;
      reasons = (Array.isArray(reported.reasons) ? reported.reasons : []).map(readinessReason);
      prerequisites = (Array.isArray(reported.prerequisites) ? reported.prerequisites : []).filter(c => CODE.test(c));
      prepares = reported.prepares_on_start === true;
      if (group === 'blocked' && !reasons.length) reasons = [readinessReason({code: 'readiness_not_reported'})];
    } else if (experiment?.status === 'ready') group = 'ready';
    else if (experiment?.status === 'saved' || experiment?.status === 'unverified' && (experiment?.saved_only || legacy === 'saved_analysis_on_request')) group = 'saved';
    else if (experiment?.status === 'unverified' && ['prepares_on_start', 'pair_requires_runtime_check'].includes(legacy)) {
      group = 'ready';
      prepares = true;
    } else if (experiment?.status === 'unverified') group = 'unverified';
    else if (experiment?.status === 'blocked' && legacy === 'experiment_pair_not_provisioned') {
      group = 'coming_later';
      reasons = [readinessReason({code: 'experiment_pair_not_provisioned'})];
      prerequisites = requirementCodes(experiment);
    } else {
      group = 'blocked';
      reasons = [readinessReason({code: experiment?.status === 'blocked' && legacy ? legacy : 'readiness_not_reported'})];
    }
    if (group === 'coming_later' && !prerequisites.length) prerequisites = requirementCodes(experiment);
    const checked = Number.isFinite(reported?.checked_at) ? reported.checked_at : null;
    const needs = prerequisites.map(code => ({code, text: PREREQUISITES[code] || codeText(code)}));
    return {group, startable: group === 'ready' || group === 'unverified', prepares_on_start: prepares, checked_at: checked, reasons, prerequisites: needs,
      label: {ready: prepares ? 'Ready to prepare' : 'Ready', unverified: 'Not checked', saved: 'Saved session', blocked: 'Blocked'}[group] || 'Coming later',
      summary: group === 'ready' ? prepares ? 'Start prepares both workers, then records. Every start gate runs again.' : 'Start is still checked by the service.'
        : group === 'unverified' ? 'This service does not report setup readiness. Start is checked by the service.'
        : group === 'saved' ? 'Runs on a saved session. Choosing it opens review; it does not record.'
        : group === 'blocked' ? reasons.map(r => r.text).join(' · ') : 'Not startable yet · needs ' + needs.map(n => n.text.toLowerCase()).join(', ')};
  }
  // Not checked and Saved sessions are listed only when a setup is in them.
  const SETUP_GROUPS = [['ready', 'Ready'], ['unverified', 'Not checked', true], ['saved', 'Saved sessions', true], ['blocked', 'Blocked'], ['coming_later', 'Coming later']];
  function setupGroups(experiments) {
    return SETUP_GROUPS.map(([id, label]) => ({id, label, items: (experiments || []).filter(e => setupReadiness(e).group === id)}))
      .filter((g, i) => !SETUP_GROUPS[i][2] || g.items.length);
  }
  const setupName = experiment => experiment?.setup_name || experiment?.question || experiment?.id || 'Setup';
  // Why Start is disabled, or '' when it may be pressed. The service still decides.
  function startBlockedReason({connected, recordingAllowed, experiment, modalities = []}) {
    if (!connected) return 'The Lab service is not connected.';
    if (!recordingAllowed) return 'Recording is blocked until capture and admission are verified.';
    const view = setupReadiness(experiment);
    if (view.group === 'coming_later') return 'Coming later: ' + setupName(experiment) + ' needs ' + view.prerequisites.map(n => n.text.toLowerCase()).join(', ') + '.';
    if (view.group === 'blocked') return 'Blocked: ' + view.reasons.map(r => r.text).join(' · ') + '.';
    if (view.group === 'saved') return 'Saved-session setup: open a saved recording to analyze it; it does not record live.';
    if (experiment?.needs_depth && !modalities.includes('depth')) return 'This setup needs a depth source on the selected camera.';
    return '';
  }
  // Readiness reasons that clear by themselves once the last session's workers
  // are released and its local work drains. The view re-checks while only
  // these block, instead of keeping a stale Blocked until reload.
  const SETTLING_REASONS = new Set(['comparison_active', 'worker_cleanup_pending', 'local_drain_pending']);
  function readinessSettling(caps, experiment) {
    const gate = (Array.isArray(caps?.gates) ? caps.gates : []).some(g => g?.id === 'workers' && g.state === 'blocked' && SETTLING_REASONS.has(g.reason));
    const view = setupReadiness(experiment);
    return gate || view.group === 'blocked' && view.reasons.length > 0 && view.reasons.every(r => SETTLING_REASONS.has(r.code));
  }
  // A start that never reached recording: the session's own reason, made
  // specific by the worker receipt reasons of its workers.cleanup event (or the
  // service's last_worker_cleanup for the same session). Plain words only; an
  // unrecognized code is shown as words, never hidden.
  const START_FAILURE_REASONS = new Set(['worker_busy', 'worker_preparation_failed', 'worker_preparation_timeout', 'worker_preparation_uncertain',
    'insufficient_worker_lease', 'paired_start_failed', 'worker_cleanup_pending', 'local_drain_pending', 'experiment_pair_not_provisioned']);
  const hostOf = arm => arm === 'b' ? 'the LA computer' : arm === 'a' ? 'the camera computer' : 'a worker computer';
  const START_FAILURE_TEXT = {
    // The Jetson (arm A) is the camera computer; LA reports its own kernel faults.
    preflight_kernel_evidence: arm => 'a health check on ' + (arm === 'b' ? 'the LA computer' : 'the camera computer') + ' flagged a recent system error',
    preflight_kernel_fault: arm => 'a health check on ' + (arm === 'b' ? 'the LA computer' : 'the camera computer') + ' flagged a recent system error',
    launch_unproven: arm => arm === 'a' ? 'the camera computer’s worker didn’t come up' : 'the LA worker didn’t come up',
    preflight_vram: 'the LA GPU is busy',
    worker_busy: 'a previous run is still cleaning up',
    worker_cleanup_pending: 'a previous run is still cleaning up',
    local_drain_pending: 'a previous run is still cleaning up',
    preflight_memory: arm => hostOf(arm) + ' is low on free memory',
    preflight_storage: arm => hostOf(arm) + ' is low on free storage',
    preflight_disk: arm => hostOf(arm) + ' is low on free storage',
    preflight_thermal: arm => hostOf(arm) + ' is too hot',
    preflight_thermal_read: arm => hostOf(arm) + '’s temperature could not be read',
    preflight_capture: 'the camera feed is not available',
    preflight_source: 'the camera feed is not available',
    preflight_worker_active: arm => 'a worker is already running on ' + hostOf(arm),
    preflight_worker_readiness: arm => 'the worker on ' + hostOf(arm) + ' did not report ready',
    preflight_failed_unit: arm => 'a service on ' + hostOf(arm) + ' has failed',
    preflight_model_health: arm => 'the model on ' + hostOf(arm) + ' failed its health check',
    preflight_inspection_error: arm => 'a health check on ' + hostOf(arm) + ' could not run',
    preflight_rejected: arm => hostOf(arm) + ' refused to start its worker',
    worker_preparation_failed: 'a worker failed while preparing',
    worker_preparation_timeout: 'the workers took too long to get ready',
    worker_preparation_uncertain: 'the workers’ state could not be confirmed',
    insufficient_worker_lease: 'the worker reservation was too short for a full session',
    paired_start_failed: 'the paired recording did not start',
    experiment_pair_not_provisioned: 'this setup has no provisioned worker pair'
  };
  const failureText = (code, arm) => {
    const text = START_FAILURE_TEXT[code];
    return typeof text === 'function' ? text(arm) : text || code.replaceAll('_', ' ');
  };
  function startFailure({session, events, caps} = {}) {
    if (!session || isRecording(session)) return null;
    const reason = typeof session.reason === 'string' && CODE.test(session.reason) ? session.reason : null;
    // A start the service refused outright is 'blocked'; a recording that failed later is not a start failure.
    if (!START_FAILURE_REASONS.has(reason) && !(session.state === 'blocked' && reason)) return null;
    const cleanup = (Array.isArray(events) ? events : []).filter(e => e?.kind === 'workers.cleanup').pop();
    const last = caps?.last_worker_cleanup?.session_id === session.id ? caps.last_worker_cleanup : null;
    const workers = cleanup?.workers || cleanup?.payload?.workers || last?.workers || {};
    const found = ['a', 'b'].map(arm => {
      const r = workers?.[arm]?.reason;
      const code = typeof r === 'string' ? r.replace(/^lease_/, '') : null;
      return code && CODE.test(code) ? {code, arm} : null;
    }).filter(Boolean);
    // A recognized worker reason first, then any worker reason, then the session's.
    const worker = found.find(x => START_FAILURE_TEXT[x.code]) || found[0] || null;
    const code = worker?.code || reason;
    return {session_id: session.id, reason, code, arm: worker?.arm || null, text: failureText(code, worker?.arm)};
  }
  function layerStatus(arm, name) {
    const l = arm?.layers?.[name];
    if (!l) return {
      available: false,
      reason: "Not produced by this setup"
    };
    return typeof l === "boolean" ? {
      available: l,
      reason: l ? "Available" : "Not produced"
    } : l;
  }
  function overlaySupport(arm, name) {
    const support = layerStatus(arm, name);
    if (!support.available) return support;
    const field = {
      boxes: 'boxes',
      labels: 'boxes',
      skeleton: 'keypoints',
      masks: 'masks',
      tracks: 'tracks',
      spatial: 'image_relations'
    }[name];
    return Array.isArray(arm?.[field]) ? support : {
      available: false,
      reason: 'Renderable frame-associated data not supplied'
    };
  }
  function causalResults(runs, t, actual) {
    return (runs || []).filter(r => Number.isFinite(r.source_end_s ?? r.t_s) && Number(r.source_end_s ?? r.t_s) <= t && (!actual || r.kind !== "offline" && Number.isFinite(r.available_at_s) && r.available_at_s <= t)).sort((a, b) => Number(a.source_end_s ?? a.t_s) - Number(b.source_end_s ?? b.t_s));
  }
  function columnRuns(runs, column) {
    if (column === 'detectorA' || column === 'detectorB') {
      const arm = column === 'detectorA' ? 'A' : 'B';
      return (runs || []).filter(r => r.run_id === column ||
        (r.event_kind || r.kind) === 'perception.output' && r.mode === 'live' && r.arm === arm &&
        (r.result?.processing?.checkpoint === 'yolo26s' || r.processing?.checkpoint === 'yolo26s' || r.result?.processing?.pipeline === 'detector-pose-v1'));
    }
    return (runs || []).filter(r => r.run_id === column ||
      !r.run_id && r.judgment && r.mode === 'live' &&
      (column === 'jevA' && r.arm === 'A' || column === 'jevB' && r.arm === 'B'));
  }
  function scoreIntervals(labels, runs) {
    // One reviewed primary-activity interval = one unit. Unsupported intervals never count as success.
    const result = {
      reviewed: 0,
      matched: 0,
      wrong: 0,
      insufficient: 0,
      unsupported: 0,
      diagnosticExcluded: 0
    };
    for (const label of labels || []) {
      if ((label.time_basis || 'session') !== 'session' || !label.reviewed || label.origin !== 'human' || !label.activity || label.activity === "Unclear") continue;
      result.reviewed++;
      const eligible = (runs || []).filter(r => !r.diagnostic && r.mode !== "diagnostic" && r.mode !== "retrospective" && Number.isFinite(r.source_end_s ?? r.t_s) && Number(r.source_end_s ?? r.t_s) >= label.start_s && Number(r.source_end_s ?? r.t_s) < label.end_s);
      if (!eligible.length) {
        result.unsupported++;
        continue;
      }
      let hit = 0,
        miss = 0,
        unknown = 0;
      eligible.forEach(r => {
        const a = r.activity || r.judgment?.activity;
        if (!a || ["Unclear", "Mixed / unclear", "Insufficient evidence"].includes(a) || r.status !== "completed") {
          unknown++;
        } else if (a === label.activity) hit++;else miss++;
      });
      if (hit > miss && hit > unknown) result.matched++;else if (miss > hit && miss > unknown) result.wrong++;else result.insufficient++;
    }
    return result;
  }
  function createDemoAdapter() {
    const saved = {
      id: "demo-session",
      site_id: "victoria",
      camera_id: "den_oak",
      name: "Desk · reading to phone",
      experiment_id: "pose-added",
      state: "saved",
      started_at: new Date(Date.now() - 240000).toISOString(),
      duration_s: 180,
      revision: 1
    };
    const sessions = [saved],
      docs = new Map([[saved.id, {
        events: [],
        annotations: [],
        runs: [],
        media: {
          depth: true,
          stereo: true,
          synthetic: true
        }
      }]]);
    function scene(t, arm) {
      const phase = Math.floor(t / 24) % 4,
        activities = ["reading", "phone_use", "computer_work", "transition"];
      const activity = arm === "A" && phase === 1 ? "reading" : activities[phase];
      return {
        phase,
        activity
      };
    }
    function output(t, id) {
      const s = scene(t, id),
        gap = t % 67 > 59;
      return {
        id,
        profile: {
          name: id === "A" ? "Objects + depth" : "Objects + depth + pose",
          runs_on: "Synthetic fixture"
        },
        status: gap ? "unavailable" : "running",
        frame_id: "demo-" + Math.floor(t * 2),
        frame: {
          id: "demo-" + Math.floor(t * 2),
          captured_at: new Date(Date.now() - 400).toISOString(),
          width: 640,
          height: 360,
          synthetic: true,
          scene: s.phase,
          session_time_s: t
        },
        observations: ["Person", s.phase === 0 ? "Book" : s.phase === 1 ? "Phone" : s.phase === 2 ? "Display" : "Moving"],
        relations: [s.phase === 3 ? "Leaving desk" : "Near desk", id === "B" ? "Seated · hands near work surface" : "Posture not measured"],
        judgment: {
          activity: gap ? "Insufficient evidence" : s.activity,
          probability: id === "A" ? .71 : .84,
          confidence: id === "A" ? .34 : .61,
          distribution: [{
            label: s.activity,
            p: id === "A" ? .71 : .84
          }, {
            label: "Mixed / unclear",
            p: id === "A" ? .29 : .16
          }],
          provider: "JEV · simulated",
          question: "Which activity is supported by the supplied observations?"
        },
        recommendation: s.phase === 0 ? "Suggest task lighting" : "Maintain current lighting",
        timing: {
          capture_to_judgment_s: id === "A" ? 1.2 : 1.5
        },
        layers: {
          boxes: {
            available: true
          },
          labels: {
            available: true
          },
          tracks: {
            available: true
          },
          skeleton: {
            available: id === "B",
            reason: "This setup does not produce keypoints"
          },
          masks: {
            available: false,
            reason: "This setup does not produce masks"
          },
          spatial: {
            available: true
          }
        },
        boxes: [{
          x: 250,
          y: 84,
          w: 96,
          h: 214,
          label: "Person"
        }, {
          x: 323,
          y: 238,
          w: 49,
          h: 25,
          label: s.phase === 1 ? "Phone" : "Book"
        }],
        keypoints: id === "B" ? [[296, 96], [290, 126], [267, 161], [256, 209], [327, 157], [348, 220], [281, 220], [272, 291], [300, 221], [315, 290]] : []
      };
    }
    function detail(s, t) {
      const doc = docs.get(s.id);
      const time = t ?? seconds(s);
      return {
        session: {
          ...s,
          duration_s: seconds(s)
        },
        ...clone(doc),
        arms: {
          A: output(time, "A"),
          B: output(time, "B")
        },
        frame: output(time, "A").frame,
        room: {
          site_id: "victoria",
          synthetic: true,
          units: "metres",
          floor_axes: "xy",
          map_revision: "demo-map",
          targets: [{
            id: "desk",
            label: "Desk",
            x: 2,
            y: 2,
            width: 1.8,
            height: .8
          }],
          people: [{
            id: "A:1",
            arm: "A",
            x: 2.2,
            y: 3
          }, {
            id: "B:1",
            arm: "B",
            x: 2.5,
            y: 3
          }]
        }
      };
    }
    return {
      async request(op, args = {}) {
        const id = args.session_id || args.sessionId || saved.id,
          s = sessions.find(x => x.id === id),
          doc = docs.get(id);
        if (op === "capabilities.get") return {
          version: 1,
          site_id: "victoria",
          demo: true,
          cameras: [{
            id: "den_oak",
            name: "Victoria · Den",
            site_id: "victoria",
            modalities: ["rgb", "depth", "left", "right"],
            ready: true
          }, {
            id: "echo_rgb",
            name: "Echo · RGB camera",
            site_id: "echo",
            modalities: ["rgb"],
            ready: true
          }],
          experiments: CATALOG.map(e => ({
            ...e,
            status: e.saved_only ? "saved" : "ready"
          })),
          devices: [{
            id: "fixture",
            name: "Synthetic device",
            budget_status: "Demo values; no hardware used"
          }],
          artifacts: [{
            id: "demo-objects",
            name: "Object detector",
            state: "ready",
            device: "Synthetic fixture"
          }, {
            id: "demo-pose",
            name: "Pose model",
            state: "available",
            device: "Synthetic fixture"
          }],
          gates: {
            recording: true,
            preparation: true,
            analysis: true,
            cloud: true
          },
          storage: {
            policy: "Keep all within quota",
            used_bytes: 0,
            quota_bytes: null
          }
        };
        if (op === "sessions.list") return {
          items: sessions.map(s => ({
            ...s,
            duration_s: seconds(s)
          }))
        };
        if (op === "sessions.start") {
          const x = {
            id: uuid(),
            site_id: args.camera_id === "echo_rgb" ? "echo" : "victoria",
            camera_id: args.camera_id || "den_oak",
            name: args.name || "Untitled experiment",
            experiment_id: args.experiment_id || "pose-added",
            state: "recording",
            started_at: new Date().toISOString(),
            revision: 1
          };
          sessions.unshift(x);
          docs.set(x.id, {
            events: [],
            annotations: [],
            runs: [],
            media: {
              depth: x.camera_id !== "echo_rgb",
              stereo: x.camera_id !== "echo_rgb",
              synthetic: true
            }
          });
          return detail(x);
        }
        if (op === "sessions.get") {
          if (!s) throw Error("Session not found");
          return detail(s, args.t_s);
        }
        if (op === "sessions.stop") {
          s.duration_s = seconds(s);
          s.stopped_at = new Date().toISOString();
          s.state = "saved";
          s.revision++;
          return detail(s);
        }
        if (op === "sessions.switch") {
          doc.events.push({
            id: uuid(),
            kind: "switch",
            t_s: seconds(s),
            from: s.experiment_id,
            to: args.experiment_id
          });
          s.experiment_id = args.experiment_id;
          s.revision++;
          return detail(s);
        }
        if (op === "bookmarks.add") {
          doc.events.push({
            id: uuid(),
            kind: "bookmark",
            t_s: args.t_s ?? seconds(s),
            note: args.note || "Review this moment",
            label: args.note || "Review this moment"
          });
          return detail(s);
        }
        if (op === "annotations.save") {
          const a = {
            ...args.annotation,
            origin: 'human',
            id: args.annotation?.id || uuid(),
            revision: (args.expected_revision || 0) + 1
          };
          const i = doc.annotations.findIndex(x => x.id === a.id);
          if (i >= 0) doc.annotations[i] = a;else doc.annotations.push(a);
          s.revision++;
          return detail(s);
        }
        if (op === "annotations.delete") {
          doc.annotations = doc.annotations.filter(a => a.id !== args.annotation_id);
          return detail(s);
        }
        if (op === "jobs.create") {
          if (args.kind === "preparation") return {
            job: {
              id: uuid(),
              state: "completed",
              kind: "preparation",
              demo: true
            }
          };
          const kinds = args.run_ids || ["qBase", "qA", "qB"];
          for (const kind of kinds) {
            for (let t = 5; t <= seconds(s); t += 5) {
              doc.runs.push({
                id: uuid(),
                run_id: kind,
                kind: "offline",
                mode: args.mode || "causal",
                status: "completed",
                source_start_s: Math.max(0, t - 10),
                source_end_s: t,
                activity: scene(t, kind === "qA" ? "A" : "B").activity,
                judgment: output(t, kind === "qA" ? "A" : "B").judgment
              });
            }
          }
          return detail(s);
        }
        if (op === "diagnostics.create") return {
          diagnostic: {
            id: uuid(),
            status: "completed",
            diagnostic: true,
            answer: "Synthetic diagnostic: the corrected fact changes the supplied evidence. No real inference ran.",
            corrected_fact: args.corrected_fact
          }
        };
        if (op === "sessions.export") return {
          manifest: detail(s),
          demo: true
        };
        if (op === "sessions.delete") {
          if (args.erase_records === true) {
            docs.delete(s.id);
            sessions.splice(sessions.findIndex(item => item.id === s.id), 1);
            return {id:s.id,deleted:true,scope:'session'};
          }
          doc.media = {
            deleted: true
          };
          s.revision++;
          return detail(s);
        }
        throw Object.assign(Error("Demo operation not available"), {
          code: "operation_unavailable"
        });
      }
    };
  }
  function normalizeDetail(d) {
    if (!d) return d;
    const session = d.session;
    const annotations = (d.annotations || []).map(a => ({
      ...a,
      start_s: a.start_s ?? a.at_ms / 1000,
      end_s: a.end_s ?? (a.end_ms ?? a.at_ms) / 1000,
      activity: activityId(a.activity || a.label),
      note: a.note || a.notes,
      reviewed: a.reviewed === true
    }));
    const events = (d.timeline_events || d.events || []).map(e => ({
      ...e,
      ...e.payload,
      kind: e.kind === 'bookmark.added' ? 'bookmark' : e.kind?.includes('switch') ? 'switch' : e.kind,
      t_s: e.t_s ?? (Number.isFinite(e.payload?.at_ms) ? e.payload.at_ms / 1000 : 0)
    }));
    const results = d.results || d.outputs || d.runs || [];
    const runs = results.map(r => {
      const value = {
          ...r,
          ...r.payload
        },
        prediction = value.prediction?.time_basis === 'session_relative_seconds' ? value.prediction : null;
      return {
        ...value,
        status: value.status || value.state,
        arm: typeof value.arm === 'string' ? value.arm.toUpperCase() : value.arm,
        event_kind: value.event_kind || value.kind,
        kind: value.kind === 'analysis.output' ? 'offline' : value.mode === 'live' ? 'live' : value.kind,
        source_end_s: value.source_end_s ?? prediction?.end ?? prediction?.at,
        judgment: value.status === 'invalid_output' ? null : value.judgment || value.result?.judgment,
        available_at_s: value.kind === 'analysis.output' ? null : value.available_at_s ?? prediction?.available_at,
        activity: activityId(value.activity ?? prediction?.activity ?? (prediction?.activities?.length === 1 ? prediction.activities[0] : null))
      };
    });
    const frames = Array.isArray(d.frames) ? Object.fromEntries(d.frames.filter(f => f.modality || f.stream).map(f => [f.modality || f.stream, f])) : d.frames;
    return {
      ...d,
      annotations,
      events,
      frames,
      jobs: (d.runs || []).filter(r => ['analysis', 'preparation', 'diagnostic'].includes(r.kind) && typeof r.state === 'string'),
      runs,
      session
    };
  }
  async function collectExport(fetchPage, args, isCurrent = () => true) {
    let cursor=0, snapshot, header, used=0;
    const records=[];
    for(let pageNumber=0;pageNumber<10000;pageNumber++) {
      if(!isCurrent())throw Error('Export cancelled because the active connection changed');
      const page=await fetchPage({...args,cursor,limit:200,...(snapshot?{snapshot_id:snapshot}:{})});
      if(!isCurrent())throw Error('Export cancelled because the active connection changed');
      if(page.format!=='perception-lab-session-v2'||page.session_id!==args.session_id||page.site_id!==args.site_id||
          !/^[a-f0-9]{64}$/.test(page.snapshot_id)||page.cursor!==cursor||
          !Number.isSafeInteger(page.total_records)||page.total_records<1||page.total_records>1000000||
          !Array.isArray(page.records)||page.records.length>200||page.metadata_only!==true||page.media_bytes_included!==false)
        throw Error('Invalid metadata export page');
      if(!header){header=page;snapshot=page.snapshot_id;}
      else if(page.snapshot_id!==snapshot||page.site_id!==header.site_id||page.total_records!==header.total_records||JSON.stringify(page.counts)!==JSON.stringify(header.counts))
        throw Error('Session changed during export; restart export');
      used+=new TextEncoder().encode(JSON.stringify(page.records)).length;
      if(used>64*1024*1024)throw Error('Metadata exceeds the 64 MiB browser export limit; no partial export was downloaded');
      records.push(...page.records);
      const next=cursor+page.records.length;
      if(page.next_cursor===null) {
        if(next!==page.total_records)throw Error('Incomplete metadata export');
        const {cursor:ignoreCursor,next_cursor:ignoreNext,records:ignoreRecords,...manifest}=header;
        return {...manifest,records,complete:true};
      }
      if(!page.records.length||page.next_cursor!==next||next>=page.total_records)throw Error('Invalid metadata export continuation');
      cursor=next;
    }
    throw Error('Metadata export exceeds the page limit; no partial export was downloaded');
  }
  // Client-side delivery measurements for live acceptance (browser and Tauri).
  // Paint times use this device's clock; display age maps them onto the Lab
  // clock with the lowest-RTT server_time midpoint offset, so it is an estimate.
  // Measurement only: nothing here changes freshness, retries or display.
  function createDeliveryMonitor(now = () => Date.now()) {
    const WINDOW = 64;
    let bound = {site: null, session: null}, since = now(), clock = [], arms = {}, polls, ended = null;
    const fresh = () => { polls = {completed: 0, failed: 0, rtt: []}; arms = {}; ended = null; since = now(); };
    fresh();
    const push = (list, value) => { list.push(value); if (list.length > WINDOW) list.shift(); };
    const stats = list => {
      if (!list.length) return null;
      const s = [...list].sort((a, b) => a - b), at = q => s[Math.min(s.length - 1, Math.floor(q * s.length))];
      return {count: list.length, last: list[list.length - 1], median: at(.5), p95: at(.95), max: s[s.length - 1]};
    };
    const arm = id => arms[id] ||= {frames: 0, frame_id: null, painted_at: null, intervals: [], longest: null, ages: [], failures: {}, retries: 0};
    const estimate = () => {
      if (!clock.length) return null;
      const best = clock.reduce((a, c) => c.rtt < a.rtt ? c : a);
      return {offset_ms: best.offset, rtt_ms: best.rtt, uncertainty_ms: best.rtt / 2, samples: clock.length};
    };
    const freeze = value => {
      if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); }
      return value;
    };
    return {
      bind(site, session) {
        if (site !== bound.site) clock = [];
        if (site !== bound.site || session !== bound.session) fresh();
        bound = {site, session};
      },
      poll(ok, serverTime, sent, received) {
        ok ? polls.completed++ : polls.failed++;
        if (!ok || !(received >= sent)) return;
        push(polls.rtt, received - sent);
        const server = typeof serverTime === 'number' && Number.isFinite(serverTime) ? timestamp(serverTime) : NaN;
        if (!Number.isFinite(server)) return;
        clock.push({offset: server - (sent + received) / 2, rtt: received - sent});
        if (clock.length > 8) clock.shift();
      },
      clock: estimate,
      paint(id, frame, at = now()) {
        const a = arm(id), frameId = frame?.frame_id ?? frame?.id;
        if (frameId == null || frameId === a.frame_id || ended != null) return;
        if (a.painted_at != null) { const gap = at - a.painted_at; push(a.intervals, gap); a.longest = Math.max(a.longest ?? 0, gap); }
        a.frames++; a.frame_id = frameId; a.painted_at = at;
        const c = estimate(), captured = frame?.captured_at == null ? NaN : timestamp(frame.captured_at);
        if (c && Number.isFinite(captured)) push(a.ages, at + c.offset_ms - captured);
      },
      failure(id, code) {
        const key = typeof code === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(code) ? code : 'other';
        const a = arm(id); a.failures[key] = (a.failures[key] || 0) + 1;
      },
      retry(id) { arm(id).retries++; },
      // Hidden tab, closed view or review mode: not a visible freeze.
      pause(id) { (id ? [arms[id]] : Object.values(arms)).forEach(a => { if (a) a.painted_at = null; }); },
      end(at = now()) { if (ended == null) ended = at; },
      reset() { fresh(); },
      snapshot(at = now()) {
        const c = estimate(), result = {version: 1, site_id: bound.site, session_id: bound.session, since_ms: since, recording: bound.session != null && ended == null,
          clock: c ? {...c, basis: 'lab_server_time_rtt_midpoint_estimate'} : {basis: 'lab_server_time_not_reported'},
          polls: {completed: polls.completed, failed: polls.failed, rtt_ms: stats(polls.rtt)}, arms: {}};
        for (const [id, a] of Object.entries(arms)) {
          const gap = a.painted_at == null ? null : (ended ?? at) - a.painted_at;
          result.arms[id] = {frames_displayed: a.frames, last_frame_id: a.frame_id, last_paint_at_ms: a.painted_at,
            current_gap_ms: gap, visible_cadence_ms: stats(a.intervals),
            longest_freeze_ms: a.longest == null && gap == null ? null : Math.max(a.longest ?? 0, gap ?? 0),
            display_age_ms: stats(a.ages), display_age_basis: c ? 'estimate' : 'unavailable_without_server_time',
            failures: {...a.failures}, retries: a.retries};
        }
        return freeze(result);
      }
    };
  }
  function createController(transport) {
    let state = {
        mode: "real",
        siteId: global.HomeSpatialViewport?.getSelectedSiteId?.() || "victoria",
        status: "idle",
        capabilities: null,
        sessions: [],
        detail: null,
        activeSession: null,
        error: null,
        pending: false,
        generation: 0,
        // perception-lab-graph-1 projections: read-only, never inference.
        graph: null,
        graphReplay: null,
        timeline: null,
        replayOutputs: null,
        projectionEpoch: 0
      },
      listeners = new Set(),
      timer = null,
      seq = 0,
      detailSeq = 0,
      pollBusy = false,
      pollAbort = null,
      liveRead = null,
      pollError = null,
      polling = false,
      visibility = null,
      demo = null,
      windowSeq = {},
      windowAborts = {},
      replay = {};
    const delivery = createDeliveryMonitor();
    // Next live poll starts this long after the previous one finishes; each
    // poll gives up after POLL_TIMEOUT_MS instead of the transport's 15 s.
    const POLL_GAP_MS = 500, POLL_TIMEOUT_MS = 3000;
    const replayBucket = t => Math.floor(Math.max(0, t) * 2) / 2;
    const emit = () => listeners.forEach(fn => fn(state));
    const patch = p => {
      state = {
        ...state,
        ...p
      };
      emit();
    };
    const call = (op, args = {}, context = state, options) => {
      const adapter = context.mode === "demo" ? demo : transport || global.HomePerceptionLabTransport;
      if (!adapter?.request) throw Object.assign(Error("Perception Lab transport is not configured"), {
        code: "transport_unavailable"
      });
      if (context.mode === 'demo') return adapter.request(op, args);
      const common = {
        siteId: context.siteId,
        sessionId: args.session_id || args.sessionId
      };
      let body;
      if(op==='sessions.get')common.atMs=Math.round(Math.max(0,args.t_s??0)*1000);
      // Graph contract reads take integer session milliseconds; live graph omits atMs.
      if((op==='sessions.graph'&&args.at_ms!=null)||op==='sessions.replay')common.atMs=args.at_ms;
      if(op==='sessions.timeline'){if(args.from_ms!=null)common.fromMs=args.from_ms;if(args.to_ms!=null)common.toMs=args.to_ms;}
      if(op==='sessions.export') {
        common.cursor=args.cursor;
        common.limit=args.limit;
        common.snapshotId=args.snapshot_id;
      }
      if (op === 'media.manifest') {
        common.cursor = args.cursor;
        common.limit = args.limit || 100;
        common.kind = args.kind;
        common.atMs = args.at_ms;
        common.encodedAtS = args.encoded_at_s;
        common.radiusMs = args.radius_ms;
      }
      if (op === 'sessions.start') body = {
        camera_id: args.camera_id,
        experiment_id: args.experiment_id,
        name: args.name,
        reasoning_enabled: args.reasoning_enabled === true,
        request_id: args.request_id
      };
      if (op === 'sessions.stop') body = {
        request_id: args.request_id
      };
      if (op === 'sessions.delete') body = {
        expected_revision: args.expected_revision,
        ...(args.erase_records === true ? {erase_records:true} : {})
      };
      if (op === 'sessions.switch') body = {
        ...(typeof args.reasoning_enabled === 'boolean' ? {
          reasoning_enabled: args.reasoning_enabled,
          expected_reasoning_revision: args.expected_reasoning_revision
        } : {experiment_id: args.experiment_id}),
        request_id: args.request_id
      };
      if (op === 'bookmarks.add') body = {
        at_ms: Math.round(args.t_s * 1000),
        label: args.note || 'Review this moment',
        request_id: args.request_id
      };
      if (op === 'annotations.save') {
        const a = args.annotation;
        common.annotationId = a.id || uuid();
        body = {
          expected_revision: args.expected_revision || 0,
          at_ms: Math.round(a.start_s * 1000),
          end_ms: Math.round(a.end_s * 1000),
          label: a.activity,
          notes: a.note || '',
          origin: 'human',
          reviewed: a.reviewed === true,
          time_basis: a.time_basis || 'session',
          ...(a.time_basis === 'encoded_video' ? {video_init_id:a.video_init_id} : {})
        };
      }
      if (op === 'annotations.delete') {
        common.annotationId = args.annotation_id;
        body = {
          expected_revision: args.expected_revision
        };
      }
      if (op === 'jobs.create') body = {
        kind: args.kind,
        request_id: args.request_id,
        ...(args.session_id ? {
          session_id: args.session_id
        } : {}),
        parameters: {
          artifact_ids: args.artifact_ids,
          run_ids: args.run_ids,
          mode: args.mode,
          keep_ready: args.keep_ready
        }
      };
      if (op === 'diagnostics.create') body = {
        request_id: args.request_id,
        session_id: args.session_id,
        parameters: {
          at_ms: Math.round(args.t_s * 1000),
          source_run: args.source_run,
          corrected_fact: args.corrected_fact
        }
      };
      if (op === 'jobs.cancel') {
        common.jobId = args.job_id;
        body = {
          request_id: args.request_id
        };
      }
      return adapter.request(op, {
        ...common,
        ...(body ? {
          body
        } : {})
      }, options);
    };
    async function refresh() {
      const generation = state.generation,
        request = ++seq;
      try {
        const caps = await call("capabilities.get", {});
        const list = await call("sessions.list", {});
        if (generation !== state.generation || request !== seq) return;
        const active = (list.items || []).find(isRecording) || null;
        patch({
          capabilities: caps,
          sessions: list.items || [],
          activeSession: active,
          status: "ready",
          error: null
        });
        if (active && !state.detail) await open(active.id);
      } catch (error) {
        if (generation === state.generation) patch({
          status: "blocked",
          error: error.message || String(error)
        });
      }
    }
    // onProgress (optional) reports each completed read of an open, so the
    // view can show step progress: 'record' (with the raw detail), 'media',
    // 'findings'. It never changes what is read.
    async function readDetail(id, t, onProgress) {
      const context = state;
      const step = (name, value) => { try { onProgress?.(name, value); } catch (_) {} };
      let d = await call('sessions.get', {
        session_id: id,
        t_s: t
      }, context);
      if (context.generation !== state.generation) return null;
      step('record', d);
      if (context.mode === 'real') {
        const manifest = await call('media.manifest', {
          session_id: id
        }, context);
        step('media', manifest);
        d = {
          ...d,
          media: manifest
        };
        if (isRecording(d.session)) {
          const live = await call('sessions.live', {
            session_id: id
          }, context);
          d = {
            ...d,
            ...live,
            live_received_at_ms: Date.now(),
            session: d.session
          };
        } else {
          try {
            d.findings = await call('sessions.findings', {
              session_id: id
            }, context);
          } catch (e) {
            d.findings_error = e.message;
          }
          step('findings', d.findings);
        }
      }
      const prior = state.detail?.session?.id === id && state.detail?.media?.generation === d.media?.generation ? state.detail : null;
      return normalizeDetail({
        ...d,
        videoMedia: prior?.videoMedia,
        depthMedia: prior?.depthMedia,
        imageMedia: prior?.imageMedia,
        playback_s: t
      });
    }
    async function mediaPage(cursor) {
      const context = state,
        sid = state.detail?.session?.id;
      if (!sid || context.mode !== 'real') return;
      try {
        const media = await call('media.manifest', {
          session_id: sid,
          cursor,
          limit: 100
        }, context);
        if (context.generation === state.generation && state.detail?.session?.id === sid) patch({
          detail: {
            ...state.detail,
            media
          }
        });
      } catch (e) {
        if (context.generation === state.generation) patch({
          error: e.message
        });
      }
    }
    async function mediaWindow(t, kind) {
      const context = state,
        sid = state.detail?.session?.id;
      if (!sid || context.mode !== 'real' || !Number.isFinite(t)) return;
      const request = (windowSeq[kind] || 0) + 1;
      windowSeq[kind] = request;
      windowAborts[kind]?.abort();
      const abort = global.AbortController ? new global.AbortController() : null;
      windowAborts[kind] = abort;
      const video = kind === 'video';
      try {
        const media = await call('media.manifest', {
          session_id: sid,
          kind,
          limit: 200,
          ...(video ? {
            encoded_at_s: t,
            radius_ms: 30000
          } : {
            at_ms: Math.round(t * 1000),
            radius_ms: 1000
          })
        }, context, abort ? {signal: abort.signal} : undefined);
        if (context.generation === state.generation && state.detail?.session?.id === sid && windowSeq[kind] === request) patch({
          detail: {
            ...state.detail,
            [video ? 'videoMedia' : kind === 'image' ? 'imageMedia' : 'depthMedia']: media
          }
        });
      } catch (e) {
        if (context.generation === state.generation && windowSeq[kind] === request) patch({
          error: e.message
        });
      }
    }
    async function open(id, t, options) {
      const generation = state.generation,
        request = ++detailSeq,
        // Only a refresh or action on the open session keeps the replay cursor's
        // outputs and room; opening a session from the list starts fresh.
        at = t ?? (options?.keepCursor === true && state.detail?.session?.id === id && Number.isFinite(state.detail.playback_s) ? state.detail.playback_s : undefined);
      try {
        const d = await readDetail(id, at, options?.onProgress);
        if (generation === state.generation && request === detailSeq) {
          patch({
            detail: d,
            error: null
          });
          if (replay.sid !== id || replay.generation !== generation) replay = {sid: id, generation};
          replay.shown = replayBucket(at ?? 0);
        }
        return d;
      } catch (e) {
        if (generation === state.generation && request === detailSeq) patch({
          error: e.message
        });
        throw e;
      }
    }
    // Replay reads outputs and room at 0.5 s cursor buckets. A forward fetch within
    // 2 s of the cursor is allowed to land (the cursor catches up once it does);
    // a seek or backward move aborts it. Late or superseded responses are dropped.
    async function replayWindow(t) {
      const context=state,sid=state.detail?.session?.id;
      if(!sid||context.mode!=='real'||!Number.isFinite(t))return;
      const bucket=replayBucket(t);
      if(replay.sid!==sid||replay.generation!==context.generation)replay={sid,generation:context.generation};
      replay.wanted=bucket;
      const busy=replay.busy;
      if(busy) {
        if(bucket>=busy.bucket&&bucket-busy.bucket<=2)return busy.done;
        windowAborts.outputs?.abort();
        windowSeq.outputs=(windowSeq.outputs||0)+1;
        replay.busy=null;
      }
      if(replay.shown===bucket)return;
      const request=(windowSeq.outputs||0)+1;
      windowSeq.outputs=request;
      windowAborts.outputs?.abort();
      const abort=global.AbortController?new global.AbortController():null;
      windowAborts.outputs=abort;
      const entry={bucket},own=replay;
      replay.busy=entry;
      entry.done=(async()=>{
        try {
          const d=await call('sessions.get',{session_id:sid,t_s:bucket},context,abort?{signal:abort.signal}:undefined);
          if(context.generation===state.generation&&state.detail?.session?.id===sid&&windowSeq.outputs===request) {
            const normalized=normalizeDetail(d);
            // Room is causal at the cursor too; null means no room evidence yet.
            patch({detail:{...state.detail,runs:normalized.runs,room:d.room??null,playback_s:bucket}});
            own.shown=bucket;
          }
        } catch(e) {
          if(context.generation===state.generation&&windowSeq.outputs===request)patch({error:e.message});
        } finally {
          if(own.busy===entry) {
            own.busy=null;
            if(replay===own&&own.wanted!==bucket&&context.generation===state.generation&&state.detail?.session?.id===sid)replayWindow(own.wanted);
          }
        }
      })();
      return entry.done;
    }
    async function action(op, args = {}) {
      if (state.mode === 'real' && state.capabilities?.browser_access?.write === false && op !== 'sessions.export') {
        patch({error:'This browser connection is read-only. Existing sessions and results remain available.'});
        throw Error('lab_browser_read_only');
      }
      if (state.pending) throw Error("Another operation is pending");
      const generation = state.generation;
      patch({
        pending: true,
        error: null
      });
      try {
        const request = {
          request_id: uuid(),
          ...args
        };
        if (op === 'sessions.start' && (request.name == null || (typeof request.name === 'string' && !request.name.trim()))) {
          request.name = 'Lab session ' + new Date().toISOString();
        }
        let result = op==='sessions.export'&&state.mode!=='demo' ?
          await collectExport(page=>call(op,page),{...request,site_id:state.siteId},()=>generation===state.generation) : await call(op,request);
        if (generation !== state.generation) return null;
        if (op === 'sessions.delete' && args.erase_records === true && result?.deleted === true && result?.scope === 'session' && result.id === args.session_id) {
          Object.values(windowAborts).forEach(abort => abort?.abort());
          ++seq; ++detailSeq;
          patch({...clearProjections(),generation:state.generation+1,detail:state.detail?.session?.id===args.session_id?null:state.detail,
            activeSession:state.activeSession?.id===args.session_id?null:state.activeSession,
            sessions:state.sessions.filter(s=>s.id!==args.session_id),pending:false,error:null});
          try { await global.HomePerceptionLabTransport?.invalidate?.(); }
          catch (_) { patch({error:'Session deleted; reconnect to refresh media transport.'}); }
          return result;
        }
        if (result?.session && op !== 'sessions.export') {
          const at = state.detail?.session?.id === result.session.id ? state.detail.playback_s : undefined;
          if (state.mode === 'demo' && Number.isFinite(at)) result = await open(result.session.id, at);else {
            result = normalizeDetail(result);
            patch({
              detail: result
            });
          }
        } else if (result?.camera_id && result?.id) {
          result = await open(result.id);
        } else if (args.session_id && !['sessions.export', 'diagnostics.create'].includes(op)) {
          result = await open(args.session_id, undefined, {keepCursor: true});
        }
        if (args.session_id && ['annotations.save', 'annotations.delete', 'bookmarks.add', 'sessions.stop', 'sessions.switch'].includes(op)) invalidateProjections(args.session_id);
        if (op === "sessions.start" || op === "sessions.stop") await refresh();
        return result;
      } catch (e) {
        if (generation === state.generation) patch({
          error: e.message || String(e)
        });
        throw e;
      } finally {
        if (generation === state.generation) patch({
          pending: false
        });
      }
    }
    // ---- Per-label review saves ----
    // One label per call through the existing annotations.save / .delete ops
    // (same id plus expected_revision). Unlike action(), a label save neither
    // takes the view-wide pending lock nor re-reads the whole session: the
    // stored label the service returns replaces the open copy, the session's
    // cached projections are dropped, and findings are marked stale until the
    // view re-reads them. Labels stay evaluation data; nothing here reaches a
    // model input.
    function labelSession(sid) {
      return state.detail?.session?.id === sid ? state.detail : null;
    }
    function storeLabels(sid, next, context) {
      if (context.generation !== state.generation) return;
      const detail = labelSession(sid);
      if (!detail) return;
      patch({detail: {...detail, annotations: next(detail.annotations || []), findings_stale: true}});
    }
    async function saveLabel(sid, annotation, expectedRevision) {
      const context = state;
      const result = await call('annotations.save', {session_id: sid, annotation, expected_revision: expectedRevision || 0}, context);
      if (context.generation !== state.generation) return null;
      let saved;
      if (result?.session) {
        // Demo adapter: it answers with the whole session detail.
        const d = normalizeDetail(result);
        saved = d.annotations.find(a => a.id === annotation.id) || null;
        storeLabels(sid, () => d.annotations, context);
      } else {
        saved = normalizeDetail({annotations: [result || {}]}).annotations[0];
        if (!saved?.id) saved = {...annotation, revision: (expectedRevision || 0) + 1, origin: 'human'};
        storeLabels(sid, list => list.some(a => a.id === saved.id) ? list.map(a => a.id === saved.id ? saved : a) : [...list, saved], context);
      }
      invalidateProjections(sid);
      return saved;
    }
    async function deleteLabel(sid, id, expectedRevision) {
      const context = state;
      await call('annotations.delete', {session_id: sid, annotation_id: id, expected_revision: expectedRevision}, context);
      if (context.generation !== state.generation) return null;
      storeLabels(sid, list => list.filter(a => a.id !== id), context);
      invalidateProjections(sid);
      return {deleted: true};
    }
    // ---- Bookmarks ----
    // A bookmark never takes the view-wide pending lock, so a press during a
    // stop, switch or other action is sent at once instead of being dropped.
    // It does not re-read the session either: the stored cue is added to the
    // open copy and the session's cached projections are dropped. One retry
    // reuses the request id, which the service answers idempotently.
    const BOOKMARK_RETRY_MS = 400;
    async function bookmark(sid, t_s, label) {
      const context = state;
      if (!sid || !Number.isFinite(t_s)) throw Object.assign(Error('A bookmark needs a session and a time'), {code: 'invalid_bookmark'});
      const note = typeof label === 'string' && label.trim() ? label.trim().slice(0, 500) : undefined;
      const args = {session_id: sid, t_s: Math.max(0, t_s), ...(note ? {note} : {}), request_id: uuid()};
      let result;
      for (let attempt = 0; ; attempt++) {
        try {
          result = await call('bookmarks.add', args, context);
          break;
        } catch (e) {
          const refused = Number.isFinite(e?.status) && e.status >= 400 && e.status < 500;
          if (attempt >= 1 || refused || context.generation !== state.generation) throw e;
          await new Promise(resolve => global.setTimeout(resolve, BOOKMARK_RETRY_MS));
        }
      }
      if (context.generation !== state.generation) return null;
      const text = typeof result?.label === 'string' && result.label ? result.label : note || 'Review this moment';
      const detail = state.detail;
      if (detail?.session?.id === sid) {
        if (result?.session) {
          // Demo adapter: it answers with the whole session detail.
          patch({detail: {...detail, events: normalizeDetail(result).events}});
        } else {
          const event = normalizeDetail({events: [{id: result?.id ?? args.request_id, kind: 'bookmark', payload: {at_ms: Math.round(args.t_s * 1000), label: text}}]}).events[0];
          if (!(detail.events || []).some(e => e.kind === 'bookmark' && e.id === event.id)) patch({detail: {...detail, events: [...(detail.events || []), event]}});
        }
      }
      invalidateProjections(sid);
      return {id: result?.id ?? null, session_id: sid, t_s: args.t_s, label: text};
    }
    // Re-read the stored findings (read-only) for the open session.
    async function findings(sid) {
      const context = state;
      if (context.mode !== 'real') return labelSession(sid)?.findings || null;
      try {
        const value = await call('sessions.findings', {session_id: sid}, context);
        if (context.generation === state.generation && labelSession(sid)) patch({detail: {...labelSession(sid), findings: value, findings_error: null, findings_stale: false}});
        return value;
      } catch (e) {
        if (context.generation === state.generation && labelSession(sid)) patch({detail: {...labelSession(sid), findings_error: e.message || String(e)}});
        throw e;
      }
    }
    // ---- Graph, timeline and replay projections (perception-lab-graph-1) ----
    // Reads only: sessions.graph / sessions.timeline / sessions.replay never run
    // inference or write. Responses are validated against the requesting site
    // and session; late or superseded responses are dropped.
    // The live graph is read only while a session records; each read costs the
    // controller's single CPU core, so reads are GRAPH_GAP_MS apart (the compact
    // sessions.live poll keeps its own 500 ms cadence).
    const GRAPH_CONTRACT = 'perception-lab-graph-1', GRAPH_GAP_MS = 3000, GRAPH_TIMEOUT_MS = 4000, REPLAY_BUCKET_MS = 500;
    let graphWatch = null, graphVisibility = null, graphHiddenSid = null;
    const projections = {}, projectionCache = new Map();
    const unavailableOp = e => ['operation_unavailable', 'lab_operation_denied', 'lab_unknown_operation'].includes(e?.code);
    function checkProjection(data, sid, context) {
      if (!data || typeof data !== 'object' || data.contract !== GRAPH_CONTRACT) throw Object.assign(Error('Graph contract not supplied'), {code: 'graph_contract_mismatch'});
      if (data.session_id !== sid || (data.site_id != null && data.site_id !== context.siteId)) throw Object.assign(Error('Graph session context mismatch'), {code: 'graph_context_mismatch'});
      if (data.inference != null && data.inference !== 'none') throw Object.assign(Error('Projection reported inference'), {code: 'graph_inference_reported'});
      return data;
    }
    function clearProjections() {
      if (graphWatch) { graphWatch.stopped = true; graphWatch.abort?.abort(); if (graphWatch.timer) global.clearTimeout(graphWatch.timer); graphWatch = null; }
      // Sent reads are not aborted; their late answers fail the sequence check.
      Object.values(projections).forEach(slot => { slot.seq++; slot.next = null; });
      projectionCache.clear();
      graphHiddenSid = null;
      return {graph: null, graphReplay: null, timeline: null, replayOutputs: null, graphPaused: null};
    }
    // Live graph: completion-chained reads GRAPH_GAP_MS apart while the session
    // records, each abandoned after GRAPH_TIMEOUT_MS; one read for a stopped
    // session. A hidden document, or a graph workspace that is not on screen
    // (scrolled away, or covered by the expanded video dock), stops the chain;
    // becoming visible again reads at once. The shown read keeps its read time.
    function watchGraph(sid) {
      if (!sid) return () => {};
      if (graphWatch && graphWatch.sid === sid && graphWatch.generation === state.generation && !graphWatch.stopped) {
        graphWatch.refs++;
        const same = graphWatch;
        return () => { if (--same.refs <= 0) stopGraph(same); };
      }
      if (graphWatch) stopGraph(graphWatch);
      const w = {sid, refs: 1, generation: state.generation, timer: null, abort: null, busy: false, read: null, stopped: false, done: false};
      graphWatch = w;
      const again = ms => {
        if (w.stopped || w.done || w.timer || graphWatch !== w) return;
        w.timer = global.setTimeout(tick, ms);
        w.timer?.unref?.();
      };
      w.again = again;
      async function tick() {
        w.timer = null;
        if (w.stopped || graphWatch !== w || global.document?.hidden || graphHiddenSid === w.sid) return;
        // The native transport may ignore abort; no second read overlaps one that is still settling.
        if (w.busy || w.read) return again(GRAPH_GAP_MS);
        w.busy = true;
        const context = state;
        const abort = global.AbortController ? new global.AbortController() : null;
        const expiry = abort ? global.setTimeout(() => abort.abort(), GRAPH_TIMEOUT_MS) : null;
        w.abort = abort;
        try {
          const read = call('sessions.graph', {session_id: sid}, context, abort ? {signal: abort.signal} : undefined);
          const settled = w.read = read.then(() => {}, () => {}).then(() => { if (w.read === settled) w.read = null; });
          const data = await (abort ? Promise.race([read, new Promise((_, reject) =>
            abort.signal.addEventListener('abort', () => reject(Object.assign(Error('Graph read abandoned'), {code: 'graph_read_timeout'})), {once: true}))]) : read);
          if (w.stopped || graphWatch !== w || context.generation !== state.generation) return;
          checkProjection(data, sid, context);
          if (data.cursor?.mode && data.cursor.mode !== 'live') throw Object.assign(Error('Live graph returned a replay cursor'), {code: 'graph_cursor_mismatch'});
          patch({graph: {session_id: sid, at_ms: null, data, status: 'ready', error: null, received_at_ms: Date.now(), reads: (state.graph?.session_id === sid ? state.graph.reads || 0 : 0) + 1}});
          if (!isRecording(data.session)) w.done = true;
        } catch (e) {
          if (w.stopped || graphWatch !== w || context.generation !== state.generation) return;
          const prior = state.graph?.session_id === sid ? state.graph : {session_id: sid, at_ms: null, data: null, reads: 0};
          patch({graph: {...prior, status: 'failed', error: abort?.signal.aborted ? 'graph_read_timeout' : e?.code || e?.message || 'graph_read_failed'}});
          if (unavailableOp(e)) w.done = true;
        } finally {
          if (expiry) global.clearTimeout(expiry);
          w.abort = null;
          w.busy = false;
          again(GRAPH_GAP_MS);
        }
      }
      if (!graphVisibility && global.document?.addEventListener) {
        graphVisibility = () => { const g = graphWatch; if (g && !global.document.hidden && !g.busy) g.again(0); };
        global.document.addEventListener('visibilitychange', graphVisibility);
      }
      again(0);
      return () => { if (--w.refs <= 0) stopGraph(w); };
    }
    // The graph view reports whether the workspace is on screen. Paused reads are
    // not queued: the next read starts when it becomes visible again.
    function graphVisible(sid, visible) {
      if (!sid) return;
      if (visible) { if (graphHiddenSid === sid) graphHiddenSid = null; }
      else graphHiddenSid = sid;
      const g = graphWatch;
      if (visible && g && g.sid === sid && !g.busy && !global.document?.hidden) g.again?.(0);
      patch({graphPaused: graphHiddenSid != null && graphHiddenSid === sid ? 'not_visible' : null});
    }
    function stopGraph(w) {
      w.stopped = true;
      w.abort?.abort();
      if (w.timer) global.clearTimeout(w.timer);
      w.timer = null;
      if (graphWatch === w) graphWatch = null;
    }
    // Replay-side reads at a cursor bucket. One read per slot is in flight at a
    // time and a sent read is never aborted (the native transport would keep its
    // permit and the controller its worker thread); only the latest wanted read
    // waits, and it is issued when the current one settles.
    const cacheable = data => !isRecording(data?.session) && data?.cursor?.clamped_to_now !== true && data?.range?.clamped_to_now !== true;
    function project(slot, op, sid, args, meta) {
      const context = state, s = projections[slot] ||= {seq: 0, busy: null, next: null};
      const key = [op, context.siteId, context.generation, state.projectionEpoch || 0, sid, JSON.stringify(args)].join('|');
      if (s.busy) {
        if (s.busy.key === key && s.busy.seq === s.seq) { s.next = null; return s.busy.done; }
        s.next = {generation: context.generation, run: () => project(slot, op, sid, args, meta)};
        return s.busy.done.then(() => null);
      }
      s.next = null;
      if (state[slot]?.key === key && state[slot].status === 'ready') return Promise.resolve(state[slot].data);
      const hit = projectionCache.get(key);
      if (hit) { patch({[slot]: {...meta, session_id: sid, key, data: hit, status: 'ready', error: null, cached: true}}); return Promise.resolve(hit); }
      const request = ++s.seq;
      // A later cursor's data is never kept while an earlier cursor loads.
      const prior = state[slot]?.session_id === sid && !(Number.isFinite(meta.at_ms) && Number.isFinite(state[slot].at_ms) && state[slot].at_ms > meta.at_ms) ? state[slot] : {data: null};
      patch({[slot]: {...prior, ...meta, session_id: sid, key, status: 'loading', error: null, requested_key: key}});
      const entry = {key, seq: request};
      s.busy = entry;
      entry.done = (async () => {
        try {
          const data = await call(op, {session_id: sid, ...args}, context);
          if (context.generation !== state.generation || s.seq !== request) return null;
          checkProjection(data, sid, context);
          if (cacheable(data)) {
            projectionCache.set(key, data);
            if (projectionCache.size > 32) projectionCache.delete(projectionCache.keys().next().value);
          }
          patch({[slot]: {...meta, session_id: sid, key, data, status: s.next ? 'loading' : 'ready', error: null, received_at_ms: Date.now()}});
          return data;
        } catch (e) {
          if (context.generation === state.generation && s.seq === request) patch({[slot]: {...(state[slot] || {}), status: 'failed', error: e?.code || e?.message || 'projection_failed'}});
          return null;
        } finally {
          if (s.busy === entry) {
            s.busy = null;
            const next = s.next; s.next = null;
            if (next && next.generation === state.generation) next.run();
          }
        }
      })();
      return entry.done;
    }
    // Labels, bookmarks and refreshes change what a projection shows: drop that
    // session's cached reads and let the view refetch at its cursor.
    function invalidateProjections(sid) {
      for (const key of [...projectionCache.keys()]) if (!sid || key.split('|')[4] === String(sid)) projectionCache.delete(key);
      Object.values(projections).forEach(slot => { slot.seq++; });
      patch({projectionEpoch: (state.projectionEpoch || 0) + 1});
    }
    const bucketMs = ms => Math.max(0, Math.floor(Math.max(0, Math.round(ms)) / REPLAY_BUCKET_MS) * REPLAY_BUCKET_MS);
    function graphAt(sid, ms) {
      if (!sid || !Number.isFinite(ms)) return Promise.resolve(null);
      const at = Math.min(1800000, bucketMs(ms));
      return project('graphReplay', 'sessions.graph', sid, {at_ms: at}, {at_ms: at});
    }
    function replayAt(sid, ms) {
      if (!sid || !Number.isFinite(ms)) return Promise.resolve(null);
      const at = Math.min(1800000, bucketMs(ms));
      return project('replayOutputs', 'sessions.replay', sid, {at_ms: at}, {at_ms: at});
    }
    function timelineWindow(sid, fromMs, toMs) {
      if (!sid) return Promise.resolve(null);
      const range = Number.isFinite(fromMs) && Number.isFinite(toMs) ? {from_ms: Math.max(0, Math.min(1800000, Math.floor(fromMs))), to_ms: Math.max(0, Math.min(1800000, Math.ceil(toMs)))} : {};
      if (range.from_ms > range.to_ms) return Promise.resolve(null);
      return project('timeline', 'sessions.timeline', sid, range, {from_ms: range.from_ms ?? null, to_ms: range.to_ms ?? null});
    }
    function schedule(ms) {
      if (!polling || timer) return;
      timer = global.setTimeout(poll, ms);
      timer?.unref?.();
    }
    // One live poll at a time: the next starts POLL_GAP_MS after this one ends.
    // A hidden tab stops the chain; visibilitychange restarts it.
    async function poll() {
      timer = null;
      if (!polling || global.document?.hidden) return;
      const active = state.activeSession;
      if (!active || state.pending || pollBusy || liveRead) return schedule(POLL_GAP_MS);
      pollBusy = true;
      const generation = state.generation;
      const abort = global.AbortController ? new global.AbortController() : null;
      const expiry = abort ? global.setTimeout(() => abort.abort(), POLL_TIMEOUT_MS) : null;
      pollAbort = abort;
      try {
        let d;
        if (state.mode === 'real') {
          delivery.bind(state.siteId, active.id);
          const sent = Date.now();
          const read = call('sessions.live', {session_id:active.id}, state, abort ? {signal:abort.signal} : undefined);
          // The native transport ignores the signal and holds a permit until it answers:
          // the poll gives up at the bound, drops the late answer and starts no read meanwhile.
          const settled = liveRead = read.then(() => {}, () => {}).then(() => { if (liveRead === settled) liveRead = null; });
          const live = await (abort ? Promise.race([read, new Promise((_, reject) =>
            abort.signal.addEventListener('abort', () => reject(Error('Live status abandoned')), {once: true}))]) : read);
          // The bound covers the compact live read only, not the one final archive read.
          if (expiry) global.clearTimeout(expiry);
          if (generation !== state.generation) return;
          if (live.session?.id !== active.id || live.site_id !== state.siteId) throw Error('Live session context mismatch');
          delivery.poll(true, live.server_time, sent, Date.now());
          if (isRecording(live.session)) {
            const prior = state.detail?.session?.id === active.id ? state.detail : {};
            d = {...prior, ...live, live_received_at_ms:Date.now()};
          } else {
            delivery.end();
            // Fetch the final archive once, after recording stops.
            d = await readDetail(active.id);
          }
        } else d = await readDetail(active.id);
        if (generation === state.generation) patch({
          activeSession: isRecording(d.session) ? d.session : null,
          sessions: state.sessions.map(s => s.id === active.id ? d.session : s),
          ...(state.detail?.session?.id === active.id ? {
            detail: d
          } : {}),
          // A later successful poll clears only the error this poller raised.
          ...(pollError && state.error === pollError ? {error: null} : {})
        });
        pollError = null;
        // The session ended here (stopped, stopped elsewhere, or failed to
        // start): re-read capabilities so setup readiness is not left showing
        // the comparison that just ended.
        if (d && generation === state.generation && !isRecording(d.session)) await refresh();
      } catch (e) {
        if (polling && state.mode === 'real') delivery.poll(false);
        if (polling && generation === state.generation) {
          pollError = abort?.signal.aborted ? 'Live status did not arrive within ' + POLL_TIMEOUT_MS / 1000 + ' s' : e.message;
          patch({
            error: pollError
          });
        }
      } finally {
        if (expiry) global.clearTimeout(expiry);
        if (pollAbort === abort) pollAbort = null;
        pollBusy = false;
        schedule(POLL_GAP_MS);
      }
    }
    function begin() {
      if (polling) return;
      polling = true;
      if (!visibility && global.document?.addEventListener) {
        visibility = () => {
          if (global.document.hidden) delivery.pause();
          else if (!pollBusy) schedule(0);
        };
        global.document.addEventListener('visibilitychange', visibility);
      }
      schedule(0);
    }
    return {
      getSnapshot: () => state,
      subscribe(fn) {
        listeners.add(fn);
        return () => listeners.delete(fn);
      },
      refresh,
      open,
      action,
      mediaPage,
      mediaWindow,
      replayWindow,
      watchGraph,
      graphVisible,
      graphAt,
      replayAt,
      timelineWindow,
      invalidateProjections,
      saveLabel,
      deleteLabel,
      findings,
      bookmark,
      start: begin,
      async setMode(mode) {
        if (state.activeSession) throw Error("Stop the current recording before changing connection mode");
        if (mode !== "demo" && mode !== "real") throw Error("Unknown mode");
        if (mode === "demo" && !demo) demo = createDemoAdapter();
        await global.HomePerceptionLabTransport?.invalidate?.();
        patch({
          ...clearProjections(),
          mode,
          siteId: mode === 'demo' ? 'victoria' : state.siteId,
          generation: state.generation + 1,
          status: "loading",
          capabilities: null,
          detail: null,
          sessions: [],
          activeSession: null,
          error: null
        });
        await refresh();
        begin();
      },
      async setSite(siteId) {
        if (!['echo', 'victoria'].includes(siteId)) throw Error('Site not supported');
        if (state.activeSession) throw Error('Stop recording before changing the experiment site');
        await global.HomePerceptionLabTransport?.invalidate?.();
        patch({
          ...clearProjections(),
          siteId,
          generation: state.generation + 1,
          status: 'loading',
          capabilities: null,
          detail: null,
          sessions: [],
          activeSession: null
        });
        await refresh();
      },
      // Delivery measurement accepts paints only for the recording session in view.
      delivery: {
        paint(arm, frame, sessionId) { if (isRecording(state.activeSession) && state.activeSession.id === sessionId) delivery.paint(arm, frame); },
        failure(arm, code, sessionId) { if (isRecording(state.activeSession) && state.activeSession.id === sessionId) delivery.failure(arm, code); },
        retry(arm, _code, sessionId) { if (isRecording(state.activeSession) && state.activeSession.id === sessionId) delivery.retry(arm); },
        pause: arm => delivery.pause(arm),
        clock: () => delivery.clock(),
        snapshot: () => delivery.snapshot(),
        reset: () => delivery.reset()
      },
      dispose() {
        Object.values(windowAborts).forEach(abort => abort?.abort());
        clearProjections();
        if (graphVisibility) global.document?.removeEventListener?.('visibilitychange', graphVisibility);
        graphVisibility = null;
        polling = false;
        pollAbort?.abort();
        if (timer) global.clearTimeout(timer);
        timer = null;
        if (visibility) global.document?.removeEventListener?.('visibilitychange', visibility);
        visibility = null;
        listeners.clear();
      }
    };
  }
  function sourceHealth(detail, connected, now = Date.now()) {
    const recent = connected && Number.isFinite(detail?.live_received_at_ms) &&
      now >= detail.live_received_at_ms && now - detail.live_received_at_ms <= 5000;
    const labels = { current: 'Last read succeeded', fetching: 'Reading', idle: 'Waiting',
      unavailable: 'Read failed', stopped: 'Stopped', not_provisioned: 'Not connected' };
    return [['preview_status', 'RGB samples'], ['numerical_status', 'Depth bundles'], ['ha_status', 'HA context']]
      .map(([key, name]) => {
        const value = recent ? detail[key] : null;
        return { key, name, state: value && labels[value] ? value : 'unknown',
          label: value && labels[value] ? labels[value] : 'Status unknown' };
      });
  }
  // Optional live() fields from newer services; older services omit them and
  // nothing is shown. Values expire with the live status (5 s), like sourceHealth.
  const liveRecent = (detail, connected, now) => connected && Number.isFinite(detail?.live_received_at_ms) &&
    now >= detail.live_received_at_ms && now - detail.live_received_at_ms <= 5000;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  function serviceDelivery(detail, armId, connected, now = Date.now()) {
    if (!liveRecent(detail, connected, now)) return null;
    const value = detail.arms?.[armId]?.delivery || detail.delivery?.[armId] || detail.delivery?.[String(armId).toLowerCase()];
    if (!value || typeof value !== 'object') return null;
    const n = key => finite(value[key]) ? value[key] : null, parts = [];
    if (n('source_age_s') != null) parts.push('source ' + n('source_age_s').toFixed(1) + ' s old');
    if (n('output_age_s') != null) parts.push('output ' + n('output_age_s').toFixed(1) + ' s old');
    if (n('fresh_10s') != null) parts.push(n('fresh_10s') + ' fresh in 10 s');
    if (n('fresh_30s') != null) parts.push(n('fresh_30s') + (n('completed_30s') != null ? '/' + n('completed_30s') + ' fresh/completed' : ' fresh') + ' in 30 s');
    else if (n('completed_30s') != null) parts.push(n('completed_30s') + ' completed in 30 s');
    if (n('max_gap_s') != null) parts.push('max gap ' + n('max_gap_s').toFixed(1) + ' s');
    if (finite(detail.server_time)) Object.keys(value).filter(k => /^last_[a-z]{1,20}_at$/.test(k) && finite(value[k])).slice(0, 4)
      .forEach(k => parts.push('last ' + k.slice(5, -3) + ' ' + Math.max(0, (timestamp(detail.server_time) - timestamp(value[k])) / 1000).toFixed(1) + ' s before poll'));
    return parts.length ? parts.join(' · ') : null;
  }
  function acquisitionCounters(detail, connected, now = Date.now()) {
    const value = liveRecent(detail, connected, now) ? detail.acquisition : null, out = [];
    if (!value || typeof value !== 'object' || Array.isArray(value)) return out;
    for (const [key, v] of Object.entries(value)) {
      if (!/^[a-z0-9_]{1,40}$/.test(key)) continue;
      if (finite(v)) out.push([key, v]);
      else if (v && typeof v === 'object' && !Array.isArray(v)) Object.entries(v).forEach(([k, n]) => {
        if (/^[a-z0-9_]{1,40}$/.test(k) && finite(n)) out.push([key + '.' + k, n]);
      });
    }
    return out.slice(0, 12);
  }
  global.HomePerceptionLabData = {
    serviceDelivery,
    acquisitionCounters,
    recordingOnly:(caps, connected) => connected === true && Array.isArray(caps?.gates) && caps.gates.some(g =>
      g?.id === 'inference' && g.state === 'blocked' && g.reason === 'recording_only_commissioning'),
    sourceHealth,
    CATALOG,
    STATIC_CATALOG,
    ACTIVITY_LABELS,
    activityLabel,
    activityId,
    FAMILIES,
    ALTERNATIVES,
    mergeCatalog,
    experimentReadiness,
    setupReadiness,
    setupGroups,
    setupName,
    startBlockedReason,
    readinessSettling,
    startFailure,
    formatTime,
    seconds,
    timestamp,
    isRecording,
    recordingLabel,
    layerStatus,
    overlaySupport,
    causalResults,
    columnRuns,
    scoreIntervals,
    normalizeDetail,
    createDemoAdapter,
    createController,
    createDeliveryMonitor,
    collectExport,
    uuid
  };
  global.HomePerceptionLabController = global.HomePerceptionLabController || createController();
  // Read-only acceptance view: each read is a fresh frozen snapshot with reset().
  if (!Object.getOwnPropertyDescriptor(global, '__perceptionLabDelivery')) Object.defineProperty(global, '__perceptionLabDelivery', {
    configurable: true,
    get() {
      const controller = global.HomePerceptionLabController;
      const snapshot = controller?.delivery?.snapshot?.();
      if (!snapshot) return undefined;
      return Object.freeze(Object.defineProperty({...snapshot}, 'reset', {value: () => controller.delivery.reset()}));
    }
  });
})(typeof window !== "undefined" ? window : globalThis);

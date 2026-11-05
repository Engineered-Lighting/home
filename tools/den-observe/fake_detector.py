"""A scripted stand-in for YOLO26s + RTMPose-m, for offline replay. Standard library only.

It never looks at pixels. By default it places one standing 'person' box with
COCO-17 keypoints at a fixed spot in the RGB frame, swaying a few pixels with
time; given `pixels` (joint name -> RGB pixel, as synth.Scene.joint_rgb gives
them) it places the keypoints there instead. Its result is 'complete'
latency_s after capture (available_ts), so freshness has something to measure.
It is a plumbing fake, not a detector, and its rows say so in detector.boxes
("fake-scripted-1").
"""
import math

import detections

NAME = "fake-scripted-1"
LATENCY_S = 0.30  # scripted capture-to-result time; R5 measured about 1.46 s live
# A standing figure in a 640x360 frame, as fractions of (width, height).
FIGURE = {"nose": (0.500, 0.180), "left_eye": (0.510, 0.165), "right_eye": (0.490, 0.165),
          "left_ear": (0.525, 0.175), "right_ear": (0.475, 0.175),
          "left_shoulder": (0.545, 0.280), "right_shoulder": (0.455, 0.280),
          "left_elbow": (0.560, 0.420), "right_elbow": (0.440, 0.420),
          "left_wrist": (0.565, 0.540), "right_wrist": (0.435, 0.540),
          "left_hip": (0.530, 0.550), "right_hip": (0.470, 0.550),
          "left_knee": (0.530, 0.730), "right_knee": (0.470, 0.730),
          "left_ankle": (0.530, 0.900), "right_ankle": (0.470, 0.900)}


def row(capture_ts, *, rgb_seq=None, width=640, height=360, centre=(0.5, 0.5), sway_px=4.0, period_s=8.0,
        pixels=None, scores=None, latency_s=LATENCY_S, clock="host_monotonic"):
    """One row for the frame captured at capture_ts. centre moves the default figure
    (fractions); pixels places each named joint exactly (None drops it); scores
    overrides a joint's score (default 0.9)."""
    dx = sway_px * math.sin(2 * math.pi * capture_ts / period_s)
    ox, oy = (centre[0] - 0.5) * width + dx, (centre[1] - 0.5) * height
    points = []
    for name in detections.COCO17:
        if pixels is not None:
            pixel = pixels.get(name)
            if pixel is None:
                points.append(None)
                continue
            u, v = pixel
        else:
            fx, fy = FIGURE[name]
            u, v = fx * width + ox, fy * height + oy
        u, v = min(max(u, 0.0), float(width)), min(max(v, 0.0), float(height))
        points.append([round(u, 3), round(v, 3), (scores or {}).get(name, 0.9)])
    us, vs = [p[0] for p in points if p], [p[1] for p in points if p]
    box = [max(0.0, min(us) - 0.04 * width), max(0.0, min(vs) - 0.06 * height),
           min(float(width), max(us) + 0.04 * width), min(float(height), max(vs) + 0.04 * height)]
    frame = {"capture_ts": capture_ts, "clock": clock, "width": width, "height": height}
    if rgb_seq is not None:
        frame["rgb_seq"] = rgb_seq
    out = {"schema": detections.SCHEMA, "frame": frame,
           "detector": {"boxes": NAME, "boxes_version": NAME, "keypoints": NAME, "keypoints_version": NAME,
                        "keypoint_layout": "coco17"},
           "detections": [{"id": f"{rgb_seq if rgb_seq is not None else 0}:0", "class": "person", "score": 0.9,
                           "box_xyxy": [round(v, 3) for v in box], "keypoints": points}]}
    if latency_s is not None:
        out["available_ts"] = capture_ts + latency_s
    return detections.validate(out)

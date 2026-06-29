#!/usr/bin/env python3
"""Stage C perception service — RTSP cameras -> YOLO pose -> tracker ingestion.

Reads camera RTSP URLs from ~/stagec/cams.json (mode 600) and runs one
FFMPEG VideoCapture reader thread per camera (~12 fps sampling, stale
frames dropped via a continuous grab() drain). A single inference loop
batches the freshest frame of each camera through yolo11s-pose
(ultralytics, device=0, half) and POSTs one measurement per camera per
tick to the Stage B tracker:

  POST http://localhost:8098/ingest/detection      (JSON list = batch)
  {"camera": "<frigate name>", "ts": <unix float>, "foot_px": [u, v],
   "score": <0..1>, "source": "pose-ankles"|"pose-hips"|"bbox-bottom",
   "person": null}

foot_px is in the CALIBRATION resolution space (1280x720 for all cams);
coords are rescaled from the actual capture resolution.

Source semantics (consumed by the tracker / agent A side):
  pose-ankles : average of visible ankle keypoints (conf > 0.35) — true
                ground contact; tracker intersects floor z=0; sigma 0.6x.
  pose-hips   : hip midpoint when ankles are occluded (seated/blocked
                person); hips sit at ~0.55 m when seated, so the tracker
                intersects the z=0.55 m hip-when-seated plane; sigma 1.0x.
  bbox-bottom : bbox bottom-center fallback; floor z=0; sigma 1.0x.

The tracker treats each measurement like a Frigate event measurement but
keyed by a synthetic per-camera stream id ('stagec:<camera>').
"""
import json
import logging
import os
import signal
import sys
import threading
import time

# Force TCP for RTSP and bound blocking socket I/O (must be set before the
# first VideoCapture is created so the FFMPEG backend picks it up).
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|rw_timeout;5000000"
)

import cv2  # noqa: E402
import requests  # noqa: E402

# ----------------------------------------------------------------------------
CAMS_JSON = os.path.expanduser("~/stagec/cams.json")
MODEL_PATH = os.path.expanduser("~/stagec/yolo11s-pose.pt")
TRACKER_URL = "http://localhost:8098/ingest/detection"

CAL_W, CAL_H = 1280, 720          # calibration resolution space
TARGET_FPS = 12.0                 # per-camera sampling rate
TICK = 1.0 / TARGET_FPS
KPT_CONF = 0.35                   # keypoint visibility threshold
DET_CONF = 0.30                   # absolute min person box confidence
DET_CONF_SOLO = 0.40              # below this, require keypoint corroboration
KPT_CORROBORATE = 0.50            # an ankle/hip joint this confident vouches
                                  # for a low-conf box (seated/far persons often
                                  # score ~0.3 while joints are >0.8; phantom
                                  # boxes have diffuse low-conf keypoints)
POST_TIMEOUT = 1.0
RECONNECT_MAX_BACKOFF = 30.0
STALE_FRAME_MAX_AGE = 2.0         # never infer on frames older than this
STATS_EVERY = 10.0                # seconds between INFO stat lines
CONN_ERR_LOG_EVERY = 30.0         # throttle connection-error logging

# COCO-17 keypoint indices
L_HIP, R_HIP, L_ANKLE, R_ANKLE = 11, 12, 15, 16

log = logging.getLogger("stagec")


class CameraReader(threading.Thread):
    """Continuously grab()s an RTSP stream (draining stale frames) and
    retrieve()s a frame every 1/TARGET_FPS s into a single latest-frame slot.
    Reconnects with exponential backoff on failure."""

    def __init__(self, name: str, url: str):
        super().__init__(name=f"reader-{name}", daemon=True)
        self.cam = name
        self.url = url
        self.lock = threading.Lock()
        self.latest = None            # (frame, ts) — consumed by take()
        self.frames_sampled = 0
        self.connected = False
        self.stop_evt = threading.Event()
        self.last_frame_wall = 0.0

    def take(self):
        """Return and clear the latest (frame, ts), or None if nothing new."""
        with self.lock:
            item, self.latest = self.latest, None
        return item

    def _open(self):
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap if cap.isOpened() else None

    def run(self):
        backoff = 1.0
        while not self.stop_evt.is_set():
            cap = self._open()
            if cap is None:
                log.warning("[%s] RTSP open failed, retrying in %.0fs", self.cam, backoff)
                self.connected = False
                self.stop_evt.wait(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_BACKOFF)
                continue
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            log.info("[%s] RTSP connected (%dx%d)", self.cam, w, h)
            self.connected = True
            backoff = 1.0
            fails = 0
            # Credit-based sampling schedule: advancing by TICK per sample
            # (instead of snapping to `now`) avoids beat-frequency lock-in
            # (e.g. a 15 fps stream gated at >=1/12s would yield 7.5 fps).
            next_sample = time.monotonic()
            while not self.stop_evt.is_set():
                if not cap.grab():
                    fails += 1
                    if fails >= 25:
                        log.warning("[%s] stream stalled (%d grab failures) — reconnecting", self.cam, fails)
                        break
                    time.sleep(0.02)
                    continue
                fails = 0
                now = time.monotonic()
                if now >= next_sample:
                    ok, frame = cap.retrieve()
                    if ok and frame is not None and frame.size:
                        next_sample += TICK
                        if next_sample < now - TICK:  # cap catch-up credit
                            next_sample = now
                        ts = time.time()
                        with self.lock:
                            self.latest = (frame, ts)
                        self.frames_sampled += 1
                        self.last_frame_wall = ts
            cap.release()
            self.connected = False
        log.info("[%s] reader stopped", self.cam)


def extract_measurement(res, cam: str, ts: float, fw: int, fh: int,
                        ignore=None):
    """Best non-ignored person in one ultralytics pose result -> contract
    dict (or None).  ``ignore`` is an optional list of [x1, y1, x2, y2]
    boxes in CALIBRATION space; a candidate whose foot point lands inside
    one is skipped (static false-positive regions, e.g. a dark shelf cubby
    YOLO reads as a seated person 24/7) and the next-best candidate gets a
    chance instead."""
    boxes = res.boxes
    if boxes is None or len(boxes) == 0:
        return None
    confs = boxes.conf.cpu().numpy()
    kpts = res.keypoints
    for i in confs.argsort()[::-1]:
        i = int(i)
        score = float(confs[i])
        if score < DET_CONF:
            break  # sorted descending — nothing better follows

        u = v = None
        source = "bbox-bottom"
        kcf = None
        if kpts is not None and kpts.conf is not None and len(kpts) > i:
            kxy = kpts.xy[i].cpu().numpy()        # (17, 2)
            kcf = kpts.conf[i].cpu().numpy()      # (17,)
        if score < DET_CONF_SOLO:
            # low-conf box: only keep it if a major lower-body joint vouches
            if kcf is None or max(kcf[j] for j in (L_ANKLE, R_ANKLE, L_HIP, R_HIP)) < KPT_CORROBORATE:
                continue
        if kcf is not None:
            ankles = [kxy[j] for j in (L_ANKLE, R_ANKLE) if kcf[j] > KPT_CONF]
            if ankles:
                u = sum(p[0] for p in ankles) / len(ankles)
                v = sum(p[1] for p in ankles) / len(ankles)
                source = "pose-ankles"
            else:
                hips = [kxy[j] for j in (L_HIP, R_HIP) if kcf[j] > KPT_CONF]
                if hips:
                    u = sum(p[0] for p in hips) / len(hips)
                    v = sum(p[1] for p in hips) / len(hips)
                    source = "pose-hips"
        if u is None:
            x1, y1, x2, y2 = boxes.xyxy[i].cpu().numpy()
            u, v = (float(x1) + float(x2)) / 2.0, float(y2)

        # rescale capture-resolution px -> 1280x720 calibration space
        # (float() casts: keypoint coords are numpy float32, not JSON-serializable)
        u = float(u) * (CAL_W / float(fw))
        v = float(v) * (CAL_H / float(fh))
        u = min(max(u, 0.0), CAL_W - 1.0)
        v = min(max(v, 0.0), CAL_H - 1.0)
        if ignore and any(bx1 <= u <= bx2 and by1 <= v <= by2
                          for bx1, by1, bx2, by2 in ignore):
            continue
        return {
            "camera": cam,
            "ts": ts,
            "foot_px": [round(u, 2), round(v, 2)],
            "score": round(score, 4),
            "source": source,
            "person": None,
        }
    return None


class Poster:
    """POSTs measurement batches; throttles connection-error logging and
    announces tracker status-code changes once instead of per tick."""

    def __init__(self):
        self.sess = requests.Session()
        self.last_status = None
        self.last_conn_err = 0.0
        self.ok = 0
        self.err = 0

    def post(self, batch):
        try:
            r = self.sess.post(TRACKER_URL, json=batch, timeout=POST_TIMEOUT)
            if r.status_code != self.last_status:
                if r.status_code < 300:
                    log.info("tracker accepting measurements (HTTP %d)", r.status_code)
                elif r.status_code == 404:
                    log.info(
                        "tracker returned 404 — /ingest/detection not deployed yet; "
                        "payload sent per contract (will succeed once agent A's endpoint lands)"
                    )
                else:
                    log.warning("tracker HTTP %d: %s", r.status_code, r.text[:200])
                self.last_status = r.status_code
            self.ok += 1
            return True
        except requests.RequestException as e:
            self.err += 1
            now = time.monotonic()
            if now - self.last_conn_err > CONN_ERR_LOG_EVERY:
                self.last_conn_err = now
                log.warning("tracker POST failed (%s) — continuing", e.__class__.__name__)
            self.last_status = None
            return False


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    with open(CAMS_JSON) as f:
        cfg = json.load(f)
    cams = cfg["cameras"]
    # per-camera static false-positive regions, calibration-space px boxes
    ignore_boxes = cfg.get("ignore_boxes") or {}
    log.info("stagec starting: cameras=%s ignore_boxes=%s -> %s",
             list(cams), {k: len(v) for k, v in ignore_boxes.items()}, TRACKER_URL)

    from ultralytics import YOLO  # deferred: slow import
    model = YOLO(MODEL_PATH if os.path.exists(MODEL_PATH) else "yolo11s-pose.pt")
    log.info("pose model loaded: %s", MODEL_PATH)

    readers = [CameraReader(name, c["rtsp"]) for name, c in cams.items()]
    for r in readers:
        r.start()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    poster = Poster()
    infer_ms_ema = None
    det_counts = {}      # source -> n
    cam_counts = {}      # camera -> n
    ticks = 0
    last_stats = time.monotonic()
    prev_sampled = {r.cam: 0 for r in readers}

    while not stop.is_set():
        t0 = time.monotonic()
        frames, metas = [], []
        now_wall = time.time()
        for r in readers:
            item = r.take()
            if item is None:
                continue
            frame, ts = item
            if now_wall - ts > STALE_FRAME_MAX_AGE:
                continue
            frames.append(frame)
            metas.append((r.cam, ts, frame.shape[1], frame.shape[0]))

        if frames:
            ti = time.monotonic()
            results = model(frames, device=0, half=True, conf=DET_CONF, verbose=False)
            dt = (time.monotonic() - ti) * 1000.0
            infer_ms_ema = dt if infer_ms_ema is None else 0.9 * infer_ms_ema + 0.1 * dt
            batch = []
            for res, (cam, ts, fw, fh) in zip(results, metas):
                m = extract_measurement(res, cam, ts, fw, fh,
                                        ignore=ignore_boxes.get(cam))
                if m is not None:
                    batch.append(m)
                    det_counts[m["source"]] = det_counts.get(m["source"], 0) + 1
                    cam_counts[cam] = cam_counts.get(cam, 0) + 1
            if batch:
                poster.post(batch)
        ticks += 1

        now = time.monotonic()
        if now - last_stats >= STATS_EVERY:
            span = now - last_stats
            cam_fps = {
                r.cam: round((r.frames_sampled - prev_sampled[r.cam]) / span, 1)
                for r in readers
            }
            prev_sampled = {r.cam: r.frames_sampled for r in readers}
            log.info(
                "stats: cam_fps=%s detections=%s by_source=%s infer_ms=%.1f "
                "posts_ok=%d posts_err=%d last_http=%s",
                cam_fps, cam_counts, det_counts,
                infer_ms_ema or -1.0, poster.ok, poster.err, poster.last_status,
            )
            det_counts, cam_counts = {}, {}
            last_stats = now

        time.sleep(max(0.0, TICK - (time.monotonic() - t0)))

    log.info("shutting down")
    for r in readers:
        r.stop_evt.set()
    for r in readers:
        r.join(timeout=5)
    log.info("stagec exited")


if __name__ == "__main__":
    main()

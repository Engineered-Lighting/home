"""spatial-tracker FastAPI service.

Wires together (and nothing more — the logic lives in the modules):
  * ModelStore poller     (model_store.py, HA apartment_model every 30 s)
  * Frigate MQTT consumer (aiomqtt, frigate/events, reconnect backoff)
  * Tracker predict loop  (tracker.py, 10 Hz KF predict + broadcast)
  * ActivityEngine + HA websocket listener (rules.py)
  * Frame recorder / replay (replay.py)
  * Calibration endpoints (calib.py)
  * Camera drift watch (6-hourly ORB match vs {DATA_DIR}/calib_ref/<cam>.jpg)

Endpoints:
  GET /healthz   {ok, mqtt_connected, ha_model_revision, tracks_active,
                  camera_drift, uptime_s}
  GET /model     current apartment model (read-through cache)
  GET /tracks    latest broadcast frame (REST snapshot)
  POST /ingest/detection   Stage-C external measurements (single object or
                  JSON list) -> Tracker.ingest_external
  GET /replay/sessions
  WS  /ws/tracks            live 10 Hz broadcast
  WS  /ws/tracks?replay=YYYYMMDD   recorded day, real-time paced, looped
  /calib/*       see calib.py

ENV (defaults): MQTT_HOST=192.168.0.125 MQTT_PORT=1883 MQTT_USER= MQTT_PASS=
  FRIGATE_URL=http://192.168.0.125:5000 HA_URL=http://192.168.0.125:8123
  HA_TOKEN= DATA_DIR=/data PORT=8098 REPLAY_MODE=0 RECORD_DEDUP_EMPTY=1
REPLAY_MODE=1 starts the service without MQTT/HA connections (replay-only).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from typing import Literal, Optional, Union

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

import calib
import replay as replay_mod
from model_store import ModelStore
from rules import ActivityEngine, HAStateListener
from tracker import Tracker

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("spatial.main")

BROADCAST_HZ = 10.0
RAW_MQTT_LOG_SAMPLES = 3

# ---- camera drift watch ------------------------------------------------------
DRIFT_CHECK_INTERVAL_S = 6 * 3600.0  # every 6 h
DRIFT_FIRST_DELAY_S = 60.0           # let the model poller populate first
DRIFT_THRESHOLD_PX = 12.0            # median keypoint displacement alarm
DRIFT_MIN_MATCHES = 10               # fewer ORB matches -> inconclusive
DRIFT_REF_DIRNAME = "calib_ref"      # {DATA_DIR}/calib_ref/<cam>.jpg


class Config:
    def __init__(self, env=os.environ):
        self.mqtt_host = env.get("MQTT_HOST", "192.168.0.125")
        self.mqtt_port = int(env.get("MQTT_PORT", "1883"))
        self.mqtt_user = env.get("MQTT_USER", "")
        self.mqtt_pass = env.get("MQTT_PASS", "")
        self.frigate_url = env.get("FRIGATE_URL", "http://192.168.0.125:5000").rstrip("/")
        self.ha_url = env.get("HA_URL", "http://192.168.0.125:8123").rstrip("/")
        self.ha_token = env.get("HA_TOKEN", "")
        self.data_dir = env.get("DATA_DIR", "/data")
        self.port = int(env.get("PORT", "8098"))
        self.replay_mode = env.get("REPLAY_MODE", "0") == "1"
        self.record_dedup_empty = env.get("RECORD_DEDUP_EMPTY", "1") != "0"


class Hub:
    """Fan-out of live frames to connected websocket clients."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def broadcast(self, text: str) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


class AppContext:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.frigate_url = cfg.frigate_url
        self.data_dir = cfg.data_dir
        self.started = time.time()
        self.mqtt_connected = False
        self.latest_frame: dict = {"type": "tracks", "ts": time.time(), "tracks": []}
        self.camera_drift: dict[str, float] = {}  # cam -> median px (> threshold)
        self.hub = Hub()
        self._http_session = None

        self.model_store = ModelStore(cfg.ha_url, cfg.ha_token, cfg.data_dir)
        self.tracker = Tracker(
            model_provider=self.model_store.get_model,
            walkable_provider=self.model_store.walkable_hull,
        )
        self.ha_listener = HAStateListener(cfg.ha_url, cfg.ha_token)
        self.rules = ActivityEngine(
            model_provider=self.model_store.get_model,
            state_getter=self.ha_listener.state_getter,
        )
        self.ha_listener.engine = self.rules
        self.recorder = replay_mod.FrameRecorder(
            cfg.data_dir, dedup_empty=cfg.record_dedup_empty)
        self.tasks: list[asyncio.Task] = []

    async def http(self):
        import aiohttp
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    async def close(self):
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self.recorder.close()
        if self._http_session is not None and not self._http_session.closed:
            await self._http_session.close()


# ---- background loops -------------------------------------------------------

async def mqtt_loop(ctx: AppContext) -> None:
    import aiomqtt

    cfg = ctx.cfg
    backoff = 1.0
    raw_logged = 0
    while True:
        try:
            kwargs: dict = {"hostname": cfg.mqtt_host, "port": cfg.mqtt_port}
            if cfg.mqtt_user:  # empty user -> anonymous connect
                kwargs["username"] = cfg.mqtt_user
                kwargs["password"] = cfg.mqtt_pass
            async with aiomqtt.Client(**kwargs) as client:
                ctx.mqtt_connected = True
                backoff = 1.0
                log.info("MQTT connected to %s:%d (auth=%s)", cfg.mqtt_host,
                         cfg.mqtt_port, "user" if cfg.mqtt_user else "anonymous")
                await client.subscribe("frigate/events")
                async for msg in client.messages:
                    raw = msg.payload
                    if raw_logged < RAW_MQTT_LOG_SAMPLES:
                        raw_logged += 1
                        log.info("raw MQTT frigate/events sample %d: %s",
                                 raw_logged, raw[:2000])
                    try:
                        payload = json.loads(raw)
                    except (TypeError, ValueError):
                        log.warning("undecodable frigate/events payload: %r", raw[:200])
                        continue
                    try:
                        ctx.tracker.process_event(payload)
                    except Exception:
                        log.exception("tracker failed on frigate event")
        except asyncio.CancelledError:
            ctx.mqtt_connected = False
            raise
        except Exception as e:
            ctx.mqtt_connected = False
            log.warning("MQTT connection error: %s; reconnecting in %.0f s", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


async def model_poll_loop(ctx: AppContext) -> None:
    session = await ctx.http()
    await ctx.model_store.poll_forever(session)


# ---- camera drift watch -------------------------------------------------------

def _calibrated_cameras(model) -> list[str]:
    """Frigate names of cameras with full intrinsics + extrinsics."""
    cams: list[str] = []
    for dev in getattr(model, "devices", []) or []:
        cam = dev.get("camera") or {}
        name = cam.get("frigate_name")
        intr = cam.get("intrinsics") or {}
        extr = cam.get("extrinsics") or {}
        if (name and intr.get("K") and extr.get("q_wxyz")
                and (extr.get("C") is not None or extr.get("t") is not None)):
            cams.append(name)
    return cams


def _median_orb_displacement_px(ref_jpg: bytes, cur_jpg: bytes):
    """Median pixel displacement of cross-checked ORB matches between two
    JPEG frames; None when either frame is undecodable or matches are too
    few to judge.  Pure CPU — call via asyncio.to_thread."""
    import cv2
    import numpy as np

    ref = cv2.imdecode(np.frombuffer(ref_jpg, np.uint8), cv2.IMREAD_GRAYSCALE)
    cur = cv2.imdecode(np.frombuffer(cur_jpg, np.uint8), cv2.IMREAD_GRAYSCALE)
    if ref is None or cur is None:
        return None
    if ref.shape != cur.shape:  # substream/res change — compare apples to apples
        cur = cv2.resize(cur, (ref.shape[1], ref.shape[0]))
    orb = cv2.ORB_create(nfeatures=1000)
    kp_ref, des_ref = orb.detectAndCompute(ref, None)
    kp_cur, des_cur = orb.detectAndCompute(cur, None)
    if des_ref is None or des_cur is None:
        return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(des_ref, des_cur)
    if len(matches) < DRIFT_MIN_MATCHES:
        return None
    src = np.float64([kp_ref[m.queryIdx].pt for m in matches])
    dst = np.float64([kp_cur[m.trainIdx].pt for m in matches])
    return float(np.median(np.linalg.norm(dst - src, axis=1)))


async def drift_check_once(ctx: AppContext) -> None:
    """One pass: fetch each calibrated camera's latest frame from Frigate and
    compare against its stored reference (saved on first sight)."""
    cams = _calibrated_cameras(ctx.model_store.get_model())
    if not cams:
        log.info("drift check: no calibrated cameras in model; skipping pass")
        return
    ref_dir = os.path.join(ctx.cfg.data_dir, DRIFT_REF_DIRNAME)
    os.makedirs(ref_dir, exist_ok=True)
    session = await ctx.http()
    for cam in cams:
        try:
            url = f"{ctx.frigate_url}/api/{cam}/latest.jpg"
            async with session.get(url, timeout=20) as resp:
                if resp.status != 200:
                    log.error("drift check: GET %s -> HTTP %d; skipping %s",
                              url, resp.status, cam)
                    continue
                cur_jpg = await resp.read()
            if not cur_jpg:
                log.error("drift check: empty frame from %s; skipping", cam)
                continue
            ref_path = os.path.join(ref_dir, f"{cam}.jpg")
            if not os.path.isfile(ref_path):
                with open(ref_path, "wb") as f:
                    f.write(cur_jpg)
                log.info("drift check: saved reference frame for %s -> %s",
                         cam, ref_path)
                continue
            with open(ref_path, "rb") as f:
                ref_jpg = f.read()
            px = await asyncio.to_thread(
                _median_orb_displacement_px, ref_jpg, cur_jpg)
            if px is None:
                log.error("drift check: %s inconclusive (undecodable frame or "
                          "< %d ORB matches)", cam, DRIFT_MIN_MATCHES)
                continue
            if px > DRIFT_THRESHOLD_PX:
                ctx.camera_drift[cam] = round(px, 1)
                log.error("CAMERA DRIFT suspected on %s: median ORB keypoint "
                          "displacement %.1f px > %.0f px vs reference %s — "
                          "extrinsics may be stale, consider re-calibrating",
                          cam, px, DRIFT_THRESHOLD_PX, ref_path)
            else:
                if ctx.camera_drift.pop(cam, None) is not None:
                    log.info("drift check: %s back under threshold", cam)
                log.info("drift check: %s OK (median displacement %.1f px)",
                         cam, px)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("drift check failed for camera %s (continuing)", cam)


async def drift_watch_loop(ctx: AppContext) -> None:
    """Every 6 h ORB-match each calibrated camera against its reference frame;
    cameras whose median keypoint displacement exceeds DRIFT_THRESHOLD_PX are
    surfaced via /healthz as ``camera_drift``.  Never raises."""
    await asyncio.sleep(DRIFT_FIRST_DELAY_S)
    while True:
        try:
            await drift_check_once(ctx)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("drift watch pass failed (retrying in %.0f h)",
                          DRIFT_CHECK_INTERVAL_S / 3600.0)
        await asyncio.sleep(DRIFT_CHECK_INTERVAL_S)


async def broadcast_loop(ctx: AppContext) -> None:
    period = 1.0 / BROADCAST_HZ
    while True:
        t0 = time.time()
        try:
            ctx.tracker.predict_step(t0)
            tracks = ctx.tracker.snapshot(t0)
            ctx.rules.annotate(tracks, t0)
            frame = {"type": "tracks", "ts": t0, "tracks": tracks}
            ctx.latest_frame = frame
            try:
                ctx.recorder.append(frame)
            except Exception:
                log.exception("recorder append failed")
            await ctx.hub.broadcast(json.dumps(frame, separators=(",", ":")))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("broadcast iteration failed")
        await asyncio.sleep(max(0.0, period - (time.time() - t0)))


# ---- app ----------------------------------------------------------------------

cfg = Config()
ctx = AppContext(cfg)


async def _startup() -> None:
    loop_tasks = [asyncio.create_task(broadcast_loop(ctx), name="broadcast")]
    if not cfg.replay_mode:
        loop_tasks += [
            asyncio.create_task(mqtt_loop(ctx), name="mqtt"),
            asyncio.create_task(model_poll_loop(ctx), name="model-poll"),
            asyncio.create_task(ctx.ha_listener.run(), name="ha-ws"),
            asyncio.create_task(drift_watch_loop(ctx), name="drift-watch"),
        ]
    else:
        log.info("REPLAY_MODE=1: MQTT and HA connections disabled")
    ctx.tasks.extend(loop_tasks)
    try:
        ctx.recorder.cleanup()
    except Exception:
        log.exception("startup recording cleanup failed")


async def _shutdown() -> None:
    await ctx.close()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await _startup()
    try:
        yield
    finally:
        await _shutdown()


app = FastAPI(title="spatial-tracker", version="0.1.0", lifespan=lifespan)
# the Home app's webview calls the calib endpoints cross-origin; without CORS
# headers every fetch() POST dies as "Failed to fetch"
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])
app.state.ctx = ctx
app.include_router(calib.build_router(ctx))


@app.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "mqtt_connected": ctx.mqtt_connected,
        "ha_model_revision": ctx.model_store.get_model().revision,
        "tracks_active": ctx.tracker.active_count(),
        "camera_drift": ctx.camera_drift,
        "uptime_s": round(time.time() - ctx.started, 1),
    }


@app.get("/model")
async def model():
    return ctx.model_store.get_model().raw


@app.get("/tracks")
async def tracks():
    return ctx.latest_frame


# ---- external (Stage C) detection ingestion ----------------------------------

class ExternalDetection(BaseModel):
    """One Stage-C measurement.  ``foot_px`` is [u, v] in the camera's
    CALIBRATION resolution space (intrinsics image_size; 1280x720 for all
    current cams) — NOT detect-res."""
    camera: str
    ts: float
    foot_px: list[float] = Field(min_length=2, max_length=2)
    score: float = Field(ge=0.0, le=1.0)
    source: Literal["pose-ankles", "pose-hips", "bbox-bottom"]
    person: Optional[str] = None


@app.post("/ingest/detection")
async def ingest_detection(
        body: Union[ExternalDetection, list[ExternalDetection]]):
    """Stage-C ingestion: a single JSON object, or a JSON list for batching.

    Thin by design — parsing/validation here, semantics in
    ``Tracker.ingest_external`` (same undistort -> ray -> floor/seat-plane ->
    walkable-gate -> Kalman path as a Frigate event measurement, under the
    synthetic per-camera stream id ``stagec:<camera>``).  ``accepted`` counts
    detections that touched a track (precise fix OR room-level evidence);
    unmapped cameras and internal errors are skipped, not 4xx/5xx — a live
    pose pipeline should not crash-loop on one bad camera name.
    """
    items = body if isinstance(body, list) else [body]
    accepted = 0
    for det in items:
        try:
            track = ctx.tracker.ingest_external(
                camera=det.camera, ts=det.ts, foot_px=det.foot_px,
                score=det.score, source=det.source, person=det.person)
        except Exception:
            log.exception("ingest_external failed for camera %s", det.camera)
            track = None
        if track is not None:
            accepted += 1
    return {"ok": True, "received": len(items), "accepted": accepted}


@app.get("/replay/sessions")
async def replay_sessions():
    return {"sessions": ctx.recorder.sessions()}


@app.websocket("/ws/tracks")
async def ws_tracks(ws: WebSocket, replay: Optional[str] = Query(default=None)):
    await ws.accept()
    if replay:
        path = ctx.recorder.path_for(replay)
        if path is None:
            await ws.send_text(json.dumps(
                {"type": "error",
                 "error": f"no recording for '{replay}' (expect YYYYMMDD)"}))
            await ws.close(code=4404)
            return
        try:
            await replay_mod.stream_replay(path, ws.send_text)
        except (WebSocketDisconnect, RuntimeError):
            pass
        return
    ctx.hub.clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # keepalive / ignore client chatter
    except WebSocketDisconnect:
        pass
    finally:
        ctx.hub.clients.discard(ws)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=cfg.port)

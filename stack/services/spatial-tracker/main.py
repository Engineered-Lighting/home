"""spatial-tracker FastAPI service.

Wires together (and nothing more — the logic lives in the modules):
  * ModelStore poller     (model_store.py, HA apartment_model every 30 s)
  * Frigate MQTT consumer (aiomqtt, frigate/events, reconnect backoff)
  * Tracker predict loop  (tracker.py, 10 Hz KF predict + broadcast)
  * ActivityEngine + HA websocket listener (rules.py)
  * Frame recorder / replay (replay.py)
  * Calibration endpoints (calib.py)

Endpoints:
  GET /healthz   {ok, mqtt_connected, ha_model_revision, tracks_active, uptime_s}
  GET /model     current apartment model (read-through cache)
  GET /tracks    latest broadcast frame (REST snapshot)
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
from typing import Optional

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

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
        "uptime_s": round(time.time() - ctx.started, 1),
    }


@app.get("/model")
async def model():
    return ctx.model_store.get_model().raw


@app.get("/tracks")
async def tracks():
    return ctx.latest_frame


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

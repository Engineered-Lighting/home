"""Manual smoke test (not collected by pytest): boots the app in-process via
uvicorn and exercises HTTP + WS endpoints. Run with:
    REPLAY_MODE=1 python tests/smoke_live.py
"""
import asyncio
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

os.environ.setdefault("REPLAY_MODE", "1")
os.environ.setdefault("DATA_DIR", os.path.join(tempfile.gettempdir(),
                                               "spatial-smoke"))
PORT = 8223

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fetch(url, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, method=method, data=data,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


async def main() -> int:
    import uvicorn
    import websockets

    import main as appmod

    config = uvicorn.Config(appmod.app, host="127.0.0.1", port=PORT,
                            log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.ensure_future(server.serve())
    for _ in range(50):
        await asyncio.sleep(0.1)
        if server.started:
            break
    assert server.started, "server did not start"

    base = f"http://127.0.0.1:{PORT}"

    async def get(path):
        return await asyncio.to_thread(_fetch, base + path)

    async def post(path, payload):
        return await asyncio.to_thread(
            _fetch, base + path, "POST", json.dumps(payload).encode(),
            {"Content-Type": "application/json"})

    code, body = await get("/healthz")
    assert code == 200 and json.loads(body)["ok"] is True, (code, body)
    print("healthz ok:", body.decode())

    code, body = await get("/model")
    assert code == 200, (code, body)
    print("model ok")

    code, body = await post("/calib/living_room/intrinsics/solve", {})
    assert code == 501 and b"ChArUco" in body, (code, body)
    print("calib solve 501 ok:", body.decode())

    code, body = await get("/calib/living_room/overlay.jpg")
    assert code == 404, (code, body)
    print("overlay 404 ok:", body.decode())

    code, body = await post(
        "/calib/living_room/extrinsics",
        {"pairs": [{"px": [0, 0], "xyz": [0, 0, 0]}] * 4,
         "image_size": [1280, 720]})
    assert code == 400, (code, body)
    print("extrinsics-without-intrinsics 400 ok")

    # live websocket: should push ~10 Hz frames
    async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/tracks") as ws:
        frames = [json.loads(await asyncio.wait_for(ws.recv(), 3))
                  for _ in range(5)]
    assert all(f["type"] == "tracks" for f in frames)
    dts = [b["ts"] - a["ts"] for a, b in zip(frames, frames[1:])]
    print(f"live ws ok: 5 frames, mean dt {sum(dts)/len(dts)*1000:.0f} ms")

    # replay websocket: write a fake recording and stream it back
    rec_dir = os.path.join(os.environ["DATA_DIR"], "recordings")
    os.makedirs(rec_dir, exist_ok=True)
    with open(os.path.join(rec_dir, "20990101.ndjson"), "w") as fh:
        for i in range(3):
            fh.write(json.dumps({"type": "tracks", "ts": 1000.0 + i * 0.05,
                                 "tracks": [{"id": f"r{i}"}]}) + "\n")
    async with websockets.connect(
            f"ws://127.0.0.1:{PORT}/ws/tracks?replay=20990101") as ws:
        got = [json.loads(await asyncio.wait_for(ws.recv(), 3))
               for _ in range(4)]  # 4th proves looping
    assert got[0]["tracks"][0]["id"] == "r0"
    assert got[3]["tracks"][0]["id"] == "r0"  # looped back to the start
    assert all(f.get("replay") for f in got)
    print("replay ws ok (looped)")

    # bad replay date -> error frame + close
    async with websockets.connect(
            f"ws://127.0.0.1:{PORT}/ws/tracks?replay=19990101") as ws:
        err = json.loads(await asyncio.wait_for(ws.recv(), 3))
    assert err["type"] == "error"
    print("replay missing-date error ok")

    code, body = await get("/replay/sessions")
    assert code == 200 and "20990101" in body.decode()
    print("replay sessions ok:", body.decode())

    server.should_exit = True
    await task
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

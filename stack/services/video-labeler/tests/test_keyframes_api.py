"""Keyframe evidence-strip endpoints (sqlite-only; JPEGs are fake bytes):
404 shapes (unknown video/window, not extracted, bad/missing file names),
the list contract, and file serving. Needs httpx (starlette TestClient)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from videolabeler import db  # noqa: E402
from videolabeler.api import media as media_api  # noqa: E402

VID = "vid_kf"
WID = f"aw_{VID}_0"
BASE = f"/api/video-labeler/videos/{VID}/windows/{WID}/keyframes"


@pytest.fixture
def client(conn, cfg):
    now = db.iso_now()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO videos (id, filename, checksum, duration_s,"
            " import_status, created_at, updated_at)"
            " VALUES (?, 'kf.mp4', 'sum_kf', 16.0, 'ready', ?, ?)",
            (VID, now, now))
        conn.execute(
            "INSERT INTO analysis_windows (id, video_id, start_s, end_s,"
            " prelabel_status, created_at) VALUES (?, ?, 0.0, 8.0,"
            " 'pending', ?)", (WID, VID, now))
    app = FastAPI()
    app.include_router(media_api.build_router(cfg))
    return TestClient(app)


def _write_keyframes(cfg, with_crop=True):
    kdir = Path(cfg.keyframes_dir) / VID / WID
    kdir.mkdir(parents=True, exist_ok=True)
    frames = []
    for k in range(2):
        (kdir / f"frame_{k}.jpg").write_bytes(b"\xff\xd8frame")
        crop = None
        if with_crop and k == 0:
            (kdir / f"crop_{k}.jpg").write_bytes(b"\xff\xd8crop")
            crop = f"crop_{k}.jpg"
        frames.append({"t": 1.0 + 2.0 * k, "frame": f"frame_{k}.jpg",
                       "crop": crop})
    (kdir / "meta.json").write_text(
        json.dumps({"video_id": VID, "window_id": WID, "frames": frames}),
        encoding="utf-8")


def test_list_404s(client):
    assert client.get("/api/video-labeler/videos/vid_nope/windows/"
                      f"{WID}/keyframes").status_code == 404
    assert client.get(f"/api/video-labeler/videos/{VID}/windows/"
                      "aw_nope/keyframes").status_code == 404
    r = client.get(BASE)  # window exists but nothing extracted yet
    assert r.status_code == 404
    assert "not extracted" in r.json()["detail"]


def test_list_shape_and_urls(client, cfg):
    _write_keyframes(cfg)
    r = client.get(BASE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["video_id"] == VID and body["window_id"] == WID
    kfs = body["keyframes"]
    assert len(kfs) == 2
    assert kfs[0]["t"] == 1.0
    assert kfs[0]["frame"] == f"{BASE}/frame_0.jpg"
    assert kfs[0]["crop"] == f"{BASE}/crop_0.jpg"
    assert kfs[1]["crop"] is None


def test_file_serving_and_guards(client, cfg):
    _write_keyframes(cfg)
    r = client.get(f"{BASE}/frame_0.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == b"\xff\xd8frame"
    assert client.get(f"{BASE}/crop_0.jpg").status_code == 200
    # name pattern is allowlisted: no traversal, no stray files
    assert client.get(f"{BASE}/frame_9.jpg").status_code == 404  # missing
    assert client.get(f"{BASE}/meta.json").status_code == 404
    assert client.get(f"{BASE}/frame_0.png").status_code == 404
    assert client.get(f"{BASE}/..%2Fmeta.json").status_code == 404

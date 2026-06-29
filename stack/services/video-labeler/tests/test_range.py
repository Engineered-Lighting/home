"""Hand-rolled single-range 206 handler: full 200, bounded/open/suffix 206
with correct Content-Range, 416 past EOF. Needs httpx (starlette TestClient);
skips if absent."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from videolabeler import db  # noqa: E402
from videolabeler.api import media  # noqa: E402
from videolabeler.api.media import RangeNotSatisfiable, parse_range  # noqa: E402

DATA = bytes(range(256)) * 4  # 1024 bytes, deterministic
SIZE = len(DATA)


@pytest.fixture
def client(conn, cfg):
    now = db.iso_now()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO videos (id, filename, checksum, local_path, import_status,"
            " created_at, updated_at) VALUES ('vid_r', 'a.mp4', 'c1',"
            " 'videos/originals/vid_r/a.mp4', 'ready', ?, ?)", (now, now))
        conn.execute(
            "INSERT INTO video_artifacts (video_id, kind, path, status, created_at,"
            " updated_at) VALUES ('vid_r', 'proxy480', 'proxies/vid_r.mp4',"
            " 'ready', ?, ?)", (now, now))
    proxy = Path(cfg.resolve("proxies/vid_r.mp4"))
    proxy.parent.mkdir(parents=True, exist_ok=True)
    proxy.write_bytes(DATA)
    app = FastAPI()
    app.include_router(media.build_router(cfg))
    return TestClient(app)


URL = "/api/video-labeler/videos/vid_r/stream"


def test_full_200_when_no_range(client):
    r = client.get(URL)
    assert r.status_code == 200
    assert r.content == DATA
    assert r.headers["accept-ranges"] == "bytes"


def test_bounded_range_206(client):
    r = client.get(URL, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 0-99/{SIZE}"
    assert r.headers["content-length"] == "100"
    assert r.content == DATA[:100]


def test_open_ended_range_206(client):
    r = client.get(URL, headers={"Range": "bytes=100-"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 100-{SIZE - 1}/{SIZE}"
    assert r.content == DATA[100:]


def test_suffix_range_206(client):
    r = client.get(URL, headers={"Range": "bytes=-100"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes {SIZE - 100}-{SIZE - 1}/{SIZE}"
    assert r.content == DATA[-100:]


def test_past_eof_416(client):
    r = client.get(URL, headers={"Range": f"bytes={SIZE + 10}-"})
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{SIZE}"


def test_end_clamped_to_eof(client):
    r = client.get(URL, headers={"Range": f"bytes=1000-{SIZE + 500}"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 1000-{SIZE - 1}/{SIZE}"


def test_unknown_video_404(client):
    r = client.get("/api/video-labeler/videos/vid_nope/stream")
    assert r.status_code == 404


def test_parse_range_malformed_serves_full():
    assert parse_range(None, 100) is None
    assert parse_range("bytes=abc", 100) is None
    assert parse_range("bytes=", 100) is None
    assert parse_range("items=0-5", 100) is None
    assert parse_range("bytes=50-10", 100) is None  # inverted -> full


def test_parse_range_forms():
    assert parse_range("bytes=0-9", 100) == (0, 9)
    assert parse_range("bytes=10-", 100) == (10, 99)
    assert parse_range("bytes=-10", 100) == (90, 99)
    assert parse_range("bytes=0-9, 20-29", 100) == (0, 9)  # first range only
    with pytest.raises(RangeNotSatisfiable):
        parse_range("bytes=100-", 100)

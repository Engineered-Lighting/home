import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

FFMPEG_AVAILABLE = (shutil.which("ffmpeg") is not None
                    and shutil.which("ffprobe") is not None)
requires_ffmpeg = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    (d / "inbox").mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(d))
    monkeypatch.setenv("INBOX_DIR", str(d / "inbox"))
    return d


@pytest.fixture
def cfg(data_dir):
    from videolabeler.config import Config
    c = Config()
    c.ensure_dirs()
    return c


@pytest.fixture
def conn(cfg):
    from videolabeler import db
    c = db.connect(cfg.db_path)
    db.apply_migrations(c)
    yield c
    c.close()


def make_test_video(path, duration: int = 4) -> Path:
    """Tiny synthetic clip: 320x240 @ 10fps testsrc."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=10",
         "-pix_fmt", "yuv420p", str(path)],
        check=True)
    return path

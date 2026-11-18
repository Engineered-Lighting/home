"""The stills den-live sends Qwen, made in the detector's process from frames it already holds (inference plan, step 2).

The loop's own process is standard library only, so the JPEGs are made here, beside the detector and the live body:
a frame crosses the pipe once, and numpy and Pillow load once.
- Ring keeps the last RING frames' bundle bytes by exact frame key: well over the ask's few seconds at the camera's
  rate, about 50 MB in this process.
- handle() answers a stills request {kind: stills, id, frames: [frame keys, oldest first]} with
  {stills_id, frames, jpegs: [base64, ...]} or {stills_id, error}. The JPEG settings are the den simulation's (chain.py's
  JPEG), and the picture is the detector's own decode of the frame's NV12 plane, so the experiments' replay can make
  the very same bytes from a recorded frame with jpeg().
"""
import base64
import io
from collections import OrderedDict

JPEG = dict(format="JPEG", quality=85, subsampling=2, optimize=False, progressive=False)
RING = 60   # frames: about 12 s at the camera's 4-5 a second, and still over ask.POOL_S at up to 12 a second
WIDTH, HEIGHT = 640, 360
MAX_IMAGE_BYTES = 256 * 1024


class Ring:
    def __init__(self, size=RING):
        self.size, self.frames = size, OrderedDict()

    def keep(self, key, data):
        self.frames[tuple(key)] = data
        while len(self.frames) > self.size:
            self.frames.popitem(last=False)

    def get(self, key):
        return self.frames.get(tuple(key))


def jpeg(np, Image, decode, nv12, width=WIDTH, height=HEIGHT):
    """One frame's NV12 bytes as the JPEG Qwen is sent."""
    plane = np.frombuffer(nv12, np.uint8).reshape(height * 3 // 2, width)
    out = io.BytesIO()
    Image.fromarray(decode(plane, width, height)).save(out, **JPEG)
    return out.getvalue()


def handle(header, ring, np, Image, decode, read_bundle):
    answer = {"stills_id": header.get("id")}
    try:
        frames = header["frames"]
        if (not isinstance(frames, list) or not 2 <= len(frames) <= 3
                or any(not isinstance(key, list) or len(key) != 3 for key in frames)):
            return dict(answer, error="invalid_frame_keys")
        images = []
        for key in frames:
            data = ring.get(key)
            if data is None:
                return dict(answer, error="frame_gone")
            _meta, nv12 = read_bundle(data, with_rgb=True)
            image = jpeg(np, Image, decode, nv12)
            if len(image) > MAX_IMAGE_BYTES:
                return dict(answer, error="still_too_big")
            images.append(image)
        if len(set(images)) != len(images):
            return dict(answer, error="same_still_twice")
        return dict(answer, frames=frames, jpegs=[base64.b64encode(image).decode("ascii") for image in images])
    except Exception as exc:  # a type name only
        return dict(answer, error=type(exc).__name__)

"""Demand-driven H264 copy-remux for display; bounded RAM, no recording/inference."""
import base64
from collections import deque
import os
import re
import select
import subprocess
import threading
import time
import uuid
from store import LabError

MAX_INIT = 256 * 1024
MAX_MEDIA = 2 * 1024 * 1024


class FragmentParser:
    """Incremental bounded ISO BMFF top-level parser for ffmpeg empty_moov output."""
    def __init__(self):
        self.buffer = bytearray()
        self.init = bytearray()
        self.ready = False
        self.moof = None

    def feed(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > MAX_MEDIA + MAX_INIT:
            raise ValueError('Oversized MP4 buffer')
        output = []
        while len(self.buffer) >= 8:
            size = int.from_bytes(self.buffer[:4], 'big')
            kind = bytes(self.buffer[4:8])
            header = 8
            if size == 1:
                if len(self.buffer) < 16:
                    break
                size = int.from_bytes(self.buffer[8:16], 'big')
                header = 16
            if size < header or size > MAX_MEDIA:
                raise ValueError('Invalid MP4 box size')
            if len(self.buffer) < size:
                break
            box = bytes(self.buffer[:size])
            del self.buffer[:size]
            if kind in (b'ftyp', b'moov') and not self.ready:
                self.init.extend(box)
                if len(self.init) > MAX_INIT:
                    raise ValueError('Oversized MP4 init')
                if kind == b'moov':
                    if self.init[4:8] != b'ftyp':
                        raise ValueError('Missing MP4 type')
                    self.ready = True
            elif kind == b'moof' and self.ready and self.moof is None:
                self.moof = box
            elif kind == b'mdat' and self.moof is not None:
                fragment = self.moof + box
                self.moof = None
                if len(fragment) > MAX_MEDIA:
                    raise ValueError('Oversized MP4 fragment')
                output.append(fragment)
            elif kind in (b'free', b'mfra') and self.moof is None:
                continue
            else:
                raise ValueError('Unexpected MP4 box')
        return output


class LiveVideo:
    def __init__(self, source, site_id, *, popen=subprocess.Popen):
        if not re.fullmatch(r'avc1\.[0-9a-fA-F]{6}', source.codec):
            raise ValueError('Provisioned H264 codec required')
        self.source, self.site_id, self.popen = source, site_id, popen
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = self.process = None
        self.closed = False
        self.last_request = 0
        self.generation = None
        self.init = b''
        self.fragments = deque()
        self.sequence = 0

    def command(self):
        return [self.source.ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-rtsp_transport', 'tcp', '-fflags', 'nobuffer', '-analyzeduration', '1000000',
                '-probesize', '262144', '-i', self.source.url, '-map', '0:v:0', '-an',
                '-c:v', 'copy', '-movflags', '+frag_keyframe+empty_moov+default_base_moof',
                '-flush_packets', '1', '-f', 'mp4', 'pipe:1']

    def read(self, after_sequence=None, generation=None):
        if after_sequence is not None and (type(after_sequence) is not int or after_sequence < 0):
            raise LabError('Invalid live cursor', 422, 'invalid_cursor')
        if generation is not None and (not isinstance(generation, str) or not re.fullmatch(r'[a-f0-9]{32}', generation)):
            raise LabError('Invalid live generation', 422, 'invalid_cursor')
        with self.lock:
            if self.closed:
                raise LabError('Live video closed', 503, 'preview_unavailable')
            self.last_request = time.monotonic()
            if self.thread is None or not self.thread.is_alive():
                if self.process is not None and self.process.poll() is None:
                    raise LabError('Live video shutdown pending', 503, 'preview_unavailable')
                self.fragments.clear()
                self.init = b''
                self.sequence = 0
                self.generation = uuid.uuid4().hex
                self.stop.clear()
                self.thread = threading.Thread(target=self._run, name='lab-live-video', daemon=True)
                self.thread.start()
            if not self.fragments:
                raise LabError('Live video connecting', 503, 'preview_connecting')
            age = time.monotonic() - self.fragments[-1][2]
            if age > 4:
                raise LabError('Live video stale', 503, 'preview_stale')
            reset = (generation != self.generation or after_sequence is None or
                     after_sequence < self.fragments[0][0] - 1 or after_sequence > self.sequence)
            selected = [self.fragments[-1]] if reset else [f for f in self.fragments if f[0] > after_sequence]
            return {'version': 1, 'site_id': self.site_id, 'camera_id': self.source.camera_id,
                    'generation': self.generation, 'codec_mime': 'video/mp4; codecs="' + self.source.codec + '"',
                    'sample_interval_ms': 250, 'init_base64': base64.b64encode(self.init).decode() if reset else None,
                    'fragments': [{'sequence': n, 'data_base64': base64.b64encode(data).decode()} for n, data, _ in selected],
                    'reset': reset, 'receipt_age_seconds': age, 'timestamp_semantics': 'remux_receipt_only',
                    'exposure_timing': 'unverified', 'recorded': False}

    def _run(self):
        process = None
        try:
            parser = FragmentParser()
            process = self.popen(self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, bufsize=0)
            with self.lock:
                self.process = process
            while not self.stop.is_set():
                with self.lock:
                    if time.monotonic() - self.last_request > 10:
                        break
                if not select.select([process.stdout], [], [], .5)[0]:
                    if process.poll() is not None:
                        break
                    continue
                data = os.read(process.stdout.fileno(), 65536)
                if not data:
                    break
                for fragment in parser.feed(data):
                    with self.lock:
                        self.init = bytes(parser.init)
                        self.sequence += 1
                        self.fragments.append((self.sequence, fragment, time.monotonic()))
                        while len(self.fragments) > 3 or sum(len(f[1]) for f in self.fragments) > MAX_MEDIA:
                            self.fragments.popleft()
        except Exception:
            pass  # Never print stream credentials or private media.
        finally:
            if process is not None:
                try:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                if process.stdout is not None:
                    process.stdout.close()
            with self.lock:
                self.fragments.clear()
                self.init = b''

    def close(self):
        with self.lock:
            self.closed = True
            self.stop.set()
            thread = self.thread
        if thread is not None:
            thread.join(timeout=5)
        return {'drained': thread is None or not thread.is_alive()}

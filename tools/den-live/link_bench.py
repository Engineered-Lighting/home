#!/usr/bin/env python3
"""Offline benchmark of the camera link: source.py's own pull, today's and the kept slim link, through a modelled link.

    python -B -X utf8 tools\\den-live\\link_bench.py --bundle <a recorded /frame.npz> [--seconds 30]

A fake camera on 127.0.0.1 publishes the recorded bundle's depth and RGB as the capture loop does (groups at 15 a
second, a bundle once 0.2 s have passed; the slim one about 20 ms after its group, the full one about 130 ms after),
and it answers both paths as the slim camera release does. Before each answer it waits as long as the link would take,
from a TCP model whose numbers were measured on the den link on 2026-11-14 (--rtt-ms, --mbps, --exit-cwnd): a new
connection pays its handshake and starts at 10 segments, slow start ends near 70 segments (HyStart, as each pull
measured), and congestion avoidance adds one a round. A kept connection keeps its window (the camera host has
tcp_slow_start_after_idle 0). The recorded bundle is padded with one stored member that no reader opens, up to today's
live sizes (--full-kb, --slim-kb), so the bytes are today's. It runs CameraSource with den-take's pinned client in
four modes, --seconds each:

  today       /frame.npz, the full bundle, a new connection for each pull (den-live 5253a409 as deployed)
  slim-only   the slim bundle, still a new connection for each pull
  kept-only   the full bundle on one kept connection
  kept-slim   the slim bundle on one kept connection, with If-None-Match and 304 (the fix)

For each it prints fetch p50 and p95 (request to arrival of each new frame), fps in, transit (the group's time to
arrival) p50 and p95, the longest wait between new frames, and how often the pose would expire: the next frame,
plus --processing-s of detection, lift and tracking, landing past PERCEPTION_S (1.5 s) after the previous frame's
capture. Numbers only, never a frame; it saves nothing. The model has no cross traffic, so its tails are a floor.
"""
import argparse
import io
import json
import random
import statistics
import struct
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_local as rl  # noqa: E402
import sofa_check as sc  # noqa: E402
import source  # noqa: E402

PERCEPTION_S = 1.5
CLOCK = time.perf_counter   # den-take's clock, which CameraSource stamps arrivals with
TOKEN = 'link-bench-' + 'x' * 24
MODES = {'today': ('full', False), 'slim-only': ('slim', False), 'kept-only': ('full', True),
         'kept-slim': ('slim', True)}


class Link:
    """A TCP sender's view of the link, per connection: round trips, its window and the line rate."""

    def __init__(self, rtt_ms, rtt_jitter_ms, mbps, mss, init_cwnd, exit_cwnd, seed):
        self.rtt_s, self.jitter_s, self.bps, self.mss = rtt_ms / 1000, rtt_jitter_ms / 1000, mbps * 1e6, mss
        self.init_cwnd, self.exit_cwnd = init_cwnd, exit_cwnd
        self.random = random.Random(seed)
        self.lock = threading.Lock()

    def rtt(self):
        with self.lock:
            return self.rtt_s + self.random.expovariate(1 / self.jitter_s) if self.jitter_s > 0 else self.rtt_s

    def answer_s(self, size, connection, first):
        """How long from the request leaving the client to the answer's last byte arriving, and the window after."""
        rtt = self.rtt()
        # A new connection's handshake; then the request's way there and the last byte's way back.
        took = (rtt if first else 0.0) + rtt
        remaining, cwnd = max(size, 1), connection.setdefault('cwnd', self.init_cwnd)
        while remaining > 0:
            window = min(cwnd * self.mss, remaining)
            serialise = window * 8 / self.bps
            remaining -= window
            took += serialise if remaining <= 0 else max(rtt, serialise)   # the next window waits for acks
            if window >= cwnd * self.mss:   # cwnd-limited: it grows
                cwnd = cwnd * 2 if cwnd < self.exit_cwnd else cwnd + 1
        connection['cwnd'] = min(cwnd, 4 * int(self.bps * rtt / 8 / self.mss) + 1)
        return took


def npy(descr, shape, body):
    header = "{'descr': %r, 'fortran_order': False, 'shape': %s, }" % (descr, tuple(shape))
    header += ' ' * ((64 - (10 + len(header) + 1) % 64) % 64) + '\n'
    return b'\x93NUMPY\x01\x00' + struct.pack('<H', len(header)) + header.encode('latin-1') + body


class Frames:
    """The recorded bundle's members, rebuilt per frame with its own sequence and time, at today's sizes."""

    def __init__(self, recorded, full_kb, slim_kb):
        with zipfile.ZipFile(io.BytesIO(recorded)) as archive:
            self.members = {name[:-4]: archive.read(name) for name in archive.namelist()}
        self.meta = json.loads(self._payload(self.members['metadata_json']).decode('utf-8'))
        self.full_kb, self.slim_kb = full_kb, slim_kb

    @staticmethod
    def _payload(raw):
        length = struct.unpack('<H', raw[8:10])[0]
        return raw[10 + length:]

    def build(self, session, seq, t_host, *, slim):
        meta = json.loads(json.dumps(self.meta))
        meta['session'] = session
        for plane in meta['frames'].values():
            plane['sequence'], plane['host_monotonic_timestamp_seconds'] = seq, t_host
        names = ('depth_mm', 'rgb_nv12') if slim else ('depth_mm', 'left', 'right', 'rgb_nv12')
        if slim:
            meta.update(bundle_schema=source.SLIM_SCHEMA, bundle_omits=['left', 'right'])
        raw = json.dumps(meta, separators=(',', ':')).encode()
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for name in names:
                archive.writestr(name + '.npy', self.members[name])
            archive.writestr('metadata_json.npy', npy('|u1', (len(raw),), raw))
            short = int((self.slim_kb if slim else self.full_kb) * 1024) - out.tell() - 200
            if short > 0:   # one stored member no reader opens: today's bytes on the link
                archive.writestr(zipfile.ZipInfo('link-bench-padding.bin'), bytes(short), zipfile.ZIP_STORED)
        return out.getvalue()


class Camera:
    """The capture loop's cadence and the slim release's answers, each delayed by the link model."""

    def __init__(self, frames, link, *, body, kept, slim_encode_s=0.02, full_encode_s=0.11, seed=7):
        self.frames, self.link, self.body, self.kept = frames, link, body, kept
        self.slim_encode_s, self.full_encode_s = slim_encode_s, full_encode_s
        self.random = random.Random(seed)
        self.lock = threading.Lock()
        self.session, self.seq = 'link-bench-session', 0
        self.latest = {'slim': None, 'full': None}
        self.captured = {}
        self.connections = 0
        self.stop = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def setup(self):
                super().setup()
                self.link_state, self.first = {}, True

            def log_message(self, *_):
                pass

            def do_GET(self):
                kept = owner.kept and self.path == source.SLIM_PATH
                if not kept:
                    self.protocol_version, self.close_connection = 'HTTP/1.0', True
                with owner.lock:
                    latest = owner.latest[owner.body]
                if latest is None or self.path not in ('/frame.npz', source.SLIM_PATH):
                    status, data, headers = 503, b'', {}
                else:
                    data, etag = latest
                    headers = {'ETag': etag, 'X-Den-Bundle-Schema': source.SLIM_SCHEMA} if kept else {}
                    status = 304 if kept and self.headers.get('If-None-Match') == etag else 200
                    data = b'' if status == 304 else data
                time.sleep(owner.link.answer_s(len(data) + 250, self.link_state, self.first))
                self.first = False
                self.send_response(status)
                self.send_header('Content-Length', str(len(data)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

        class Server(ThreadingHTTPServer):
            daemon_threads = True

            def process_request(self, request, client_address):
                with owner.lock:
                    owner.connections += 1
                super().process_request(request, client_address)

            def handle_error(self, request, client_address):
                pass

        self.server = Server(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        threading.Thread(target=self._capture, daemon=True).start()

    def _capture(self):
        """Groups at 15 a second; a bundle once 0.2 s have passed (the loop wakes every 5 ms or so)."""
        start, group, last_bundle = CLOCK(), 0, -1.0
        while not self.stop.is_set():
            group += 1
            at = start + group / 15
            while CLOCK() < at and not self.stop.is_set():
                time.sleep(0.002)
            now = at + self.random.uniform(0, 0.005)
            if now - last_bundle < 0.2:
                continue
            last_bundle, self.seq = now, self.seq + 1
            seq, t_host = self.seq, 1000.0 + now
            slim = self.frames.build(self.session, seq, t_host, slim=True)
            full = self.frames.build(self.session, seq, t_host, slim=False)
            etag = '"%s-%d"' % (self.session, seq)
            with self.lock:
                self.captured[seq] = at
            self._at(now + self.slim_encode_s, 'slim', slim, etag)
            self._at(now + self.slim_encode_s + self.full_encode_s, 'full', full, etag)

    def _at(self, when, kind, data, etag):
        def publish():
            delay = when - CLOCK()
            if delay > 0:
                time.sleep(delay)
            with self.lock:
                self.latest[kind] = (data, etag)
        threading.Thread(target=publish, daemon=True).start()

    def close(self):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()


def quantile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))] if values else None


def run(mode, frames, link_args, seconds, processing_s):
    body, kept = MODES[mode]
    camera = Camera(frames, Link(**link_args), body=body, kept=kept)
    take = rl.den_take(sc.archive(Path(tempfile.gettempdir()) / 'den-live-test-tools', 'observe'))
    client = source.camera_client(take, camera.url, TOKEN)
    if not kept:   # today's pull: den-take's client, a new connection each time
        client.link = None
        if body == 'slim':
            client._get_full = lambda timeout: client.get(source.SLIM_PATH, timeout) + (None,)
    box = source.Mailbox()
    src = source.CameraSource(take, client, box)
    got, halt = [], threading.Event()

    def drain():
        while not halt.is_set():
            item = box.take(0.05)
            if item is not None:
                got.append(item)
    drainer = threading.Thread(target=drain, daemon=True)
    drainer.start()
    src.start()
    time.sleep(seconds)
    src.halt.set()
    src.join(10)
    halt.set()
    drainer.join(5)
    camera.close()
    with camera.lock:
        captured = dict(camera.captured)
    arrivals = [(b.arrival_mono, captured[b.rgb_seq], b.fetch_s) for b in got if b.rgb_seq in captured][3:]
    fetch = [f for _, _, f in arrivals]
    transit = [a - c for a, c, _ in arrivals]
    waits = [b[0] - a[0] for a, b in zip(arrivals, arrivals[1:])]
    expiry = [b[0] + processing_s - a[1] for a, b in zip(arrivals, arrivals[1:])]
    span = arrivals[-1][0] - arrivals[0][0] if len(arrivals) > 1 else 0
    ms = lambda v: None if v is None else round(v * 1000)
    return {'mode': mode, 'frames': len(arrivals), 'camera_frames': len(captured),
            'connections': camera.connections, 'fetch_ms_p50': ms(quantile(fetch, .5)),
            'fetch_ms_p95': ms(quantile(fetch, .95)), 'fps_in': round((len(arrivals) - 1) / span, 2) if span else None,
            'transit_ms_p50': ms(quantile(transit, .5)), 'transit_ms_p95': ms(quantile(transit, .95)),
            'longest_wait_ms': ms(max(waits) if waits else None),
            'pose_age_ms_max': ms(max(expiry) if expiry else None),
            'expiries': sum(1 for e in expiry if e > PERCEPTION_S),
            'errors': src.errors, 'not_modified': client.link.counts['not_modified'] if client.link else 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--bundle', required=True, help='one recorded /frame.npz')
    parser.add_argument('--seconds', type=float, default=30.0)
    parser.add_argument('--modes', default=','.join(MODES))
    parser.add_argument('--full-kb', type=float, default=772.0, help="today's live /frame.npz (2026-11-14: 652-775 kB)")
    parser.add_argument('--slim-kb', type=float, default=400.0, help='its slim bundle (measured 342-407 kB)')
    parser.add_argument('--rtt-ms', type=float, default=43.0, help='the link round trip without queueing (min srtt)')
    parser.add_argument('--rtt-jitter-ms', type=float, default=5.0, help='mean queueing on top (srtt median 48 ms)')
    parser.add_argument('--mbps', type=float, default=84.0, help='the uplink on one warm connection (measured)')
    parser.add_argument('--mss', type=int, default=1368)
    parser.add_argument('--init-cwnd', type=int, default=10)
    parser.add_argument('--exit-cwnd', type=int, default=64, help="where each pull's slow start ended (61-74)")
    parser.add_argument('--processing-s', type=float, default=0.25, help='detection, lift and tracking (< 0.25 s)')
    args = parser.parse_args(argv)
    frames = Frames(Path(args.bundle).read_bytes(), args.full_kb, args.slim_kb)
    link_args = dict(rtt_ms=args.rtt_ms, rtt_jitter_ms=args.rtt_jitter_ms, mbps=args.mbps, mss=args.mss,
                     init_cwnd=args.init_cwnd, exit_cwnd=args.exit_cwnd, seed=11)
    print(json.dumps({'schema': 'den-live-link-bench-1', 'model': dict(link_args, full_kb=args.full_kb,
                      slim_kb=args.slim_kb, seconds=args.seconds, processing_s=args.processing_s)}))
    for mode in args.modes.split(','):
        print(json.dumps(run(mode, frames, link_args, args.seconds, args.processing_s)), flush=True)


if __name__ == '__main__':
    main()

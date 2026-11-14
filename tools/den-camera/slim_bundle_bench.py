#!/usr/bin/env python3
"""Offline benchmark of the slim bundle on one recorded /frame.npz: bytes per member, and the camera's encode time.

    python -I -B tools/den-camera/slim_bundle_bench.py <recorded bundle .npz>     # or - to read it from stdin

It decodes the recorded bundle, then encodes it again with both releases' own encoders (encode_bundle, as
pilot-20261111-depth builds /frame.npz, and the slim release's encode_slim_bundle for /frame-slim.npz), each run
--repeat times, and times den-live's read of each (the three members it opens). It prints one JSON object: numbers
only, never a frame. It saves nothing. On the camera host it gives the Jetson's own timings, with the bundle piped in.
"""
import argparse
import importlib.util
import io
import json
import statistics
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELEASE = HERE.parent / 'fixtures' / 'installed-oak-capture-20261114.py'
EARLIER = HERE.parent / 'fixtures' / 'installed-oak-capture-20261111.py'
DEN_LIVE_READS = ('depth_mm', 'rgb_nv12', 'metadata_json')


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def timed(fn, repeat):
    times, out = [], None
    for _ in range(repeat):
        started = time.perf_counter()
        out = fn()
        times.append(time.perf_counter() - started)
    return out, round(statistics.median(times) * 1000, 1)


def kib(n):
    return round(n / 1024, 1)


def bench(blob, repeat, release=RELEASE, earlier=EARLIER):
    import numpy as np
    new, old = load(release, '_bench_slim_release'), load(earlier, '_bench_earlier_release')
    loaded = np.load(io.BytesIO(blob), allow_pickle=False)
    arrays = {name: loaded[name] for name in loaded.files if name != 'metadata_json'}
    metadata = json.loads(bytes(loaded['metadata_json']).decode('utf-8'))
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        recorded = {info.filename[:-4]: kib(info.compress_size) for info in archive.infolist()}
    full, full_ms = timed(lambda: old.encode_bundle(arrays, metadata), repeat)
    slim, slim_ms = timed(lambda: new.encode_slim_bundle(arrays, metadata), repeat)

    def read(data):
        got = np.load(io.BytesIO(data), allow_pickle=False)
        return [got[name] for name in DEN_LIVE_READS]
    _, read_full_ms = timed(lambda: read(full), repeat)
    _, read_slim_ms = timed(lambda: read(slim), repeat)
    same = all(np.array_equal(a, b) for a, b in zip(read(full)[:2], read(slim)[:2]))   # depth_mm and rgb_nv12
    return {'schema': 'den-camera-slim-bench-1', 'recorded_kB': kib(len(blob)), 'recorded_members_kB': recorded,
            'full_kB': kib(len(full)), 'slim_kB': kib(len(slim)), 'slim_fraction': round(len(slim) / len(full), 3),
            'encode_full_ms': full_ms, 'encode_slim_ms': slim_ms,
            'den_live_read_full_ms': read_full_ms, 'den_live_read_slim_ms': read_slim_ms,
            'depth_and_rgb_identical': bool(same), 'repeat': repeat}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('bundle', help='a recorded /frame.npz, or - for stdin')
    parser.add_argument('--repeat', type=int, default=8)
    args = parser.parse_args(argv)
    blob = sys.stdin.buffer.read() if args.bundle == '-' else Path(args.bundle).read_bytes()
    print(json.dumps(bench(blob, max(1, args.repeat))))


if __name__ == '__main__':
    main()

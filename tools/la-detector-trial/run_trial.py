"""Prepared LA-only three-frame experiment. Requires explicit owner admission.

This is not a production scheduler. No restarts, retries, downloads or camera
access. Preserve the attempt marker even on failure; do not auto-rerun.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

INPUT_HASHES = (
    'd0121d7895d5410851d47831678ce1be742dc95173beea4e818dffd99ffa36cb',
    'e5cad304c09ae772694fbea886fcb827f854b8d935f1619fdf78d9045f1fc009',
    'b6823eeb82f4c94f6b5ddfdc9c79318bc68086db476286f3a031de2013f6950d',
)
SESSION = '91003eca11e74f47b269ac1d6681c605'
CHECKPOINT = '646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b'
ALLOCATOR_BYTES = 512 * 1024**2


def check_limits(values):
    if (values['memory.max'].strip() != str(4 * 1024**3)
            or values['memory.swap.max'].strip() != '0'
            or values['pids.max'].strip() != '64'):
        raise ValueError('required RAM/swap/PID limits absent')
    quota, period = values['cpu.max'].split()
    if not quota.isdecimal() or not period.isdecimal() or not 0 < int(quota) <= int(period):
        raise ValueError('required one-CPU limit absent')


def check_host():
    if sys.platform != 'linux' or socket.gethostname() != 'EngineeredLightingServer1':
        raise ValueError('explicit LA host required')
    if os.environ.get('LAB_COMMISSIONING_SCOPE') != 'owner-approved-la-three-saved-frames-v1':
        raise ValueError('explicit one-shot scope required; this is not authorization by itself')
    lines = Path('/proc/self/cgroup').read_text().splitlines()
    paths = [line[3:] for line in lines if line.startswith('0::/')]
    if len(paths) != 1 or not paths[0].endswith('/lab-la-yolo26-saved-trial.service'):
        raise ValueError('dedicated bounded systemd unit required')
    root = Path('/sys/fs/cgroup') / paths[0].lstrip('/')
    check_limits({name: (root/name).read_text() for name in ('memory.max', 'memory.swap.max', 'pids.max', 'cpu.max')})


def check_manifest(manifest):
    frames = manifest.get('frames', [])
    if (manifest.get('site_id') != 'victoria' or manifest.get('camera_id') != 'den'
            or manifest.get('session_id') != SESSION or manifest.get('time_basis') != 'encoded_video'
            or len(frames) != 3 or tuple(f.get('sha256') for f in frames) != INPUT_HASHES
            or len({f.get('frame_id') for f in frames}) != 3
            or any((f.get('width'), f.get('height')) != (1920, 1080) for f in frames)):
        raise ValueError('exact three registered saved frames required')
    return frames


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--checkpoint', required=True, type=Path)
    parser.add_argument('--adapter-directory', required=True, type=Path)
    args = parser.parse_args()
    check_host()  # All guards before importing CUDA/model packages.
    root = args.inputs.resolve(strict=True)
    raw = (root/'manifest.json').read_bytes()
    if len(raw) > 16384:
        raise ValueError('manifest exceeds bound')
    manifest = json.loads(raw)
    frames = check_manifest(manifest)
    output = root/'la-yolo26s-fp32-v1.json'
    if output.exists():
        raise ValueError('existing receipt must not be overwritten')
    # Permanent failed-attempt marker prevents an accidental second load/run.
    fd = os.open(root/'la-yolo26s-fp32-v1.attempt', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump({'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'started_at': time.time()}, stream)
        stream.flush(); os.fsync(stream.fileno())
    gpu = subprocess.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
                         capture_output=True, text=True, check=True, timeout=5).stdout.strip().splitlines()
    if len(gpu) != 1 or not gpu[0].isdigit() or int(gpu[0]) < 32768:
        raise ValueError('required 32GiB pre-load GPU headroom absent')
    sys.path.insert(0, str(args.adapter_directory.resolve(strict=True)))
    import importlib.metadata as metadata
    for name, expected in [('torch', '2.10.0+cu128'), ('torchvision', '0.25.0+cu128'), ('ultralytics', '8.4.0')]:
        if metadata.version(name) != expected:
            raise ValueError('candidate runtime differs')
    import numpy as np
    from PIL import Image
    import torch
    from ultralytics_worker import UltralyticsWorker
    torch.set_num_threads(1)
    total = torch.cuda.get_device_properties(0).total_memory
    torch.cuda.set_per_process_memory_fraction(ALLOCATOR_BYTES / total, 0)
    torch.cuda.reset_peak_memory_stats()
    images = []
    for frame in frames:
        path = root/frame['filename']
        if path.is_symlink() or path.resolve().parent != root or path.stat().st_size > 4*1024**2:
            raise ValueError('unsafe input')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != frame['sha256']:
            raise ValueError('input hash differs')
        with Image.open(path) as image:
            if image.format != 'PNG' or image.size != (1920,1080):
                raise ValueError('input format differs')
            rgb = np.asarray(image.convert('RGB'))
        if hashlib.sha256(rgb.tobytes()).hexdigest() != frame['rgb_sha256']:
            raise ValueError('decoded pixels differ')
        images.append(np.ascontiguousarray(rgb[:,:,::-1]))
    started = time.monotonic()
    profile = dict(runtime_version='8.4.0', task='detect', checkpoint='yolo26s', precision='fp32',
        sha256=CHECKPOINT, license='AGPL-3.0', device_id='echo-la-rtx-pro-6000',
        engine_epoch='la-saved-trial-'+str(time.time_ns()), profile_id='yolo26s-fp32-paired-v1',
        model_version='ultralytics-assets-v8.4.0', device='cuda:0', format='pt',
        preprocessing=dict(imgsz=[640,640], confidence=.25, iou=.7, max_det=128, retina_masks=False))
    pending = {f['frame_id'] for f in frames}
    def admit(phase, proposed, request_id):
        if proposed != profile or time.monotonic()-started > 90:
            return False
        if phase == 'load':
            return request_id is None
        if phase in {'infer','infer_with_warmup'} and request_id in pending:
            pending.remove(request_id)
            return True
        return False
    worker = UltralyticsWorker(profile, admit)
    worker.load(args.checkpoint.resolve(strict=True))
    load_seconds = time.monotonic()-started
    results=[]
    for frame, image in zip(frames,images):
        begin=time.monotonic()
        result=worker.infer(image,frame_id=frame['frame_id'],request_id=frame['frame_id'])
        torch.cuda.synchronize()
        results.append(dict(source=frame,result=result,worker_wall_ms=(time.monotonic()-begin)*1000))
    receipt=dict(schema_version=1,site_id='victoria',camera_id='den',session_id=SESSION,
        mode='saved_session',time_basis='encoded_video',exposure_mapping=manifest['exposure_mapping'],
        processing_site_id='echo',profile=profile,live_latency_ms=None,load_seconds=load_seconds,
        peak_torch_allocated_bytes=torch.cuda.max_memory_allocated(),
        peak_torch_reserved_bytes=torch.cuda.max_memory_reserved(),results=results,
        runtime=dict(python=sys.version.split()[0],torch=torch.__version__,cuda=torch.version.cuda),
        resource_limit_note='512MiB is a Torch allocator limit, not whole-process VRAM isolation')
    fd=os.open(output.with_suffix('.partial'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(receipt,stream);stream.flush();os.fsync(stream.fileno())
    os.replace(output.with_suffix('.partial'),output)
    print(json.dumps({'completed':len(results),'load_seconds':load_seconds,
                      'worker_wall_ms':[r['worker_wall_ms'] for r in results]}))


if __name__ == '__main__':
    main()

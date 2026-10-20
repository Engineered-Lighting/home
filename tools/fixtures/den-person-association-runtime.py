"""Authored detector I/O through the actual Lab ledger, runner and read model.

Offline test fixture only. No camera, model, network, service or physical I/O.
Temporary SQLite/media and both bounded runner threads are always cleaned up.
"""
from pathlib import Path
import base64
import json
import struct
import sys
import tempfile
import time
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'stack/services/perception-lab'))
from config import Settings
from controller import Controller
from runtime import PairedRunner
from service_runtime import ServiceRuntime
from store import Store


def png(sequence):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    pixels = (b'\0' + bytes([sequence, 40, 80]) * 640) * 360
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 640, 360, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


def result(request, people):
    boxes, poses, points, edges = [], [], [], []
    for identity, x, has_pose in people:
        box = dict(instance_id=identity, label='person', x=x, y=30, w=140, h=270, confidence=.9)
        boxes.append(box)
        if not has_pose:
            continue
        start = len(points)
        points.extend([dict(x=x+60, y=70, joint_id=0, instance_id=identity, score=.8),
                       dict(x=x+60, y=120, joint_id=1, instance_id=identity, score=.7)])
        edges.append([start, start+1])
        poses.append(dict(instance_id=identity, layout='coco17', crop_xyxy=[x, 30, x+140, 300],
            score_semantics='raw model response; not a calibrated probability',
            joints=[dict(joint_id=i, name='joint-'+str(i), pixel=[x+60, 70+i*50] if i<2 else None,
                         score=.8 if i<2 else -.1, status='supported' if i<2 else 'uncertain',
                         reason=None if i<2 else 'below_threshold') for i in range(17)]))
    return dict(schema_version=1, frame_id=request['frame']['frame_id'], coordinate_space='source_pixels',
                objects=[dict(label='person', instance_id=row[0]) for row in people], boxes=boxes,
                pose_instances=poses, keypoints=points, skeleton_edges=edges)


def main():
    now = [100.1]
    frames = {}
    media = {}
    calls = []
    with tempfile.TemporaryDirectory(prefix='den-person-association-') as directory:
        store = Store(Settings(storage_root=Path(directory), min_free_bytes=0), clock=lambda: now[0])
        profiles = {arm: dict(profile_id='authored-'+arm, artifact_sha256='a'*64,
                             model_version='authored-pose-1', engine_epoch='authored-engine-'+arm) for arm in ('a', 'b')}
        runner = service = None
        try:
            session, _ = store.create_session('den', 'detection', 'Offline person continuity', 'offline-person-continuity')
            store.transition(session['id'], 'starting')
            store.transition(session['id'], 'recording')
            def execute(request):
                calls.append(request['request_id'])
                return dict(request_id=request['request_id'], engine_epoch=request['profile']['engine_epoch'],
                            artifact_sha256=request['profile']['artifact_sha256'], terminal=True, state='completed',
                            result=result(request, frames[request['frame']['frame_id']]))
            runner = PairedRunner(store, session['id'], profiles, execute, clock=lambda: now[0], experiment_generation=1)
            service = ServiceRuntime(store, {}, lambda *_: profiles, execute)
            service.runners[session['id']] = runner
            controller = Controller(store, service)
            states = []
            specifications = [
                ('first', 100, 'capture-1', [('left-1', 20, True), ('right-1', 350, True)]),
                ('reordered', 101, 'capture-1', [('right-2', 342, False), ('left-2', 28, True)]),
                ('departure', 102, 'capture-1', [('left-3', 36, True)]),
                ('ambiguous', 103, 'capture-1', [('cross-1', 42, True), ('cross-2', 70, True)]),
                ('separated', 104, 'capture-1', [('left-5', 50, True), ('right-5', 350, True)]),
                ('gap', 108, 'capture-1', [('left-6', 58, True), ('right-6', 342, True)]),
                ('source-reset', 109, 'capture-2', [('right-7', 334, True), ('left-7', 66, True)]),
            ]
            for index, (label, observed, epoch, people) in enumerate(specifications, 1):
                now[0] = observed+.1
                frame_id = 'authored-frame-'+str(index)
                frames[frame_id] = people
                raw = png(index)
                item = store.add_media(session['id'], raw, 'image/png', dict(site_id='victoria', camera_id='den',
                    frame_id=frame_id, capture_epoch=epoch, width=640, height=360, observed_at=observed,
                    valid_until=observed+2, clock_mapping_status='verified', clock_mapping_id='authored-clock'))
                frame = dict(frame_id=frame_id, capture_epoch=epoch, site_id='victoria', camera_id='den',
                    session_id=session['id'], observed_at=observed, available_at=observed,
                    valid_until=observed+2, media_ids=[item['id']])
                runner.offer(frame)
                deadline = time.monotonic()+3
                while True:
                    with store.lock:
                        count = store.db.execute("SELECT COUNT(*) FROM events WHERE session_id=? AND kind='perception.output' AND json_extract(payload,'$.frame_id')=?",
                                                 (session['id'], frame_id)).fetchone()[0]
                    if count == 2:
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError('bounded_authored_workers_did_not_complete')
                    time.sleep(.001)
                descriptor = store.get_media(session['id'], item['id'], session['generation'])
                descriptor = {k: v for k, v in descriptor.items() if k not in {'path', 'storage_path'}}
                media[item['id']] = dict(descriptor=descriptor, base64=base64.b64encode(raw).decode())
                states.append(dict(label=label, now=now[0], live=controller.live(session['id']), graph=controller.graph(session['id'])))
            before = len(calls)
            runner.offer(frame)
            duplicate = controller.live(session['id'])
            if len(calls) != before:
                raise RuntimeError('duplicate_original_dispatched')
            runner.close()
            runner.pool.shutdown(wait=True)
            store.transition(session['id'], 'stopped')
            stopped = controller.live(session['id'])
            output = dict(schema='den-person-association-offline-fixture-1', session=store.get_session(session['id']),
                states=states, duplicate=duplicate, stopped=stopped, media=media,
                cleanup=dict(runner_closed=runner.snapshot()['closed'], active=runner.snapshot()['active'],
                             pending=runner.snapshot()['pending']),
                source_mode='authored_external_detector_io', physical_operations=False, host_operations=0,
                model_calls=0, camera_calls=0, network_calls=0, three_d_placement=False)
            print(json.dumps(output, separators=(',', ':'), allow_nan=False))
        finally:
            if service is not None:
                service.close(timeout=2)
            elif runner is not None:
                runner.close()
                runner.pool.shutdown(wait=True)
            store.close()


if __name__ == '__main__':
    main()

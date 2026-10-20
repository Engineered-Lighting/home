"""Actual optional-preview RAM/Store/PairedRunner/ASGI fixture export.

Only camera/model/alignment/equipment/candidate inputs are authored. No host,
household original, browser, native fixture, physical path or test suite runs.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for folder in ('stack/services/perception-lab', 'tools/den-live-runtime',
               'tools/shared-home', 'stack/services/home-context'):
    sys.path.append(str(ROOT / folder))
import test_apartment_desk_preview_source as source_fixture
import test_apartment_desk_preview as candidate_fixture


def generate(options):
    assert isinstance(options, dict) and set(options) == {'equipment_ref'}
    equipment = options['equipment_ref']
    assert set(equipment) == {'revision', 'digest'} and type(equipment['revision']) is int
    assert isinstance(equipment['digest'], str) and re.fullmatch('[a-f0-9]{64}', equipment['digest'])
    source_fixture.DeskPreviewSourceTests.setUpClass()
    case = source_fixture.DeskPreviewSourceTests('runTest')
    runtime = None
    try:
        case.setUp()
        original_candidate = candidate_fixture.configured_candidate
        def authored_candidate(pack):
            value = original_candidate(pack)
            value['equipment_ref'] = deepcopy(equipment)
            return value
        # The external candidate input is frozen BEFORE the real private
        # opt-in loader/binder and before any source/model request is admitted.
        with patch.object(candidate_fixture, 'configured_candidate', side_effect=authored_candidate):
            runtime, source, configured, token = case.build()
        from app import create_app, PREFIX
        from fastapi.testclient import TestClient
        headers = {'Authorization': 'Bearer ' + token, 'X-Home-Lab-Site': 'victoria'}
        f = case.f
        application = create_app(f.store.settings, runtime, f.store)
        with TestClient(application) as client:
            base = PREFIX + '/sessions/' + f.session['id']
            live_response = client.get(base + '/live', headers=headers)
            graph_response = client.get(base + '/graph', headers=headers)
            assert live_response.status_code == graph_response.status_code == 200
            live, graph = live_response.json(), graph_response.json()
            arm = live['arms']['B']; packet = arm['virtual_target_preview']
            assert packet['available'] and packet['observation'] == arm['observation']
            mid = arm['frame']['media_id']; params = {'generation': f.session['generation']}
            descriptor = client.get(base + '/media/' + mid + '/descriptor', params=params, headers=headers)
            pixels = client.get(base + '/media/' + mid, params=params, headers=headers)
            assert descriptor.status_code == pixels.status_code == 200
            assert hashlib.sha256(pixels.content).hexdigest() == descriptor.json()['sha256']
            out = dict(schema='home-authored-desk-preview-integration-1', version=1,
                external_inputs='authored_camera_model_alignment_equipment_and_candidate',
                host_operations=0, real_model_calls=0, camera_calls=0, native_launches=0,
                physical_permission=False, browser_execution=False, now=f.now,
                session=deepcopy(f.session), live=live, graph=graph, descriptor=descriptor.json(),
                png_hex=pixels.content.hex(), candidate=json.loads(case.candidate_bytes),
                candidate_sha256=hashlib.sha256(case.candidate_bytes).hexdigest(),
                npz_reads=f.reads.count('/frame.npz'), durable_media_rows=len(f.store.list_media(f.session['id'])['items']),
                resident_bytes_before_stop=source.status()['resident_bytes'])
            # Stop through the real command owner: the lower-level runtime
            # stop alone deliberately does not transition the Store session.
            out['stop_command'] = application.state.controller.stop(f.session['id'])
            assert out['stop_command']['state'] == 'stopped'
            stopped = client.get(base + '/live', headers=headers)
            assert stopped.status_code == 200
            out['stopped'] = stopped.json()
        out['cleanup'] = dict(resident_bytes=source.status()['resident_bytes'],
            resident_frames=source.status()['resident_frames'],
            durable_media_rows=len(f.store.list_media(f.session['id'])['items']),
            paired_active=deepcopy(runtime.runners[f.session['id']].snapshot()['active'])
                if f.session['id'] in runtime.runners else None)
        assert out['cleanup']['resident_bytes'] == out['cleanup']['resident_frames'] == 0
        return out
    finally:
        case.doCleanups()
        source_fixture.DeskPreviewSourceTests.doClassCleanups()


if __name__ == '__main__':
    raw = sys.stdin.buffer.read(16385)
    assert len(raw) <= 16384
    sys.stdout.write(json.dumps(generate(json.loads(raw)), sort_keys=True, separators=(',', ':'), allow_nan=False))

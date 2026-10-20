"""Authored external boundaries through the real Store/ASGI/verifier/projector.

The caller supplies its authored test pack's exact identity. This creates no
standalone room renderer, physical device, camera service or external model.
"""
from pathlib import Path
import base64
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT/'stack/services/perception-lab', ROOT/'tools/den-live-runtime',
        ROOT/'tools/shared-home', ROOT/'stack/services/home-context'):
    if str(folder) not in sys.path:
        sys.path.append(str(folder))
from test_bundle_rgb_collect import BundledRgbCollectionTests
from lab_assessment_collect import read_verified_attempts
from test_apartment_room_pose_diagnostic import inputs, digest
from apartment_room_pose_diagnostic import project_diagnostic_room_pose


def generate(scene):
    fixture = BundledRgbCollectionTests('runTest')
    try:
        fixture.make(pose=True, flat_pose=True)
        assert fixture.collect()['complete']
        collection = read_verified_attempts(fixture.output)
        candidate = collection['attempts'][0]
        binding = collection['binding']['binding']
        values = inputs()
        values.update(collection=collection, attempt_id=candidate['source_event_id'])
        values['scope'].update(contentRevision=scene['contentRevision'], frameDigest=scene['frameDigest'],
            registrationVersion=scene['registrationVersion'], sessionId=binding['session']['id'], generation=binding['media_generation'])
        values['active_collision_sha256'] = scene['collisionDigest']
        alignment = values['alignment']
        alignment['pack'].update(content_revision=scene['contentRevision'], frame_sha256=scene['frameDigest'],
            registration_version=scene['registrationVersion'], collision_sha256=scene['collisionDigest'])
        # Known authored quarter-turn in the actual test pack. These are test
        # transform inputs, never an inferred placement of a household body.
        alignment['matrix'][0][3] = 2.
        alignment['matrix'][1][3] = 2.
        alignment['matrix_sha256'] = digest(alignment['matrix'])
        alignment['camera'].update(capture_epoch=candidate['original_event']['payload']['capture_epoch'],
            calibration_content_id=candidate['media_records']['bundle']['metadata']['calibration_content_id'])
        values['alignment_original_bytes'] = (json.dumps(alignment, sort_keys=True, indent=2)+'\n').encode()
        values['review'].update(alignment_sha256=hashlib.sha256(values['alignment_original_bytes']).hexdigest(),
            matrix_sha256=alignment['matrix_sha256'])
        packet = project_diagnostic_room_pose(**values)
        assert packet['renderable'], packet['reason']
        recorded = project_diagnostic_room_pose(**dict(values, replay=True, session_state='stopped'))
        assert recorded['renderable'] and recorded['status'] == 'recorded'
        session_id = binding['session']['id']
        live = fixture.api('GET', '/sessions/' + session_id + '/live')
        graph = fixture.api('GET', '/sessions/' + session_id + '/graph')
        live['arms']['B']['diagnostic_room_pose'] = packet
        frame = candidate['original_frame_record']
        image = fixture.api.read_media(frame)
        assert hashlib.sha256(image).hexdigest() == frame['sha256']
        return dict(evidence='authored_external_boundaries_actual_Store_ASGI_original_byte_verifier_projector',
            scope=values['scope'], live=live, graph=graph, frame_descriptor=frame,
            image_base64=base64.b64encode(image).decode(), diagnostic_room_pose=packet, recorded_diagnostic_room_pose=recorded)
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    scene = json.loads(sys.stdin.buffer.read(8192))
    sys.stdout.write(json.dumps(generate(scene), sort_keys=True, separators=(',', ':'), allow_nan=False))

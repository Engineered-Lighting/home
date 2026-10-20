"""Actual single-owner RAM source, Store, PairedRunner and ASGI fixture.

Only the external camera, model and reviewed room transform are authored. The
caller supplies its authored pack identity; no household data or host is read.
"""
from copy import deepcopy
from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[2]
for name in ('stack/services/perception-lab','tools/den-live-runtime','tools/shared-home','stack/services/home-context'):
    sys.path.append(str(ROOT/name))
from test_apartment_viewing_metric_source import MetricSourceTests
from test_apartment_view_alignment import encoded,sha,digest

def generate(scene):
    class BrowserSource(MetricSourceTests):
        def approximate_configuration(self):
            fixture,bootstrap,originals,config=super().approximate_configuration()
            fixture.pack.update(content_revision=scene['contentRevision'],registration_version=scene['registrationVersion'],
                frame_sha256=scene['frameDigest'],collision_sha256=scene['collisionDigest'])
            # An explicitly authored location in the actual authored room used
            # by the browser test. This does not estimate a household transform.
            fixture.matrix[0][3]=2.;fixture.matrix[1][3]=2.
            matrix_digest=digest(fixture.matrix)
            fixture.candidate['matrix_sha256']=fixture.doc['matrix_sha256']=matrix_digest
            source_digest=sha(encoded(fixture.candidate))
            fixture.doc['source_candidate']['sha256']=source_digest
            fixture.review.update(matrix_sha256=matrix_digest,source_candidate_sha256=source_digest,
                alignment_sha256=sha(encoded(fixture.doc)))
            originals={'alignment':encoded(fixture.doc),'review':encoded(fixture.review),'source_candidate':encoded(fixture.candidate)}
            config['pack']=deepcopy(fixture.pack)
            for role,raw in originals.items():config['files'][role]['sha256']=sha(raw)
            return fixture,bootstrap,originals,config
    BrowserSource.setUpClass()
    fixture=BrowserSource('runTest')
    try:
        fixture.setUp()
        fixture.event=deepcopy(fixture.event)
        fixture.event['payload']['result'].update(keypoints=[{'x':320.,'y':150.,'score':.9,'joint_id':0,'instance_id':'authored-person'}],skeleton_edges=[])
        fixture.test_protected_bootstrap_projects_actual_supported_joint_and_stop_retires_every_owned_buffer()
        out=fixture.authored_source_fixture
        assert out['packet']['approximate_pose']['scope']['contentRevision']==scene['contentRevision']
        return out
    finally:
        fixture.doCleanups();BrowserSource.doClassCleanups()

if __name__=='__main__':
    scene=json.loads(sys.stdin.buffer.read(8192))
    sys.stdout.write(json.dumps(generate(scene),sort_keys=True,separators=(',',':'),allow_nan=False))

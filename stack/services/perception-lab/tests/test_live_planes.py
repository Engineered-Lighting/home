import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from live_planes import LivePlanes,plane_bundle
from store import LabError


class PlaneTests(unittest.TestCase):
    def bundle(self,**updates):
        meta=dict(session='boot1',depth_alignment='rectified_left',depth_units='millimetres',invalid_depth=0,
            frames={k:dict(sequence=1) for k in ['depth','left','right','rgb']})
        meta.update(updates)
        output=io.BytesIO()
        np.savez(output,depth_mm=np.full((400,640),1234,dtype='<u2'),left=np.zeros((400,640),dtype='u1'),
            right=np.ones((400,640),dtype='u1'),rgb_nv12=np.zeros((540,640),dtype='u1'),
            metadata_json=np.frombuffer(json.dumps(meta).encode(),dtype='u1'))
        return output.getvalue()

    def test_depth_bytes_and_shared_cache_without_recording(self):
        client=Mock();client._read.return_value=(self.bundle(),'')
        source=LivePlanes(client,'victoria','den')
        import base64
        with patch('live_planes.time.monotonic',return_value=100):
            depth=source.read('depth');left=source.read('left')
        self.assertEqual(client._read.call_count,1)
        self.assertEqual(int.from_bytes(base64.b64decode(depth['data_base64'])[:2],'little'),1234)
        self.assertEqual(depth['alignment_frame'],'rectified_left')
        self.assertFalse(depth['recorded']);self.assertEqual(left['format'],'gray_u8')
        with patch('live_planes.time.monotonic',return_value=104),self.assertRaises(LabError):source.read('depth')
        with self.assertRaises(LabError):source.read('confidence')

    def test_unknown_depth_semantics_rejected(self):
        with self.assertRaises(ValueError):plane_bundle(self.bundle(depth_alignment='rgb'))


if __name__=='__main__':unittest.main()

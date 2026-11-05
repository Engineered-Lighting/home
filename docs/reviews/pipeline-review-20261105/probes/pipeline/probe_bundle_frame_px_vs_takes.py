"""probe_bundle_frame_px_vs_takes.py: den-bundle crops in a 1920x1080 frame; every frame the pipeline has is 640x360.

What it checks
  den-bundle's METHOD-V1 closes frame_px.den-oak at [1920, 1080] (note R2's RTSP RGB)
  and clips each crop to it. The frames the pipeline actually holds are 640x360: R's
  takes ("Only 640x360 at about 4 frames per second", den-take README), den-observe's
  camera entry rgb_size (its synth RGB_SIZE, and observe.py refuses detector rows of
  any other size), the capture server's NV12 bundles ("RGB is NV12 640x360"), and
  both live Qwen consumers (IMAGE_SIZE (640, 360)). So image_box values that reach
  rung 5 are 640x360 pixels.
  The probe reads den-observe's RGB size from its own synth module, then assembles a
  den-bundle bundle for a person boxed near the right and bottom edges of a 640x360
  frame and checks whether crop_ref stays inside 640x360; it also runs den-bundle's
  gate `aligned` clause on that bundle. FAIL = the crop leaves the real frame and the
  gate passes it.

How to run
  python3 -I probe_bundle_frame_px_vs_takes.py <claude/rung-5-bundle root> <claude/rung-2-observe root>

Synthetic data only; nothing is written; no network.
"""
import os
import sys


def main():
    bundle_root, observe_root = map(os.path.abspath, sys.argv[1:3])
    sys.path.insert(0, os.path.join(observe_root, 'tools', 'den-observe'))
    import synth as observe_synth
    rgb = tuple(observe_synth.RGB_SIZE)
    sys.path.remove(os.path.join(observe_root, 'tools', 'den-observe'))
    del sys.modules['synth']
    sys.path.insert(0, os.path.join(bundle_root, 'tools', 'den-bundle'))
    import assemble as asm
    import gate
    import method
    import records
    import synth

    m = method.load_method()
    print('RESULT den-observe RGB size %s; den-bundle frame_px %s' % (rgb, m['frame_px']))
    obs, tracks = [], []
    for t in synth.steps(0.0, 6.0, 0.25):
        o = synth.observation(synth.oid('A', t), t, 1.95, 0.95)
        o['image_box'] = [520, 120, 632, 352]          # a seated person at the right edge, 640x360 pixels
        o['keypoints'] = [[560.0, 200.0, 0.9]] * 17
        obs.append(o)
        tracks.append(synth.track_line('A', 1.95, 0.95, [o['obs_id']]))
    store = records.Store(obs, tracks)
    b = asm.assemble(store, m, 'A', synth.T0 + 5.0)
    crop = b['frames'][-1]['crop_ref']['box']
    inside = crop[2] <= rgb[0] and crop[3] <= rgb[1]
    res = gate.check([b], store, m, [], None, scored=False, rule=method.load_router())
    print('RESULT box [520,120,632,352] -> crop_ref %s; inside the 640x360 frame: %s; gate aligned: %s'
          % (crop, inside, 'ok' if not res['clauses']['aligned']['failures'] else res['clauses']['aligned']['failures']))
    print('%s crop_ref extends %d px right and %d px below a 640x360 frame and the gate passes it'
          % ('FAIL' if not inside and not res['clauses']['aligned']['failures'] else 'PASS',
             max(0, crop[2] - rgb[0]), max(0, crop[3] - rgb[1])))


if __name__ == '__main__':
    main()

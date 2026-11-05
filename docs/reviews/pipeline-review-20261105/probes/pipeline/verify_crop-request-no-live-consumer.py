"""verify_crop-request-no-live-consumer.py: would a router v0 crop be accepted by either live GPU host consumer?

What it checks
  den-bundle (rung 5) makes each bundle's interpreter request a crop: frames[].crop_ref
  is a padded box from the latest boxed frame (README "frames[].crop_ref is the crop
  request"; METHOD-V1 "Crop request"). On origin/main, the two GPU host live consumers
  validate every image in the envelope: lab_live_person_scene._image (and its size
  check) and lab_live_scene._jpeg. This probe:
    1. assembles a synthetic den-bundle bundle with den-bundle's own synth/assemble and
       reads its crop_ref size;
    2. encodes an in-memory JPEG of that crop size, and a whole 640x360 JPEG as control;
    3. runs both consumers' own image validators on each (pure functions; no ledger,
       no network, no GPU host).
  PASS/FAIL per image; RESULT line summarises whether any live consumer accepts a crop.

How to run
  python3 -I verify_crop-request-no-live-consumer.py <claude/rung-5-bundle root> <origin/main root>

Synthetic data only; nothing is written; no network.
"""
import base64
import io
import os
import sys


def main():
    bundle_root, main_root = map(os.path.abspath, sys.argv[1:3])
    sys.path.insert(0, os.path.join(bundle_root, 'tools', 'den-bundle'))
    import assemble as asm
    import method
    import records
    import synth
    sys.path.insert(0, os.path.join(main_root, 'tools', 'shared-home'))
    import home_shared.lab_live_person_scene as person
    import home_shared.lab_live_scene as whole
    from PIL import Image

    m = method.load_method()
    obs, tracks = [], []
    for t in synth.steps(0.0, 6.0, 0.25):
        synth.seen(obs, tracks, 'A', t, 1.95, 0.95)
    b = asm.assemble(records.Store(obs, tracks), m, 'A', synth.T0 + 5.0)
    crop = [f['crop_ref'] for f in b['frames'] if f['crop_ref']][-1]['box']
    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    print('RESULT den-bundle crop_ref box %s -> crop image %dx%d px' % (crop, cw, ch))

    def jpeg_url(size):
        buf = io.BytesIO()
        Image.new('RGB', size, (90, 80, 70)).save(buf, format='JPEG')
        return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()

    accepted_crop = False
    for label, size in (('crop', (cw, ch)), ('whole frame', (640, 360))):
        url = jpeg_url(size)
        for name, check in (('lab_live_person_scene', lambda u: person._image(u, person.MAX_IMAGE_BYTES)),
                            ('lab_live_scene', lambda u: whole._jpeg(u, 256 * 1024))):
            try:
                got = check(url)
                ok = got == (640, 360)
                verdict = 'accepted' if ok else 'refused (size %s != IMAGE_SIZE, envelope check -> invalid_request)' % (got,)
            except Exception as e:  # LiveSceneRefusal / LabRequestError
                ok = False
                verdict = 'refused: %s %s' % (type(e).__name__, getattr(e, 'reason', None) or e)
            if label == 'crop' and ok:
                accepted_crop = True
            print('RESULT %-11s %dx%d -> %-22s %s' % (label, size[0], size[1], name, verdict))
    print('%s no live GPU host consumer accepts a den-bundle crop (only whole 640x360 frames)'
          % ('FAIL' if not accepted_crop else 'PASS'))


if __name__ == '__main__':
    main()

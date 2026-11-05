"""verify_SEAM-20: do light-model.json's extra top-level keys cause any consumer failure of their own?

Builds a light-model.json with rung 8 Stage 1's own code on its own synthetic den
(the same helpers its tests use), then:
  1. lists the top-level keys schemas.json's LightModel does not name;
  2. calls Stage 1's published predict() on the model as written and with every
     extra key stripped;
  3. calls rung 10's LightModel adapter (den-verify) on the model as written and
     with the extras stripped, with Stage 1's channel names and its basis keys.
If the extras were the cause of a consumer failure, stripping them would change
den-verify's result. It does not: the failure is the basis key form (SEAM-17/18).

Run:
  python3 -B verify_SEAM-20.py <rung-8-stage1 checkout> [<rung-10-verify checkout>]
The second root defaults to a sibling folder named rung-10-verify.
Synthetic data only, temp dir only, no network.
"""
import json
import os
import subprocess
import sys
import tempfile

VERIFY_SNIPPET = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import light_model
for label in ("as written", "extras stripped"):
    lm = json.load(open(sys.argv[2] if label == "as written" else sys.argv[3]))
    for ch in ("den_front_left.white", sorted(lm["basis"])[0]):
        try:
            out = light_model.LightModel(lm).predict({ch: {"level": 1.0, "cct_k": 2700}})
            print("RESULT den-verify %-15s channel %-28s -> ok (%d surfaces)" % (label, ch, len(out)))
        except Exception as e:
            print("RESULT den-verify %-15s channel %-28s -> %s: %s" % (label, ch, type(e).__name__, e))
'''


def main():
    r8 = os.path.abspath(sys.argv[1])
    r10 = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(os.path.dirname(r8), 'rung-10-verify')
    light_dir = os.path.join(r8, 'tools', 'den-light')
    sys.path.insert(0, light_dir)
    import test_stage1 as T   # noqa: E402  Stage 1's own synthetic helpers
    import model, predict, synthetic  # noqa: E402

    m = T.method1()
    den = synthetic.SyntheticDen(block_px=40, noise=1e-4)
    r = T.readings_for(den, m)
    fitted = model.fit(r, r['temperatures'], m)
    scored = model.score(r, fitted, m)
    lm = json.loads(json.dumps(model.light_model(fitted, T.FIXTURES, m, session_id='s1', readings_sha256='0' * 64,
                                                 meter_norm_sha256='1' * 64, score_error=scored['error'])))
    schemas = json.load(open(os.path.join(r8, 'docs', 'architecture', 'live-v1', 'schemas.json')))
    rec = next(x for x in schemas['records'] if x['id'] == 'light_model')
    canon = set()
    for f in rec['fields']:
        canon.update(p.strip() for p in f['name'].split('/'))
    extras = sorted(k for k in lm if k not in canon and not k.endswith('_note'))
    print('RESULT canon fields present: %s' % (canon <= set(lm)))
    print('RESULT extra top-level keys: %s' % ', '.join(extras))
    stripped = {k: v for k, v in lm.items() if k not in extras}

    tmp = tempfile.mkdtemp(prefix='verify_seam20_')
    a, b = os.path.join(tmp, 'light-model.json'), os.path.join(tmp, 'light-model-stripped.json')
    json.dump(lm, open(a, 'w'))
    json.dump(stripped, open(b, 'w'))

    scene = {lm['channels'][0]['channel_key']: {'level': 1.0, 'cct_k': 2700}}
    for label, model_json in (('as written', lm), ('extras stripped', stripped)):
        try:
            out = predict.predict(scene, model_json)
            print('RESULT Stage 1 predict() %-15s -> ok, %d points' % (label, len(out['surfaces']) - 1))
        except Exception as e:
            print('RESULT Stage 1 predict() %-15s -> %s: %s' % (label, type(e).__name__, e))

    res = subprocess.run([sys.executable, '-B', '-c', VERIFY_SNIPPET, os.path.join(r10, 'tools', 'den-verify'), a, b],
                         capture_output=True, text=True)
    print(res.stdout.rstrip())
    if res.stderr.strip():
        print(res.stderr.strip()[-800:])
    lines = [l for l in res.stdout.splitlines() if l.startswith('RESULT den-verify')]
    same = len(lines) == 4 and [l.split('->')[1] for l in lines[:2]] == [l.split('->')[1] for l in lines[2:]]
    print('%s: stripping the extra keys %s den-verify\'s outcome (extras are %s the cause of the consumer failure)'
          % ('PASS' if same else 'FAIL', 'does not change' if same else 'changes', 'not' if same else ''))
    print('temp dir: %s' % tmp)


if __name__ == '__main__':
    main()

"""verify_tracks-file-unverified: independent re-check of finding
tracks-file-unverified with a REAL Ctrl+C (SIGINT sent to the `track`
process) instead of an injected exception.

Steps (synthetic data, a temp dir, no network):
  1. Writes 8 synthetic takes, a CLOSED copy of METHOD.md (open slots set to
     their proposed values, room = the synthetic den), runs `track` once.
  2. Up to 40 times (a take whose tracks.run.json was itself cut is noted
     and the session re-tracked): starts `track --session` again (same method), sends
     SIGINT after a random delay, and checks every take's tracks.jsonl line
     count against its tracks.run.json `track_records`.
  3. When a take is left short, runs a SCORED `score` and reports whether it
     was refused (exit 2) or accepted.

Run:  python3 verify_tracks-file-unverified.py <root of the rung-3-track checkout>
"""
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-track')
CLI = os.path.join(TOOL, 'den_track.py')
sys.path.insert(0, TOOL)
import method as method_mod  # noqa: E402
import synth  # noqa: E402

ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
BROKEN = set()


def closed_method(dst):
    path = os.path.join(TOOL, 'METHOD.md')
    with open(path, encoding='utf-8') as f:
        text = f.read()
    resolved = method_mod.resolve(method_mod.read_block(path))
    resolved['room'] = synth.ROOM_MODEL
    start = text.index('```json den-track-method')
    end = text.index('```', start + 10)
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text[:start] + '```json den-track-method\n' + json.dumps(resolved, indent=2) + '\n' + text[end:])


def run(*args):
    p = subprocess.run([sys.executable, CLI] + list(args), capture_output=True, text=True, env=ENV)
    return p.returncode, p.stdout + p.stderr


def short_takes(sess):
    out = []
    for t in sorted(os.listdir(sess)):
        d = os.path.join(sess, t)
        if not os.path.isdir(d):
            continue
        with open(os.path.join(d, 'tracks.jsonl'), encoding='utf-8') as f:
            n = sum(1 for l in f if l.strip())
        try:
            want = json.load(open(os.path.join(d, 'tracks.run.json')))['track_records']
        except ValueError:
            BROKEN.add(t)          # Ctrl+C landed while run.json was written: not this finding
            continue
        if n != want:
            out.append((t, n, want))
    return out


def main():
    rng = random.Random(7)
    tmp = tempfile.mkdtemp(prefix='v_trk_')
    try:
        m = os.path.join(tmp, 'METHOD.md')
        closed_method(m)
        sess = os.path.join(tmp, 's')
        assert run('--method', m, 'synth', '--out', sess, '--seeds', '1',
                   '--scenes', 'touching_crossing,near_pass,close_crossing,crossing_behind_armchair,'
                               'clean_crossing,entry_exit,swap_at_door,armchair_occlusion')[0] == 0
        assert run('--method', m, 'track', '--session', sess)[0] == 0
        t0 = time.time()
        assert run('--method', m, 'track', '--session', sess)[0] == 0
        full = time.time() - t0
        print('one full track run: %.2f s' % full)
        hit = None
        for attempt in range(1, 41):
            p = subprocess.Popen([sys.executable, CLI, '--method', m, 'track', '--session', sess],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=ENV)
            time.sleep(rng.uniform(0.25, max(0.3, full)))
            p.send_signal(signal.SIGINT)
            err = p.communicate()[1].decode('utf-8', 'replace')
            short = short_takes(sess)
            if BROKEN:
                print('attempt %d: Ctrl+C left an empty/partial tracks.run.json in %s; re-tracking to reset' % (attempt, sorted(BROKEN)))
                BROKEN.clear()
                assert run('--method', m, 'track', '--session', sess)[0] == 0
                continue
            if short:
                hit = (attempt, short, 'KeyboardInterrupt' in err)
                break
            if p.returncode == 0:
                continue
        if not hit:
            print('no take left short in 40 real Ctrl+C attempts')
            print('RESULT: PASS/not reproduced')
            return
        attempt, short, kb = hit
        print('attempt %d: Ctrl+C left %s (lines, run.json track_records); traceback shows KeyboardInterrupt: %s'
              % (attempt, short, kb))
        rc, out = run('--method', m, 'score', '--session', sess)
        print('scored run after the interrupted re-run: exit %d (%s)' % (rc, 'REFUSED' if rc == 2 else 'ACCEPTED'))
        print([l for l in out.splitlines() if l.startswith(('gate', 'pooled', 'silent', 'takes', 'den-track'))])
        print('RESULT: %s' % ('FAIL (defect reproduced): truncated tracks.jsonl accepted by a scored run'
                              if rc != 2 else 'PASS'))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()

"""probe_r3a_tracks_unverified: a SCORED run reads tracks.jsonl without
checking it against tracks.run.json (no hash of the tracks file; the
`track_records` count it writes is never compared), so a tracks file
truncated by an interrupted re-run of `track`, or edited by hand, is scored
as if it were the method's output.

Steps (synthetic data, temp dirs only, no network):
  1. Two synthetic touching_crossing takes, a CLOSED copy of METHOD.md,
     `track`, then a scored `score` (reference numbers).
  2. Re-runs `track` in-process with Ctrl+C (KeyboardInterrupt) injected
     while take 2's tracks.jsonl is being written, through the real
     records.write_jsonl.  take 2 keeps its tracks.run.json from step 1.
  3. Scored `score` again: expect a refusal (truncated tracks).  Then sets
     every track record's association to `ambiguous` (a hand edit) and
     scores again: expect a refusal.

Run:  python3 probe_r3a_tracks_unverified.py <root of the rung-3-track checkout>
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(sys.argv[1])
TOOL = os.path.join(ROOT, 'tools', 'den-track')
CLI = os.path.join(TOOL, 'den_track.py')
sys.path.insert(0, TOOL)
import method as method_mod  # noqa: E402
import synth  # noqa: E402


def closed_method(dst):
    with open(method_mod.METHOD_PATH, encoding='utf-8') as f:
        text = f.read()
    resolved = method_mod.resolve(method_mod.read_block(method_mod.METHOD_PATH))
    resolved['room'] = synth.ROOM_MODEL
    start = text.index('```json den-track-method')
    end = text.index('```', start + 10)
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text[:start] + '```json den-track-method\n' + json.dumps(resolved, indent=2) + '\n' + text[end:])


def run(*args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    p = subprocess.run([sys.executable, CLI] + list(args), capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def summary(sess):
    r = json.load(open(os.path.join(sess, 'e2-report.json')))
    return 'silent %d, reported %d, IDF1 %.3f, gate %s' % (r['silent_swaps'], r['reported_switches'],
                                                          r['pooled']['idf1'], r['gate']['passed'])


def nlines(p):
    with open(p, encoding='utf-8') as f:
        return sum(1 for l in f if l.strip())


def main():
    tmp = tempfile.mkdtemp(prefix='r3a_trk_')
    try:
        m = os.path.join(tmp, 'METHOD.md')
        closed_method(m)
        sess = os.path.join(tmp, 's')
        assert run('--method', m, 'synth', '--out', sess, '--seeds', '2', '--scenes', 'touching_crossing')[0] == 0
        assert run('--method', m, 'track', '--session', sess)[0] == 0
        rc, out = run('--method', m, 'score', '--session', sess)
        print('step 1 scored: exit %d; %s' % (rc, summary(sess)))
        takes = sorted(os.listdir(sess))
        takes = [t for t in takes if os.path.isdir(os.path.join(sess, t))]
        t2 = os.path.join(sess, takes[1])
        n1 = nlines(os.path.join(sess, takes[0], 'tracks.jsonl'))
        n2 = nlines(os.path.join(t2, 'tracks.jsonl'))
        run2 = json.load(open(os.path.join(t2, 'tracks.run.json')))

        # step 2: Ctrl+C half way through writing take 2's tracks.jsonl
        import den_track
        import records
        real_json = records.json

        class Shim:
            calls = 0

            def __getattr__(self, k):
                return getattr(real_json, k)

            def dumps(self, *a, **kw):
                Shim.calls += 1
                if Shim.calls == n1 + n2 // 2:
                    raise KeyboardInterrupt
                return real_json.dumps(*a, **kw)

        records.json = Shim()
        try:
            den_track.main(['--method', m, 'track', '--session', sess])
            print('step 2: no interrupt happened?')
        except KeyboardInterrupt:
            print('step 2: Ctrl+C during the re-run of track, in %s' % takes[1])
        finally:
            records.json = real_json
        left = nlines(os.path.join(t2, 'tracks.jsonl'))
        run2b = json.load(open(os.path.join(t2, 'tracks.run.json')))
        print('  take 2 tracks.jsonl now %d of %d records; tracks.run.json unchanged: %s (track_records %d)'
              % (left, n2, run2b == run2, run2b['track_records']))
        rc, out = run('--method', m, 'score', '--session', sess)
        refused = rc == 2
        print('step 3a scored after the interrupted re-run: exit %d; %s' % (rc, 'REFUSED' if refused else summary(sess)))
        a_bad = not refused and left < n2

        # step 3b: hand edit: every record ambiguous
        assert run('--method', m, 'track', '--session', sess)[0] == 0
        for t in takes:
            p = os.path.join(sess, t, 'tracks.jsonl')
            recs = [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]
            for r in recs:
                r['association']['status'] = 'ambiguous'
            with open(p, 'w', encoding='utf-8', newline='\n') as f:
                for r in recs:
                    f.write(json.dumps(r) + '\n')
        rc, out = run('--method', m, 'score', '--session', sess)
        refused_b = rc == 2
        print('step 3b scored after editing tracks.jsonl: exit %d; %s' % (rc, 'REFUSED' if refused_b else summary(sess)))
        print('RESULT truncated tracks: %s' % ('FAIL: a scored run accepted a tracks.jsonl cut to %d of %d records' % (left, n2) if a_bad else 'PASS'))
        print('RESULT edited tracks: %s' % ('FAIL: a scored run accepted hand-edited track records' if not refused_b else 'PASS'))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()

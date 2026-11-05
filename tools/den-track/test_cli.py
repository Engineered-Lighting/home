"""The command line, end to end on synthetic takes in a temporary folder.

- METHOD.md reads the same with CRLF and LF;
- a scored run refuses while a slot is open, practice runs do not;
- once the slots are closed a scored run goes through, and it refuses
  tracks made under another method or from changed observations;
- nothing opens a connection to anything but 127.0.0.1.
"""
import contextlib
import io
import json
import os
import shutil
import socket
import tempfile
import unittest

import den_track
import method

HERE = os.path.dirname(os.path.abspath(__file__))


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = den_track.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def closed_method(src, dst, room=None):
    """A copy of METHOD.md with every open slot closed at its proposed value."""
    with open(src, encoding='utf-8') as f:
        text = f.read()
    m = method.Method(src)
    block = json.loads(json.dumps(m.block))

    def close(node):
        if method.is_open(node):
            return node['proposed']
        if isinstance(node, dict):
            return {k: close(v) for k, v in node.items()}
        return node

    block = close(block)
    if room is not None:
        block['room'] = room
    start = text.index(method.FENCE)
    end = text.index('\n```', start + len(method.FENCE))
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text[:start] + method.FENCE + '\n' + json.dumps(block, indent=2) + text[end:])
    return dst


class NoNetwork:
    """Refuse every connection that is not to 127.0.0.1."""

    def __enter__(self):
        self.saved = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
        self.attempts = []

        def guard(addr):
            host = addr[0] if isinstance(addr, tuple) else addr
            self.attempts.append(host)
            if host != '127.0.0.1':
                raise AssertionError('den-track tried to reach %r' % (host,))

        def connect(sock, addr):
            guard(addr)
            return self.saved[0](sock, addr)

        def connect_ex(sock, addr):
            guard(addr)
            return self.saved[1](sock, addr)

        def create_connection(addr, *a, **k):
            guard(addr)
            return self.saved[2](addr, *a, **k)

        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = connect, connect_ex, create_connection
        return self

    def __exit__(self, *exc):
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = self.saved
        return False


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='den-track-cli-')
        self.session = os.path.join(self.dir, 'session')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def synth(self, *extra):
        code, out, err = run('synth', '--out', self.session, '--seeds', '1',
                             '--scenes', 'clean_crossing,touching_crossing,armchair_occlusion', *extra)
        self.assertEqual(code, 0, err)

    def test_method_crlf_reads_the_same(self):
        with open(method.METHOD_PATH, encoding='utf-8') as f:
            text = f.read()
        crlf = os.path.join(self.dir, 'METHOD.md')
        with open(crlf, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write(text)
        with open(crlf, 'rb') as f:
            self.assertIn(b'\r\n', f.read())
        a, b = method.Method(), method.Method(crlf)
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertEqual(a.values, b.values)
        self.assertEqual([n for n, _ in a.open], [n for n, _ in b.open])

    def test_method_lists_the_open_slots(self):
        code, out, _ = run('method')
        self.assertEqual(code, 0)
        for name, _ in method.Method().open:
            self.assertIn(name, out)
        self.assertTrue(all(ord(c) < 128 for c in out), 'PowerShell 5.1 consoles want ASCII')

    def test_scored_run_refuses_while_slots_are_open(self):
        self.assertTrue(method.Method().open, 'this test expects open slots in METHOD.md')
        self.synth()
        self.assertEqual(run('track', '--session', self.session)[0], 0)
        code, out, err = run('score', '--session', self.session)
        self.assertEqual(code, 2)
        self.assertIn('refusing a scored run', err)
        self.assertFalse(os.path.exists(os.path.join(self.session, 'e2-report.json')))
        code, out, err = run('score', '--session', self.session, '--practice')
        self.assertIn(code, (0, 1), err)
        self.assertIn('PRACTICE: not a score', out)
        with open(os.path.join(self.session, 'e2-report.json'), encoding='utf-8') as f:
            rep = json.load(f)
        self.assertTrue(rep['practice'])
        self.assertEqual(rep['crossings'], 3)

    def test_scored_run_with_the_slots_closed(self):
        m = closed_method(method.METHOD_PATH, os.path.join(self.dir, 'METHOD.md'))
        self.synth()
        self.assertEqual(run('--method', m, 'track', '--session', self.session)[0], 0)
        code, out, err = run('--method', m, 'score', '--session', self.session)
        self.assertEqual(code, 1, err)              # computed; 3 windows are fewer than the gate's 20
        self.assertIn('E2 geometry-only arm: SCORED', out)
        self.assertIn('fewer than 20 windows', out)
        self.assertIn('upper bound on the silent-swap rate', out)

        # Tracks made under another method are refused.
        other = closed_method(method.METHOD_PATH, os.path.join(self.dir, 'OTHER.md'),
                              room={'exits': [[-2.4, 4.4]], 'floor': [-2.2, 0.8, 2.2, 5.2]})
        code, _, err = run('--method', other, 'score', '--session', self.session)
        self.assertEqual(code, 2)
        self.assertIn('was tracked under method', err)

        # So are observations changed after tracking, and truth without its hash.
        take = sorted(os.listdir(self.session))[0]
        obs = os.path.join(self.session, take, 'observations.jsonl')
        with open(obs, encoding='utf-8') as f:
            lines = f.readlines()
        with open(obs, 'w', encoding='utf-8', newline='\n') as f:
            f.writelines(lines[:-1])
        code, _, err = run('--method', m, 'score', '--session', self.session)
        self.assertEqual(code, 2)
        self.assertIn('changed after it was tracked', err)

    def test_truth_must_name_its_observations(self):
        m = closed_method(method.METHOD_PATH, os.path.join(self.dir, 'METHOD.md'))
        self.synth()
        take = os.path.join(self.session, sorted(os.listdir(self.session))[0])
        path = os.path.join(take, 'truth.jsonl')
        with open(path, encoding='utf-8') as f:
            lines = [json.loads(l) for l in f]
        del lines[0]['observations_sha256']
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.writelines(json.dumps(l) + '\n' for l in lines)
        run('--method', m, 'track', '--session', self.session)
        code, _, err = run('--method', m, 'score', '--session', self.session)
        self.assertEqual(code, 2)
        self.assertIn('does not name its observations_sha256', err)

    def test_practice_and_no_network(self):
        with NoNetwork() as guard:
            code, out, err = run('practice', '--seeds', '1')
            self.synth()
            run('track', '--session', self.session)
            run('score', '--session', self.session, '--practice')
        self.assertEqual(code, 0, err)
        self.assertEqual(guard.attempts, [])
        self.assertIn('PRACTICE', out)
        self.assertIn('crossing_behind_armchair', out)

    def test_hash_matches_what_synth_wrote(self):
        self.synth()
        take = os.path.join(self.session, sorted(os.listdir(self.session))[0])
        code, out, _ = run('hash', '--file', os.path.join(take, 'observations.jsonl'))
        self.assertEqual(code, 0)
        with open(os.path.join(take, 'truth.jsonl'), encoding='utf-8') as f:
            self.assertEqual(json.loads(f.readline())['observations_sha256'], out.strip())

    def test_errors_exit_2_with_a_message(self):
        code, _, err = run('track', '--session', os.path.join(self.dir, 'nothing-here'))
        self.assertEqual(code, 2)
        self.assertIn('is not a folder', err)
        code, _, err = run('synth', '--out', self.session, '--scenes', 'ballet')
        self.assertEqual(code, 2)
        self.assertIn('no synthetic scene', err)


if __name__ == '__main__':
    unittest.main()

"""The method files, JSON Lines with CRLF or LF, and the command line, in a temp folder."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import den_bundle  # noqa: E402
import method      # noqa: E402
import records     # noqa: E402


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = den_bundle.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class TempDir(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='den-bundle-test-')
        self.addCleanup(shutil.rmtree, self.dir, True)

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def crlf_copy(self, src, name):
        with open(src, 'rb') as f:
            data = f.read().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        with open(self.path(name), 'wb') as f:
            f.write(data)
        return self.path(name)


class TestMethodFiles(TempDir):
    def test_crlf_and_lf_read_the_same(self):
        for load, src in ((method.load_method, method.METHOD_PATH), (method.load_router, method.ROUTER_PATH)):
            lf, crlf = load(src), load(self.crlf_copy(src, os.path.basename(src)))
            self.assertEqual((lf.sha256, lf.values, lf.open), (crlf.sha256, crlf.values, crlf.open))

    def test_the_repo_files_are_lf_and_ascii(self):
        for name in sorted(os.listdir(HERE)):
            if name.endswith(('.py', '.md')):
                with open(os.path.join(HERE, name), 'rb') as f:
                    data = f.read()
                self.assertNotIn(b'\r', data, name)
                data.decode('ascii')

    def test_the_open_slots_are_the_ones_the_readme_lists(self):
        self.assertEqual(method.load_method().open, ['window_s', 'max_gap_s', 'pad_fraction', 'pad_min_px'])
        self.assertEqual(method.load_router().open, ['timeout_s'])
        with open(os.path.join(HERE, 'README.md'), encoding='utf-8') as f:
            readme = f.read()
        for slot in ('window_s', 'max_gap_s', 'pad_fraction', 'pad_min_px', 'timeout_s'):
            self.assertIn('`%s`' % slot, readme)

    def test_today_s_caps_are_closed_at_the_ladder_s_values(self):
        r = method.load_router()
        self.assertEqual([r[k] for k in ('min_interval_s', 'max_per_minute', 'minute_s', 'max_in_flight', 'backoff_s')],
                         [4, 15, 60, 1, 30])
        self.assertEqual(method.load_method()['depth_skew_s'], 0.067)

    def test_bad_blocks_are_refused(self):
        good = method.load_router().block
        cases = [dict(good, timeout_s={'open': True}), dict(good, max_in_flight=2), dict(good, order='random'),
                 {k: v for k, v in good.items() if k != 'backoff_s'}, dict(good, extra=1), dict(good, min_interval_s=0)]
        for block in cases:
            with self.assertRaises(method.MethodError):
                method.router_from(block)
        m = method.load_method().block
        for block in (dict(m, frame_px={'den-oak': [1921, 1080]}), dict(m, window_s=0)):
            with self.assertRaises(method.MethodError):
                method.method_from(block)

    def test_two_blocks_or_none_are_refused(self):
        with open(method.ROUTER_PATH, encoding='utf-8') as f:
            text = f.read()
        block = text[text.index(method.ROUTER_FENCE):]
        block = block[:block.index('```\n', len(method.ROUTER_FENCE)) + 4]
        for name, body in (('two.md', text + '\n' + block), ('none.md', 'no block here\n')):
            with open(self.path(name), 'w', encoding='utf-8') as f:
                f.write(body)
            with self.assertRaises(method.MethodError):
                method.load_router(self.path(name))

    def test_the_hash_ignores_prose_and_follows_numbers(self):
        with open(method.ROUTER_PATH, encoding='utf-8') as f:
            text = f.read()
        with open(self.path('prose.md'), 'w', encoding='utf-8') as f:
            f.write('A new first line.\n' + text)
        with open(self.path('number.md'), 'w', encoding='utf-8') as f:
            f.write(text.replace('"backoff_s": 30', '"backoff_s": 31'))
        sha = method.load_router().sha256
        self.assertEqual(method.load_router(self.path('prose.md')).sha256, sha)
        self.assertNotEqual(method.load_router(self.path('number.md')).sha256, sha)


class TestJsonLines(TempDir):
    def test_crlf_lines_and_blank_lines_read_the_same(self):
        recs = [{'a': 1, 'b': 'A'}, {'a': 2, 'b': None}]
        records.write_jsonl(self.path('x.jsonl'), recs)
        with open(self.path('x.jsonl'), 'rb') as f:
            self.assertNotIn(b'\r', f.read())
        crlf = self.crlf_copy(self.path('x.jsonl'), 'y.jsonl')
        with open(crlf, 'ab') as f:
            f.write(b'\r\n')
        self.assertEqual(records.read_jsonl(crlf), recs)

    def test_a_bad_line_names_its_number(self):
        with open(self.path('bad.jsonl'), 'w', encoding='utf-8') as f:
            f.write('{"a": 1}\n[1, 2]\n')
        with self.assertRaisesRegex(records.RecordError, 'line 2'):
            records.read_jsonl(self.path('bad.jsonl'))


class TestCommandLine(TempDir):
    def setUp(self):
        super().setUp()
        code, out, _ = cli('fixtures', '--out', self.path('fx'))
        self.assertEqual(code, 0)
        self.assertTrue(out.isascii())

    def scene(self, name):
        return ['--observations', self.path('fx', name, 'observations.jsonl'),
                '--tracks', self.path('fx', name, 'tracks.jsonl')]

    def test_fixtures_route_and_gate_pass_end_to_end(self):
        for name in ('crossing', 'missing_depth', 'stale_observation', 'two_tracks', 'track_leaves'):
            rq, bd = self.path(name + '-requests.jsonl'), self.path(name + '-bundles.jsonl')
            code, out, err = cli('route', *self.scene(name), '--from', '3', '--to', '70', '--requests', rq, '--bundles', bd)
            self.assertEqual(code, 0, err)
            self.assertIn('practice replay', out)
            code, out, err = cli('gate', *self.scene(name), '--bundles', bd, '--requests', rq,
                                 '--crossings', self.path('fx', name, 'crossings.jsonl'))
            self.assertEqual(code, 0, out + err)
            self.assertIn('verdict: pass (practice: open slots', out)
            self.assertTrue(out.isascii())

    def test_a_scored_gate_refuses_with_exit_2(self):
        rq, bd = self.path('rq.jsonl'), self.path('bd.jsonl')
        cli('route', *self.scene('two_tracks'), '--from', '2', '--to', '20', '--requests', rq, '--bundles', bd)
        code, out, err = cli('gate', *self.scene('two_tracks'), '--bundles', bd, '--scored')
        self.assertEqual((code, out), (2, ''))
        self.assertIn('refuses', err)
        self.assertIn('timeout_s', err)

    def test_a_failing_gate_exits_1(self):
        rq, bd = self.path('rq.jsonl'), self.path('bd.jsonl')
        cli('route', *self.scene('two_tracks'), '--from', '2', '--to', '20', '--requests', rq, '--bundles', bd)
        bundles = records.read_jsonl(bd)
        bundles[0]['missing'] = []
        bundles[0]['frames'] = []
        records.write_jsonl(bd, bundles)
        code, out, _ = cli('gate', *self.scene('two_tracks'), '--bundles', bd)
        self.assertEqual(code, 1)
        self.assertIn('missing      FAIL', out)

    def test_assemble_writes_one_bundle(self):
        out_file = self.path('one.jsonl')
        code, out, _ = cli('assemble', *self.scene('stale_observation'), '--track', 'A', '--at', '7', '--out', out_file)
        self.assertEqual(code, 0)
        (bundle,) = records.read_jsonl(out_file)
        self.assertEqual((bundle['version'], bundle['missing']), ('person_bundle/v1', ['track', 'person_box', 'keypoints']))
        self.assertIn('practice: open slots', out)

    def test_bad_input_is_refused_with_exit_2(self):
        code, _, err = cli('assemble', '--observations', self.path('nope.jsonl'), '--tracks', self.path('nope.jsonl'),
                           '--track', 'A', '--at', '0', '--out', self.path('o.jsonl'))
        self.assertEqual(code, 2)
        self.assertIn('refused', err)

    def test_fixture_files_are_utf8_lf_json_lines(self):
        with open(self.path('fx', 'crossing', 'tracks.jsonl'), 'rb') as f:
            data = f.read()
        self.assertNotIn(b'\r', data)
        self.assertEqual(len([json.loads(x) for x in data.decode('utf-8').splitlines()]), 50)


if __name__ == '__main__':
    unittest.main()

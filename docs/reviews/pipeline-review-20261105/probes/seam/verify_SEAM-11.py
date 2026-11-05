"""verify_SEAM-11: where does den-take's take.py connect when --camera is left out?

No connection is ever made: http.client.HTTPConnection is replaced by a recorder
that notes the host and port and raises ConnectionRefusedError, so take.py's
own "cannot reach the camera" refusal ends each run. free_bytes is patched so
the 35 GB check passes in a temp folder.

Cases:
  a) no --camera, no --token-file (the defaults): does it try to connect at all
     on a machine without C:\\den-takes\\private\\camera-token.txt?
  b) no --camera, a synthetic --token-file: which host does it try?
  c) --camera http://127.0.0.1:<port>: which host does it try?

Run:
  python3 -B verify_SEAM-11.py <den-take-script checkout>
Synthetic token only, temp dir only; nothing leaves this process.
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock


def main():
    root = os.path.abspath(sys.argv[1])
    sys.path.insert(0, os.path.join(root, 'tools', 'den-take'))
    import take  # noqa: E402

    attempts = []

    class Recorder:
        def __init__(self, host, port=None, *a, **k):
            attempts.append('%s:%s' % (host, port))
            raise ConnectionRefusedError('probe: no connection is made')

    tmp = tempfile.mkdtemp(prefix='verify_seam11_')
    token = os.path.join(tmp, 'private', 'camera-token.txt')
    os.makedirs(os.path.dirname(token))
    with open(token, 'w') as f:
        f.write('synthetic-probe-token-' + 'x' * 24 + '\n')
    cases = (('a defaults', ['--script', 'commission', '--out', os.path.join(tmp, 'a')]),
             ('b token, no --camera', ['--script', 'commission', '--out', os.path.join(tmp, 'b'), '--token-file', token]),
             ('c token, --camera 127.0.0.1', ['--script', 'commission', '--out', os.path.join(tmp, 'c'),
                                              '--token-file', token, '--camera', 'http://127.0.0.1:18090']))
    print('RESULT DEFAULT_CAMERA = %s' % take.DEFAULT_CAMERA)
    with mock.patch.object(take.http.client, 'HTTPConnection', Recorder), \
            mock.patch.object(take, 'free_bytes', lambda p: 10 ** 12):
        for label, argv in cases:
            del attempts[:]
            err, out = io.StringIO(), io.StringIO()
            with redirect_stderr(err), redirect_stdout(out):
                code = take.main(argv)
            print('RESULT %-28s exit %d, connection attempts %s, said: %s'
                  % (label, code, attempts or 'none', (err.getvalue() + out.getvalue()).strip()[:160]))
    readme = open(os.path.join(root, 'tools', 'den-take', 'README.md'), encoding='utf-8').read()
    step4 = 'take.py --script commission --out C:\\den-takes\\c1\n' in readme
    print('RESULT README step 4 runs take.py without --camera (relies on the default): %s' % step4)
    print('temp dir: %s' % tmp)


if __name__ == '__main__':
    main()

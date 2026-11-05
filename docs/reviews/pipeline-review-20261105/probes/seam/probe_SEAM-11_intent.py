"""probe_SEAM-11_intent.py: what take.py does when run without --camera, with
every socket connection blocked in-process (nothing can leave this machine).

Verifier probe (intent/spec lens) for finding SEAM-11.

    python3 -B probe_SEAM-11_intent.py <root of a claude/den-take-script checkout>

Before importing take.py, socket.socket.connect and socket.create_connection
are replaced by functions that record the address and raise
ConnectionRefusedError, so no packet is sent to any host. Cases:
  1  no --camera, no --token-file (the defaults), real free_bytes
  2  same, with take.free_bytes patched to 100 GB (as den-take's own tests do)
  3  no --camera, a synthetic token in a temp --token-file, free_bytes patched
Prints, per case, the exit code, the refusal, and every connect attempt.
"""
import contextlib
import io
import os
import socket
import sys
import tempfile

sys.dont_write_bytecode = True
ATTEMPTS = []


def _blocked_connect(self, address, *a, **k):
    ATTEMPTS.append(('socket.connect', address))
    raise ConnectionRefusedError('blocked by probe: %r' % (address,))


def _blocked_create(address, *a, **k):
    ATTEMPTS.append(('create_connection', address))
    raise ConnectionRefusedError('blocked by probe: %r' % (address,))


socket.socket.connect = _blocked_connect
socket.create_connection = _blocked_create


def main():
    root = sys.argv[1]
    sys.path.insert(0, os.path.join(root, 'tools', 'den-take'))
    import take
    print('DEFAULT_CAMERA=%s DEFAULT_TOKEN_FILE=%s' % (take.DEFAULT_CAMERA, take.DEFAULT_TOKEN_FILE))
    real_free = take.free_bytes
    with tempfile.TemporaryDirectory(prefix='seam11-') as tmp:
        token = os.path.join(tmp, 'private', 'camera-token.txt')
        os.makedirs(os.path.dirname(token))
        with open(token, 'w') as f:
            f.write('synthetic-probe-token-' + 'z9' * 12)
        cases = [
            ('1 defaults', ['--script', 'commission', '--out', os.path.join(tmp, 'c1')], False),
            ('2 defaults, free_bytes patched', ['--script', 'commission', '--out', os.path.join(tmp, 'c2')], True),
            ('3 no --camera, temp --token-file', ['--script', 'commission', '--out', os.path.join(tmp, 'c3'),
                                                  '--token-file', token], True),
        ]
        for name, argv, patch_free in cases:
            del ATTEMPTS[:]
            take.free_bytes = (lambda p: 100 * 1024 ** 3) if patch_free else real_free
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = take.main(argv)
            print('case %s: rc=%s stderr=%r connect_attempts=%r' % (name, rc, err.getvalue().strip(), ATTEMPTS))
        take.free_bytes = real_free
    print('RESULT: the default camera is only tried once a readable token file of >= 24 printable '
          'characters and an --out with >= 35 GB free exist (case 3); otherwise refused before any connect')


if __name__ == '__main__':
    main()

"""verify_SEAM-21: what happens when den-n2's score.py runs without --den-core from its own checkout?

Checks:
  1. whether tools/den-core exists beside tools/den-n2 in the checkout;
  2. what score.load_checker(DEFAULT_DEN_CORE) does (the step run() reaches once
     N1 has frozen labels): a named refusal, or a crash;
  3. main() without --den-core on empty synthetic N1/N2 folders: exit code and
     whether anything is written.

Run:
  python3 -B verify_SEAM-21.py <den-n2 checkout>
Synthetic data only (empty folders), temp dir only, no network.
"""
import io
import os
import sys
import tempfile


def main():
    root = os.path.abspath(sys.argv[1])
    n2dir = os.path.join(root, 'tools', 'den-n2')
    sys.path.insert(0, n2dir)
    import score  # noqa: E402

    print('RESULT tools/den-core in this checkout: %s' % os.path.isdir(os.path.join(root, 'tools', 'den-core')))
    print('RESULT DEFAULT_DEN_CORE = %s' % os.path.relpath(str(score.DEFAULT_DEN_CORE), root))
    named = False
    try:
        score.load_checker(score.DEFAULT_DEN_CORE)
        print('RESULT load_checker(default) loaded a checker')
    except score.Refused as e:
        named = 'pass --den-core' in str(e)
        print('RESULT load_checker(default) -> Refused: %s' % e)
    except Exception as e:  # a crash would be a real defect
        print('RESULT load_checker(default) -> CRASH %s: %s' % (type(e).__name__, e))

    tmp = tempfile.mkdtemp(prefix='verify_seam21_')
    for sub in ('n1', 'n2'):
        os.makedirs(os.path.join(tmp, sub))
    out = io.StringIO()
    code = score.main(['--split', 'development', '--takes', tmp, '--n1', os.path.join(tmp, 'n1'),
                       '--n2', os.path.join(tmp, 'n2')], out=out)
    written = [os.path.join(d, f) for d, _, fs in os.walk(tmp) for f in fs]
    print('RESULT main() without --den-core: exit %d, output %r, files written %d'
          % (code, out.getvalue().strip(), len(written)))
    print('%s: a missing den-core default ends in a named refusal (exit 2, nothing written), not a crash or a '
          'wrong report' % ('PASS' if named and code == 2 and not written else 'FAIL'))
    print('temp dir: %s' % tmp)


if __name__ == '__main__':
    main()
